"""SystemUsage widget — compact CPU + RAM gauges with auto-refresh.

Single-row layout: ``CPU  23.4% [▰▰▰▱▱…]   RAM  8.4/16.0 GiB [▰▰▰▰…]``.

`psutil` is preferred when present (cleanest, cross-platform). When absent
the widget falls back to non-blocking ``top -l 1 -n 0`` + ``vm_stat`` +
``sysctl hw.memsize`` shell-outs on macOS via
``asyncio.create_subprocess_exec`` so the Textual event loop is never
blocked. On unsupported platforms without psutil, the widget renders an
error banner and stops scheduling refreshes.
"""

from __future__ import annotations

import asyncio
import re
import sys
from dataclasses import dataclass

from textual.widgets import Static


# Try psutil but never require it — patchbai's deps stay minimal. If a user
# happens to have psutil in their venv (e.g. via a transitive dep), we use
# it; otherwise we shell out on macOS.
try:
    import psutil  # type: ignore
    _HAS_PSUTIL = True
except ImportError:
    psutil = None  # type: ignore
    _HAS_PSUTIL = False


_FILLED = "▰"
_EMPTY = "▱"
_GIB = 1024 ** 3


@dataclass
class _Sample:
    cpu_pct: float
    ram_used_gib: float
    ram_total_gib: float

    @property
    def ram_pct(self) -> float:
        if self.ram_total_gib <= 0:
            return 0.0
        return 100.0 * self.ram_used_gib / self.ram_total_gib


def _color_for(pct: float) -> str:
    if pct < 50:
        return "green"
    if pct < 80:
        return "yellow"
    return "red"


def _bar(pct: float, width: int) -> str:
    pct = max(0.0, min(100.0, pct))
    filled = int(round(pct / 100.0 * width))
    filled = max(0, min(width, filled))
    color = _color_for(pct)
    return (
        f"[{color}]{_FILLED * filled}[/]"
        f"[dim]{_EMPTY * (width - filled)}[/]"
    )


# --------------------------------------------------------------- macOS shells

_VMSTAT_PAGE_SIZE_RE = re.compile(r"page size of (\d+) bytes")
_VMSTAT_LINE_RE = re.compile(r"^([^:]+):\s+(\d+)")
_TOP_CPU_RE = re.compile(
    r"CPU usage:\s+([\d.]+)%\s+user,\s+([\d.]+)%\s+sys,\s+([\d.]+)%\s+idle"
)


async def _run(*argv: str) -> str:
    """Run a command without blocking the event loop; return stdout text."""
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    out, _ = await proc.communicate()
    return out.decode("utf-8", errors="replace")


async def _macos_cpu_pct() -> float:
    """Parse ``top -l 1 -n 0`` for the CPU usage line.

    `top` already samples internally; ``user + sys`` is equivalent to
    ``100 - idle`` within rounding, and is more robust to spaces/punctuation
    drift than parsing the idle field.
    """
    text = await _run("top", "-l", "1", "-n", "0")
    m = _TOP_CPU_RE.search(text)
    if not m:
        raise RuntimeError("could not parse CPU usage from `top`")
    return float(m.group(1)) + float(m.group(2))


async def _macos_ram_gib() -> tuple[float, float]:
    """Return ``(used_gib, total_gib)`` using ``vm_stat`` + ``sysctl``.

    ``used = (active + wired_down + occupied_by_compressor) × page_size`` —
    matches Activity Monitor's "Memory Used" closely. Page size is read from
    the ``vm_stat`` header (16 KiB on Apple Silicon, 4 KiB on Intel).
    """
    vm_text, mem_text = await asyncio.gather(
        _run("vm_stat"),
        _run("sysctl", "-n", "hw.memsize"),
    )

    page_size = 4096
    m = _VMSTAT_PAGE_SIZE_RE.search(vm_text)
    if m:
        page_size = int(m.group(1))

    pages: dict[str, int] = {}
    for line in vm_text.splitlines():
        mm = _VMSTAT_LINE_RE.match(line)
        if mm:
            pages[mm.group(1).strip().lower()] = int(mm.group(2))

    active = pages.get("pages active", 0)
    wired = pages.get("pages wired down", 0)
    compressed = pages.get("pages occupied by compressor", 0)
    used_bytes = (active + wired + compressed) * page_size

    total_bytes = int(mem_text.strip() or "0")
    if total_bytes <= 0:
        raise RuntimeError("hw.memsize returned 0")

    return used_bytes / _GIB, total_bytes / _GIB


