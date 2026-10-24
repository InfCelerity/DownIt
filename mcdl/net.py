"""HTTP 下载内核：并发、断点续传、SHA1 校验、自动回退、进度统计。"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from . import console
from .mirror import alternate, rewrite

USER_AGENT = "DownIt/1.0 (Minecraft downloader)"
CHUNK = 256 * 1024


class DownloadError(RuntimeError):
    def __init__(self, message: str, failures: list[tuple[str, str]] | None = None):
        super().__init__(message)
        self.failures = failures or []


@dataclass
class DownloadTask:
    url: str
    dest: Path
    sha1: str | None = None
    size: int | None = None
    label: str = ""
    verify: str = "sha1"  # sha1 | size | none
    fallback_url: str | None = None
    retries: int = 2

    def satisfied(self) -> bool:
        try:
            st = self.dest.stat()
        except OSError:
            return False
        if not st.st_size and (self.size or 0) > 0:
            return False
        if self.size is not None and st.st_size != self.size:
            return False
        if self.verify == "sha1" and self.sha1:
            return sha1_file(self.dest) == self.sha1
        return True


@dataclass
class DownloadStats:
    downloaded: int = 0
    total: int = 0
    files: int = 0
    skipped: int = 0
    elapsed: float = 0.0
    failures: list[tuple[str, str]] = field(default_factory=list)


def sha1_file(path: Path) -> str:
    digest = hashlib.sha1()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _ssl_context() -> ssl.SSLContext:
    return ssl.create_default_context()


def open_url(url: str, headers: dict[str, str] | None = None, timeout: float = 20.0):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    return urllib.request.urlopen(request, timeout=timeout, context=_ssl_context())


# ---------------------------------------------------------------- 元数据请求

_HOST_FAILURES: dict[str, float] = {}
_FAILURE_TTL = 600.0


def _candidates(url: str, mirror: str) -> list[str]:
    """候选地址：首选源在前，另一个源作兜底；本次运行中失败的域名会被降级。"""
    result: list[str] = []
    for candidate in (rewrite(url, mirror), alternate(url, mirror)):
        if candidate not in result:
            result.append(candidate)
    if len(result) > 1:
        failed_at = _HOST_FAILURES.get(urllib.parse.urlparse(result[0]).netloc)
        if failed_at and time.monotonic() - failed_at < _FAILURE_TTL:
            result.reverse()
    return result


def _read_url(url: str, timeout: float) -> bytes:
    with open_url(url, timeout=timeout) as resp:
        return resp.read()


def _race(urls: list[str], timeout: float) -> tuple[int, bytes]:
    """并发请求多个源，返回最先成功的一个（下标, 内容）。

    小 JSON 请求总量很小，并发抢占比顺序等待超时要快得多——尤其是官方源在
    国内不稳定、只能靠镜像的时候。
    """
    box: "queue.Queue[tuple[int, bool, object]]" = queue.Queue()

    def worker(index: int, url: str) -> None:
        try:
            box.put((index, True, _read_url(url, timeout)))
        except BaseException as exc:  # noqa: BLE001
            box.put((index, False, exc))

    for index, url in enumerate(urls):
        threading.Thread(target=worker, args=(index, url), daemon=True).start()

    deadline = time.monotonic() + timeout + 1.5
    errors: list[Exception] = []
    received = 0
    while received < len(urls):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            index, ok, value = box.get(timeout=remaining)
        except queue.Empty:
            break
        received += 1
        if ok:
            return index, value  # type: ignore[return-value]
        errors.append(value)  # type: ignore[arg-type]
    raise DownloadError(str(errors[-1]) if errors else "请求超时")


def get_json_with_source(
    url: str, mirror: str = "official", timeout: float = 12.0
) -> tuple[dict, bool]:
    """返回 (JSON, 是否来自备用源)。"""
    urls = _candidates(url, mirror)
    index, raw = _race(urls, timeout)
    try:
        return json.loads(raw.decode("utf-8")), index > 0
    except ValueError as exc:
        raise DownloadError(f"{url} 返回的不是合法 JSON：{exc}") from exc


def get_json(url: str, mirror: str = "official", timeout: float = 12.0) -> dict:
    data, _ = get_json_with_source(url, mirror, timeout)
    return data


def get_text(url: str, mirror: str = "official", timeout: float = 12.0) -> str:
    _, raw = _race(_candidates(url, mirror), timeout)
    return raw.decode("utf-8", "replace")


# ---------------------------------------------------------------- 批量下载


def _head_size(url: str, timeout: float = 8.0) -> int | None:
    try:
        request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=timeout, context=_ssl_context()) as resp:
            length = resp.headers.get("Content-Length")
            return int(length) if length and length.isdigit() else None
    except Exception:  # noqa: BLE001 - 拿不到就按未知处理
        return None


def fill_sizes(tasks: list[DownloadTask], workers: int = 8) -> None:
    """对缺少大小的文件发 HEAD 请求补齐，便于统计总进度。"""
    unknown = [task for task in tasks if task.size is None]
    if not unknown or len(unknown) > 64:
        return
    with ThreadPoolExecutor(max_workers=min(workers, len(unknown))) as pool:
        futures = {pool.submit(_head_size, task.url): task for task in unknown}
        for future in as_completed(futures):
            size = future.result()
            if size:
                futures[future].size = size


class _Counter:
    def __init__(self, progress: console.ProgressBar, total: int, files_total: int, approximate: bool):
        self.lock = threading.Lock()
        self.done = 0
        self.files = 0
        self.total = total
        self.files_total = files_total
        self.approximate = approximate
        self.progress = progress
        self.current = ""
        self.last = 0.0

    def add(self, n: int) -> None:
        with self.lock:
            self.done += n
            now = time.monotonic()
            if now - self.last > 0.1:
                self.last = now
                self.progress.update(
                    self.done,
                    self.total or None,
                    self.files,
                    self.files_total,
                    self.current,
                    approximate=self.approximate,
                )

    def preadd(self, n: int) -> None:
        """断点续传时把已存在的字节计入进度。"""
        with self.lock:
            self.done += n

    def file_done(self) -> None:
        with self.lock:
            self.files += 1

    def set_current(self, label: str) -> None:
        self.current = label


def _fetch(task: DownloadTask, url: str, part: Path, counter: _Counter, cancel: threading.Event) -> None:
    existing = part.stat().st_size if part.exists() else 0
    headers = {"Range": f"bytes={existing}-"} if existing else {}
    try:
        resp = open_url(url, headers=headers, timeout=20.0)
    except urllib.error.HTTPError as exc:
        if exc.code == 416 and task.size and existing == task.size:
            return
        raise

    with resp:
        status = getattr(resp, "status", 200)
        if existing and status == 206:
            mode, written = "ab", existing
        else:
            mode, written, existing = "wb", 0, 0
        if task.size is not None and written > task.size:
            mode, written, existing = "wb", 0, 0

        if existing:
            counter.preadd(existing)
        with open(part, mode) as fh:
            while True:
                if cancel.is_set():
                    raise DownloadError("已取消")
                chunk = resp.read(CHUNK)
                if not chunk:
                    break
                fh.write(chunk)
                written += len(chunk)
                counter.add(len(chunk))


def download_many(
    tasks: list[DownloadTask],
    workers: int = 8,
    label: str = "下载",
    quiet: bool = False,
    cancel: threading.Event | None = None,
) -> DownloadStats:
    """并发下载一组文件，返回统计结果。已存在且校验通过的文件自动跳过。"""
    cancel = cancel or threading.Event()

    unique: dict[str, DownloadTask] = {}
    for task in tasks:
        unique.setdefault(str(task.dest).lower(), task)
    tasks = list(unique.values())

    pending = [task for task in tasks if not task.satisfied()]
    skipped = len(tasks) - len(pending)
    stats = DownloadStats(skipped=skipped)
    if not pending:
        if not quiet:
            console.ok(f"{label}：{len(tasks)} 个文件已全部就绪")
        return stats

    fill_sizes(pending, workers)
    total = sum(t.size or 0 for t in pending)
    unknown = any(t.size is None for t in pending)
    stats.total = total

    progress = console.ProgressBar(label, enabled=False if quiet else None)
    counter = _Counter(progress, total, len(pending), unknown)
    if not quiet:
        console.info(f"{label}：需下载 {len(pending)} 个文件，共 {console.human_bytes(total)}")

    started = time.monotonic()
    failures: list[tuple[str, str]] = []

    def run(task: DownloadTask) -> None:
        counter.set_current(task.label or task.dest.name)
        task.dest.parent.mkdir(parents=True, exist_ok=True)
        part = task.dest.with_name(task.dest.name + ".part")
        urls = [task.url]
        if task.fallback_url and task.fallback_url != task.url:
            urls.append(task.fallback_url)
        last_error = ""
        for attempt in range(task.retries + 1):
            for url in urls:
                try:
                    _fetch(task, url, part, counter, cancel)
                    if task.size is not None and part.stat().st_size != task.size:
                        raise DownloadError(f"大小不符：期望 {task.size}，实际 {part.stat().st_size}")
                    if task.verify == "sha1" and task.sha1:
                        actual = sha1_file(part)
                        if actual != task.sha1:
                            part.unlink(missing_ok=True)
                            raise DownloadError(f"SHA1 不符（{actual[:8]}…）")
                    os.replace(part, task.dest)
                    counter.file_done()
                    return
                except Exception as exc:  # noqa: BLE001 - 单文件失败不应中断整批
                    last_error = f"{type(exc).__name__}: {exc}"
                    if isinstance(exc, DownloadError) and str(exc) == "已取消":
                        raise
            time.sleep(min(2 ** attempt * 0.6, 4))
        raise DownloadError(last_error or "未知错误")

    try:
        with ThreadPoolExecutor(max_workers=max(1, min(workers, 32))) as pool:
            futures = {pool.submit(run, task): task for task in pending}
            for future in as_completed(futures):
                task = futures[future]
                try:
                    future.result()
                except Exception as exc:  # noqa: BLE001
                    failures.append((str(task.dest), f"{task.url} -> {exc}"))
    finally:
        progress.close()

    stats.downloaded = counter.done
    stats.elapsed = time.monotonic() - started
    stats.failures = failures
    if failures:
        raise DownloadError(f"{len(failures)} 个文件下载失败", failures)
    return stats
