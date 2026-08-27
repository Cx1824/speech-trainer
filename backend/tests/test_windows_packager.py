"""Windows 私测打包器的跨平台结构回归测试。"""

from __future__ import annotations

import hashlib
import io
import importlib.util
import json
import shutil
import sqlite3
import zipfile
from pathlib import Path
from types import ModuleType


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PACKAGER_PATH = REPOSITORY_ROOT / "scripts" / "build_windows_test_package.py"


def _load_packager() -> ModuleType:
    spec = importlib.util.spec_from_file_location("windows_packager", PACKAGER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_empty_saved_model_uses_current_deepseek_default(tmp_path: Path) -> None:
    packager = _load_packager()
    database = tmp_path / "config.db"
    connection = sqlite3.connect(database)
    try:
        connection.execute("CREATE TABLE api_config (id INTEGER PRIMARY KEY, llm_json TEXT)")
        connection.execute(
            "INSERT INTO api_config (id, llm_json) VALUES (1, ?)",
            (
                json.dumps(
                    {
                        "provider": "deepseek",
                        "api_key": "test-key-for-package",
                        "base_url": "",
                        "model": "",
                    }
                ),
            ),
        )
        connection.commit()
    finally:
        connection.close()

    config = packager._read_deepseek_config(database)

    assert config["base_url"] == "https://api.deepseek.com"
    assert config["model"] == "deepseek-v4-pro"


def test_command_wrappers_remain_ascii_and_keep_failures_visible() -> None:
    for name in ("启动训练器.cmd", "停止训练器.cmd"):
        content = (REPOSITORY_ROOT / "packaging" / "windows" / name).read_bytes()
        text = content.decode("ascii")
        assert "powershell.exe" in text
        assert "windows-launcher.ps1" in text

    start_text = (
        REPOSITORY_ROOT / "packaging" / "windows" / "启动训练器.cmd"
    ).read_text(encoding="ascii")
    assert "launch.success" in start_text
    assert "pause" in start_text.lower()


def test_windows_launcher_uses_verified_bundled_runtime_without_winget() -> None:
    launcher = (
        REPOSITORY_ROOT / "packaging" / "windows" / "windows-launcher.ps1"
    ).read_text(encoding="utf-8")

    assert '$BundledPython = Join-Path $BundledPythonDir "python.exe"' in launcher
    assert "Expand-Archive -LiteralPath $archive" in launcher
    assert "Get-FileHash -LiteralPath $archive -Algorithm SHA256" in launcher
    assert "--no-index" in launcher
    assert "--find-links $WheelhouseDir" in launcher
    assert "$env:PYTHONNOUSERSITE = \"1\"" in launcher
    assert "$env:SPEECH_TRAINER_MODEL_DIR = $PackagedModelDir" in launcher
    assert "Test-InstalledDependencies $staging" in launcher
    assert "$probeCode = \"import sys;" in launcher
    assert "sys.maxsize>2**32" in launcher
    assert 'struct.calcsize(`"P`")' not in launcher
    assert "winget" not in launcher.lower()
    assert "Get-Command \"python" not in launcher
    assert "$VenvPython" not in launcher
    assert "install_local_asr.py\")" not in launcher


def test_windows_dependency_set_avoids_unneeded_uvicorn_standard_extra() -> None:
    packager = _load_packager()

    dependencies = packager._windows_dependencies()

    assert any(item.startswith("uvicorn") for item in dependencies)
    assert all("uvicorn[standard]" not in item for item in dependencies)
    assert any(item.startswith("colorama") for item in dependencies)


def test_offline_models_copy_only_verified_distribution_files(tmp_path: Path) -> None:
    packager = _load_packager()
    source_root = tmp_path / "installed-models"
    model_root = source_root / "fixture-model"
    model_root.mkdir(parents=True)
    payloads = {
        "tokens.txt": b"tokens",
        "model.int8.onnx": b"model",
        "LICENSE": b"license",
    }
    for name, payload in payloads.items():
        (model_root / name).write_bytes(payload)
    (model_root / "personal-recording.wav").write_bytes(b"must-not-be-copied")
    packager.MODEL_BUNDLES = (
        {
            "name": "fixture-model",
            "archive": "fixture.tar.bz2",
            "url": "https://example.invalid/fixture.tar.bz2",
            "sha256": "archive-fixture",
            "required": ("tokens.txt", "model.int8.onnx"),
            "files": {
                name: hashlib.sha256(payload).hexdigest()
                for name, payload in payloads.items()
            },
        },
    )
    package_root = tmp_path / "package"
    package_root.mkdir()

    manifest = packager._prepare_offline_models(
        package_root,
        tmp_path / "unused-cache",
        source_root,
    )

    packaged = package_root / "runtime" / "models" / "fixture-model"
    assert manifest["offline"] is True
    assert set(path.name for path in packaged.iterdir()) == set(payloads)
    assert not (packaged / "personal-recording.wav").exists()


def _fake_windows_x64_python_zip() -> bytes:
    executable = bytearray(512)
    executable[:2] = b"MZ"
    executable[0x3C:0x40] = (0x80).to_bytes(4, "little")
    executable[0x80:0x84] = b"PE\0\0"
    executable[0x84:0x86] = (0x8664).to_bytes(2, "little")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as bundle:
        bundle.writestr("python.exe", executable)
        bundle.writestr("python312.dll", b"fixture")
        bundle.writestr("vcruntime140.dll", b"fixture")
        bundle.writestr("LICENSE.txt", b"fixture")
        bundle.writestr("Lib/venv/__init__.py", b"")
        bundle.writestr("Lib/site-packages/pip/__init__.py", b"")
    return output.getvalue()


def _write_fake_runtime(package_root: Path, packager: ModuleType) -> None:
    runtime = package_root / "runtime"
    wheels = runtime / "wheels"
    wheels.mkdir(parents=True)
    python_archive = _fake_windows_x64_python_zip()
    (runtime / packager.PYTHON_RUNTIME_ARCHIVE).write_bytes(python_archive)
    (runtime / "runtime.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "python_version": packager.PYTHON_RUNTIME_VERSION,
                "architecture": "x86_64",
                "archive": packager.PYTHON_RUNTIME_ARCHIVE,
                "archive_sha256": hashlib.sha256(python_archive).hexdigest(),
            }
        ),
        encoding="utf-8",
    )
    requirements = b"fastapi\n"
    (runtime / "requirements.txt").write_bytes(requirements)
    wheel_names = (
        "sherpa_onnx-1.0-cp312-cp312-win_amd64.whl",
        "sherpa_onnx_core-1.0-py3-none-win_amd64.whl",
        "numpy-1.0-cp312-cp312-win_amd64.whl",
        "scipy-1.0-cp312-cp312-win_amd64.whl",
    )
    wheel_hashes = {}
    for name in wheel_names:
        payload = f"fixture:{name}".encode()
        (wheels / name).write_bytes(payload)
        wheel_hashes[name] = hashlib.sha256(payload).hexdigest()
    (runtime / "wheelhouse.json").write_text(
        json.dumps(
            {
                "requirements_sha256": hashlib.sha256(requirements).hexdigest(),
                "target": "CPython 3.12 / Windows x64",
                "wheels": wheel_hashes,
            }
        ),
        encoding="utf-8",
    )
    bundled_models = []
    for model in packager.MODEL_BUNDLES:
        model_root = runtime / "models" / model["name"]
        model_root.mkdir(parents=True)
        files = {}
        for name in model["required"]:
            payload = f"fixture:{model['name']}:{name}".encode()
            (model_root / name).write_bytes(payload)
            files[name] = hashlib.sha256(payload).hexdigest()
        bundled_models.append(
            {
                "name": model["name"],
                "source": model["url"],
                "source_archive": model["archive"],
                "source_sha256": model["sha256"],
                "required": list(model["required"]),
                "files": files,
            }
        )
    (runtime / "models.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "offline": True,
                "models": bundled_models,
            }
        ),
        encoding="utf-8",
    )


