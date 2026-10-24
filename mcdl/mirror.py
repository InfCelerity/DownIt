"""下载地址镜像：官方（Mojang / Fabric / Forge）与中国大陆镜像（BMCLAPI）。"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
from pathlib import Path

OFFICIAL_MANIFEST = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
CONFIG_PATH = Path.home() / ".mcdl" / "config.json"

# 前缀替换规则：官方地址 -> 国内镜像地址
_BMCLAPI_RULES: list[tuple[str, str]] = [
    ("https://piston-meta.mojang.com/", "https://bmclapi2.bangbang93.com/"),
    ("https://launchermeta.mojang.com/", "https://bmclapi2.bangbang93.com/"),
    ("https://piston-data.mojang.com/", "https://bmclapi2.bangbang93.com/"),
    ("https://launcher.mojang.com/", "https://bmclapi2.bangbang93.com/"),
    ("https://libraries.minecraft.net/", "https://bmclapi2.bangbang93.com/maven/"),
    ("https://resources.download.minecraft.net/", "https://bmclapi2.bangbang93.com/assets/"),
    ("https://meta.fabricmc.net/", "https://bmclapi2.bangbang93.com/fabric-meta/"),
    ("https://maven.fabricmc.net/", "https://bmclapi2.bangbang93.com/maven/"),
    ("https://maven.minecraftforge.net/", "https://bmclapi2.bangbang93.com/maven/"),
    ("https://maven.neoforged.net/releases/", "https://bmclapi2.bangbang93.com/maven/"),
    ("https://maven.neoforged.net/", "https://bmclapi2.bangbang93.com/maven/"),
]

MIRRORS = {
    "official": "Mojang 官方源",
    "bmclapi": "BMCLAPI 国内镜像",
}

PROBE_URLS = {
    "official": "https://libraries.minecraft.net/com/google/code/gson/gson/2.10/gson-2.10.jar",
    "bmclapi": "https://bmclapi2.bangbang93.com/maven/com/google/code/gson/gson/2.10/gson-2.10.jar",
}

_CACHE_TTL = 6 * 3600


def rewrite(url: str, mirror: str) -> str:
    """把官方 URL 重写为指定镜像 URL；非已知前缀原样返回。"""
    if mirror != "bmclapi":
        return url
    for src, dst in _BMCLAPI_RULES:
        if url.startswith(src):
            return dst + url[len(src):]
    return url


def alternate(url: str, mirror: str) -> str:
    """返回“另一个源”的地址，用于失败回退。"""
    return rewrite(url, "bmclapi") if mirror == "official" else url


def _read_config() -> dict:
    try:
        with open(CONFIG_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}


def _write_config(data: dict) -> None:
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
    except OSError:
        pass


def _probe(url: str, budget: float = 1.6) -> float:
    """下载一小段，返回字节/秒；失败返回 0。"""
    request = urllib.request.Request(
        url, headers={"User-Agent": "mcdl/1.0", "Range": "bytes=0-262143"}
    )
    start = time.monotonic()
    received = 0
    try:
        with urllib.request.urlopen(request, timeout=budget + 1.5) as resp:
            while time.monotonic() - start < budget:
                chunk = resp.read(65536)
                if not chunk:
                    break
                received += len(chunk)
                if received >= 262144:
                    break
    except Exception:
        return 0.0
    elapsed = max(time.monotonic() - start, 0.01)
    return received / elapsed if received else 0.0


def measure() -> dict[str, float]:
    """并发测速两个源。"""
    results: dict[str, float] = {}

    def worker(name: str, url: str) -> None:
        results[name] = _probe(url)

    threads = [threading.Thread(target=worker, args=(name, url), daemon=True) for name, url in PROBE_URLS.items()]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=4.0)
    return results


def pick_download_source(url: str, mirror: str = "official", budget: float = 1.5) -> str:
    """对同一个文件分别测官方与镜像，返回更适合下载的源。

    用于 Forge 这类「官方仓库在 Cloudflare、国内经常卡死」的场景。
    """
    if mirror == "bmclapi":
        return "bmclapi"
    official = _probe(url, budget)
    mirrored = _probe(rewrite(url, "bmclapi"), budget)
    if official <= 0:
        return "bmclapi" if mirrored > 0 else "official"
    if official < 300_000 and mirrored > official * 2:
        return "bmclapi"
    return "official"


def pick_mirror(preferred: str = "auto", use_cache: bool = True) -> str:
    """auto：测速选择；结果缓存 6 小时，避免每次启动都探测。"""
    if preferred in ("official", "bmclapi"):
        return preferred
    if os.environ.get("MCDL_NO_CACHE"):
        use_cache = False

    if use_cache:
        config = _read_config()
        cached = config.get("mirror")
        stamped = config.get("mirror_checked_at") or 0
        if cached in ("official", "bmclapi") and time.time() - stamped < _CACHE_TTL:
            return cached

    speeds = measure()
    best = max(speeds, key=lambda name: speeds.get(name, 0)) if speeds else "official"
    if speeds.get(best, 0) <= 0:
        best = "official"
    _write_config({**_read_config(), "mirror": best, "mirror_checked_at": int(time.time())})
    return best