async def _sample_shellout() -> _Sample:
    cpu, ram = await asyncio.gather(_macos_cpu_pct(), _macos_ram_gib())
    used, total = ram
    return _Sample(cpu_pct=cpu, ram_used_gib=used, ram_total_gib=total)


async def _sample_psutil() -> _Sample:
    cpu = psutil.cpu_percent(interval=None)  # type: ignore[union-attr]
    vm = psutil.virtual_memory()  # type: ignore[union-attr]
    return _Sample(
        cpu_pct=float(cpu),
        ram_used_gib=vm.used / _GIB,
        ram_total_gib=vm.total / _GIB,
    )


# ----------------------------------------------------------------- the widget


class SystemUsage(Static):
    """Single-row CPU + RAM gauge that refreshes itself.

    ``interval`` (seconds, clamped >= 0.25, default 1.5) controls refresh
    cadence. ``bar_width`` (cells, clamped >= 2, default 12) controls the
    width of each progress bar.
    """

    DEFAULT_CSS = """
    SystemUsage {
        height: auto;
        min-height: 1;
        padding: 0 1;
        content-align: left middle;
    }
    """

    def __init__(
        self,
        *,
        interval: float = 1.5,
        bar_width: int = 12,
        **kwargs,
    ) -> None:
        super().__init__("CPU  …   RAM  …", **kwargs)
        self._interval = max(0.25, float(interval))
        self._bar_width = max(2, int(bar_width))
        self._supported = _HAS_PSUTIL or sys.platform == "darwin"
        self._source = "psutil" if _HAS_PSUTIL else "shell"
        self._timer = None

    def on_mount(self) -> None:
        # Prime psutil so the very first sample isn't 0.0 (psutil's
        # cpu_percent needs a baseline tick to compute deltas against).
        if _HAS_PSUTIL:
            try:
                psutil.cpu_percent(interval=None)  # type: ignore[union-attr]
            except Exception:
                pass

        if not self._supported:
            self._show_error(
                f"unsupported platform: {sys.platform} (install psutil)"
            )
            return

        self.border_title = f"system — {self._source}"
        # Kick one immediate refresh, then schedule periodic ones.
        self.run_worker(self._tick(), exclusive=True)
        self._timer = self.set_interval(self._interval, self._schedule_tick)

    def _schedule_tick(self) -> None:
        # `set_interval` fires on the event loop; offload the actual sample
        # to a worker so a slow shell-out can never delay the next tick.
        self.run_worker(self._tick(), exclusive=True)

    async def _tick(self) -> None:
        try:
            if _HAS_PSUTIL:
                sample = await _sample_psutil()
            else:
                sample = await _sample_shellout()
        except Exception as exc:  # never raise into the layout
            self._show_error(str(exc))
            return
        self._show_sample(sample)

    # Named ``_show_*`` rather than ``_render*`` to avoid colliding with
    # ``Widget._render`` (which has a different signature/return shape).
    def _show_sample(self, s: _Sample) -> None:
        cpu_bar = _bar(s.cpu_pct, self._bar_width)
        ram_bar = _bar(s.ram_pct, self._bar_width)
        cpu_str = f"[b]CPU[/b] {s.cpu_pct:5.1f}% {cpu_bar}"
        ram_str = (
            f"[b]RAM[/b] {s.ram_used_gib:5.1f}/{s.ram_total_gib:.1f} GiB "
            f"{ram_bar}"
        )
        self.update(f"{cpu_str}   {ram_str}")
        # Recover from a transient error: title might still say "error".
        if self.border_title and "error" in self.border_title:
            self.border_title = f"system — {self._source}"

    def _show_error(self, msg: str) -> None:
        self.update("CPU  ?   RAM  ?")
        # Truncate so the border title stays compact.
        self.border_title = f"system — error: {msg[:80]}"
