"""模组加载器修补：Fabric（直装 profile）、Forge / NeoForge（官方安装器 + 依赖预拉取）。"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

from . import console, installer, java as java_mod, manifest, net
from . import mirror as mirror_mod
from .mirror import alternate, rewrite

FABRIC_META = "https://meta.fabricmc.net/"
FORGE_PROMOTIONS = "https://files.minecraftforge.net/net/minecraftforge/forge/promotions_slim.json"
FORGE_MAVEN = "https://maven.minecraftforge.net/"
FORGE_MIRROR = "https://bmclapi2.bangbang93.com/maven"
FORGE_METADATA = f"{FORGE_MAVEN}net/minecraftforge/forge/maven-metadata.xml"
BMCL_FORGE_LIST = "https://bmclapi2.bangbang93.com/forge/minecraft/{mc}"

NEOFORGE_MAVEN = "https://maven.neoforged.net/releases/"
NEOFORGE_MIRROR = "https://bmclapi2.bangbang93.com/maven"
NEOFORGE_METADATA = f"{NEOFORGE_MAVEN}net/neoforged/neoforge/maven-metadata.xml"
NEOFORGE_LEGACY_METADATA = f"{NEOFORGE_MAVEN}net/neoforged/forge/maven-metadata.xml"


# ---------------------------------------------------------------- Fabric


def fabric_loaders(mc_version: str, mirror: str = "official") -> list[dict]:
    """Fabric Meta 在部分网络下握手很慢，重试几次再放弃。"""
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            data = net.get_json(f"{FABRIC_META}v2/versions/loader/{mc_version}", mirror, timeout=6)
            if isinstance(data, list):
                return data
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            time.sleep(1.2 * (attempt + 1))
    raise RuntimeError(f"Fabric 版本列表获取失败：{last_error}")


def resolve_fabric_loader(mc_version: str, want: str | None, mirror: str = "official") -> str:
    if want and want not in ("latest", "stable"):
        return want
    loaders = fabric_loaders(mc_version, mirror)
    if not loaders:
        raise RuntimeError(f"Fabric 不支持 Minecraft {mc_version}")
    for item in loaders:
        loader = item.get("loader") or {}
        if loader.get("stable"):
            return loader["version"]
    return loaders[0]["loader"]["version"]


def install_fabric(
    ctx: installer.InstallContext,
    mc_version: str,
    want_loader: str | None = None,
    skip_base: bool = False,
) -> installer.InstallSummary:
    summary = installer.InstallSummary()
    if not skip_base:
        summary = installer.install_base(ctx, mc_version, summary)

    console.step("获取 Fabric 安装配置")
    loader = resolve_fabric_loader(mc_version, want_loader, ctx.mirror)
    profile, fell_back = net.get_json_with_source(
        f"{FABRIC_META}v2/versions/loader/{mc_version}/{loader}/profile/json", ctx.mirror, timeout=5
    )
    lib_mirror = "bmclapi" if (fell_back and ctx.mirror != "bmclapi") else ctx.mirror
    if lib_mirror != ctx.mirror:
        console.info("Fabric 官方仓库响应慢，依赖库改用镜像下载")
    profile_id = profile.get("id") or f"fabric-loader-{loader}-{mc_version}"
    profile["id"] = profile_id
    target = manifest.save_version_json(ctx.game_dir, profile_id, profile)
    console.info(f"Fabric {loader} 配置已写入 versions/{profile_id}/{profile_id}.json")

    platform = manifest.Platform.current()
    libs, natives = manifest.resolve_libraries({"libraries": profile.get("libraries", [])}, platform)
    console.step(f"Fabric 依赖库（{len(libs)} 个）")
    tasks = [
        net.DownloadTask(
            url=rewrite(lib.url, lib_mirror),
            dest=ctx.game_dir / "libraries" / lib.path,
            sha1=lib.sha1,
            size=lib.size,
            verify="sha1" if lib.sha1 else "size",
            fallback_url=alternate(lib.url, lib_mirror),
            label=lib.name,
        )
        for lib in libs
    ]
    stats = net.download_many(tasks, workers=ctx.workers, label="Fabric 依赖", quiet=ctx.quiet)
    summary.downloaded_bytes += stats.downloaded
    summary.downloaded_files += stats.files
    summary.skipped_files += stats.skipped

    installer.write_launcher_profile(
        ctx.game_dir, profile_id, f"Fabric {loader} · {mc_version}", profile_id
    )
    summary.version_id = profile_id
    summary.base_version = mc_version
    summary.notes.append(f"已在启动器中创建配置：Fabric {loader} · {mc_version}")
    summary.notes.append(f"版本目录：{target.parent}")
    return summary


# ---------------------------------------------------------------- Forge


def resolve_forge_version(mc_version: str, want: str | None, mirror: str = "official") -> str:
    if want and want not in ("latest", "recommended"):
        return want
    preference = want or "recommended"
    try:
        promo = net.get_json(FORGE_PROMOTIONS, "official")
        key = f"{mc_version}-{preference}"
        if key in promo.get("promos", {}):
            return promo["promos"][key]
        if f"{mc_version}-latest" in promo.get("promos", {}):
            return promo["promos"][f"{mc_version}-latest"]
    except Exception:
        pass
    # 兜底：从 BMCLAPI 的版本列表里取最高版本
    data = net.get_json(f"https://bmclapi2.bangbang93.com/forge/minecraft/{mc_version}", "official")
    versions = [item["version"] for item in data if item.get("version")]
    if not versions:
        raise RuntimeError(f"没有找到 Minecraft {mc_version} 对应的 Forge 版本")
    return sorted(versions, key=_build_key)[-1]


_FORGE_LIST_CACHE: dict[str, tuple[float, list[str]]] = {}
_FORGE_PROMO_CACHE: dict[str, tuple[float, dict]] = {}


def forge_versions(mc_version: str, mirror: str = "official") -> list[str]:
    """返回某个 Minecraft 版本可用的 Forge 版本（升序）。"""
    cached = _FORGE_LIST_CACHE.get(mc_version)
    if cached and time.time() - cached[0] < 600:
        return cached[1]

    versions: list[str] = []
    try:
        data = net.get_json(BMCL_FORGE_LIST.format(mc=mc_version), "official", timeout=15)
        versions = [str(item["version"]) for item in data if item.get("version")]
    except Exception:
        versions = []
    if not versions:
        xml = _metadata_text(FORGE_METADATA, mirror, "forge-meta.xml")
        versions = [
            value.split("-", 1)[1]
            for value in re.findall(r"<version>([^<]+)</version>", xml)
            if value.startswith(f"{mc_version}-")
        ]
    versions = sorted(set(versions), key=_build_key)
    _FORGE_LIST_CACHE[mc_version] = (time.time(), versions)
    return versions


def forge_recommended(mc_version: str) -> str | None:
    """官方推荐的 Forge 版本；拿不到就返回 None。"""
    cached = _FORGE_PROMO_CACHE.get(mc_version)
    if cached and time.time() - cached[0] < 21600:
        return cached[1].get("recommended") or cached[1].get("latest")
    try:
        promo = net.get_json(FORGE_PROMOTIONS, "official", timeout=6)
        promos = promo.get("promos", {})
        value = promos.get(f"{mc_version}-recommended") or promos.get(f"{mc_version}-latest")
        _FORGE_PROMO_CACHE[mc_version] = (time.time(), {"recommended": value} if value else {})
        return value
    except Exception:
        return None


def install_forge(
    ctx: installer.InstallContext,
    mc_version: str,
    want_loader: str | None = None,
    java_pref: str | None = None,
    skip_base: bool = False,
) -> installer.InstallSummary:
    summary = installer.InstallSummary()
    if not skip_base:
        summary = installer.install_base(ctx, mc_version, summary)

    console.step("解析 Forge 版本")
    version = resolve_forge_version(mc_version, want_loader, ctx.mirror)
    console.info(f"目标：Minecraft {mc_version} + Forge {version}")
    installer_name = f"forge-{mc_version}-{version}-installer.jar"
    url = f"{FORGE_MAVEN}net/minecraftforge/forge/{mc_version}-{version}/{installer_name}"
    return _install_with_installer(
        ctx,
        mc_version,
        kind="forge",
        display="Forge",
        version=version,
        installer_url=url,
        installer_name=installer_name,
        maven_base=FORGE_MAVEN,
        mirror_base=FORGE_MIRROR,
        java_pref=java_pref,
        summary=summary,
    )


# ---------------------------------------------------------------- NeoForge


def _build_key(value: str) -> tuple[int, ...]:
    parts = []
    for chunk in re.split(r"[.\-+]", value):
        parts.append(int(chunk) if chunk.isdigit() else 0)
    return tuple(parts)


def neoforge_prefix(mc_version: str) -> str:
    """1.21.1 -> 21.1.   1.21 -> 21.0.   26.3 -> 26.3."""
    parts = mc_version.split(".")
    if parts[0] == "1" and len(parts) >= 2:
        minor = parts[1]
        patch = parts[2] if len(parts) >= 3 else "0"
        return f"{minor}.{patch}."
    return mc_version + "."


def _metadata_text(url: str, mirror: str, cache_name: str) -> str:
    """拉取 Maven 元数据并落盘缓存；BMCL 的 S3 回源偶尔很慢，缓存能救第二次。"""
    cache = Path.home() / ".mcdl" / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / cache_name
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            xml = net.get_text(url, mirror, timeout=25)
            path.write_text(xml, encoding="utf-8")
            return xml
        except Exception as exc:  # noqa: BLE001 - 镜像偶发抖动，重试即可
            last_error = exc
            time.sleep(1.5 * (attempt + 1))
    if path.exists():
        console.warn(f"元数据拉取失败，使用本地缓存：{path}")
        return path.read_text(encoding="utf-8")
    raise RuntimeError(f"无法获取 NeoForge 版本列表：{last_error}")


def neoforge_versions(mc_version: str, mirror: str = "official") -> list[str]:
    """返回该 Minecraft 版本可用的 NeoForge 版本（升序）。"""
    if mc_version == "1.20.1":
        xml = _metadata_text(NEOFORGE_LEGACY_METADATA, mirror, "neoforge-legacy-meta.xml")
        versions = [v for v in re.findall(r"<version>([^<]+)</version>", xml) if v.startswith("1.20.1-")]
        return sorted(versions, key=_build_key)
    xml = _metadata_text(NEOFORGE_METADATA, mirror, "neoforge-meta.xml")
    prefix = neoforge_prefix(mc_version)
    versions = [v for v in re.findall(r"<version>([^<]+)</version>", xml) if v.startswith(prefix)]
    return sorted(versions, key=_build_key)


def resolve_neoforge_version(mc_version: str, want: str | None, mirror: str = "official") -> str:
    if want and want not in ("latest", "recommended", "stable"):
        return want
    versions = neoforge_versions(mc_version, mirror)
    if not versions:
        raise RuntimeError(f"没有找到 Minecraft {mc_version} 对应的 NeoForge 版本（需要 1.20.2 及以上）")
    return versions[-1]


def install_neoforge(
    ctx: installer.InstallContext,
    mc_version: str,
    want_loader: str | None = None,
    java_pref: str | None = None,
    skip_base: bool = False,
) -> installer.InstallSummary:
    summary = installer.InstallSummary()
    if not skip_base:
        summary = installer.install_base(ctx, mc_version, summary)

    console.step("解析 NeoForge 版本")
    version = resolve_neoforge_version(mc_version, want_loader, ctx.mirror)
    console.info(f"目标：Minecraft {mc_version} + NeoForge {version}")
    if mc_version == "1.20.1":
        installer_name = f"forge-{version}-installer.jar"
        url = f"{NEOFORGE_MAVEN}net/neoforged/forge/{version}/{installer_name}"
    else:
        installer_name = f"neoforge-{version}-installer.jar"
        url = f"{NEOFORGE_MAVEN}net/neoforged/neoforge/{version}/{installer_name}"
    return _install_with_installer(
        ctx,
        mc_version,
        kind="neoforge",
        display="NeoForge",
        version=version,
        installer_url=url,
        installer_name=installer_name,
        maven_base=NEOFORGE_MAVEN,
        mirror_base=NEOFORGE_MIRROR,
        java_pref=java_pref,
        summary=summary,
    )


# ---------------------------------------------------------------- 通用安装器流程


def _install_with_installer(
    ctx: installer.InstallContext,
    mc_version: str,
    *,
    kind: str,
    display: str,
    version: str,
    installer_url: str,
    installer_name: str,
    maven_base: str,
    mirror_base: str,
    java_pref: str | None,
    summary: installer.InstallSummary,
) -> installer.InstallSummary:
    cache = ctx.game_dir / ".mcdl-cache"
    cache.mkdir(parents=True, exist_ok=True)
    installer_path = cache / installer_name

    # 官方 Maven 在 Cloudflare / 海外，国内经常连不上：先测速决定这一阶段用哪个源
    source = mirror_mod.pick_download_source(installer_url, ctx.mirror)
    if source != ctx.mirror:
        console.info(f"{display} 官方仓库不可用或过慢，改用 BMCLAPI 镜像")

    stats = net.download_many(
        [
            net.DownloadTask(
                url=rewrite(installer_url, source),
                dest=installer_path,
                verify="size",
                fallback_url=alternate(installer_url, source),
                label=installer_name,
            )
        ],
        workers=1,
        label=f"{display} 安装器",
        quiet=ctx.quiet,
    )
    summary.downloaded_bytes += stats.downloaded
    summary.downloaded_files += stats.files

    profile, version_json = _read_install_profile(installer_path)
    complete = _prefetch_artifacts(ctx, profile, version_json, source, maven_base)

    installer.ensure_launcher_profiles(ctx.game_dir)

    base_version = manifest.load_local_version(ctx.game_dir, mc_version) or {}
    required = manifest.java_major(base_version) or 8
    java_info = java_mod.pick(java_pref, required)
    if java_info is None:
        raise RuntimeError(f"没有找到 Java，无法运行 {display} 安装器；请先安装 Java 或用 --java 指定路径")
    if required and java_info.major < required:
        console.warn(f"{display} 需要 Java {required}+，当前是 Java {java_info.major}，可能失败")
    elif java_info.major >= 22 and (required or 0) <= 17:
        console.warn(
            f"当前 Java {java_info.major} 较新，{display} 安装器（目标 Java {required}）可能不兼容；失败时可用 --java 换旧版"
        )

    console.step(f"运行 {display} 安装器（执行打补丁）")
    started = time.time()
    log_path = cache / f"{kind}-install-{mc_version}-{version}.log"

    attempts: list[list[str]] = []
    if complete:
        attempts.append(["--offline"])
    if source == "bmclapi":
        attempts.append(["--mirror", mirror_base])
    attempts.append([])

    target = None
    for index, extra in enumerate(attempts):
        mode = "离线" if extra == ["--offline"] else ("镜像" if extra[:1] == ["--mirror"] else "联网")
        console.info(f"安装模式：{mode}")
        _run_installer(java_info, installer_path, ctx.game_dir, log_path, ctx.quiet, extra)
        target = _find_version_dir(ctx.game_dir, mc_version, kind, started)
        if target is not None:
            break
        if index < len(attempts) - 1:
            console.warn("本次未完成，换一种模式重试")
    summary.elapsed += time.time() - started
    if target is None:
        raise RuntimeError(f"{display} 安装器未能生成版本目录；日志：{log_path}")

    summary.version_id = target.name
    summary.base_version = mc_version
    summary.notes.append(f"已生成版本：{target.name}")
    summary.notes.append(f"安装器日志：{log_path}")
    try:
        installer.write_launcher_profile(
            ctx.game_dir, target.name, f"{display} {version} · {mc_version}", target.name
        )
    except Exception:
        pass
    return summary


def _read_install_profile(installer_path: Path) -> tuple[dict, dict]:
    """返回 (install_profile, 安装器内嵌的版本 JSON)。"""
    try:
        with zipfile.ZipFile(installer_path) as archive:
            profile = json.loads(archive.read("install_profile.json").decode("utf-8"))
            version_json: dict = {}
            json_path = str(profile.get("json") or "").lstrip("/")
            if json_path and json_path in archive.namelist():
                version_json = json.loads(archive.read(json_path).decode("utf-8"))
            return profile, version_json
    except (KeyError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
        raise RuntimeError(f"安装器损坏或格式不支持：{exc}") from exc


def _prefetch_artifacts(
    ctx: installer.InstallContext,
    profile: dict,
    version_json: dict | None = None,
    source: str | None = None,
    maven_base: str = FORGE_MAVEN,
) -> bool:
    """先用镜像把安装器要用的依赖拉到 libraries/，安装器会自动跳过已存在的文件。"""
    source = source or ctx.mirror
    tasks: dict[str, net.DownloadTask] = {}
    known: set[str] = set()

    def add(name: str, artifact: dict | None) -> None:
        artifact = artifact or {}
        path = artifact.get("path") or manifest.maven_path(name)
        if not path:
            return
        url = artifact.get("url") or (maven_base + path)
        if not url:
            return
        dest = ctx.game_dir / "libraries" / path
        key = str(dest).lower()
        if key in tasks:
            return
        tasks[key] = net.DownloadTask(
            url=rewrite(url, source),
            dest=dest,
            sha1=artifact.get("sha1"),
            size=artifact.get("size"),
            verify="sha1" if artifact.get("sha1") else "size",
            fallback_url=alternate(url, source),
            label=name,
        )
        known.add(name)

    for lib in profile.get("libraries", []):
        add(lib.get("name", ""), (lib.get("downloads") or {}).get("artifact"))
    for lib in (version_json or {}).get("libraries", []):
        add(lib.get("name", ""), (lib.get("downloads") or {}).get("artifact"))

    processor_coords: set[str] = set()
    for processor in profile.get("processors", []):
        if processor.get("jar"):
            processor_coords.add(processor["jar"])
        processor_coords.update(processor.get("classpath", []))
    for coord in processor_coords:
        if coord not in known:
            add(coord, None)

    if not tasks:
        return True
    console.step(f"{'NeoForge' if 'neoforged' in maven_base else 'Forge'} 依赖预拉取（{len(tasks)} 个）")
    try:
        net.download_many(list(tasks.values()), workers=ctx.workers, label="依赖", quiet=ctx.quiet)
    except net.DownloadError as exc:
        console.warn(f"部分依赖预拉取失败，交给安装器重试：{exc}")
        for target, detail in exc.failures[:3]:
            console.info(f"  失败：{Path(target).name} — {detail[:120]}")
        return False
    return all(task.satisfied() for task in tasks.values())


def _run_installer(
    java_info: java_mod.JavaInfo,
    installer_path: Path,
    game_dir: Path,
    log_path: Path,
    quiet: bool,
    extra_args: list[str] | None = None,
) -> bool:
    command = [
        str(java_info.path),
        "-jar",
        str(installer_path),
        "--installClient",
        str(game_dir),
        *(extra_args or []),
    ]
    with open(log_path, "w", encoding="utf-8", errors="replace") as log:
        log.write(" ".join(command) + "\n\n")
        try:
            proc = subprocess.Popen(
                command,
                cwd=str(game_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as exc:
            raise RuntimeError(f"无法启动 Java：{exc}") from exc

        tail: list[str] = []
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.rstrip()
            if not line:
                continue
            log.write(line + "\n")
            tail.append(line)
            tail = tail[-60:]
            if not quiet and sys.stdout.isatty():
                sys.stdout.write(
                    "\r\x1b[2K  " + console.truncate(line, shutil.get_terminal_size((100, 24)).columns - 4)
                )
                sys.stdout.flush()
        code = proc.wait()
        if not quiet and sys.stdout.isatty():
            sys.stdout.write("\r\x1b[2K")
            sys.stdout.flush()

    if code != 0:
        detail = "\n".join(tail[-8:])
        console.warn(f"安装器退出码 {code}：{detail}")
        return False
    return True


def _find_version_dir(
    game_dir: Path, mc_version: str, kind: str, started: float
) -> Path | None:
    root = game_dir / "versions"
    if not root.exists():
        return None
    best: tuple[float, Path] | None = None
    for child in root.iterdir():
        if not child.is_dir() or kind not in child.name.lower():
            continue
        json_path = child / f"{child.name}.json"
        if not json_path.exists():
            continue
        try:
            with open(json_path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("inheritsFrom") not in (None, mc_version):
            continue
        mtime = json_path.stat().st_mtime
        if mtime + 5 < started:
            continue
        if best is None or mtime > best[0]:
            best = (mtime, child)
    return best[1] if best else None
