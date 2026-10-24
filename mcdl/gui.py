"""本地服务 + 图形界面：托管 React 前端，并通过 JSON API 驱动安装任务。"""

from __future__ import annotations

import json
import mimetypes
import os
import re
import sys
import threading
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import __version__, console, installer, java as java_mod, loaders, manifest, mirror, optifine

DEFAULT_DIR = Path.home() / ".minecraft"
DEFAULT_MIRROR = "auto"

_MANIFEST_CACHE: dict = {"data": None, "ts": 0.0}
_ACTIVE_JOB = None
_JOB_LOCK = threading.Lock()
_WINDOW = None
_JAVA_CACHE: dict = {"ts": 0.0, "data": []}

_LOADERS = {"none", "fabric", "forge", "neoforge", "optifine"}


def web_root() -> Path:
    """开发时用源码目录；打包成 exe 后从解包目录取前端静态文件。"""
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        packaged = base / "mcdl" / "web" / "dist"
        if packaged.exists():
            return packaged
        return base / "web" / "dist"
    return Path(__file__).with_name("web") / "dist"


class Job:
    def __init__(self, title: str):
        self.id = uuid.uuid4().hex[:8]
        self.title = title
        self.status = "running"
        self.error = ""
        self.events: list[dict] = []
        self.lock = threading.Lock()
        self.started = time.time()

    def push(self, event: dict) -> None:
        with self.lock:
            event["t"] = round(time.time() - self.started, 2)
            self.events.append(event)

    def log(self, text: str) -> None:
        self.push({"kind": "log", "text": text})

    def set_status(self, status: str, error: str = "") -> None:
        self.status = status
        self.error = error
        self.push({"kind": "status", "status": status, "error": error})

    def slice(self, since: int) -> tuple[list[dict], str, str]:
        with self.lock:
            return self.events[since:], self.status, self.error


class JobStream:
    """把控制台输出切行转成任务事件。"""

    _ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

    def __init__(self, job: Job):
        self.job = job
        self.buffer = ""

    def write(self, text: str) -> int:
        if not text:
            return 0
        text = self._ANSI.sub("", text).replace("\r", "\n")
        self.buffer += text
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            line = line.rstrip()
            if line:
                self.job.log(line)
        return len(text)

    def flush(self) -> None:
        if self.buffer.strip():
            self.job.log(self.buffer.strip())
            self.buffer = ""

    def isatty(self) -> bool:
        return False


def _javas() -> list[dict]:
    """探测本机 Java，结果缓存一分钟（探测会执行 java -version）。"""
    if time.time() - _JAVA_CACHE["ts"] > 60:
        try:
            _JAVA_CACHE["data"] = [
                {"path": str(info.path), "version": info.version, "major": info.major}
                for info in java_mod.detect_all()
            ]
        except Exception:
            _JAVA_CACHE["data"] = []
        _JAVA_CACHE["ts"] = time.time()
    return _JAVA_CACHE["data"]


def _pick_dir() -> str | None:
    """桌面窗口模式下弹系统目录选择框；浏览器模式返回 None。"""
    window = _WINDOW
    if window is None:
        return None
    try:
        import webview

        result = window.create_file_dialog(webview.FOLDER_DIALOG, directory=str(DEFAULT_DIR))
        if result:
            return str(result[0])
    except Exception:
        return None
    return None


def _installed(game_dir: Path) -> list[dict]:
    root = game_dir / "versions"
    if not root.exists():
        return []
    result = []
    for child in sorted(root.iterdir(), reverse=True):
        json_path = child / f"{child.name}.json"
        if not child.is_dir() or not json_path.exists():
            continue
        name = child.name.lower()
        if "optifine" in name:
            loader = "optifine"
        elif "neoforge" in name:
            loader = "neoforge"
        elif "fabric" in name:
            loader = "fabric"
        elif "forge" in name:
            loader = "forge"
        else:
            loader = "none"
        result.append({"id": child.name, "loader": loader})
    return result


