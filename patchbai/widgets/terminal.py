import asyncio
import os
import select

from textual.app import ComposeResult
from textual.containers import Container
from textual.widgets import Static

import ptyprocess
import pyte

from patchbai.widgets._terminal_render import render_screen


def _default_command() -> list[str]:
    return [os.environ.get("SHELL", "/bin/sh")]


class Terminal(Container):
    """Real PTY hosted in a Textual panel.

    Spawns a subprocess via ptyprocess.PtyProcessUnicode, feeds output
    through pyte for ANSI emulation, and renders the screen on a 50ms
    poll. Anything typed here is OPAQUE to the orchestrator (intentional
    escape-hatch behavior — use this for an interactive `claude` CLI
    session inside Patchbai).

    Props:
      command: argv list (default: [$SHELL])
      cwd: working directory (default: process cwd)
      env: extra env vars merged into os.environ

    Limitations: line-mode keystroke forwarding only; no mouse; resize
    on the fly is best-effort. POSIX-only (ptyprocess).
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

    DEFAULT_COLS = 80
    DEFAULT_ROWS = 24
    HISTORY_LINES = 2000  # ~17 MB worst case at 80 cols × 112B per pyte Char

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

    def _tick(self) -> None:
        if self._pty is None:
            return
        # Drain everything available without blocking. select-loop keeps us nonblocking.
        any_data = False
        while True:
            try:
                ready, _, _ = select.select([self._pty.fd], [], [], 0)
                if not ready:
                    break
                chunk = self._pty.read(4096)
            except EOFError:
                self._teardown()
                break
            except Exception:
                break
            if not chunk:
                break
            self._stream.feed(chunk)
            any_data = True
        if any_data:
            self._refresh()

    def _refresh(self) -> None:
        try:
            screen = self.query_one("#terminal-screen", Static)
        except Exception:
            return
        text = render_screen(self._screen, show_cursor=True)
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
        key = event.key
        char = event.character
        try:
            if char is not None and len(char) == 1 and char.isprintable():
                self._pty.write(char)
                event.stop()
            elif key == "enter":
                self._pty.write("\n")
                event.stop()
            elif key == "backspace":
                self._pty.write("\x7f")
                event.stop()
            elif key == "tab":
                self._pty.write("\t")
                event.stop()
            elif key == "ctrl+c":
                self._pty.write("\x03")
                event.stop()
            elif key == "ctrl+d":
                self._pty.write("\x04")
                event.stop()
        except Exception:
            pass

    @classmethod
    def default_border_title(cls, props: dict) -> str:
        from pathlib import Path as _P
        command = props.get("command")
        if command and isinstance(command, list) and len(command) > 0:
            return f"Terminal: {_P(command[0]).name}"
        return "Terminal"
