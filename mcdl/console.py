"""终端输出：颜色、表格、进度条。全部只用标准库。"""

from __future__ import annotations

import os
import shutil
import sys
import time
import unicodedata

# ---------------------------------------------------------------- 颜色

RESET = "\x1b[0m"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"
RED = "\x1b[31m"
GREEN = "\x1b[32m"
YELLOW = "\x1b[33m"
BLUE = "\x1b[34m"
MAGENTA = "\x1b[35m"
CYAN = "\x1b[36m"
GRAY = "\x1b[90m"


def _enable_windows_vt() -> bool:
    if os.name != "nt":
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        # ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except Exception:
        return False


class _NullStream:
    """打包成无控制台 exe 时，标准流是 None；用一个黑洞顶替。"""

    encoding = "utf-8"

    def write(self, text: str) -> int:
        return len(text)

    def flush(self) -> None:
        return None

    def isatty(self) -> bool:
        return False


def init_console(force_color: bool | None = None) -> bool:
    """初始化颜色支持，返回是否启用 ANSI。"""
    global _COLOR
    if sys.stdout is None:
        sys.stdout = _NullStream()  # type: ignore[assignment]
    if sys.stderr is None:
        sys.stderr = _NullStream()  # type: ignore[assignment]
    if force_color is True:
        _COLOR = True
    elif force_color is False:
        _COLOR = False
    else:
        _COLOR = (
            sys.stdout.isatty()
            and not os.environ.get("NO_COLOR")
            and os.environ.get("TERM") != "dumb"
            and _enable_windows_vt()
        )
    return _COLOR


_COLOR = False


def paint(text: str, *styles: str) -> str:
    if not _COLOR or not styles:
        return text
    return "".join(styles) + text + RESET


def width(text: str) -> int:
    """终端显示宽度（CJK 记 2）。"""
    total = 0
    for ch in text:
        total += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return total


def truncate(text: str, limit: int) -> str:
    if limit <= 0:
        return ""
    if width(text) <= limit:
        return text
    out, used = "", 0
    for ch in text:
        w = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if used + w > limit - 1:
            break
        out += ch
        used += w
    return out + "…"


def pad(text: str, limit: int, align: str = "left") -> str:
    text = truncate(text, limit)
    space = " " * max(0, limit - width(text))
    if align == "right":
        return space + text
    if align == "center":
        left = len(space) // 2
        return " " * left + text + " " * (len(space) - left)
    return text + space


# 事件钩子：GUI 通过它拿到结构化进度，而不必解析终端文本
_EVENT_SINK = None


def set_event_sink(sink) -> None:
    global _EVENT_SINK
    _EVENT_SINK = sink


def emit(kind: str, **data) -> None:
    if _EVENT_SINK is None:
        return
    try:
        _EVENT_SINK({"kind": kind, **data})
    except Exception:  # noqa: BLE001 - 事件回调异常不影响主流程
        pass


# ---------------------------------------------------------------- 输出


def line(text: str = "") -> None:
    sys.stdout.write(text + "\n")


def info(text: str) -> None:
    line(f"{paint('·', GRAY)} {text}")


def ok(text: str) -> None:
    line(f"{paint('✓', GREEN)} {text}")


def warn(text: str) -> None:
    line(f"{paint('!', YELLOW)} {text}")


def error(text: str) -> None:
    line(f"{paint('×', RED)} {text}", )


def step(text: str) -> None:
    line(f"{paint('›', CYAN)} {paint(text, BOLD)}")


def header(title: str, subtitle: str = "") -> None:
    line()
    line(paint(title, BOLD, CYAN) + ("  " + paint(subtitle, GRAY) if subtitle else ""))


def bullet(key: str, value: str) -> None:
    line(f"  {paint(key.ljust(14), GRAY)}{value}")


def human_bytes(num: float) -> str:
    if num is None:
        return "?"
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(num)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} B"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def human_count(num: int) -> str:
    if num >= 10000:
        return f"{num / 10000:.1f} 万"
    return str(num)


