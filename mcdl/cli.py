"""命令行入口。"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from . import __version__, console, installer, java as java_mod, loaders, manifest, mirror, net, optifine


# ---------------------------------------------------------------- 工具


def default_game_dir() -> Path:
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / ".minecraft"
        return Path.home() / ".minecraft"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "minecraft"
    return Path.home() / ".minecraft"


def fmt_duration(seconds: float) -> str:
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds} 秒"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes} 分 {sec} 秒"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} 小时 {minutes} 分"


def build_context(args: argparse.Namespace) -> installer.InstallContext:
    game_dir = Path(args.dir).expanduser().resolve() if args.dir else default_game_dir()
    chosen = mirror.pick_mirror(args.mirror)
    if not args.quiet:
        console.header(f"DownIt v{__version__}", "Minecraft 下载器")
        console.bullet("游戏目录", str(game_dir))
        console.bullet("下载源", mirror.MIRRORS[chosen] + ("" if chosen == "official" else "（bmclapi2.bangbang93.com）"))
        console.bullet("并发数", str(args.workers))
    return installer.InstallContext(
        game_dir=game_dir,
        mirror=chosen,
        workers=args.workers,
        skip_assets=args.skip_assets,
        quiet=args.quiet,
    )


def print_summary(summary: installer.InstallSummary, ctx: installer.InstallContext) -> None:
    console.header("完成", summary.version_id)
    console.bullet("游戏目录", str(ctx.game_dir))
    if summary.base_version and summary.base_version != summary.version_id:
        console.bullet("原版版本", summary.base_version)
    console.bullet(
        "下载量",
        f"{summary.downloaded_files} 个文件 · {console.human_bytes(summary.downloaded_bytes)}"
        if summary.downloaded_files
        else "无需下载（文件已全部就绪）",
    )
    if summary.skipped_files:
        console.bullet("已跳过", f"{summary.skipped_files} 个已存在文件")
    if summary.assets:
        console.bullet("资源文件", f"{summary.assets} 个")
    console.bullet("总耗时", fmt_duration(summary.elapsed + 0.0))
    for note in summary.notes:
        console.info(note)
    console.line()
    console.info(f"用启动器选择版本 “{summary.version_id}” 即可进入游戏")


# ---------------------------------------------------------------- 命令


def cmd_list(args: argparse.Namespace) -> int:
    console.header("Minecraft 版本列表")
    data = manifest.fetch_manifest(mirror.pick_mirror(args.mirror))
    rows: list[list[str]] = []
    all_matched = manifest.select_versions(data, args.type, args.search, 0)
    versions = all_matched[: args.limit] if args.limit else all_matched
    for entry in versions:
        phase = manifest.version_phase(entry)
        rows.append(
            [entry["id"], manifest.PHASE_LABELS.get(phase, phase), entry.get("releaseTime", "")[:10]]
        )
    console.table(["版本", "类型", "发布时间"], rows)
    console.line()
    console.info(f"正式版最新：{data['latest']['release']}    快照最新：{data['latest']['snapshot']}")
    console.info(
        f"共 {len(all_matched)} 个版本"
        + (f"，已显示前 {len(versions)} 个（--limit 可调整）" if len(all_matched) > len(versions) else "")
    )
    return 0


def cmd_install(args: argparse.Namespace) -> int:
    ctx = build_context(args)
    started = time.monotonic()
    if args.fabric is not None:
        summary = loaders.install_fabric(ctx, args.version, args.fabric)
    elif args.forge is not None:
        summary = loaders.install_forge(ctx, args.version, args.forge)
    elif args.neoforge is not None:
        summary = loaders.install_neoforge(ctx, args.version, args.neoforge)
    elif args.optifine is not None:
        summary = optifine.install_optifine(ctx, args.version, args.optifine, args.optifine_loader)
    else:
        summary = installer.install_base(ctx, args.version)
    summary.elapsed = max(summary.elapsed, time.monotonic() - started)
    print_summary(summary, ctx)
    return 0


def cmd_fabric(args: argparse.Namespace) -> int:
    ctx = build_context(args)
    started = time.monotonic()
    summary = loaders.install_fabric(ctx, args.version, args.loader)
    summary.elapsed = max(summary.elapsed, time.monotonic() - started)
    print_summary(summary, ctx)
    return 0


def cmd_forge(args: argparse.Namespace) -> int:
    ctx = build_context(args)
    started = time.monotonic()
    summary = loaders.install_forge(ctx, args.version, args.loader, args.java)
    summary.elapsed = max(summary.elapsed, time.monotonic() - started)
    print_summary(summary, ctx)
    return 0


def cmd_neoforge(args: argparse.Namespace) -> int:
    ctx = build_context(args)
    started = time.monotonic()
    summary = loaders.install_neoforge(ctx, args.version, args.loader, args.java)
    summary.elapsed = max(summary.elapsed, time.monotonic() - started)
    print_summary(summary, ctx)
    return 0


def cmd_optifine(args: argparse.Namespace) -> int:
    ctx = build_context(args)
    started = time.monotonic()
    summary = optifine.install_optifine(
        ctx, args.version, args.edition, args.loader, args.loader_version, args.java
    )
    summary.elapsed = max(summary.elapsed, time.monotonic() - started)
    print_summary(summary, ctx)
    return 0


def cmd_info(args: argparse.Namespace) -> int:
    chosen = mirror.pick_mirror(args.mirror)
    data = manifest.fetch_manifest(chosen)
    version = manifest.fetch_version(args.version, data, chosen)
    asset_index = version.get("assetIndex") or {}
    client = manifest.client_download(version) or {}
    console.header(f"Minecraft {args.version}", version.get("type", ""))
    console.bullet("发布时间", str(version.get("releaseTime", ""))[:19].replace("T", " "))
    console.bullet("Java 版本", f"Java {manifest.java_major(version) or '未知'}+")
    console.bullet("客户端大小", console.human_bytes(client.get("size") or 0))
    console.bullet("依赖库数量", str(len(version.get("libraries", []))))
    console.bullet("资源索引", f"{asset_index.get('id', '无')}（{console.human_bytes(asset_index.get('totalSize') or 0)}）")
    console.bullet("主类", version.get("mainClass", "?"))

    game_dir = Path(args.dir).expanduser().resolve() if args.dir else default_game_dir()
    console.bullet("游戏目录", str(game_dir))
    local = manifest.load_local_version(game_dir, args.version)
    if local:
        version_dir = manifest.version_dir(game_dir, args.version)
        jar_ok = (version_dir / f"{args.version}.jar").exists()
        console.ok(f"已安装（客户端 jar：{'有' if jar_ok else '缺失'}）")
        installed = installed_loaders(game_dir)
        if installed:
            console.info("该目录已安装的加载器版本：" + "、".join(installed[:6]))
    else:
        console.warn("本地尚未安装")
    return 0


def installed_loaders(game_dir: Path) -> list[str]:
    root = game_dir / "versions"
    if not root.exists():
        return []
    names = []
    for child in sorted(root.iterdir()):
        if child.is_dir() and ("fabric" in child.name.lower() or "forge" in child.name.lower()):
            names.append(child.name)
    return names


def cmd_verify(args: argparse.Namespace) -> int:
    game_dir = Path(args.dir).expanduser().resolve() if args.dir else default_game_dir()
    console.header("校验本地文件", str(game_dir))
    try:
        version = manifest.load_full_version(game_dir, args.version)
    except FileNotFoundError:
        console.error(f"本地没有版本 {args.version}")
        return 1

    platform = manifest.Platform.current()
    libs, natives = manifest.resolve_libraries(version, platform)
    issues: list[list[str]] = []
    checked = 0

    def check_file(path: Path, sha1: str | None, label: str) -> None:
        nonlocal checked
        checked += 1
        if not path.exists():
            issues.append([label, "缺失", str(path)])
        elif sha1 and net.sha1_file(path) != sha1:
            issues.append([label, "SHA1 不符", str(path)])

    client = manifest.client_download(version)
    if client:
        # 加载器版本（Fabric/Forge）的客户端 jar 属于父版本目录
        jar_version = version.get("_parent_id") or args.version
        check_file(
            manifest.version_dir(game_dir, jar_version) / f"{jar_version}.jar",
            client.get("sha1"),
            f"{jar_version}.jar",
        )
    for lib in libs + natives:
        check_file(game_dir / "libraries" / lib.path, lib.sha1, lib.path)

    asset_info = version.get("assetIndex") or {}
    if asset_info:
        index_id = asset_info.get("id")
        index_path = game_dir / "assets" / "indexes" / f"{index_id}.json"
        check_file(index_path, asset_info.get("sha1"), f"indexes/{index_id}.json")
        if index_path.exists():
            import json

            try:
                with open(index_path, encoding="utf-8") as fh:
                    index = json.load(fh)
                objects = index.get("objects") or {}
                seen: set[str] = set()
                for info in objects.values():
                    digest = info["hash"]
                    if digest in seen:
                        continue
                    seen.add(digest)
                    path = game_dir / "assets" / "objects" / digest[:2] / digest
                    checked += 1
                    if not path.exists():
                        issues.append(["资源对象", "缺失", digest])
                    elif path.stat().st_size != info.get("size", path.stat().st_size):
                        issues.append(["资源对象", "大小不符", digest])
            except (json.JSONDecodeError, OSError):
                issues.append(["资源索引", "无法读取", str(index_path)])

    if not issues:
        console.ok(f"全部通过：{checked} 个文件完好")
        return 0

    console.table(["文件", "问题", "位置"], issues[:40])
    if len(issues) > 40:
        console.info(f"…… 另有 {len(issues) - 40} 项问题")
    console.error(f"{len(issues)} / {checked} 个文件存在问题，重新运行安装命令可自动补齐")
    return 1


def cmd_java(args: argparse.Namespace) -> int:
    console.header("Java 运行时")
    found = java_mod.detect_all()
    if not found:
        console.warn("没有找到任何 Java。请安装 Java 或设置 JAVA_HOME")
        return 1
    console.table(
        ["Java", "主版本", "路径"],
        [[info.version, str(info.major), str(info.path)] for info in found],
        ["left", "right", "left"],
    )
    return 0


def cmd_gui(args: argparse.Namespace) -> int:
    from . import gui

    return gui.serve(
        host=args.host,
        port=args.port,
        game_dir=Path(args.dir).expanduser().resolve() if args.dir else default_game_dir(),
        mirror_name=args.mirror,
        window=not (args.browser or args.server_only),
        open_browser=not args.server_only,
    )


# ---------------------------------------------------------------- 参数


def make_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--dir", help="游戏目录，默认使用官方启动器的 .minecraft")
    common.add_argument(
        "--mirror",
        choices=["auto", "official", "bmclapi"],
        default="auto",
        help="下载源：auto 自动测速（默认）/ official 官方 / bmclapi 国内镜像",
    )
    common.add_argument("--workers", type=int, default=8, help="并发下载数，默认 8")
    common.add_argument("--skip-assets", action="store_true", help="跳过资源文件（体积大，但游戏内无声音）")
    common.add_argument("--quiet", action="store_true", help="安静模式，只输出关键信息")
    common.add_argument("--no-color", action="store_true", help="关闭彩色输出")

    parser = argparse.ArgumentParser(
        prog="mcdl",
        description="DownIt —— Minecraft 下载器：从 Mojang CDN 下载游戏并修补 Fabric / Forge。",
        epilog="示例：\n"
        "  mcdl list --limit 10\n"
        "  mcdl install 1.20.1\n"
        "  mcdl fabric 1.20.1\n"
        "  mcdl forge 1.20.1 --loader recommended\n"
        "  mcdl neoforge 1.21.1\n"
        "  mcdl optifine 1.20.1 --loader forge\n"
        "  mcdl install 1.20.1 --dir \"D:/Games/.minecraft\" --mirror bmclapi\n"
        "  mcdl verify 1.20.1\n"
        "  mcdl gui",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"DownIt {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="命令")

    p_list = sub.add_parser("list", parents=[common], help="列出可下载的 Minecraft 版本")
    p_list.add_argument(
        "--type",
        choices=["release", "snapshot", "pre", "rc", "old_beta", "old_alpha", "all"],
        default="release",
        help="版本阶段：正式版 / 快照 / 预发布 / 候选版 / 远古版本",
    )
    p_list.add_argument("--search", default="", help="按版本号筛选，例如 1.20")
    p_list.add_argument("--limit", type=int, default=20, help="显示条数，0 表示全部")
    p_list.set_defaults(func=cmd_list)

    p_install = sub.add_parser("install", parents=[common], help="下载并安装一个版本（可顺带装加载器）")
    p_install.add_argument("version", help="Minecraft 版本号，例如 1.20.1")
    install_loader = p_install.add_mutually_exclusive_group()
    install_loader.add_argument("--fabric", nargs="?", const="latest", metavar="LOADER", help="同时安装 Fabric（可指定加载器版本）")
    install_loader.add_argument("--forge", nargs="?", const="recommended", metavar="FORGE", help="同时安装 Forge（可指定版本）")
    install_loader.add_argument("--neoforge", nargs="?", const="latest", metavar="VERSION", help="同时安装 NeoForge（可指定版本）")
    install_loader.add_argument("--optifine", nargs="?", const="latest", metavar="EDITION", help="同时安装 OptiFine（可指定版本）")
    p_install.add_argument(
        "--optifine-loader",
        choices=["none", "forge"],
        default="none",
        help="OptiFine 的安装方式：none 独立版本（默认）/ forge 作为 Forge 模组",
    )
    p_install.set_defaults(func=cmd_install)

    p_fabric = sub.add_parser("fabric", parents=[common], help="安装原版 + Fabric")
    p_fabric.add_argument("version", help="Minecraft 版本号")
    p_fabric.add_argument("--loader", default="latest", help="Fabric 加载器版本，默认取最新稳定版")
    p_fabric.set_defaults(func=cmd_fabric)

    p_forge = sub.add_parser("forge", parents=[common], help="安装原版 + Forge")
    p_forge.add_argument("version", help="Minecraft 版本号")
    p_forge.add_argument("--loader", default="recommended", help="latest / recommended / 具体版本号")
    p_forge.add_argument("--java", help="运行 Forge 安装器所用的 java 路径")
    p_forge.set_defaults(func=cmd_forge)

    p_neoforge = sub.add_parser("neoforge", parents=[common], help="安装原版 + NeoForge")
    p_neoforge.add_argument("version", help="Minecraft 版本号，例如 1.21.1")
    p_neoforge.add_argument("--loader", default="latest", help="NeoForge 版本，默认取最新")
    p_neoforge.add_argument("--java", help="运行安装器所用的 java 路径")
    p_neoforge.set_defaults(func=cmd_neoforge)

    p_optifine = sub.add_parser("optifine", parents=[common], help="安装原版 + OptiFine")
    p_optifine.add_argument("version", help="Minecraft 版本号，例如 1.20.1")
    p_optifine.add_argument("--edition", default="latest", help="OptiFine 版本，如 HD_U_I6；默认最新正式版")
    p_optifine.add_argument(
        "--loader", choices=["none", "forge"], default="none", help="none 独立安装 / forge 作为模组"
    )
    p_optifine.add_argument("--loader-version", help="配合 --loader forge 时的 Forge 版本")
    p_optifine.add_argument("--java", help="运行安装器所用的 java 路径")
    p_optifine.set_defaults(func=cmd_optifine)

    p_info = sub.add_parser("info", parents=[common], help="查看版本详情与本地安装状态")
    p_info.add_argument("version", help="Minecraft 版本号")
    p_info.set_defaults(func=cmd_info)

    p_verify = sub.add_parser("verify", parents=[common], help="校验本地文件完整性（SHA1）")
    p_verify.add_argument("version", help="Minecraft 版本号")
    p_verify.set_defaults(func=cmd_verify)

    p_java = sub.add_parser("java", parents=[common], help="列出本机可用的 Java")
    p_java.set_defaults(func=cmd_java)

    p_gui = sub.add_parser("gui", parents=[common], help="启动本地图形界面（浏览器）")
    p_gui.add_argument("--port", type=int, default=8765, help="端口，默认 8765")
    p_gui.add_argument("--host", default="127.0.0.1", help="监听地址")
    p_gui.add_argument("--browser", action="store_true", help="用默认浏览器打开，而不是桌面窗口")
    p_gui.add_argument("--server-only", action="store_true", help="只启动服务，不打开窗口/浏览器")
    p_gui.set_defaults(func=cmd_gui)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = make_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        # 双击 exe 时没有参数：直接进图形界面
        from . import gui

        console.init_console(None)
        return gui.serve(
            host="127.0.0.1",
            port=8765,
            game_dir=default_game_dir(),
            mirror_name="auto",
            window=True,
        )
    console.init_console(False if getattr(args, "no_color", False) else None)
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        console.line()
        console.warn("已取消")
        return 130
    except net.DownloadError as exc:
        console.error(str(exc))
        for target, detail in exc.failures[:10]:
            console.line(f"    {target}\n      {detail}")
        if len(exc.failures) > 10:
            console.line(f"    …… 另有 {len(exc.failures) - 10} 个失败文件")
        return 1
    except (RuntimeError, FileNotFoundError, KeyError, ValueError) as exc:
        console.error(str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
