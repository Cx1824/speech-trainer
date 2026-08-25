#!/usr/bin/env python3
"""显式安装 Speech Trainer 默认的本地实时 ASR 模型。"""

from __future__ import annotations

import argparse
import hashlib
import inspect
import shutil
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.providers.asr.sherpa_local import (
    DEFAULT_MODEL,
    FINALIZER_ARCHIVE,
    FINALIZER_MODEL,
    FINALIZER_SHA256,
    FINALIZER_URL,
    MODEL_ARCHIVE,
    MODEL_SHA256,
    MODEL_URL,
    default_model_root,
    missing_finalizer_model_files,
    missing_model_files,
)


def _download(destination: Path, *, url: str, sha256: str, label: str) -> None:
    digest = hashlib.sha256()
    downloaded = 0
    request = urllib.request.Request(url, headers={"User-Agent": "SpeechTrainer/0.1"})
    with urllib.request.urlopen(request, timeout=60) as response, destination.open("wb") as output:
        total = int(response.headers.get("Content-Length", "0"))
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            output.write(chunk)
            digest.update(chunk)
            downloaded += len(chunk)
            if total:
                print(f"\r正在下载{label}：{downloaded / total:.0%}", end="", flush=True)
    print()
    actual = digest.hexdigest()
    if actual != sha256:
        raise RuntimeError("模型文件校验失败，已停止安装")


def _safe_extract(archive: Path, destination: Path, *, model_name: str) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with tarfile.open(archive, "r:bz2") as package:
        members = package.getmembers()
        for member in members:
            target = (destination / member.name).resolve()
            if not target.is_relative_to(root) or member.issym() or member.islnk():
                raise RuntimeError("模型压缩包包含不安全路径，已停止安装")
        kwargs = {"filter": "data"} if "filter" in inspect.signature(package.extractall).parameters else {}
        package.extractall(destination, members=members, **kwargs)
    extracted = destination / model_name
    if not extracted.is_dir():
        raise RuntimeError("模型压缩包结构不符合预期")
    return extracted


def install(
    target: Path,
    *,
    model_name: str = DEFAULT_MODEL,
    archive_name: str = MODEL_ARCHIVE,
    url: str = MODEL_URL,
    sha256: str = MODEL_SHA256,
    required_files: Callable[[Path], list[str]] = missing_model_files,
    label: str = "实时语音模型",
    force: bool = False,
) -> Path:
    target = target.resolve()
    if target.name != model_name:
        raise RuntimeError(f"模型目录名称必须是：{model_name}")
    if target.is_dir() and not required_files(target):
        print(f"{label}已安装：{target}")
        return target
    if target.exists() and not force:
        raise RuntimeError(f"目标目录不完整：{target}；确认后可使用 --force 重新安装")

    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="speech-trainer-model-", dir=target.parent) as temp:
        temp_dir = Path(temp)
        archive = temp_dir / archive_name
        _download(archive, url=url, sha256=sha256, label=label)
        extracted = _safe_extract(
            archive,
            temp_dir / "unpacked",
            model_name=model_name,
        )
        missing = required_files(extracted)
        if missing:
            raise RuntimeError("模型缺少必要文件，已停止安装")
        if target.exists():
            shutil.rmtree(target)
        shutil.move(str(extracted), str(target))
    print(f"{label}安装完成：{target}")
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description="安装本地中文实时识别和字幕精校模型")
    parser.add_argument("--target", type=Path, default=default_model_root() / DEFAULT_MODEL)
    parser.add_argument(
        "--streaming-only",
        action="store_true",
        help="仅安装实时模型，不安装句末字幕精校模型",
    )
    parser.add_argument("--force", action="store_true", help="重新安装不完整的目标目录")
    args = parser.parse_args()
    streaming_target = args.target.expanduser()
    install(streaming_target, force=args.force)
    if not args.streaming_only:
        install(
            streaming_target.parent / FINALIZER_MODEL,
            model_name=FINALIZER_MODEL,
            archive_name=FINALIZER_ARCHIVE,
            url=FINALIZER_URL,
            sha256=FINALIZER_SHA256,
            required_files=missing_finalizer_model_files,
            label="句末字幕精校模型",
            force=args.force,
        )


if __name__ == "__main__":
    main()
