"""原版安装流程：客户端 jar、依赖库、原生库解压、资源文件。"""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from . import assets as assets_mod
from . import console, manifest, net
from .mirror import alternate, rewrite


@dataclass
class InstallContext:
    game_dir: Path
    mirror: str = "official"
    workers: int = 8
    skip_assets: bool = False
    quiet: bool = False


@dataclass
class InstallSummary:
    version_id: str = ""
    base_version: str = ""
    libraries: int = 0
    assets: int = 0
    downloaded_bytes: int = 0
    downloaded_files: int = 0
    skipped_files: int = 0
    elapsed: float = 0.0
    notes: list[str] = field(default_factory=list)


def install_base(ctx: InstallContext, version_id: str, summary: InstallSummary | None = None) -> InstallSummary:
    """安装一个原版版本（客户端 + 依赖库 + 资源）。"""
    summary = summary or InstallSummary()
    summary.version_id = version_id
    summary.base_version = version_id
    game_dir = ctx.game_dir
    game_dir.mkdir(parents=True, exist_ok=True)

    console.step(f"获取版本信息 {version_id}")
    manifest_data = manifest.fetch_manifest(ctx.mirror)
    version = manifest.fetch_version(version_id, manifest_data, ctx.mirror)
    manifest.save_version_json(game_dir, version_id, version)
    console.info(f"版本 JSON 已保存到 versions/{version_id}/{version_id}.json")

    # 1. 客户端 jar
    client = manifest.client_download(version)
    if client:
        console.step("客户端 jar")
        origin = client["url"]
        result = net.download_many(
            [
                net.DownloadTask(
                    url=rewrite(origin, ctx.mirror),
                    dest=manifest.version_dir(game_dir, version_id) / f"{version_id}.jar",
                    sha1=client.get("sha1"),
                    size=client.get("size"),
                    verify="sha1",
                    fallback_url=alternate(origin, ctx.mirror),
                    label=f"{version_id}.jar",
                )
            ],
            workers=1,
            label="客户端",
            quiet=ctx.quiet,
        )
        _accumulate(summary, result)

    # 2. 依赖库
    platform = manifest.Platform.current()
    artifacts, natives = manifest.resolve_libraries(version, platform)
    console.step(f"依赖库（{len(artifacts)} 个）")
    lib_tasks = [
        net.DownloadTask(
            url=rewrite(lib.url, ctx.mirror),
            dest=game_dir / "libraries" / lib.path,
            sha1=lib.sha1,
            size=lib.size,
            verify="sha1" if lib.sha1 else "size",
            fallback_url=alternate(lib.url, ctx.mirror),
            label=lib.name,
        )
        for lib in artifacts
    ]
    result = net.download_many(lib_tasks, workers=ctx.workers, label="依赖库", quiet=ctx.quiet)
    _accumulate(summary, result)

    # 3. 原生库（老版本需要解压）
    if natives:
        console.step(f"原生库（{len(natives)} 个）")
        native_tasks = [
            net.DownloadTask(
                url=rewrite(lib.url, ctx.mirror),
                dest=game_dir / "libraries" / lib.path,
                sha1=lib.sha1,
                size=lib.size,
                verify="sha1" if lib.sha1 else "size",
                fallback_url=alternate(lib.url, ctx.mirror),
                label=lib.name,
            )
            for lib in natives
        ]
        result = net.download_many(native_tasks, workers=ctx.workers, label="原生库", quiet=ctx.quiet)
        _accumulate(summary, result)
        _extract_natives(game_dir, version_id, natives, ctx.quiet)

    # 4. 资源文件
    if ctx.skip_assets:
        summary.notes.append("已按参数跳过资源文件（assets）")
        console.warn("已跳过资源文件；缺少资源会导致游戏内无声音/无语言文件")
    else:
        console.step(f"资源文件（索引 {version.get('assets', '?')}）")
        asset_stats = assets_mod.install_assets(
            game_dir, version, ctx.mirror, ctx.workers, ctx.quiet
        )
        summary.assets = asset_stats.objects
        summary.downloaded_bytes += asset_stats.bytes
        summary.downloaded_files += asset_stats.downloaded_files

    summary.libraries = len(artifacts)
    return summary


def _accumulate(summary: InstallSummary, stats: net.DownloadStats) -> None:
    summary.downloaded_bytes += stats.downloaded
    summary.downloaded_files += stats.files
    summary.skipped_files += stats.skipped
    summary.elapsed += stats.elapsed


def _extract_natives(
    game_dir: Path, version_id: str, natives: list[manifest.LibraryFile], quiet: bool
) -> None:
    target = manifest.version_dir(game_dir, version_id) / "natives"
    target.mkdir(parents=True, exist_ok=True)
    extracted = 0
    for lib in natives:
        jar_path = game_dir / "libraries" / lib.path
        if not jar_path.exists():
            continue
        excludes = [pattern.replace("${arch}", "64") for pattern in lib.extract_exclude] or ["META-INF/"]
        with zipfile.ZipFile(jar_path) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                name = info.filename
                if any(name.startswith(pattern) for pattern in excludes):
                    continue
                out = target / name
                if out.exists() and out.stat().st_size == info.file_size:
                    continue
                out.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as src, open(out, "wb") as dst:
                    dst.write(src.read())
                extracted += 1
    if extracted and not quiet:
        console.info(f"原生库已解压到 versions/{version_id}/natives")


def ensure_launcher_profiles(game_dir: Path) -> Path:
    """确保 launcher_profiles.json 存在——Forge/NeoForge/OptiFine 安装器都要求这个文件。"""
    path = game_dir / "launcher_profiles.json"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"profiles": {}, "settings": {}, "version": 3}, indent=2), encoding="utf-8"
        )
    return path


def write_launcher_profile(game_dir: Path, profile_id: str, name: str, last_version: str) -> None:
    """把安装好的版本登记进官方启动器的 launcher_profiles.json。"""
    path = game_dir / "launcher_profiles.json"
    data: dict = {"profiles": {}, "settings": {}, "version": 3}
    if path.exists():
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, OSError):
            pass
    import datetime

    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    data.setdefault("profiles", {})
    data["profiles"][profile_id] = {
        "created": now,
        "icon": "Grass",
        "lastUsed": now,
        "lastVersionId": last_version,
        "name": name,
        "type": "custom",
    }
    data.setdefault("settings", {})
    data.setdefault("version", 3)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