def table(headers: list[str], rows: list[list[str]], aligns: list[str] | None = None) -> None:
    """打印对齐表格。"""
    if not rows:
        line(paint("（无数据）", GRAY))
        return
    cols = len(headers)
    aligns = aligns or ["left"] * cols
    widths = [width(str(h)) for h in headers]
    for row in rows:
        for i in range(cols):
            widths[i] = max(widths[i], width(str(row[i]) if i < len(row) else ""))
    term = shutil.get_terminal_size((100, 24)).columns
    # 超过终端宽度时按比例压缩每一列
    total = sum(widths) + 2 * (cols - 1)
    if total > term - 2:
        budget = max(term - 2 - 2 * (cols - 1), cols * 6)
        scale = budget / total
        widths = [max(6, int(w * scale)) for w in widths]

    head = "  ".join(paint(pad(str(h), widths[i]), BOLD, GRAY) for i, h in enumerate(headers))
    line(head)
    line(paint("  ".join("─" * w for w in widths), GRAY))
    for row in rows:
        cells = [pad(str(row[i]) if i < len(row) else "", widths[i], aligns[i]) for i in range(cols)]
        line("  ".join(cells))


# ---------------------------------------------------------------- 进度条


class ProgressBar:
    """单行刷新进度条；非 TTY 时自动降级为分段打印。"""

    def __init__(self, label: str = "下载", stream=None, enabled: bool | None = None):
        self.label = label
        self.stream = stream or sys.stdout
        self.enabled = self.stream.isatty() if enabled is None else enabled
        self.start = time.monotonic()
        self.last_draw = 0.0
        self.samples: list[tuple[float, int]] = []
        self.last_bytes = 0
        self.last_stage = ""

    def _rate(self, done: int) -> float:
        now = time.monotonic()
        self.samples.append((now, done))
        while len(self.samples) > 2 and now - self.samples[0][0] > 5:
            self.samples.pop(0)
        if len(self.samples) < 2:
            return 0.0
        t0, b0 = self.samples[0]
        if now <= t0:
            return 0.0
        return (done - b0) / (now - t0)

    def update(
        self,
        done: int,
        total: int | None,
        files_done: int = 0,
        files_total: int = 0,
        stage: str = "",
        approximate: bool = False,
    ) -> None:
        now = time.monotonic()
        if self.enabled and now - self.last_draw < 0.1 and (total is None or done < total):
            return
        self.last_draw = now
        rate = self._rate(done)
        self.last_stage = stage

        if approximate and files_total:
            frac = min(files_done / files_total, 1.0)
            pct = f"{frac * 100:5.1f}%"
        elif total and total > 0:
            frac = min(done / total, 1.0)
            pct = f"{frac * 100:5.1f}%"
        else:
            frac = 0.0
            pct = "  ?  "
        bar_len = max(12, min(34, shutil.get_terminal_size((100, 24)).columns - 66))
        filled = int(bar_len * frac)
        bar = "█" * filled + "░" * (bar_len - filled)

        parts = [f"{paint(bar, CYAN)} {pct}"]
        if total and approximate:
            parts.append(f"{human_bytes(done)}（已知 {human_bytes(total)}）")
        elif total:
            parts.append(f"{human_bytes(done)}/{human_bytes(total)}")
        else:
            parts.append(human_bytes(done))
        if rate > 0:
            parts.append(f"{human_bytes(rate)}/s")
        if files_total:
            parts.append(f"{files_done}/{files_total} 个文件")
        text = "  ".join(parts)
        if stage:
            remain = shutil.get_terminal_size((100, 24)).columns - width(text) - 4
            if remain > 12:
                text += "  " + paint(truncate(stage, remain), GRAY)

        if self.enabled:
            self.stream.write("\r\x1b[2K" + text)
            self.stream.flush()
        elif now - self.last_draw >= 2 or (total and done >= total):
            self.stream.write(f"  {self.label} {pct.strip()} {human_bytes(done)}{'/' + human_bytes(total) if total else ''}\n")
            self.stream.flush()

        emit(
            "progress",
            done=done,
            total=total or 0,
            files=files_done,
            files_total=files_total,
            stage=stage,
        )

    def close(self) -> None:
        if self.enabled:
            self.stream.write("\n")
            self.stream.flush()
