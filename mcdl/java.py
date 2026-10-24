"""Java 运行时探测：找到可用的 java，并检查主版本是否满足版本要求。"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class JavaInfo:
    path: Path
    version: str = ""
    major: int = 0
    vendor: str = ""

    def __str__(self) -> str:
        return f"{self.path} (Java {self.major})"


_VERSION_RE = re.compile(r'version "([^"]+)"')
_VENDOR_RE = re.compile(r"(?:Java\(TM\)|OpenJDK|Runtime Environment).*", re.IGNORECASE)


def _probe(path: Path) -> JavaInfo | None:
    if not path.exists():
        return None
    try:
        proc = subprocess.run(
            [str(path), "-version"],
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    output = (proc.stderr or "") + (proc.stdout or "")
    match = _VERSION_RE.search(output)
    if not match:
        return None
    version = match.group(1)
    major = _major_of(version)
    vendor = ""
    for line in output.splitlines():
        if "Runtime Environment" in line:
            vendor = line.strip()[:80]
            break
    return JavaInfo(path=path, version=version, major=major, vendor=vendor)


def _major_of(version: str) -> int:
    parts = version.replace("_", ".").split(".")
    try:
        if parts[0] == "1" and len(parts) > 1:
            return int(parts[1])
        return int(parts[0])
    except (ValueError, IndexError):
        return 0


def candidate_paths() -> list[Path]:
    found: list[Path] = []
    java_home = os.environ.get("JAVA_HOME")
    if java_home:
        for name in ("java.exe", "java"):
            p = Path(java_home) / "bin" / name
            if p.exists():
                found.append(p)
    which = shutil.which("java")
    if which:
        found.append(Path(which))

    if os.name == "nt":
        roots = [
            Path(r"C:\Program Files\Java"),
            Path(r"C:\Program Files\Eclipse Adoptium"),
            Path(r"C:\Program Files\Microsoft"),
            Path(r"C:\Program Files\Zulu"),
            Path(r"C:\Program Files\BellSoft"),
            Path(r"C:\Program Files (x86)\Java"),
        ]
        for root in roots:
            if not root.exists():
                continue
            for child in sorted(root.iterdir(), reverse=True):
                exe = child / "bin" / "java.exe"
                if exe.exists():
                    found.append(exe)
    else:
        for root in ("/usr/lib/jvm", "/Library/Java/JavaVirtualMachines"):
            base = Path(root)
            if not base.exists():
                continue
            for child in base.iterdir():
                exe = child / "bin" / "java"
                if exe.exists():
                    found.append(exe)
    # 去重且保持顺序
    unique: list[Path] = []
    for path in found:
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        if resolved not in unique:
            unique.append(resolved)
    return unique


def detect_all() -> list[JavaInfo]:
    results: list[JavaInfo] = []
    for path in candidate_paths():
        info = _probe(path)
        if info:
            results.append(info)
    results.sort(key=lambda info: info.major, reverse=True)
    return results


def pick(preferred: str | None = None, required_major: int | None = None) -> JavaInfo | None:
    """选择 Java：显式路径优先，其次找主版本最接近要求的。"""
    if preferred:
        info = _probe(Path(preferred))
        return info
    available = detect_all()
    if not available:
        return None
    if required_major:
        exact = [info for info in available if info.major == required_major]
        if exact:
            return exact[0]
        newer = [info for info in available if info.major > required_major]
        if newer:
            return sorted(newer, key=lambda i: i.major)[0]
    return available[0]
