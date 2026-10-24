"""资源文件（assets）安装：索引下载、对象下载、旧版虚拟目录映射。"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from . import console, net
from .mirror import alternate, rewrite


@dataclass
class AssetStats:
    index_id: str = ""
    objects: int = 0
    downloaded_files: int = 0
    bytes: int = 0
    skipped: bool = False


def install_assets(
    game_dir: Path,
    version: dict,
    mirror: str = "official",
    workers: int = 8,
    quiet: bool = False,
) -> AssetStats:
    stats = AssetStats()
    index_info = version.get("assetIndex")
    if not index_info:
        if not quiet:
            console.warn("该版本没有 assetIndex（远古版本），跳过资源文件")
        stats.skipped = True
        return stats

    index_id = index_info.get("id") or version.get("assets") or "legacy"
    stats.index_id = index_id
    assets_dir = game_dir / "assets"
    index_path = assets_dir / "indexes" / f"{index_id}.json"
    objects_dir = assets_dir / "objects"

    tasks = [
        net.DownloadTask(
            url=rewrite(index_info["url"], mirror),
            dest=index_path,
            sha1=index_info.get("sha1"),
            size=index_info.get("size"),
            verify="sha1",
            fallback_url=alternate(index_info["url"], mirror),
            label=f"indexes/{index_id}.json",
        )
    ]
    net.download_many(tasks, workers=1, label="资源索引", quiet=quiet)

    with open(index_path, encoding="utf-8") as fh:
        index = json.load(fh)

    entries = list((index.get("objects") or {}).items())
    stats.objects = len(entries)

    pending: dict[str, net.DownloadTask] = {}
    for name, info in entries:
        digest = info["hash"]
        rel = f"{digest[:2]}/{digest}"
        if rel in pending:
            continue
        origin = f"https://resources.download.minecraft.net/{rel}"
        pending[rel] = net.DownloadTask(
            url=rewrite(origin, mirror),
            dest=objects_dir / digest[:2] / digest,
            sha1=digest,
            size=info.get("size"),
            verify="size",  # 对象以内容哈希寻址，大小一致即认定命中，避免重复哈希拖慢速度
            fallback_url=alternate(origin, mirror),
            label=name,
        )

    result = net.download_many(list(pending.values()), workers=workers, label="资源文件", quiet=quiet)
    stats.downloaded_files = result.files
    stats.bytes = result.downloaded

    if index.get("virtual") or index.get("map_to_resources"):
        _materialize(index, objects_dir, assets_dir, game_dir, index_id, quiet)
    return stats


def _materialize(
    index: dict,
    objects_dir: Path,
    assets_dir: Path,
    game_dir: Path,
    index_id: str,
    quiet: bool,
) -> None:
    """旧版本（1.6 及更早）需要把散列存储展开成真实目录结构。"""
    if index.get("virtual"):
        target_root = assets_dir / "virtual" / index_id
    else:
        target_root = game_dir / "resources"

    copied = 0
    for name, info in (index.get("objects") or {}).items():
        src = objects_dir / info["hash"][:2] / info["hash"]
        dst = target_root / name
        if dst.exists() and dst.stat().st_size == src.stat().st_size:
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied += 1
    if copied and not quiet:
        console.info(f"已展开旧版资源目录：{target_root}（{copied} 个文件）")
