import asyncio
import os
import select

from textual.app import ComposeResult
from textual.containers import Container
from textual.widgets import Static

import ptyprocess
import pyte

from patchfeld.widgets._terminal_keys import encode_key
from patchfeld.widgets._terminal_render import render_screen


def _default_command() -> list[str]:
    return [os.environ.get("SHELL", "/bin/sh")]


class Terminal(Container):
    """Real PTY hosted in a Textual panel.

    Spawns a subprocess via ptyprocess.PtyProcessUnicode, feeds output
    through pyte for ANSI emulation, and re-renders whenever the PTY fd
    becomes readable via asyncio.add_reader. Anything typed here is
    OPAQUE to the orchestrator (intentional escape-hatch behavior — use
    this for an interactive `claude` CLI session inside Patchfeld).

    Props:
      command: argv list (default: [$SHELL])
      cwd: working directory (default: process cwd)
      env: extra env vars merged into os.environ

    Limitations: no mouse forwarding; no bracketed paste; no Kitty
    keyboard protocol. POSIX-only (ptyprocess).
    """

    DEFAULT_CSS = """
    Terminal {
        border: round $surface-lighten-2;
        padding: 0 1;
        background: black;
        color: white;
    }
    Terminal Static {
        background: black;
    }
    """

    can_focus = True

    DEFAULT_COLS = 80
    DEFAULT_ROWS = 24
    HISTORY_LINES = 2000  # scrollback rows; memory grows linearly with terminal width
    READ_BUDGET_BYTES = 64 * 1024  # per-tick drain cap; hitting it is fine — add_reader fires again next tick

    def __init__(
        self,
        *,
        command: list[str] | None = None,
        cwd: str | None = None,
        env: dict | None = None,
    ) -> None:
        super().__init__()
        self._command = command or _default_command()
        self._cwd = cwd
        environ = dict(os.environ)
        if env:
            environ.update(env)
        self._env = environ
        self._pty = None
        self._screen = pyte.HistoryScreen(self.DEFAULT_COLS, self.DEFAULT_ROWS, history=self.HISTORY_LINES)
        self._stream = pyte.Stream(self._screen)
        self._timer = None
        self._reader_registered: bool = False
        self._last_write: bytes | None = None
        # Cache of the last rendered Text so _refresh can skip Static.update
        # (and the layout pass it triggers) when the screen is unchanged.
        # Many PTY chunks (cursor blinks, escape continuations, idle reads)
        # produce no visible change.
        self._last_text = None

    def compose(self) -> ComposeResult:
        yield Static("", id="terminal-screen")

    def on_mount(self) -> None:
        try:
            self._pty = ptyprocess.PtyProcessUnicode.spawn(
                self._command,
                cwd=self._cwd,
                env=self._env,
                dimensions=(self.DEFAULT_ROWS, self.DEFAULT_COLS),
            )
        except Exception as e:
            self._show_error(f"PTY spawn failed: {e}")
            return
        loop = asyncio.get_running_loop()
        loop.add_reader(self._pty.fd, self._tick)
        self._reader_registered = True

    def on_resize(self, event) -> None:
        """Propagate Textual size changes to the PTY and the pyte screen.

        Uses `self.content_size` (region shrunk by the styles gutter, i.e.
        border + padding) as the authoritative inner dimensions. This is
        populated by the time the Resize event fires, so we don't need to
        consult the inner Static (whose auto-height can collapse to 1) or
        subtract CSS chrome by hand.
        """
        if self._pty is None:
            return
        inner = self.content_size
        cols = max(1, inner.width)
        rows = max(1, inner.height)
        if cols == self._screen.columns and rows == self._screen.lines:
            return
        # setwinsize and screen.resize form a logical pair: if one fails the
        # other leaves the system in an inconsistent state, so they share a
        # single guard. No logging in Phase 1 — failures are silent for now.
        try:
            self._pty.setwinsize(rows, cols)
            self._screen.resize(rows, cols)
        except Exception:
            return
        self._refresh()

    def on_unmount(self) -> None:
        self._teardown()

    def _teardown(self) -> None:
        if self._reader_registered and self._pty is not None:
            try:
                asyncio.get_running_loop().remove_reader(self._pty.fd)
            except Exception:
                pass
            self._reader_registered = False
        if self._timer is not None:
            try:
                self._timer.stop()
            except Exception:
                pass
            self._timer = None
        if self._pty is not None:
            try:
                self._pty.close(force=True)
            except Exception:
                pass
            self._pty = None

    def _announce_exit(self) -> None:
        # Capture status if available before teardown clears _pty.
        # Banner status: int (real exit code), or "?" sentinel when we can't determine it.
        status: int | str = "?"
        if self._pty is not None:
            try:
                ex = self._pty.exitstatus
                if ex is None:
                    # isalive() polls waitpid(WNOHANG) and populates exitstatus
                    # as a side effect. Non-blocking; safe even if a grandchild
                    # inherited the slave fd and is still running.
                    if not self._pty.isalive():
                        ex = self._pty.exitstatus
                if ex is not None:
                    status = ex
            except Exception:
                pass
        banner = f"\r\n[process exited {status}]\r\n"
        try:
            self._stream.feed(banner)
        except Exception:
            pass
        self._teardown()
        self._refresh()

    def action_restart(self) -> None:
        """Respawn the subprocess in-place. Safe to call after exit."""
        if self._pty is not None:
            # Already running — nothing to do.
            return
        # Reset the screen so the old session's tail doesn't accumulate forever.
        self._screen = pyte.HistoryScreen(
            self._screen.columns, self._screen.lines, history=self.HISTORY_LINES
        )
        self._stream = pyte.Stream(self._screen)
        self.on_mount()

    def _tick(self) -> None:
        if self._pty is None:
            return
        # Drain everything available without blocking, but bounded so a flooding
        # child can't starve the asyncio loop. add_reader will fire again next tick.
        any_data = False
        bytes_read = 0
        eof = False
        while bytes_read < self.READ_BUDGET_BYTES:
            try:
                ready, _, _ = select.select([self._pty.fd], [], [], 0)
                if not ready:
                    break
                chunk = self._pty.read(4096)
            except EOFError:
                eof = True
                break
            except Exception:
                # TODO(phase 2): surface PTY read errors via _show_error
                break
            if not chunk:
                eof = True
                break
            self._stream.feed(chunk)
            bytes_read += len(chunk)
            any_data = True
        if eof:
            self._announce_exit()
        elif any_data:
            self._refresh()

    def _refresh(self) -> None:
        try:
            screen = self.query_one("#terminal-screen", Static)
        except Exception:
            return
        text = render_screen(self._screen, show_cursor=True)
        if self._last_text is not None and text == self._last_text:
            # Identical to last frame — skip the Static.update + layout pass.
            return
        self._last_text = text
        screen.update(text)

    def _show_error(self, msg: str) -> None:
        from rich.text import Text
        try:
            self.query_one("#terminal-screen", Static).update(Text(msg))
        except Exception:
            pass

    def on_key(self, event) -> None:
        if self._pty is None:
            return
        data = encode_key(event.key, event.character)
        if data is None:
            return
        try:
            # encode_key returns bytes; PtyProcessUnicode.write wraps a utf-8 text
            # stream, so decode->re-encode is lossless for any output of encode_key
            # (all paths produce well-formed utf-8).
            self._pty.write(data.decode("utf-8", errors="replace"))
        except Exception:
            # TODO(phase 2): surface PTY write errors via _show_error
            return
        self._last_write = data
        event.stop()

    @classmethod
    def default_border_title(cls, props: dict) -> str:
        from pathlib import Path as _P
        command = props.get("command")
        if command and isinstance(command, list) and len(command) > 0:
            return f"Terminal: {_P(command[0]).name}"
        return "Terminal"
