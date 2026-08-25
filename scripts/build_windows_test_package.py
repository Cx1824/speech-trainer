#!/usr/bin/env python3
"""构建不含个人训练数据、但含临时 DeepSeek Key 的 Windows 好友测试包。"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import subprocess
import tempfile
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
        "runtime": "Python 3.11/3.12; no Node.js required",
        "services": {
            "llm": {
                "provider": config["provider"],
                "base_url": config["base_url"],
                "model": config["model"],
                "api_key_included": True,
                "api_key_exposed_by_config_api": False,
            },
            "asr": "local sherpa-onnx",
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
                bundle.write(path, name.as_posix())


def _verify_archive(archive: Path, api_key: str) -> dict[str, int]:
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
        return {
            "file_count": len(names),
            "uncompressed_bytes": sum(item.file_size for item in bundle.infolist()),
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="构建 Windows 好友测试包")
    parser.add_argument("--config-db", type=Path, default=DEFAULT_CONFIG_DB)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts")
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
    archive = output_dir / f"SpeechTrainer-Windows-Test-{date_label}.zip"

    with tempfile.TemporaryDirectory(prefix="speech-trainer-windows-") as temp:
        package_root = Path(temp) / PACKAGE_ROOT_NAME
        package_root.mkdir()
        _copy_application(package_root)
        _write_private_env(package_root / "backend" / ".env", config)
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
                "deepseek_key_included": True,
                "personal_training_data_included": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
