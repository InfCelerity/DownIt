"""OptiFine：从官网解析版本、带令牌下载、无界面安装（或作为 Forge 模组放入 mods）。"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

from . import console, installer, java as java_mod, loaders, manifest, net

DOWNLOADS_PAGE = "https://optifine.net/downloads"
ADLOADX = "https://optifine.net/adloadx?f={filename}"
DOWNLOADS = "https://optifine.net/downloadx?f={filename}&x={token}"

_FILENAME_RE = re.compile(r"OptiFine_([0-9][\w.]*?)_([A-Za-z0-9_]+)\.jar")
_EDITIONS_CACHE: dict[str, tuple[float, list[dict]]] = {}
_PREVIEW_SUFFIX = ("_pre", "_PRE")


# ---------------------------------------------------------------- 版本列表


def optifine_editions(mc_version: str, mirror: str = "official") -> list[dict]:
    """返回某个 Minecraft 版本可用的 OptiFine 版本（官网顺序：新的在前）。"""
    cached = _EDITIONS_CACHE.get(mc_version)
    if cached and time.time() - cached[0] < 900:
        return cached[1]
    html = net.get_text(DOWNLOADS_PAGE, mirror, timeout=25)
    editions: list[dict] = []
    seen: set[str] = set()
    for match in _FILENAME_RE.finditer(html):
        version, edition = match.group(1), match.group(2)
        if version != mc_version:
            continue
        filename = f"OptiFine_{version}_{edition}.jar"
        if filename in seen:
            continue
        seen.add(filename)
        editions.append(
            {
                "file": filename,
                "edition": edition,
                "preview": edition.upper().endswith(_PREVIEW_SUFFIX) or "pre" in edition.lower(),
                "label": f"OptiFine {edition}",
            }
        )
    _EDITIONS_CACHE[mc_version] = (time.time(), editions)
    return editions


def resolve_edition(mc_version: str, want: str | None, mirror: str = "official") -> dict:
    editions = optifine_editions(mc_version, mirror)
    if not editions:
        raise RuntimeError(f"OptiFine 官网没有 Minecraft {mc_version} 的版本")
    if want and want.lower() in ("latest", "recommended", "stable"):
        want = None
    if want:
        needle = want.upper()
        for item in editions:
            if item["edition"].upper() == needle or needle in item["file"].upper():
                return item
        raise RuntimeError(f"没有找到 OptiFine {want}（Minecraft {mc_version}）")
    # 优先正式版，其次最新预览版
    # 官网按「新 → 旧」排列，取第一个正式版
    stable = [item for item in editions if not item["preview"]]
    return (stable or editions)[0]


# ---------------------------------------------------------------- 下载


def download_optifine(ctx: installer.InstallContext, item: dict) -> Path:
    """带 adloadx 令牌下载 OptiFine jar（有缓存则复用）。"""
    cache = ctx.game_dir / ".mcdl-cache" / "optifine"
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / item["file"]
    if target.exists() and target.stat().st_size > 1_000_000:
        if not ctx.quiet:
            console.ok(f"OptiFine 安装包已存在：{target.name}")
        return target

    console.step(f"下载 {item['file']}")
    page = net.get_text(ADLOADX.format(filename=item["file"]), ctx.mirror, timeout=25)
    token_match = re.search(r"downloadx\?f=[^\"'&]+&x=([0-9a-fA-F]+)", page)
    if not token_match:
        raise RuntimeError("OptiFine 下载页没有返回令牌，可能被官网限制，请稍后再试")
    url = DOWNLOADS.format(filename=item["file"], token=token_match.group(1))

    net.download_many(
        [net.DownloadTask(url=url, dest=target, verify="size", label=item["file"])],
        workers=1,
        label="OptiFine",
        quiet=ctx.quiet,
    )
    return target


# ---------------------------------------------------------------- 安装


def install_optifine(
    ctx: installer.InstallContext,
    mc_version: str,
    want_edition: str | None = None,
    loader: str = "none",
    loader_version: str | None = None,
    java_pref: str | None = None,
) -> installer.InstallSummary:
    """安装 OptiFine。

    - loader="forge"：OptiFine 作为 Forge 模组放进 mods/（官方支持的方式）
    - loader="none"：运行 OptiFine 自带安装器，生成独立的 OptiFine 版本
    """
    if loader in ("fabric", "neoforge"):
        raise RuntimeError(f"OptiFine 不能直接装在 {loader} 上：Fabric 需要 OptiFabric，NeoForge 不支持 OptiFine")

    item = resolve_edition(mc_version, want_edition, ctx.mirror)
    console.info(f"目标：Minecraft {mc_version} + {item['label']}")

    summary = installer.InstallSummary()
    if loader == "forge":
        summary = loaders.install_forge(ctx, mc_version, loader_version, java_pref)
        jar = download_optifine(ctx, item)
        mods_dir = ctx.game_dir / "mods"
        mods_dir.mkdir(parents=True, exist_ok=True)
        dest = mods_dir / jar.name
        if not dest.exists() or dest.stat().st_size != jar.stat().st_size:
            dest.write_bytes(jar.read_bytes())
        summary.notes.append(f"OptiFine 已放入 mods/：{dest.name}（Forge 会自动加载）")
        summary.notes.append("首次启动若崩溃，请确认 Forge 版本与 OptiFine 版本匹配")
        return summary

    summary = installer.install_base(ctx, mc_version, summary)
    jar = download_optifine(ctx, item)

    installer.ensure_launcher_profiles(ctx.game_dir)
    java_info = _pick_java_for_optifine(ctx, mc_version, java_pref)
    java_dir = manifest.version_dir(ctx.game_dir, mc_version)
    if not (java_dir / f"{mc_version}.jar").exists():
        raise RuntimeError(f"缺少原版客户端 jar：{java_dir}，请先安装 {mc_version}")

    console.step("运行 OptiFine 安装器（打补丁，无界面）")
    started = time.time()
    _run_installer(ctx, java_info, jar, mc_version)
    summary.elapsed += time.time() - started

    target = _find_optifine_dir(ctx.game_dir, mc_version, started)
    if target is None:
        raise RuntimeError("OptiFine 安装器没有生成版本目录；请检查 Java 版本是否兼容")

    summary.version_id = target.name
    summary.base_version = mc_version
    summary.notes.append(f"已生成版本：{target.name}")
    try:
        installer.write_launcher_profile(
            ctx.game_dir, target.name, f"{item['label']} · {mc_version}", target.name
        )
    except Exception:
        pass
    return summary


def _pick_java_for_optifine(
    ctx: installer.InstallContext, mc_version: str, java_pref: str | None
) -> java_mod.JavaInfo:
    base = manifest.load_local_version(ctx.game_dir, mc_version) or {}
    required = manifest.java_major(base) or 8
    info = java_mod.pick(java_pref, required)
    if info is None:
        raise RuntimeError("没有找到 Java，无法运行 OptiFine 安装器；请安装 Java 或用 --java 指定路径")
    if info.major >= 21:
        console.warn(
            f"OptiFine 安装器较老，Java {info.major} 可能不兼容；失败时用 --java 指定 Java 8/17"
        )
    return info


def _working_dir_env(game_dir: Path, cache_root: Path) -> dict[str, str]:
    """OptiFine 安装器只会装到 %APPDATA%/.minecraft（或 ~/.minecraft）。

    自定义目录时造一个临时「假 APPDATA」，里面用目录联接指向真正的游戏目录。
    """
    env = dict(os.environ)
    if os.name == "nt":
        default = Path(os.environ.get("APPDATA", "")) / ".minecraft"
    else:
        default = Path.home() / ".minecraft"
    try:
        if default.resolve() == game_dir.resolve():
            return env
    except OSError:
        pass

    fake_home = cache_root / "optifine-home"
    shim = fake_home / ".minecraft"
    fake_home.mkdir(parents=True, exist_ok=True)
    if shim.exists():
        try:
            shim.rmdir()
        except OSError:
            pass
    if os.name == "nt":
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(shim), str(game_dir)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        env["APPDATA"] = str(fake_home)
    else:
        try:
            shim.symlink_to(game_dir, target_is_directory=True)
        except OSError as exc:
            raise RuntimeError(f"无法创建临时目录链接，请把游戏目录设为默认位置：{exc}") from exc
        env["HOME"] = str(fake_home)
    return env


def _run_installer(
    ctx: installer.InstallContext, java_info: java_mod.JavaInfo, jar: Path, mc_version: str
) -> None:
    cache_root = ctx.game_dir / ".mcdl-cache"
    cache_root.mkdir(parents=True, exist_ok=True)
    log_path = cache_root / f"optifine-install-{mc_version}.log"
    env = _working_dir_env(ctx.game_dir, cache_root)
    command = [str(java_info.path), "-Djava.awt.headless=true", "-cp", str(jar), "optifine.Installer"]

    with open(log_path, "w", encoding="utf-8", errors="replace") as log:
        log.write(" ".join(command) + "\n\n")
        proc = subprocess.run(
            command,
            cwd=str(cache_root),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        log.write((proc.stdout or "") + "\n" + (proc.stderr or ""))
    if proc.returncode != 0:
        tail = ((proc.stdout or "") + (proc.stderr or "")).strip().splitlines()[-6:]
        raise RuntimeError("OptiFine 安装器执行失败：\n" + "\n".join(tail) + f"\n日志：{log_path}")


def _find_optifine_dir(game_dir: Path, mc_version: str, started: float) -> Path | None:
    root = game_dir / "versions"
    if not root.exists():
        return None
    best: tuple[float, Path] | None = None
    for child in root.iterdir():
        if not child.is_dir() or "optifine" not in child.name.lower():
            continue
        json_path = child / f"{child.name}.json"
        if not json_path.exists():
            continue
        mtime = json_path.stat().st_mtime
        if mtime + 5 < started:
            continue
        if best is None or mtime > best[0]:
            best = (mtime, child)
    return best[1] if best else None