def test_archive_contains_only_the_expected_fake_key_location(
    tmp_path: Path,
) -> None:
    packager = _load_packager()
    fixture_root = tmp_path / "fixture"
    backend = fixture_root / "backend"
    frontend = fixture_root / "frontend"
    windows = fixture_root / "packaging" / "windows"

    (backend / "app").mkdir(parents=True)
    (backend / "scripts").mkdir()
    (frontend / "dist").mkdir(parents=True)
    windows.mkdir(parents=True)
    (backend / "app" / "main.py").write_text("app = object()\n", encoding="utf-8")
    (backend / "scripts" / "install_local_asr.py").write_text("\n", encoding="utf-8")
    (backend / "pyproject.toml").write_text("[project]\nname='fixture'\n", encoding="utf-8")
    (frontend / "dist" / "index.html").write_text("<main>fixture</main>\n", encoding="utf-8")
    (fixture_root / "LICENSE").write_text("fixture license\n", encoding="utf-8")
    (fixture_root / "THIRD_PARTY_NOTICES.md").write_text("fixture notices\n", encoding="utf-8")
    for name in (
        "README-WINDOWS.txt",
        "windows-launcher.ps1",
        "启动训练器.cmd",
        "停止训练器.cmd",
    ):
        shutil.copy2(REPOSITORY_ROOT / "packaging" / "windows" / name, windows / name)

    packager.ROOT = fixture_root
    packager.BACKEND = backend
    packager.FRONTEND = frontend
    packager.WINDOWS_FILES = windows

    config = {
        "provider": "deepseek",
        "base_url": "https://api.deepseek.com",
        "api_key": "test-key-for-package",
        "model": "deepseek-v4-pro",
    }
    package_root = tmp_path / packager.PACKAGE_ROOT_NAME
    package_root.mkdir()
    packager._copy_application(package_root)
    packager._write_private_env(package_root / "backend" / ".env", config)
    _write_fake_runtime(package_root, packager)
    packager._write_manifest(package_root, config)
    archive = tmp_path / "fixture.zip"
    packager._build_archive(package_root, archive)

    verification = packager._verify_archive(archive, config["api_key"])

    assert verification["file_count"] > 0
    assert verification["python_version"] == packager.PYTHON_RUNTIME_VERSION
    assert verification["wheel_count"] == 4
    assert verification["model_count"] == 2


def test_pe_verifier_rejects_32_bit_runtime() -> None:
    packager = _load_packager()
    executable = bytearray(512)
    executable[:2] = b"MZ"
    executable[0x3C:0x40] = (0x80).to_bytes(4, "little")
    executable[0x80:0x84] = b"PE\0\0"
    executable[0x84:0x86] = (0x014C).to_bytes(2, "little")

    try:
        packager._verify_windows_x64_executable(bytes(executable), "fixture")
    except RuntimeError as exc:
        assert "不是 Windows x64" in str(exc)
    else:
        raise AssertionError("32-bit executable should have been rejected")
