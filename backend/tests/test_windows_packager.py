"""Windows 私测打包器的跨平台结构回归测试。"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sqlite3
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
    packager._write_manifest(package_root, config)
    archive = tmp_path / "fixture.zip"
    packager._build_archive(package_root, archive)

    verification = packager._verify_archive(archive, config["api_key"])

    assert verification["file_count"] > 0