def _run_job(job: Job, params: dict) -> None:
    console.init_console(False)
    console.set_event_sink(job.push)
    old_stdout = sys.stdout
    sys.stdout = JobStream(job)
    try:
        game_dir = Path(params.get("dir") or DEFAULT_DIR).expanduser()
        game_dir.mkdir(parents=True, exist_ok=True)
        version = params["version"]
        loader = params.get("loader") or "none"
        loader_version = (params.get("loader_version") or "").strip() or None
        optifine_mode = params.get("optifine_mode") or "none"

        titles = {
            "none": "原版",
            "fabric": "Fabric",
            "forge": "Forge",
            "neoforge": "NeoForge",
            "optifine": "OptiFine",
        }
        console.header(f"安装 {version}", titles.get(loader, loader))
        console.bullet("游戏目录", str(game_dir))
        console.info("正在选择下载源…")
        source = mirror.pick_mirror(params.get("mirror") or "auto")
        console.info(f"下载源：{mirror.MIRRORS[source]}")
        console.bullet("并发数", str(params.get("workers") or 8))

        java_path = (params.get("java") or "").strip() or None
        ctx = installer.InstallContext(
            game_dir=game_dir,
            mirror=source,
            workers=int(params.get("workers") or 8),
            skip_assets=bool(params.get("skip_assets")),
            quiet=bool(params.get("quiet")),
        )
        started = time.time()
        if loader == "fabric":
            summary = loaders.install_fabric(ctx, version, loader_version)
        elif loader == "forge":
            summary = loaders.install_forge(ctx, version, loader_version, java_path)
        elif loader == "neoforge":
            summary = loaders.install_neoforge(ctx, version, loader_version, java_path)
        elif loader == "optifine":
            summary = optifine.install_optifine(
                ctx, version, loader_version, optifine_mode, None, java_path
            )
        else:
            summary = installer.install_base(ctx, version)
        summary.elapsed = max(summary.elapsed, time.time() - started)

        console.line()
        console.ok(f"安装完成：{summary.version_id}")
        if summary.downloaded_files:
            console.info(
                f"{summary.downloaded_files} 个文件 · {console.human_bytes(summary.downloaded_bytes)}"
                + (f" · {summary.skipped_files} 个已存在" if summary.skipped_files else "")
            )
        else:
            console.info(f"无需下载，{summary.skipped_files} 个文件已就绪")
        for note in summary.notes:
            console.info(note)
        job.set_status("done")
    except Exception as exc:  # noqa: BLE001 - 任何失败都要反馈到界面
        console.line()
        console.error(str(exc))
        job.set_status("error", str(exc))
    finally:
        console.set_event_sink(None)
        sys.stdout = old_stdout


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = f"DownIt/{__version__}"

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003 - 静默访问日志
        pass

    # ------------------------------------------------------------ helpers

    def _send(self, body: bytes, content_type: str, code: int = 200) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data, code: int = 200) -> None:
        self._send(
            json.dumps(data, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8",
            code,
        )

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}

    def _static(self, relative: str) -> None:
        root = web_root().resolve()
        target = (root / relative).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            self._json({"error": "forbidden"}, 403)
            return
        if not target.exists() or not target.is_file():
            self._json({"error": "not found"}, 404)
            return
        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if target.suffix == ".js":
            content_type = "text/javascript; charset=utf-8"
        elif target.suffix == ".css":
            content_type = "text/css; charset=utf-8"
        elif target.suffix in (".html", ".htm"):
            content_type = "text/html; charset=utf-8"
        self._send(target.read_bytes(), content_type)

    # ------------------------------------------------------------ routes

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        if path in ("/", "/index.html"):
            self._static("index.html")
        elif path == "/api/state":
            self._json(
                {
                    "version": __version__,
                    "game_dir": str(DEFAULT_DIR),
                    "mirror": DEFAULT_MIRROR,
                    "mirrors": mirror.MIRRORS,
                    "installed": _installed(DEFAULT_DIR),
                    "javas": _javas(),
                    "workers": 8,
                    "busy": bool(_ACTIVE_JOB and _ACTIVE_JOB.status == "running"),
                    "job": _ACTIVE_JOB.id if _ACTIVE_JOB else None,
                }
            )
        elif path == "/api/versions":
            self._json(self._versions(parsed.query))
        elif path == "/api/loader-versions":
            self._json(self._loader_versions(parse_qs(parsed.query)))
        elif path == "/api/pick-dir":
            self._json({"path": _pick_dir()})
        elif path.startswith("/api/jobs/"):
            job = _ACTIVE_JOB
            job_id = path.rsplit("/", 1)[-1]
            if job is None or job.id != job_id:
                self._json({"error": "任务不存在"}, 404)
                return
            since = 0
            query = parse_qs(parsed.query)
            if "since" in query:
                try:
                    since = max(0, int(query["since"][0]))
                except ValueError:
                    since = 0
            events, status, error = job.slice(since)
            self._json(
                {"events": events, "status": status, "error": error, "next": since + len(events)}
            )
        elif path.startswith("/api/"):
            self._json({"error": "not found"}, 404)
        else:
            self._static(path.lstrip("/"))

    def do_POST(self) -> None:  # noqa: N802
        global _ACTIVE_JOB
        if urlparse(self.path).path != "/api/install":
            self._json({"error": "not found"}, 404)
            return
        with _JOB_LOCK:
            if _ACTIVE_JOB and _ACTIVE_JOB.status == "running":
                self._json({"error": "已有任务正在进行"}, 409)
                return
            params = self._read_body()
            version = str(params.get("version") or "").strip()
            if not version:
                self._json({"error": "缺少版本号"}, 400)
                return
            loader = str(params.get("loader") or "none")
            if loader not in _LOADERS:
                self._json({"error": f"未知的加载器：{loader}"}, 400)
                return
            job = Job(f"{version} · {loader}")
            _ACTIVE_JOB = job
        threading.Thread(target=_run_job, args=(job, params), daemon=True).start()
        self._json({"job": job.id})

    # ------------------------------------------------------------ data

    def _loader_versions(self, query: dict) -> dict:
        loader = (query.get("loader") or ["none"])[0]
        mc_version = (query.get("version") or [""])[0]
        preferred = (query.get("mirror") or [DEFAULT_MIRROR])[0]
        if loader == "none" or not mc_version:
            return {"loader": loader, "version": mc_version, "default": "", "versions": []}
        try:
            source = mirror.pick_mirror(preferred)
            if loader == "fabric":
                items = loaders.fabric_loaders(mc_version, source)
                versions = [
                    {
                        "value": item["loader"]["version"],
                        "label": item["loader"]["version"],
                        "note": "稳定" if item["loader"].get("stable") else "",
                    }
                    for item in items
                ]
                default = next((v["value"] for v in versions if v["note"] == "稳定"), "")
            elif loader == "forge":
                raw = loaders.forge_versions(mc_version, source)
                default = loaders.forge_recommended(mc_version) or (raw[-1] if raw else "")
                versions = [
                    {"value": v, "label": v, "note": "推荐" if v == default else ""}
                    for v in reversed(raw)
                ]
            elif loader == "neoforge":
                raw = loaders.neoforge_versions(mc_version, source)
                default = raw[-1] if raw else ""
                versions = [
                    {"value": v, "label": v, "note": "最新" if v == default else ""}
                    for v in reversed(raw)
                ]
            elif loader == "optifine":
                editions = optifine.optifine_editions(mc_version, source)
                default = next(
                    (item["edition"] for item in editions if not item["preview"]),
                    editions[0]["edition"] if editions else "",
                )
                versions = [
                    {
                        "value": item["edition"],
                        "label": item["label"],
                        "note": "预览" if item["preview"] else ("推荐" if item["edition"] == default else ""),
                    }
                    for item in editions
                ]
            else:
                return {"loader": loader, "version": mc_version, "default": "", "versions": []}
            return {
                "loader": loader,
                "version": mc_version,
                "default": default,
                "versions": versions,
            }
        except Exception as exc:  # noqa: BLE001 - 版本列表失败不应阻塞手动输入
            return {"loader": loader, "version": mc_version, "default": "", "versions": [], "error": str(exc)}

    def _versions(self, query_string: str) -> dict:
        query = parse_qs(query_string)
        preferred = (query.get("mirror") or [DEFAULT_MIRROR])[0]
        now = time.time()
        try:
            if not _MANIFEST_CACHE["data"] or now - _MANIFEST_CACHE["ts"] > 600:
                source = mirror.pick_mirror(preferred)
                _MANIFEST_CACHE["data"] = manifest.fetch_manifest(source)
                _MANIFEST_CACHE["ts"] = now
        except Exception as exc:  # noqa: BLE001
            if not _MANIFEST_CACHE["data"]:
                return {"error": str(exc), "versions": []}
        data = _MANIFEST_CACHE["data"]
        return {
            "latest": data["latest"],
            "versions": [
                {
                    "id": v["id"],
                    "type": v.get("type"),
                    "phase": manifest.version_phase(v),
                    "releaseTime": (v.get("releaseTime") or "")[:10],
                }
                for v in data["versions"][:300]
            ],
        }


