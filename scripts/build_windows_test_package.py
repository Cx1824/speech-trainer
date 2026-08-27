#!/usr/bin/env python3
"""构建不含个人训练数据、但含临时 DeepSeek Key 的 Windows 好友测试包。"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
BACKEND = ROOT / "backend"
WINDOWS_FILES = ROOT / "packaging" / "windows"
DEFAULT_CONFIG_DB = (
    Path.home()
    / "Library"
    / "Application Support"
    / "SpeechTrainer"
    / "data"
    / "speech_trainer.db"
)
PACKAGE_ROOT_NAME = "SpeechTrainer-Windows-Test"
PYTHON_RUNTIME_VERSION = "3.12.14"
PYTHON_RUNTIME_RELEASE = "20260814"
PYTHON_RUNTIME_SOURCE = (
    "https://github.com/astral-sh/python-build-standalone/releases/download/"
    "20260814/cpython-3.12.14%2B20260814-x86_64-pc-windows-msvc-"
    "install_only_stripped.tar.gz"
)
PYTHON_RUNTIME_SOURCE_SHA256 = (
    "89f18f6932917163b74339ebcec2645c8e47ae7f1c5f2ac37f2b4f4cf3beb647"
)
PYTHON_RUNTIME_ARCHIVE = f"python-{PYTHON_RUNTIME_VERSION}-windows-x86_64.zip"
RUNTIME_CACHE = ROOT / "artifacts" / "runtime-cache"
MODEL_BUNDLES = (
    {
        "name": "sherpa-onnx-streaming-zipformer-zh-int8-2025-06-30",
        "archive": "sherpa-onnx-streaming-zipformer-zh-int8-2025-06-30.tar.bz2",
        "url": (
            "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
            "sherpa-onnx-streaming-zipformer-zh-int8-2025-06-30.tar.bz2"
        ),
        "sha256": "5a2832047ea1f97dd0dc595b816c230c4bafad65cfc0341fa57517cadc50afd0",
        "required": (
            "tokens.txt",
            "encoder.int8.onnx",
            "decoder.onnx",
            "joiner.int8.onnx",
        ),
        "files": {
            "README.md": "24a953594138ebd61a4e60637febcff1cecd4f79ed05632109a937b74f9ebcd7",
            "tokens.txt": "6193c7ea1c96d0d9a1e9652789b40d13a8a913b434a5451e93158f5a09fd6652",
            "encoder.int8.onnx": "5ac51e27981bb4dab01bb9be4958453ba50c3b61c063ddda0eab23fd3671aa4f",
            "decoder.onnx": "06522ad63cec0fdf6809f4e1db9bb4f7d710c34582e3b35db62ac60eccafac7e",
            "joiner.int8.onnx": "b34584dc6f561089e1d747fedebb3765f2caa72c927ef54d7ca55e5ae40a814b",
        },
    },
    {
        "name": "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17",
        "archive": "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2",
        "url": (
            "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
            "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2"
        ),
        "sha256": "7d1efa2138a65b0b488df37f8b89e3d91a60676e416f515b952358d83dfd347e",
        "required": ("tokens.txt", "model.int8.onnx"),
        "files": {
            "LICENSE": "221c6df10b0931a5629adad671ea48fb7747e034c414b6d2bfa275bc3dd4ea17",
            "README.md": "763991a00edaea534ab36bf1b7cf89e61e911666dcfabbba71f91f9f7c593a63",
            "tokens.txt": "f449eb28dc567533d7fa59be34e2abca8784f771850c78a47fb731a31429a1dc",
            "model.int8.onnx": "c71f0ce00bec95b07744e116345e33d8cbbe08cef896382cf907bf4b51a2cd51",
        },
    },
)
DEFAULT_MODEL_SOURCE_DIR = (
    Path.home() / "Library" / "Application Support" / "SpeechTrainer" / "models"
)
WINDOWS_ONLY_DEPENDENCIES = (
    # click declares this only when platform_system == "Windows". pip download
    # evaluates markers on the macOS build host even with --platform, so include
    # the target-only dependency explicitly in the offline wheelhouse.
    "colorama>=0.4.6",
)


def _read_deepseek_config(database: Path) -> dict[str, str]:
    if not database.is_file():
        raise RuntimeError(f"找不到运行配置数据库：{database}")
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        row = connection.execute(
            "SELECT llm_json FROM api_config WHERE id = 1"
        ).fetchone()
    finally:
        connection.close()
    if not row or not row[0]:
        raise RuntimeError("运行配置中没有已保存的 LLM 配置")

    config = json.loads(row[0])
    provider = str(config.get("provider", "")).strip()
    api_key = str(config.get("api_key", "")).strip()
    if provider != "deepseek":
        raise RuntimeError("当前 LLM 不是 DeepSeek，已停止构建以免打包错误密钥")
    if not api_key:
        raise RuntimeError("当前 DeepSeek 配置没有 API Key")
    return {
        "provider": provider,
        "base_url": str(config.get("base_url", "")).strip()
        or "https://api.deepseek.com",
        "api_key": api_key,
        "model": str(config.get("model", "")).strip() or "deepseek-v4-pro",
    }


def _dotenv(value: str) -> str:
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\r", "\\r")
        .replace("\n", "\\n")
    )
    return f'"{escaped}"'


def _write_private_env(destination: Path, config: dict[str, str]) -> None:
    content = "\n".join(
        [
            "# 私人好友测试包：包含临时共享 DeepSeek Key，禁止公开分发。",
            "APP_ENV=production",
            "APP_HOST=127.0.0.1",
            "APP_PORT=17860",
            "APP_LOG_LEVEL=INFO",
            "CORS_ORIGINS=http://127.0.0.1:17860,http://localhost:17860",
            "ALLOWED_HOSTS=localhost,127.0.0.1,[::1]",
            "DATABASE_URL=sqlite+aiosqlite:///./data/speech_trainer.db",
            "UPLOAD_DIR=./uploads",
            "REPORT_DIR=./output",
            "FRONTEND_DIST_DIR=../frontend_dist",
            "",
            "LLM_PROVIDER=deepseek",
            f"LLM_BASE_URL={_dotenv(config['base_url'])}",
            f"LLM_API_KEY={_dotenv(config['api_key'])}",
            f"LLM_MODEL={_dotenv(config['model'])}",
            "LLM_TEMPERATURE=0.7",
            "",
            "ASR_PROVIDER=sherpa_onnx",
            "ASR_BASE_URL=",
            "ASR_API_KEY=",
            "ASR_API_SECRET=",
            "ASR_MODEL=sherpa-onnx-streaming-zipformer-zh-int8-2025-06-30",
            "",
            "TTS_PROVIDER=edge",
            "TTS_BASE_URL=",
            "TTS_API_KEY=",
            "TTS_API_SECRET=",
            "TTS_VOICE=zh-CN-YunjianNeural",
            "TTS_SPEED=1.0",
            "",
        ]
    )
    destination.write_text(content, encoding="utf-8", newline="\n")


def _download_verified_runtime(destination: Path) -> Path:
    """下载并校验固定版本的 Windows Python；只发生在构建机。"""
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file() and _sha256(destination) == PYTHON_RUNTIME_SOURCE_SHA256:
        return destination

    temporary = destination.with_suffix(destination.suffix + ".tmp")
    with urllib.request.urlopen(PYTHON_RUNTIME_SOURCE, timeout=120) as response:
        with temporary.open("wb") as output:
            shutil.copyfileobj(response, output)
    actual_hash = _sha256(temporary)
    if actual_hash != PYTHON_RUNTIME_SOURCE_SHA256:
        temporary.unlink(missing_ok=True)
        raise RuntimeError(
            "Windows Python 运行时校验失败："
            f"期望 {PYTHON_RUNTIME_SOURCE_SHA256}，实际 {actual_hash}"
        )
    temporary.replace(destination)
    return destination


def _download_verified_asset(
    destination: Path,
    *,
    url: str,
    sha256: str,
    description: str,
) -> Path:
    """下载并校验构建资产；下载只发生在构建机，不发生在测试电脑。"""
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file() and _sha256(destination) == sha256:
        return destination

    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.unlink(missing_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "SpeechTrainer/0.1"})
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            with temporary.open("wb") as output:
                shutil.copyfileobj(response, output, length=1024 * 1024)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    actual_hash = _sha256(temporary)
    if actual_hash != sha256:
        temporary.unlink(missing_ok=True)
        raise RuntimeError(
            f"{description}校验失败：期望 {sha256}，实际 {actual_hash}"
        )
    temporary.replace(destination)
    return destination


def _prepare_offline_models(
    package_root: Path,
    cache_dir: Path,
    installed_root: Path | None = None,
) -> dict[str, object]:
    """把校验后的官方模型解入好友测试包，使目标电脑无需访问 GitHub。"""
    destination_root = package_root / "runtime" / "models"
    destination_root.mkdir(parents=True, exist_ok=True)
    bundled_models: list[dict[str, object]] = []

    for bundle in MODEL_BUNDLES:
        model_name = str(bundle["name"])
        expected_files = dict(bundle["files"])
        installed = installed_root / model_name if installed_root else None
        temporary = None
        if installed and installed.is_dir() and all(
            (installed / name).is_file()
            and _sha256(installed / name) == expected_hash
            for name, expected_hash in expected_files.items()
        ):
            extracted = installed
        else:
            archive = _download_verified_asset(
                cache_dir / str(bundle["archive"]),
                url=str(bundle["url"]),
                sha256=str(bundle["sha256"]),
                description=f"语音模型 {bundle['name']}",
            )
            temporary = tempfile.TemporaryDirectory(
                prefix="speech-trainer-model-build-"
            )
            extraction_root = Path(temporary.name)
            resolved_root = extraction_root.resolve()
            with tarfile.open(archive, "r:bz2") as source:
                members = source.getmembers()
                for member in members:
                    target = (extraction_root / member.name).resolve()
                    if (
                        not target.is_relative_to(resolved_root)
                        or member.issym()
                        or member.islnk()
                    ):
                        temporary.cleanup()
                        raise RuntimeError(
                            f"语音模型压缩包包含不安全路径：{member.name}"
                        )
                source.extractall(extraction_root, members=members, filter="data")
            extracted = extraction_root / model_name
            if not extracted.is_dir():
                temporary.cleanup()
                raise RuntimeError(f"语音模型压缩包结构错误：{model_name}")

        destination = destination_root / model_name
        destination.mkdir(parents=True)
        try:
            for name, expected_hash in expected_files.items():
                source_file = extracted / name
                if (
                    not source_file.is_file()
                    or _sha256(source_file) != expected_hash
                ):
                    raise RuntimeError(f"语音模型文件校验失败：{model_name}/{name}")
                shutil.copy2(source_file, destination / name)
        finally:
            if temporary is not None:
                temporary.cleanup()

        packaged_root = destination_root / model_name
        files = {
            path.relative_to(packaged_root).as_posix(): _sha256(path)
            for path in sorted(packaged_root.rglob("*"))
            if path.is_file()
        }
        bundled_models.append(
            {
                "name": bundle["name"],
                "source": bundle["url"],
                "source_archive": bundle["archive"],
                "source_sha256": bundle["sha256"],
                "required": list(bundle["required"]),
                "files": files,
            }
        )

    metadata: dict[str, object] = {
        "schema_version": 1,
        "offline": True,
        "models": bundled_models,
    }
    (package_root / "runtime" / "models.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return metadata


def _prepare_bundled_python(package_root: Path, source_archive: Path) -> dict[str, str]:
    """把固定上游 tar.gz 转成 PowerShell 5.1 可解压的 ZIP。"""
    if _sha256(source_archive) != PYTHON_RUNTIME_SOURCE_SHA256:
        raise RuntimeError("指定的 Windows Python 运行时 SHA-256 不匹配")

    runtime_dir = package_root / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    destination = runtime_dir / PYTHON_RUNTIME_ARCHIVE
    included: set[str] = set()
    with tarfile.open(source_archive, "r:gz") as source:
        with zipfile.ZipFile(
            destination,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
        ) as output:
            for member in source:
                if not member.isfile() or not member.name.startswith("python/"):
                    continue
                relative = member.name.removeprefix("python/")
                path = Path(relative)
                if not relative or path.is_absolute() or ".." in path.parts:
                    raise RuntimeError(f"Python 运行时包含不安全路径：{member.name}")
                stream = source.extractfile(member)
                if stream is None:
                    raise RuntimeError(f"无法读取 Python 运行时文件：{member.name}")
                output.writestr(relative.replace("\\", "/"), stream.read())
                included.add(relative.replace("\\", "/"))

    required = {
        "python.exe",
        "LICENSE.txt",
        "Lib/venv/__init__.py",
        "Lib/site-packages/pip/__init__.py",
    }
    missing = required - included
    if missing:
        raise RuntimeError(f"Python 运行时缺少必要文件：{sorted(missing)}")

    metadata = {
        "schema_version": 1,
        "python_version": PYTHON_RUNTIME_VERSION,
        "architecture": "x86_64",
        "archive": PYTHON_RUNTIME_ARCHIVE,
        "archive_sha256": _sha256(destination),
        "source": PYTHON_RUNTIME_SOURCE,
        "source_release": PYTHON_RUNTIME_RELEASE,
        "source_sha256": PYTHON_RUNTIME_SOURCE_SHA256,
    }
    (runtime_dir / "runtime.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return metadata


def _windows_dependencies() -> list[str]:
    """读取项目依赖；Windows 单进程包不需要 uvicorn 的开发热重载扩展。"""
    project = tomllib.loads((BACKEND / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = list(project["project"]["dependencies"])
    dependencies = [
        dependency.replace("uvicorn[standard]", "uvicorn")
        for dependency in dependencies
    ]
    return dependencies + list(WINDOWS_ONLY_DEPENDENCIES)


def _prepare_windows_wheelhouse(package_root: Path, cache_dir: Path) -> dict[str, object]:
    """预下载 CPython 3.12 / Windows x64 wheels，目标电脑离线安装依赖。"""
    dependencies = _windows_dependencies()
    requirements = "\n".join(dependencies) + "\n"
    requirements_sha256 = hashlib.sha256(requirements.encode("utf-8")).hexdigest()
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_marker = cache_dir / "wheelhouse.json"

    cache_valid = False
    if cache_marker.is_file():
        try:
            cached = json.loads(cache_marker.read_text(encoding="utf-8"))
            cached_wheels = cached.get("wheels", {})
            cache_valid = (
                cached.get("requirements_sha256") == requirements_sha256
                and isinstance(cached_wheels, dict)
                and bool(cached_wheels)
                and all(
                    (cache_dir / name).is_file()
                    and _sha256(cache_dir / name) == digest
                    for name, digest in cached_wheels.items()
                )
            )
        except (OSError, ValueError, TypeError):
            cache_valid = False

    if not cache_valid:
        for path in cache_dir.glob("*.whl"):
            path.unlink()
        requirements_file = cache_dir / "requirements.txt"
        requirements_file.write_text(requirements, encoding="utf-8")
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "download",
                "--disable-pip-version-check",
                "--only-binary=:all:",
                "--platform=win_amd64",
                "--python-version=3.12",
                "--implementation=cp",
                "--abi=cp312",
                f"--dest={cache_dir}",
                f"--requirement={requirements_file}",
            ],
            check=True,
        )
        wheels = {
            path.name: _sha256(path)
            for path in sorted(cache_dir.glob("*.whl"))
        }
        if not wheels:
            raise RuntimeError("Windows 依赖 wheel 下载结果为空")
        cache_payload = {
            "requirements_sha256": requirements_sha256,
            "target": "CPython 3.12 / Windows x64",
            "wheels": wheels,
        }
        cache_marker.write_text(
            json.dumps(cache_payload, indent=2) + "\n",
            encoding="utf-8",
        )
    else:
        cache_payload = json.loads(cache_marker.read_text(encoding="utf-8"))

    runtime_dir = package_root / "runtime"
    wheelhouse = runtime_dir / "wheels"
    shutil.copytree(
        cache_dir,
        wheelhouse,
        ignore=shutil.ignore_patterns("requirements.txt", "wheelhouse.json"),
    )
    (runtime_dir / "requirements.txt").write_text(requirements, encoding="utf-8")
    packaged_wheels = {
        path.name: _sha256(path)
        for path in sorted(wheelhouse.glob("*.whl"))
    }
    metadata = {
        "requirements_sha256": requirements_sha256,
        "target": "CPython 3.12 / Windows x64",
        "wheels": packaged_wheels,
    }
    (runtime_dir / "wheelhouse.json").write_text(
        json.dumps(metadata, indent=2) + "\n",
        encoding="utf-8",
    )
    return metadata


def _copy_application(package_root: Path) -> None:
    backend_target = package_root / "backend"
    frontend_target = package_root / "frontend_dist"
    scripts_target = package_root / "scripts"

    shutil.copytree(
        BACKEND / "app",
        backend_target / "app",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
    )
    scripts_target.mkdir(parents=True)
    (backend_target / "scripts").mkdir(parents=True)
    shutil.copy2(BACKEND / "pyproject.toml", backend_target / "pyproject.toml")
    shutil.copy2(
        BACKEND / "scripts" / "install_local_asr.py",
        backend_target / "scripts" / "install_local_asr.py",
    )
    shutil.copytree(FRONTEND / "dist", frontend_target)
    # Windows PowerShell 5.1 会把无 BOM 的脚本按系统 ANSI 编码读取；启动器
    # 含中文提示，因此测试包内显式写成 UTF-8 BOM，避免中文系统外乱码或解析失败。
    launcher_text = (WINDOWS_FILES / "windows-launcher.ps1").read_text(
        encoding="utf-8"
    )
    (scripts_target / "windows-launcher.ps1").write_text(
        launcher_text,
        encoding="utf-8-sig",
        newline="\r\n",
    )
    # Keep CMD contents ASCII with CRLF line endings. This avoids depending on the
    # tester's active Windows code page before PowerShell has even started.
    for command_name in ("启动训练器.cmd", "停止训练器.cmd"):
        command_text = (WINDOWS_FILES / command_name).read_text(encoding="ascii")
        (package_root / command_name).write_text(
            command_text,
            encoding="ascii",
            newline="\r\n",
        )
    shutil.copy2(WINDOWS_FILES / "README-WINDOWS.txt", package_root / "使用说明.txt")
    for name in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
        shutil.copy2(ROOT / name, package_root / name)

    # 运行目录在首次启动后只保存测试者自己的内容；不复制开发者数据库或材料。
    for name in ("data", "uploads", "output"):
        (backend_target / name).mkdir(parents=True, exist_ok=True)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_manifest(package_root: Path, config: dict[str, str]) -> None:
    files = {}
    for path in sorted(package_root.rglob("*")):
        if path.is_file() and path.name != "PACKAGE-MANIFEST.json":
            files[path.relative_to(package_root).as_posix()] = _sha256(path)
    manifest = {
        "package": PACKAGE_ROOT_NAME,
        "built_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "target": "Windows 10/11 x64",
        "runtime": (
            f"Bundled CPython {PYTHON_RUNTIME_VERSION} Windows x64; "
            "no system Python or Node.js required"
        ),
        "services": {
            "llm": {
                "provider": config["provider"],
                "base_url": config["base_url"],
                "model": config["model"],
                "api_key_included": True,
                "api_key_exposed_by_config_api": False,
            },
            "asr": "local sherpa-onnx with two bundled offline models",
            "tts": "edge",
        },
        "personal_training_data_included": False,
        "files": files,
    }
    (package_root / "PACKAGE-MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _build_archive(package_root: Path, archive: Path) -> None:
    with zipfile.ZipFile(
        archive,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as bundle:
        for path in sorted(package_root.rglob("*")):
            if path.is_file():
                name = Path(PACKAGE_ROOT_NAME) / path.relative_to(package_root)
                # Wheel and nested ZIP payloads are already compressed. Storing them
                # avoids a long, ineffective second compression pass.
                compression = (
                    zipfile.ZIP_STORED
                    if path.suffix.lower() in {".whl", ".zip"}
                    else zipfile.ZIP_DEFLATED
                )
                bundle.write(path, name.as_posix(), compress_type=compression)


def _verify_windows_x64_executable(payload: bytes, description: str) -> None:
    """Verify the minimum PE structure and AMD64 machine field."""
    if len(payload) < 0x40 or payload[:2] != b"MZ":
        raise RuntimeError(f"{description} 不是有效的 Windows 可执行文件")
    pe_offset = int.from_bytes(payload[0x3C:0x40], "little")
    if pe_offset + 6 > len(payload) or payload[pe_offset : pe_offset + 4] != b"PE\0\0":
        raise RuntimeError(f"{description} 缺少有效的 PE 文件头")
    machine = int.from_bytes(payload[pe_offset + 4 : pe_offset + 6], "little")
    if machine != 0x8664:
        raise RuntimeError(f"{description} 不是 Windows x64 程序")


def _verify_archive(archive: Path, api_key: str) -> dict[str, int | str]:
    forbidden_parts = {
        ".git",
        "node_modules",
        "__pycache__",
        ".venv",
        "evals",
        "tests",
    }
    key_bytes = api_key.encode("utf-8")
    key_locations: list[str] = []
    with zipfile.ZipFile(archive) as bundle:
        names = bundle.namelist()
        required = {
            f"{PACKAGE_ROOT_NAME}/启动训练器.cmd",
            f"{PACKAGE_ROOT_NAME}/停止训练器.cmd",
            f"{PACKAGE_ROOT_NAME}/scripts/windows-launcher.ps1",
            f"{PACKAGE_ROOT_NAME}/backend/.env",
            f"{PACKAGE_ROOT_NAME}/backend/app/main.py",
            f"{PACKAGE_ROOT_NAME}/frontend_dist/index.html",
            f"{PACKAGE_ROOT_NAME}/使用说明.txt",
            f"{PACKAGE_ROOT_NAME}/runtime/runtime.json",
            f"{PACKAGE_ROOT_NAME}/runtime/requirements.txt",
            f"{PACKAGE_ROOT_NAME}/runtime/wheelhouse.json",
            f"{PACKAGE_ROOT_NAME}/runtime/models.json",
        }
        missing = required - set(names)
        if missing:
            raise RuntimeError(f"压缩包缺少必要文件：{sorted(missing)}")

        for name in names:
            path = Path(name)
            if any(part in forbidden_parts for part in path.parts):
                raise RuntimeError(f"压缩包混入不应分发的目录：{name}")
            if path.suffix.lower() in {".db", ".sqlite", ".sqlite3"}:
                raise RuntimeError(f"压缩包混入数据库：{name}")
            data = bundle.read(name)
            if key_bytes in data:
                key_locations.append(name)

        expected_key_file = f"{PACKAGE_ROOT_NAME}/backend/.env"
        if key_locations != [expected_key_file]:
            raise RuntimeError("DeepSeek Key 应且只能出现在测试包 backend/.env 中")

        launcher = bundle.read(
            f"{PACKAGE_ROOT_NAME}/scripts/windows-launcher.ps1"
        )
        if not launcher.startswith(b"\xef\xbb\xbf"):
            raise RuntimeError("Windows PowerShell 启动器缺少 UTF-8 BOM")

        for command_name in ("启动训练器.cmd", "停止训练器.cmd"):
            command = bundle.read(f"{PACKAGE_ROOT_NAME}/{command_name}")
            try:
                command.decode("ascii")
            except UnicodeDecodeError as exc:
                raise RuntimeError(f"{command_name} 必须为 ASCII") from exc
            if b"\n" in command.replace(b"\r\n", b""):
                raise RuntimeError(f"{command_name} 不是纯 CRLF 换行")
        start_command = bundle.read(f"{PACKAGE_ROOT_NAME}/启动训练器.cmd")
        if b"launch.success" not in start_command or b"pause" not in start_command:
            raise RuntimeError("启动脚本缺少成功标记或失败停留保护")

        config_payload = json.loads(
            bundle.read(f"{PACKAGE_ROOT_NAME}/PACKAGE-MANIFEST.json")
        )
        if config_payload["personal_training_data_included"] is not False:
            raise RuntimeError("包清单没有明确排除个人训练数据")

        runtime_prefix = f"{PACKAGE_ROOT_NAME}/runtime/"
        runtime_manifest = json.loads(bundle.read(runtime_prefix + "runtime.json"))
        if (
            runtime_manifest.get("schema_version") != 1
            or runtime_manifest.get("python_version") != PYTHON_RUNTIME_VERSION
            or runtime_manifest.get("architecture") != "x86_64"
        ):
            raise RuntimeError("内置 Python 清单版本或架构错误")
        runtime_archive_name = runtime_manifest.get("archive")
        if (
            not isinstance(runtime_archive_name, str)
            or Path(runtime_archive_name).name != runtime_archive_name
        ):
            raise RuntimeError("内置 Python 清单的压缩包路径无效")
        runtime_archive_path = runtime_prefix + runtime_archive_name
        if runtime_archive_path not in names:
            raise RuntimeError("压缩包缺少内置 Python 运行时")
        runtime_archive = bundle.read(runtime_archive_path)
        if hashlib.sha256(runtime_archive).hexdigest() != runtime_manifest.get(
            "archive_sha256"
        ):
            raise RuntimeError("内置 Python 运行时 SHA-256 不匹配")
        with zipfile.ZipFile(io.BytesIO(runtime_archive)) as python_bundle:
            python_names = set(python_bundle.namelist())
            required_python_files = {
                "python.exe",
                "python312.dll",
                "vcruntime140.dll",
                "Lib/site-packages/pip/__init__.py",
            }
            missing_python = required_python_files - python_names
            if missing_python:
                raise RuntimeError(
                    f"内置 Python 运行时缺少文件：{sorted(missing_python)}"
                )
            _verify_windows_x64_executable(
                python_bundle.read("python.exe"), "内置 Python"
            )

        requirements = bundle.read(runtime_prefix + "requirements.txt")
        wheelhouse = json.loads(bundle.read(runtime_prefix + "wheelhouse.json"))
        if hashlib.sha256(requirements).hexdigest() != wheelhouse.get(
            "requirements_sha256"
        ):
            raise RuntimeError("离线依赖清单 SHA-256 不匹配")
        expected_wheels = wheelhouse.get("wheels")
        if not isinstance(expected_wheels, dict) or not expected_wheels:
            raise RuntimeError("离线依赖清单为空")
        actual_wheels = {
            name.removeprefix(runtime_prefix + "wheels/")
            for name in names
            if name.startswith(runtime_prefix + "wheels/")
        }
        if actual_wheels != set(expected_wheels):
            raise RuntimeError("离线依赖文件与清单不一致")
        for wheel_name, expected_hash in expected_wheels.items():
            if Path(wheel_name).name != wheel_name or not wheel_name.endswith(".whl"):
                raise RuntimeError("离线依赖清单包含无效路径")
            wheel_payload = bundle.read(runtime_prefix + "wheels/" + wheel_name)
            if hashlib.sha256(wheel_payload).hexdigest() != expected_hash:
                raise RuntimeError(f"离线依赖 SHA-256 不匹配：{wheel_name}")

        normalized_wheels = {name.lower().replace("-", "_") for name in actual_wheels}
        required_wheel_patterns = {
            "sherpa_onnx": ("sherpa_onnx_", "cp312", "win_amd64.whl"),
            "sherpa_onnx_core": (
                "sherpa_onnx_core_",
                "py3_none_win_amd64.whl",
            ),
            "numpy": ("numpy_", "cp312", "win_amd64.whl"),
            "scipy": ("scipy_", "cp312", "win_amd64.whl"),
        }
        for dependency, fragments in required_wheel_patterns.items():
            if not any(all(part in name for part in fragments) for name in normalized_wheels):
                raise RuntimeError(f"离线依赖缺少 Windows x64 版本：{dependency}")

        model_manifest = json.loads(bundle.read(runtime_prefix + "models.json"))
        packaged_models = model_manifest.get("models")
        if (
            model_manifest.get("schema_version") != 1
            or model_manifest.get("offline") is not True
            or not isinstance(packaged_models, list)
            or len(packaged_models) != len(MODEL_BUNDLES)
        ):
            raise RuntimeError("离线语音模型清单无效")
        for model in packaged_models:
            model_name = model.get("name")
            expected_files = model.get("files")
            required_files = model.get("required")
            if (
                not isinstance(model_name, str)
                or Path(model_name).name != model_name
                or not isinstance(expected_files, dict)
                or not expected_files
                or not isinstance(required_files, list)
            ):
                raise RuntimeError("离线语音模型清单包含无效条目")
            model_prefix = runtime_prefix + "models/" + model_name + "/"
            actual_files = {
                name.removeprefix(model_prefix)
                for name in names
                if name.startswith(model_prefix)
            }
            if actual_files != set(expected_files):
                raise RuntimeError(f"离线语音模型文件与清单不一致：{model_name}")
            if not set(required_files).issubset(actual_files):
                raise RuntimeError(f"离线语音模型缺少必要文件：{model_name}")
            for file_name, expected_hash in expected_files.items():
                if (
                    not isinstance(file_name, str)
                    or Path(file_name).is_absolute()
                    or ".." in Path(file_name).parts
                ):
                    raise RuntimeError("离线语音模型清单包含不安全路径")
                payload = bundle.read(model_prefix + file_name)
                if hashlib.sha256(payload).hexdigest() != expected_hash:
                    raise RuntimeError(f"离线语音模型校验失败：{model_name}/{file_name}")
        return {
            "file_count": len(names),
            "uncompressed_bytes": sum(item.file_size for item in bundle.infolist()),
            "python_version": runtime_manifest["python_version"],
            "wheel_count": len(expected_wheels),
            "model_count": len(packaged_models),
        }


def _next_archive_path(output_dir: Path, date_label: str) -> Path:
    base = output_dir / f"SpeechTrainer-Windows-Test-{date_label}.zip"
    if not base.exists():
        return base
    revision = 2
    while True:
        candidate = output_dir / f"SpeechTrainer-Windows-Test-{date_label}-r{revision}.zip"
        if not candidate.exists():
            return candidate
        revision += 1


def main() -> None:
    parser = argparse.ArgumentParser(description="构建 Windows 好友测试包")
    parser.add_argument("--config-db", type=Path, default=DEFAULT_CONFIG_DB)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts")
    parser.add_argument(
        "--python-runtime-archive",
        type=Path,
        default=RUNTIME_CACHE / "cpython-3.12.14-windows-x86_64.tar.gz",
        help="构建机缓存的固定 Windows Python 上游压缩包",
    )
    parser.add_argument(
        "--wheel-cache-dir",
        type=Path,
        default=RUNTIME_CACHE / "wheels-cp312-win_amd64",
        help="构建机的 Windows x64 wheel 缓存目录",
    )
    parser.add_argument(
        "--model-cache-dir",
        type=Path,
        default=RUNTIME_CACHE / "models",
        help="构建机缓存的固定语音模型压缩包目录",
    )
    parser.add_argument(
        "--model-source-dir",
        type=Path,
        default=DEFAULT_MODEL_SOURCE_DIR,
        help="构建机已安装且经过校验的语音模型目录",
    )
    parser.add_argument(
        "--skip-frontend-build",
        action="store_true",
        help="复用现有 frontend/dist",
    )
    args = parser.parse_args()

    config = _read_deepseek_config(args.config_db.expanduser().resolve())
    if not args.skip_frontend_build:
        subprocess.run(["npm", "run", "build"], cwd=FRONTEND, check=True)
    if not (FRONTEND / "dist" / "index.html").is_file():
        raise RuntimeError("frontend/dist 不完整，请先构建前端")

    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    date_label = datetime.now().strftime("%Y%m%d")
    archive = _next_archive_path(output_dir, date_label)

    runtime_source = args.python_runtime_archive.expanduser().resolve()
    default_runtime_source = (
        RUNTIME_CACHE / "cpython-3.12.14-windows-x86_64.tar.gz"
    ).resolve()
    if not runtime_source.is_file():
        if runtime_source != default_runtime_source:
            raise RuntimeError(f"指定的 Windows Python 运行时不存在：{runtime_source}")
        _download_verified_runtime(runtime_source)
    wheel_cache = args.wheel_cache_dir.expanduser().resolve()

    with tempfile.TemporaryDirectory(prefix="speech-trainer-windows-") as temp:
        package_root = Path(temp) / PACKAGE_ROOT_NAME
        package_root.mkdir()
        _copy_application(package_root)
        _write_private_env(package_root / "backend" / ".env", config)
        _prepare_bundled_python(package_root, runtime_source)
        _prepare_windows_wheelhouse(package_root, wheel_cache)
        _prepare_offline_models(
            package_root,
            args.model_cache_dir.expanduser().resolve(),
            args.model_source_dir.expanduser().resolve(),
        )
        _write_manifest(package_root, config)
        _build_archive(package_root, archive)

    verification = _verify_archive(archive, config["api_key"])
    archive_hash = _sha256(archive)
    checksum = archive.with_suffix(archive.suffix + ".sha256")
    checksum.write_text(f"{archive_hash}  {archive.name}\n", encoding="ascii")
    print(
        json.dumps(
            {
                "archive": str(archive),
                "sha256_file": str(checksum),
                "archive_bytes": archive.stat().st_size,
                "file_count": verification["file_count"],
                "uncompressed_bytes": verification["uncompressed_bytes"],
                "bundled_python": verification["python_version"],
                "offline_wheel_count": verification["wheel_count"],
                "offline_model_count": verification["model_count"],
                "deepseek_key_included": True,
                "personal_training_data_included": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
