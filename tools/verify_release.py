from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import zlib
from collections.abc import Sequence
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "release-manifest.txt"
COMMIT_MESSAGE = "feat: add checkpointed agent runtime and MCP evidence"

TEXT_SUFFIXES = {"", ".json", ".md", ".py", ".svg", ".toml", ".txt"}
SECRET_PATTERNS = (
    re.compile(r"api[_-]?key\s*[:=]\s*['\"]?[A-Za-z0-9_-]{12,}", re.IGNORECASE),
    re.compile(r"password\s*[:=]\s*['\"]?[^\s'\"]{8,}", re.IGNORECASE),
    re.compile(r"bearer\s+[A-Za-z0-9._-]{16,}", re.IGNORECASE),
    re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
)
PII_PATTERNS = (
    re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE),
    re.compile(r"\bC:\\Users\\[^\\\s]+", re.IGNORECASE),
    re.compile(r"/(?:Users|home)/[^/\s]+"),
    re.compile(r"(?:\+\d{1,3}[- ]?)?0\d{1,4}[- ]\d{1,4}[- ]\d{3,4}"),
)
PROHIBITED_CLAIMS = (
    "survive process loss",
    "new process resumes",
    "process exits after checkpoint",
    "committed evaluation report",
)


def _run(
    command: Sequence[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    print("+", subprocess.list2cmdline(list(command)), flush=True)
    return subprocess.run(
        list(command),
        cwd=cwd,
        env=env,
        check=True,
        text=True,
        capture_output=capture,
    )


def _manifest_entries() -> list[str]:
    raw = [
        line.strip()
        for line in MANIFEST.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if raw != sorted(raw):
        raise ValueError("release manifest must be sorted")
    if len(raw) != len(set(raw)):
        raise ValueError("release manifest contains duplicate paths")
    if "release-manifest.txt" not in raw or "tools/verify_release.py" not in raw:
        raise ValueError("release manifest must include itself and the verifier")
    for value in raw:
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or path.as_posix() != value:
            raise ValueError(f"unsafe release path: {value}")
        if not (ROOT / Path(*path.parts)).is_file():
            raise FileNotFoundError(value)
    return raw


def _git_visible_source_files() -> list[str]:
    result = _run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        capture=True,
    )
    return sorted(line for line in result.stdout.splitlines() if line)


def _copy_manifest(entries: Sequence[str], destination: Path) -> None:
    for value in entries:
        relative = Path(*PurePosixPath(value).parts)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)


def _remove_readonly(
    function: object,
    path: str,
    _error: object,
) -> None:
    os.chmod(path, stat.S_IWRITE)
    if not callable(function):
        raise TypeError("cleanup function is not callable")
    function(path)


def _scan_text(entries: Sequence[str], root: Path) -> None:
    findings: list[str] = []
    for value in entries:
        path = root / Path(*PurePosixPath(value).parts)
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        content = path.read_text(encoding="utf-8")
        for pattern in (*SECRET_PATTERNS, *PII_PATTERNS):
            if pattern.search(content):
                findings.append(f"{value}: {pattern.pattern}")
    if findings:
        raise ValueError("secret/PII scan failed:\n" + "\n".join(findings))


def _check_portfolio_cover(root: Path) -> None:
    path = root / "docs" / "assets" / "portfolio-cover.png"
    data = path.read_bytes()
    signature = b"\x89PNG\r\n\x1a\n"
    if not data.startswith(signature) or len(data) < 33:
        raise ValueError("portfolio cover is not a valid PNG header")
    length = struct.unpack(">I", data[8:12])[0]
    chunk_type = data[12:16]
    if length != 13 or chunk_type != b"IHDR":
        raise ValueError("portfolio cover has an invalid IHDR chunk")
    width, height = struct.unpack(">II", data[16:24])
    if (width, height) != (1000, 750):
        raise ValueError(f"portfolio cover dimensions changed: {(width, height)}")

    offset = len(signature)
    chunk_names: list[bytes] = []
    while offset < len(data):
        if offset + 12 > len(data):
            raise ValueError("portfolio cover contains a truncated PNG chunk")
        chunk_length = struct.unpack(">I", data[offset : offset + 4])[0]
        name = data[offset + 4 : offset + 8]
        payload_start = offset + 8
        payload_end = payload_start + chunk_length
        chunk_end = payload_end + 4
        if chunk_end > len(data):
            raise ValueError("portfolio cover contains an invalid PNG chunk length")
        stored_crc = struct.unpack(">I", data[payload_end:chunk_end])[0]
        calculated_crc = zlib.crc32(name)
        calculated_crc = zlib.crc32(data[payload_start:payload_end], calculated_crc) & 0xFFFFFFFF
        if stored_crc != calculated_crc:
            raise ValueError(f"portfolio cover has an invalid CRC for chunk {name!r}")
        chunk_names.append(name)
        offset = chunk_end
    if offset != len(data) or not chunk_names or chunk_names[-1] != b"IEND":
        raise ValueError("portfolio cover does not end with IEND")
    metadata_chunks = {b"tEXt", b"zTXt", b"iTXt", b"eXIf"}
    found_metadata = sorted(set(chunk_names) & metadata_chunks)
    if found_metadata:
        raise ValueError(f"portfolio cover contains metadata chunks: {found_metadata}")


def _check_license_and_boundary(root: Path) -> None:
    license_text = (root / "LICENSE").read_text(encoding="utf-8")
    if "MIT License" not in license_text or "Permission is hereby granted" not in license_text:
        raise ValueError("LICENSE is not the expected MIT license text")
    readme = (root / "README.md").read_text(encoding="utf-8")
    required = ("does not terminate an OS subprocess", "same Python process")
    missing = [phrase for phrase in required if phrase not in readme]
    prohibited = [phrase for phrase in PROHIBITED_CLAIMS if phrase in readme.casefold()]
    if missing or prohibited:
        raise ValueError(f"evidence boundary failed: missing={missing}, prohibited={prohibited}")


def _init_and_clone(seed: Path, clone: Path) -> str:
    _run(["git", "init", "--initial-branch=main"], cwd=seed)
    _run(["git", "add", "--all"], cwd=seed)
    _run(
        [
            "git",
            "-c",
            "user.name=Local Release Verifier",
            "-c",
            "user.email=" + "release-verifier" + "@" + "local.invalid",
            "commit",
            "-m",
            COMMIT_MESSAGE,
        ],
        cwd=seed,
    )
    commit = _run(["git", "rev-parse", "HEAD"], cwd=seed, capture=True).stdout.strip()
    _run(["git", "clone", "--local", "--no-hardlinks", str(seed), str(clone)], cwd=seed.parent)
    return commit


def _quality_gates(clone: Path, builder_python: Path, work_root: Path) -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(clone / "src")
    commands = (
        [sys.executable, "-m", "pytest", "-q"],
        [sys.executable, "-m", "ruff", "check", "."],
        [sys.executable, "-m", "mypy", "src", "tests", "tools"],
        [sys.executable, "-m", "compileall", "-q", "src", "tests", "tools"],
        [
            sys.executable,
            "-m",
            "agent_runtime_demo.cli",
            "evaluate",
            "fixtures",
            "--output",
            "reports/release-verification.json",
        ],
    )
    for command in commands:
        _run(command, cwd=clone, env=env)

    evaluation = json.loads(
        (clone / "reports" / "release-verification.json").read_text(encoding="utf-8")
    )
    if evaluation["summary"] != {"scenarios": 3, "passed": 3, "failed": 0}:
        raise ValueError("workflow evaluation did not pass 3/3")
    if evaluation["mcp"]["summary"] != {"checks": 4, "passed": 4, "failed": 0}:
        raise ValueError("MCP evaluation did not pass 4/4")

    _run(
        [str(builder_python), "-c", "import setuptools, wheel"],
        cwd=clone,
    )
    wheel_dir = clone / "dist"
    _run(
        [
            str(builder_python),
            "-m",
            "pip",
            "wheel",
            ".",
            "--no-index",
            "--no-deps",
            "--no-build-isolation",
            "--wheel-dir",
            str(wheel_dir),
        ],
        cwd=clone,
    )
    wheels = list(wheel_dir.glob("agent_runtime_demo-*.whl"))
    if len(wheels) != 1:
        raise ValueError(f"expected one wheel, found {len(wheels)}")

    fresh_venv = work_root / "fresh-venv"
    _run([sys.executable, "-m", "venv", str(fresh_venv)], cwd=work_root)
    fresh_python = fresh_venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    _run(
        [
            str(fresh_python),
            "-m",
            "pip",
            "install",
            str(wheels[0]),
            "--no-index",
            "--no-deps",
        ],
        cwd=work_root,
    )
    demo = _run(
        [str(fresh_python), "-m", "agent_runtime_demo.cli", "demo"],
        cwd=work_root,
        capture=True,
    )
    print(demo.stdout, end="")
    payload = json.loads(demo.stdout)
    expected = {
        "final_status": "completed",
        "checkpoint_attempts": 1,
        "completed_steps": 5,
        "artifact_written": True,
    }
    if any(payload.get(key) != value for key, value in expected.items()):
        raise ValueError(f"installed demo assertion failed: {payload}")


def verify(builder_python: Path, *, keep_temp: bool) -> None:
    entries = _manifest_entries()
    visible = _git_visible_source_files()
    if visible != entries:
        missing = sorted(set(entries) - set(visible))
        extra = sorted(set(visible) - set(entries))
        raise ValueError(f"manifest/source mismatch: missing={missing}, extra={extra}")

    work_root = Path(tempfile.mkdtemp(prefix="agent-runtime-release-verify-")).resolve()
    seed = work_root / "seed"
    clone = work_root / "clone"
    seed.mkdir()
    succeeded = False
    try:
        _copy_manifest(entries, seed)
        _scan_text(entries, seed)
        _check_portfolio_cover(seed)
        _check_license_and_boundary(seed)
        commit = _init_and_clone(seed, clone)
        clone_files = _run(["git", "ls-files"], cwd=clone, capture=True).stdout.splitlines()
        if sorted(clone_files) != entries:
            raise ValueError("local clone does not exactly match release manifest")
        _scan_text(entries, clone)
        _check_portfolio_cover(clone)
        _check_license_and_boundary(clone)
        _quality_gates(clone, builder_python, work_root)
        status = _run(["git", "status", "--porcelain"], cwd=clone, capture=True).stdout
        if status:
            raise ValueError(f"release gates changed tracked files:\n{status}")
        succeeded = True
        print(f"release verification passed: files={len(entries)} commit={commit}")
    finally:
        if keep_temp or not succeeded:
            print(f"verification workspace retained: {work_root}")
        else:
            temp_root = Path(tempfile.gettempdir()).resolve()
            if work_root.parent != temp_root or not work_root.name.startswith(
                "agent-runtime-release-verify-"
            ):
                raise RuntimeError(f"refusing to remove unexpected path: {work_root}")
            shutil.rmtree(work_root, onerror=_remove_readonly)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="manifest-only local release verifier")
    parser.add_argument("--builder-python", type=Path, default=Path(sys.executable))
    parser.add_argument("--keep-temp", action="store_true")
    args = parser.parse_args(argv)
    verify(args.builder_python.resolve(), keep_temp=args.keep_temp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