def _start_server(host: str, port: int) -> tuple[ThreadingHTTPServer, int] | None:
    for offset in range(10):
        try:
            server = ThreadingHTTPServer((host, port + offset), Handler)
            return server, port + offset
        except OSError:
            continue
    return None


def serve(
    host: str = "127.0.0.1",
    port: int = 8765,
    game_dir: Path | None = None,
    mirror_name: str = "auto",
    window: bool = True,
    open_browser: bool = True,
) -> int:
    global DEFAULT_DIR, DEFAULT_MIRROR
    if game_dir:
        DEFAULT_DIR = game_dir
    DEFAULT_MIRROR = mirror_name

    bound = _start_server(host, port)
    if bound is None:
        console.error("端口被占用，无法启动图形界面")
        return 1
    server, port = bound
    url = f"http://{host}:{port}/"

    mode = "桌面窗口" if window else "浏览器"
    console.header("DownIt", f"{url}  ({mode})")
    console.bullet("游戏目录", str(DEFAULT_DIR))
    console.info("关闭窗口或按 Ctrl+C 结束")

    if window:
        try:
            import webview

            threading.Thread(target=server.serve_forever, daemon=True).start()
            global _WINDOW
            _WINDOW = webview.create_window(
                "DownIt", url, width=1200, height=880, min_size=(980, 680)
            )
            webview.start()
            server.shutdown()
            return 0
        except Exception as exc:  # noqa: BLE001 - 没有 WebView2 时退回浏览器
            console.warn(f"无法打开桌面窗口（{exc}），改用浏览器")

    if open_browser and not os.environ.get("MCDL_NO_BROWSER"):
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        console.line()
        console.info("已退出")
    finally:
        server.server_close()
    return 0
