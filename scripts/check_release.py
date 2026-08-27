#!/usr/bin/env python3
"""Fail fast when a GitHub release candidate contains common private artifacts."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[1]
MAX_FILE_BYTES = 5 * 1024 * 1024

REQUIRED_FILES = {
    ".github/workflows/ci.yml",
    "CHANGELOG.md",
    "CODE_OF_CONDUCT.md",
    "CONTRIBUTING.md",
    "LICENSE",
    "README.md",
    "SECURITY.md",
    "THIRD_PARTY_NOTICES.md",
}

PRIVATE_PREFIXES = (
    "artifacts/",
    "backend/data/",
    "backend/output/",
    "backend/uploads/",
)

PRIVATE_SUFFIXES = {
    ".7z",
    ".db",
    ".flac",
    ".gz",
    ".log",
    ".m4a",
    ".mp3",
    ".ogg",
    ".sqlite",
    ".sqlite3",
    ".tar",
    ".wav",
    ".webm",
    ".zip",
}

SECRET_PATTERNS = {
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    "AWS access key": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "API key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "non-empty dotenv secret": re.compile(
        r"(?mi)^[ \t]*(?:LLM|ASR|TTS|OPENAI|DEEPSEEK|DASHSCOPE)_"
        r"(?:API_KEY|API_SECRET)[ \t]*=[ \t]*[^ \t\r\n#][^\r\n]*$"
    ),
    "personal home path": re.compile(
        r"(?<!https:)/(?:Users|home)/[^/\s]+/|[A-Za-z]:\\Users\\[^\\\r\n]+\\"
    ),
}

LEGACY_MODEL_PATH_PREFIXES = (
    "backend/app/",
    "backend/.env.example",
    "frontend/src/",
    "scripts/",
)
LEGACY_MODELS = ("deepseek-" + "chat", "deepseek-" + "reasoner")
MARKDOWN_LINK = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")


def run_git(*args: str) -> bytes:
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(detail or f"git {' '.join(args)} failed")
    return result.stdout


def candidate_paths() -> list[str]:
    output = run_git("ls-files", "--cached", "--others", "--exclude-standard", "-z")
    return sorted(part.decode("utf-8") for part in output.split(b"\0") if part)


def private_path_reason(path: str) -> str | None:
    normalized = PurePosixPath(path).as_posix()
    if normalized.startswith(PRIVATE_PREFIXES):
        return "private runtime directory"
    name = PurePosixPath(normalized).name
    if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
        return "environment file"
    if PurePosixPath(normalized).suffix.lower() in PRIVATE_SUFFIXES:
        return "private or binary artifact"
    return None


def scan_text(path: str, text: str) -> list[str]:
    findings: list[str] = []
    for label, pattern in SECRET_PATTERNS.items():
        if pattern.search(text):
            findings.append(f"{path}: possible {label}")
    if path.startswith(LEGACY_MODEL_PATH_PREFIXES):
        for model in LEGACY_MODELS:
            if model in text:
                findings.append(f"{path}: deprecated default model {model}")
    return findings


def scan_candidate_files(paths: list[str]) -> list[str]:
    findings: list[str] = []
    for path in paths:
        reason = private_path_reason(path)
        if reason:
            findings.append(f"{path}: {reason}")
            continue

        absolute = ROOT / path
        if absolute.is_symlink():
            try:
                absolute.resolve().relative_to(ROOT)
            except ValueError:
                findings.append(f"{path}: symlink points outside the repository")
                continue
        if not absolute.is_file():
            continue
        size = absolute.stat().st_size
        if size > MAX_FILE_BYTES:
            findings.append(f"{path}: exceeds the 5 MiB repository limit")
            continue

        data = absolute.read_bytes()
        if b"\0" in data[:4096]:
            continue
        text = data.decode("utf-8", errors="replace")
        findings.extend(scan_text(path, text))
    return findings


def scan_history() -> list[str]:
    findings: list[str] = []
    names = run_git("log", "--all", "--format=", "--name-only").decode(
        "utf-8", errors="replace"
    )
    for path in sorted({line.strip() for line in names.splitlines() if line.strip()}):
        reason = private_path_reason(path)
        if reason:
            findings.append(f"history contains {path}: {reason}")

    patches = run_git("log", "--all", "--format=", "-p", "--no-ext-diff").decode(
        "utf-8", errors="replace"
    )
    for label, pattern in SECRET_PATTERNS.items():
        if pattern.search(patches):
            findings.append(f"Git history contains a possible {label}")
    return findings


def validate_required_files(paths: list[str]) -> list[str]:
    candidates = set(paths)
    findings = [
        f"missing required file: {path}"
        for path in sorted(REQUIRED_FILES - candidates)
    ]
    for path in sorted(REQUIRED_FILES & candidates):
        if not (ROOT / path).is_file() or (ROOT / path).stat().st_size == 0:
            findings.append(f"required file is empty: {path}")
    return findings


def validate_markdown_links(paths: list[str]) -> list[str]:
    findings: list[str] = []
    for path in paths:
        if PurePosixPath(path).suffix.lower() != ".md":
            continue
        absolute = ROOT / path
        if not absolute.is_file() or absolute.stat().st_size > MAX_FILE_BYTES:
            continue
        text = absolute.read_text(encoding="utf-8", errors="replace")
        for match in MARKDOWN_LINK.finditer(text):
            target = match.group(1).strip().split(maxsplit=1)[0].strip("<>")
            if not target or target.startswith(("#", "http://", "https://", "mailto:")):
                continue
            relative_target = unquote(target.split("#", 1)[0])
            resolved = (absolute.parent / relative_target).resolve()
            try:
                resolved.relative_to(ROOT)
            except ValueError:
                findings.append(f"{path}: link points outside repository: {target}")
                continue
            if not resolved.exists():
                findings.append(f"{path}: broken relative link: {target}")
    return findings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--history",
        action="store_true",
        help="also scan all local Git patches and historical path names",
    )
    args = parser.parse_args()

    try:
        paths = candidate_paths()
        findings = validate_required_files(paths)
        findings.extend(scan_candidate_files(paths))
        findings.extend(validate_markdown_links(paths))
        if args.history:
            findings.extend(scan_history())
    except (OSError, RuntimeError) as exc:
        print(f"release check could not run: {exc}", file=sys.stderr)
        return 2

    if findings:
        print("Release check failed:")
        for finding in findings:
            print(f"- {finding}")
        return 1

    scope = "candidate files and Git history" if args.history else "candidate files"
    print(f"Release check passed: {len(paths)} {scope} inspected.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
