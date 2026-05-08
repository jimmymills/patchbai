# Runtime cwd Change + Footer cwd Indicator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user re-root the running Patchfeld workspace at a new cwd from inside the app (modal, keybinding, `/cd` slash command, or orchestrator MCP tool) AND show the active cwd in the footer status bar at all times.

**Architecture:** A new `App.change_cwd(new_cwd)` orchestrates a deterministic re-root: refuses if any child agents are still running, then stops the orchestrator + manager, swaps `self.cwd`, rebuilds both, reloads (or seeds) the new cwd's `.patchfeld/workspace.json`, re-applies the active theme, and publishes a new `WorkspaceCwdChanged` event. The `StatusBar` adds an `sb-cwd` Static that subscribes to that event and renders an abbreviated, width-aware path. Triggers are layered (action → keybinding → modal screen, `/cd` slash command in `OrchestratorSession`, `change_cwd` MCP tool in `orchestrator/tools.py`) so every existing entry point — manual user, scripted keybinding, conversational orchestrator — works.

**Tech Stack:** Python 3.12, Textual 8.x (`ModalScreen`, `Binding`, `Static`), pydantic v2, pytest-asyncio.

---

## Design Decisions Made (orchestrator may override during review)

These decisions resolve the ambiguities in the spec. They are documented here so reviewers can reverse them with one task each.

| # | Decision | Rationale | Cheap reversal |
|---|----------|-----------|----------------|
| D1 | **Refuse re-root while non-terminal children are running.** Return a structured error naming the running agents. | The Claude Agent SDK fixes a child's process cwd at spawn time — we cannot retroactively rebase a running child without restarting it. Carrying it across a re-root would silently make its tool calls write to the *old* project, which is far worse than a friendly refusal. | Add a `force=true` flag in v2 that calls `manager.kill(...)` on each running agent before swapping. |
| D2 | **Re-root performs an orchestrator reset.** The orchestrator's session index, transcripts, and SDK options are bound to a cwd; we tear the inner SDK session down and start a fresh one against the new cwd's `.patchfeld/`. The previous session continues to exist on disk under the old cwd and can be resumed by going back. | Resuming an SDK session whose `ClaudeAgentOptions.cwd` differs from where the session was first opened is uncharted. Treating re-root like "open this other project" is symmetric with `patchfeld` initial launch. | Add a `keep_session=true` flag in v2 that passes the old `_sdk_session_id` to `_swap_inner(resume=...)` after the cwd change. |
| D3 | **Load `<new_cwd>/.patchfeld/workspace.json` if it exists; otherwise seed from `dashboard_layout()`.** Each project owns its own workspace state. | Symmetric with the existing `_load_or_seed_workspace()` boot path. Users who switch back and forth between projects expect each to remember its layout. | Add a `carry_layout=true` flag that copies the current Workspace into the new cwd before mounting. |
| D4 | **`FileTree(path=...)` props are not auto-rebased.** A FileTree with `path="src"` mounted via the new cwd's saved layout is interpreted relative to the *process's* cwd at construction time — same as today. | The layout JSON is the source of truth; we don't want a re-root to silently mutate paths the user typed. Saved layouts that meant to be project-relative should already be using absolute paths or `path: "."`. | Document explicitly in `FileTree.__init__` doctring; no code change needed. |
| D5 | **Footer formatting**: show `cwd: ~/foo/bar` when the path is under `Path.home()`; otherwise absolute. Truncate from the LEFT with `…/last/segs` when the text would exceed `min(40, container_width // 2)` chars. Sit between `sb-layout` and `sb-error`. | Matches the visible-real-estate constraints of the existing 1-row StatusBar; left-truncation keeps the most informative trailing segments. | Tweak constants in `chrome.py::_format_cwd`. |
| D6 | **Triggers exposed:** `ctrl+shift+d` keybinding → `ChangeCwdScreen` modal; `/cd <path>` slash command; `change_cwd` action in the registry (so users can re-bind); `change_cwd` MCP tool for the orchestrator LLM. | Mirrors the layered exposure of `/reset`, `/resume`, layouts, themes — every existing capability has a manual, scripted, and orchestrator surface. | Drop any of the four; each is independent. |

---

## File Structure

**Created**

- `patchfeld/widgets/change_cwd_screen.py` — `ChangeCwdScreen(ModalScreen[str | None])`. Single text Input with the current cwd pre-filled. Submit → dismiss with the trimmed string; Escape → dismiss `None`.
- `tests/test_widget_change_cwd_screen.py` — modal smoke tests (renders, escape cancels, submit returns trimmed string).
- `tests/test_app_change_cwd.py` — integration tests for `App.change_cwd` (happy path, refuse-with-running-children, no-op same path, invalid path, slash command path).
- `tests/test_chrome_cwd.py` — StatusBar cwd display tests (initial value, abbreviation under `$HOME`, left-truncation, updates on `WorkspaceCwdChanged`).
- `tests/test_orchestrator_change_cwd_tool.py` — MCP-tool-level tests for the new orchestrator tool.

**Modified**

- `patchfeld/events.py` — add `WorkspaceCwdChanged(cwd: str)`.
- `patchfeld/widgets/chrome.py` — `StatusBar` gains an `sb-cwd` Static, an `_on_cwd_changed` subscriber, an `on_resize` re-formatter, and a pure `_format_cwd(path, available_width)` helper.
- `patchfeld/app.py` — add `change_cwd()`, `action_change_cwd()`, `action_open_change_cwd()`, register the action, add the `ctrl+shift+d` Binding, publish `WorkspaceCwdChanged` from `on_mount` and from `change_cwd`.
- `patchfeld/orchestrator/session.py` — handle `/cd <path>` in `_on_user_message`; expose a small async helper `_handle_cd_command` that calls `app.change_cwd`.
- `patchfeld/orchestrator/tools.py` — register `change_cwd` MCP tool routed through `app.change_cwd`.

**No changes**

- `patchfeld/persistence/paths.py` — already takes `cwd` as an argument everywhere. The new manager/orchestrator built with the new cwd will read/write the right files.
- `patchfeld/workspace/spec.py` — Workspace model already has no cwd field; cwd is implicit from where it lives on disk.
- `patchfeld/widgets/file_tree.py`, `widgets/notebook.py`, `widgets/terminal.py` — these read `app.cwd` at mount time, so re-mounting via `_mount_workspace` after a cwd swap picks up the new value automatically.

---

## Data Flow

User trigger (any of the four surfaces) →
`App.change_cwd(new_cwd)` (the single seam) →
1. acquire `self._cwd_swap_lock` (an `asyncio.Lock` added to `__init__`)
2. resolve the path (`Path(new_cwd).expanduser().resolve()`) and validate it
3. early-exit no-op if the resolved path equals `self.cwd`
4. snapshot `[info for info in self.manager.list_infos() if not info.state.is_terminal]` — refuse if non-empty
5. save the current workspace one last time at the OLD cwd
6. `await self.orchestrator.stop()` (unsubscribes from the bus, stops inner SDK)
7. `await self.manager.shutdown()` (kills sessions — already none — and unsubscribes)
8. swap `self.cwd = new_cwd`; reset `self._workspace = None`, `self._active_tab_id = None`, `self._current_layout_name = None`, `self._tab_focus_snapshots.clear()`
9. rebuild `self.manager = AgentManager(cwd=new_cwd, bus=self.event_bus, adapter_factory=RealSDKAdapter)`
10. rebuild `self.orchestrator = OrchestratorSession(cwd=new_cwd, bus=self.event_bus, manager=self.manager, ...)` with the same wiring as `__init__`; set `_auto_title_enabled = True`
11. `await self.orchestrator.start()`
12. `ws = self._load_or_seed_workspace(); self._workspace = ws; self._active_tab_id = ws.active`
13. clear the live `TabbedContent` panes via `_mount_workspace(ws)` — this re-mounts every widget against the new cwd
14. re-apply the theme using the new workspace's `active_theme` (or global config) — same call as in `on_mount`
15. `self.event_bus.publish(WorkspaceCwdChanged(cwd=str(new_cwd)))`
16. release the lock; return `{"changed": str(new_cwd)}`

The footer's `_on_cwd_changed` handler reads the event and re-renders. The StatusBar's existing `_on_layout_applied` keeps working because the new workspace boot also publishes `LayoutApplied` per tab via `apply_layout`.

The bus itself is **never replaced** during the swap. App-level subscriptions (`AgentTokensTouched`, `AgentStateChanged`, `AgentSpawned`, `LayoutResized`, `OpenResumePicker`, the new `WorkspaceCwdChanged`) all stay live across re-roots. Only the orchestrator's and manager's own subscriptions cycle (they handle their own lifecycles in start/stop and __init__/shutdown).

---

## Edge Cases

- **Path is the same as current** (after `.resolve()`): no-op, return `{"unchanged": True}`. Don't tear anything down.
- **Path does not exist / is not a directory**: return `{"error": "invalid_path", "path": str(input)}`. UI surfaces this as a notify-toast in the modal handler.
- **Path expansion of `~user`** when the user doesn't exist: `Path.expanduser` raises; catch and surface as "invalid_path".
- **Non-terminal children are running**: return `{"error": "agents_running", "agents": [{"id": ..., "name": ...}, ...]}`. Modal handler renders the names in a notify-toast.
- **`workspace.json` at new cwd is malformed**: `_load_or_seed_workspace` already falls through to `dashboard_layout()`. Unchanged behavior.
- **Two cwd swaps requested concurrently** (e.g., a fast `/cd` followed by a modal submit): the asyncio lock serialises them. Second swap reads `self.cwd` post-first-swap as its starting point.
- **Theme load fails for the new cwd's saved theme**: existing fallback to `"default"` already in `on_mount` is reused.
- **Symlinks**: `Path.resolve()` collapses them. `cwd-symlink → real-path` shows up in the footer as `real-path`, which keeps the displayed cwd canonical (matches what subprocesses see).
- **New cwd lacks `.patchfeld/`**: `save_workspace` creates it. No error.
- **Footer width too narrow to even fit `cwd: …/x`**: `_format_cwd` returns the bare last segment with no prefix (e.g., `patchfeld`), trusting Textual's CSS to clip.
- **Action invoked before `on_mount` finishes**: `action_open_change_cwd` early-returns if `self._workspace is None`.
- **Persisted layout at new cwd uses a `FileTree(path="src")`**: mounts with whatever Path that resolves to under the *new* cwd (Textual's `DirectoryTree` accepts the relative string and Path resolves at first I/O). This is consistent with FileTree's documented contract; D4 above.

---

## Implementation Order

| # | Task | Reason it's first |
|---|------|-------------------|
| 1 | New `WorkspaceCwdChanged` event + StatusBar `_format_cwd` pure helper | Pure, no dependencies — TDD friendly. |
| 2 | StatusBar renders cwd at boot | Lets us see progress visually at every later step. |
| 3 | StatusBar listens for `WorkspaceCwdChanged` | Decouples display from any swap mechanism. |
| 4 | `ChangeCwdScreen` modal | Standalone modal, mirrors `NewTabScreen`. |
| 5 | `App.change_cwd` happy path | Core seam everything else routes through. |
| 6 | `change_cwd` refuses on running children | Adds the safety guarantee before exposing more triggers. |
| 7 | Action registration + `ctrl+shift+d` binding + modal wiring | First user-facing trigger. |
| 8 | `/cd <path>` slash command in OrchestratorSession | Second trigger, shares the same seam. |
| 9 | `change_cwd` MCP tool | Third trigger. |
| 10 | Integration smoke + footer-on-real-resize test | End-to-end coverage. |

Each task ends in a commit so the tree always builds.

---

## Task 1: Add `WorkspaceCwdChanged` event and a pure `_format_cwd` helper

**Files:**
- Modify: `patchfeld/events.py` (add new dataclass after `TabSwitched`, before `FileSelected`)
- Modify: `patchfeld/widgets/chrome.py` (add module-level `_format_cwd`)
- Create: `tests/test_chrome_cwd.py`

- [ ] **Step 1.1: Write the failing tests for the formatter**

Create `tests/test_chrome_cwd.py`:

```python
from pathlib import Path

import pytest

from patchfeld.widgets.chrome import _format_cwd


def test_format_cwd_uses_tilde_when_under_home(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    nested = tmp_path / "Developer" / "patchfeld"
    assert _format_cwd(nested, available_width=80) == "~/Developer/patchfeld"


def test_format_cwd_keeps_absolute_when_outside_home(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "elsewhere"))
    assert _format_cwd(Path("/var/log/foo"), available_width=80) == "/var/log/foo"


def test_format_cwd_left_truncates_when_too_long():
    p = Path("/a/very/very/very/long/nested/path/with/many/segs/leaf")
    out = _format_cwd(p, available_width=20)
    # Must end at a segment boundary, start with the truncation marker, fit budget.
    assert out.startswith("…/")
    assert out.endswith("/leaf")
    assert len(out) <= 20


def test_format_cwd_falls_back_to_basename_when_budget_tiny():
    p = Path("/a/b/c/leaf")
    assert _format_cwd(p, available_width=4) == "leaf"
```

- [ ] **Step 1.2: Run, confirm failure**

```bash
uv run pytest tests/test_chrome_cwd.py -v
```

Expected: ImportError on `_format_cwd`.

- [ ] **Step 1.3: Add `_format_cwd` to `patchfeld/widgets/chrome.py`**

At the top of `chrome.py`, after the imports, before `class CommandBar`:

```python
def _format_cwd(path: Path, *, available_width: int) -> str:
    """Render `path` for the StatusBar, abbreviating under $HOME and
    left-truncating with '…/' to fit `available_width` characters.

    Pure: no I/O, no widget access. Drives `_on_cwd_changed` and
    `on_resize` in StatusBar.
    """
    try:
        home = Path.home()
        abs_p = Path(path).resolve()
        try:
            rel = abs_p.relative_to(home)
            display = "~" + ("/" + str(rel) if str(rel) != "." else "")
        except ValueError:
            display = str(abs_p)
    except Exception:
        display = str(path)

    if available_width <= 0 or len(display) <= available_width:
        return display
    # Try left-truncation that ends at a segment boundary.
    parts = display.split("/")
    # Keep peeling leading segments until "…/" + tail fits.
    for keep in range(len(parts) - 1, 0, -1):
        candidate = "…/" + "/".join(parts[-keep:])
        if len(candidate) <= available_width:
            return candidate
    # Budget too tight even for "…/leaf" — return bare basename.
    return parts[-1]
```

Add the matching `from pathlib import Path` import at the top if not already present.

- [ ] **Step 1.4: Run tests, confirm pass**

```bash
uv run pytest tests/test_chrome_cwd.py -v
```

Expected: 4 passed.

- [ ] **Step 1.5: Add the `WorkspaceCwdChanged` event**

In `patchfeld/events.py`, after `class TabSwitched` and before `class FileSelected`:

```python
@dataclass(frozen=True)
class WorkspaceCwdChanged:
    """The app's working directory has been re-rooted at runtime. The
    workspace state has already been reloaded from `cwd` and the active
    layout re-applied; subscribers should re-render any cwd-dependent UI."""
    cwd: str
```

- [ ] **Step 1.6: Commit**

```bash
git add patchfeld/events.py patchfeld/widgets/chrome.py tests/test_chrome_cwd.py
git commit -m "feat(chrome): add _format_cwd helper and WorkspaceCwdChanged event"
```

---

## Task 2: StatusBar renders the active cwd at boot

**Files:**
- Modify: `patchfeld/widgets/chrome.py` (StatusBar gains `sb-cwd`, on_mount reads `app.cwd`)
- Modify: `tests/test_chrome_cwd.py` (add boot-render test)

- [ ] **Step 2.1: Write the failing test**

Append to `tests/test_chrome_cwd.py`:

```python
import pytest

from patchfeld.app import PatchfeldApp
from patchfeld.widgets.chrome import StatusBar
from textual.widgets import Static


@pytest.mark.asyncio
async def test_status_bar_shows_cwd_at_boot(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    project = tmp_path / "proj"
    project.mkdir()
    app = PatchfeldApp(cwd=project, global_dir=tmp_path / "cfg")
    async with app.run_test() as pilot:
        await pilot.pause()
        bar = app.query_one(StatusBar)
        text = bar.query_one("#sb-cwd", Static).renderable
        assert "~/proj" in str(text)
```

- [ ] **Step 2.2: Run, confirm failure**

```bash
uv run pytest tests/test_chrome_cwd.py::test_status_bar_shows_cwd_at_boot -v
```

Expected: `NoMatches` for `#sb-cwd`.

- [ ] **Step 2.3: Add `sb-cwd` Static to StatusBar.compose**

In `patchfeld/widgets/chrome.py`, modify `StatusBar.compose` to insert the cwd between `sb-layout` and `sb-error`:

```python
    def compose(self) -> ComposeResult:
        yield Static("tokens 0/0", id="sb-tokens")
        yield Static("$0.00", id="sb-cost")
        yield Static("0 agents", id="sb-agents")
        yield Static(f"layout: {self._layout_name}", id="sb-layout")
        yield Static("", id="sb-cwd")
        yield Static("", id="sb-error")
```

In `StatusBar.on_mount`, after the existing `bus is None` guard (but BEFORE the early return), seed the cwd label from `self.app.cwd`:

```python
    def on_mount(self) -> None:
        from patchfeld.events import LayoutApplied, StatsUpdated, WorkspaceCwdChanged
        bus = self._bus or getattr(self.app, "event_bus", None)
        # Initial cwd render — read app.cwd directly so we display correctly
        # even if the WorkspaceCwdChanged event was published before this
        # widget mounted.
        try:
            cwd = getattr(self.app, "cwd", None)
            if cwd is not None:
                self._render_cwd(Path(cwd))
        except Exception:
            pass
        if bus is None:
            return
        self._unsub = bus.subscribe(StatsUpdated, self._on_stats)
        self._unsub_layout = bus.subscribe(LayoutApplied, self._on_layout_applied)
        self._unsub_cwd = bus.subscribe(WorkspaceCwdChanged, self._on_cwd_changed)
```

Add the import for `Path` at the top of `chrome.py` if missing.

Add to `StatusBar.__init__`, after `self._unsub_layout = lambda: None`:

```python
        self._unsub_cwd = lambda: None
        self._cwd_path: Path | None = None
```

Add to `StatusBar.on_unmount`:

```python
        self._unsub_cwd()
```

Add a new `_render_cwd` method to StatusBar:

```python
    def _render_cwd(self, path: Path) -> None:
        self._cwd_path = path
        widget = self.query_one("#sb-cwd", Static)
        # Allocate roughly half the bar width to cwd, capped at 40 chars.
        try:
            container_width = max(self.size.width, 0)
        except Exception:
            container_width = 0
        budget = max(0, min(40, container_width // 2 if container_width else 40))
        widget.update(f"cwd: {_format_cwd(path, available_width=budget)}")
```

Add a placeholder `_on_cwd_changed` (real wiring in Task 3):

```python
    def _on_cwd_changed(self, event) -> None:
        self._render_cwd(Path(event.cwd))
```

- [ ] **Step 2.4: Run tests, confirm pass**

```bash
uv run pytest tests/test_chrome_cwd.py -v
```

Expected: 5 passed.

- [ ] **Step 2.5: Commit**

```bash
git add patchfeld/widgets/chrome.py tests/test_chrome_cwd.py
git commit -m "feat(chrome): show active cwd in status bar at boot"
```

---

## Task 3: StatusBar updates on `WorkspaceCwdChanged` and on resize

**Files:**
- Modify: `patchfeld/widgets/chrome.py` (`on_resize` re-renders)
- Modify: `tests/test_chrome_cwd.py` (event-driven update test)

- [ ] **Step 3.1: Write the failing tests**

Append to `tests/test_chrome_cwd.py`:

```python
@pytest.mark.asyncio
async def test_status_bar_updates_on_cwd_changed_event(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    proj_a = tmp_path / "a"
    proj_b = tmp_path / "b"
    proj_a.mkdir()
    proj_b.mkdir()
    from patchfeld.events import WorkspaceCwdChanged

    app = PatchfeldApp(cwd=proj_a, global_dir=tmp_path / "cfg")
    async with app.run_test() as pilot:
        await pilot.pause()
        bar = app.query_one(StatusBar)
        app.event_bus.publish(WorkspaceCwdChanged(cwd=str(proj_b)))
        await pilot.pause()
        text = bar.query_one("#sb-cwd", Static).renderable
        assert "~/b" in str(text)
```

- [ ] **Step 3.2: Run, confirm pass**

If Task 2 was implemented correctly (with `_on_cwd_changed` already wired), this test passes immediately. If it doesn't, fix the wiring before continuing.

```bash
uv run pytest tests/test_chrome_cwd.py::test_status_bar_updates_on_cwd_changed_event -v
```

Expected: pass.

- [ ] **Step 3.3: Add `on_resize` re-render**

Append to `StatusBar` in `chrome.py`:

```python
    def on_resize(self, _event) -> None:
        # Re-render so the cwd budget tracks the actual container width.
        if self._cwd_path is not None:
            self._render_cwd(self._cwd_path)
```

- [ ] **Step 3.4: Add a resize regression test**

Append to `tests/test_chrome_cwd.py`:

```python
@pytest.mark.asyncio
async def test_status_bar_truncates_cwd_on_narrow_terminal(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    deep = tmp_path / "one" / "two" / "three" / "four" / "five" / "six" / "leaf"
    deep.mkdir(parents=True)
    app = PatchfeldApp(cwd=deep, global_dir=tmp_path / "cfg")
    async with app.run_test(size=(40, 10)) as pilot:
        await pilot.pause()
        bar = app.query_one(StatusBar)
        text = str(bar.query_one("#sb-cwd", Static).renderable)
        assert "leaf" in text
        # On a 40-col terminal the budget is ~20 → must use ellipsis.
        assert "…/" in text or "~/" in text  # one of: truncated or shortenable
```

- [ ] **Step 3.5: Run, confirm pass**

```bash
uv run pytest tests/test_chrome_cwd.py -v
```

Expected: 7 passed.

- [ ] **Step 3.6: Commit**

```bash
git add patchfeld/widgets/chrome.py tests/test_chrome_cwd.py
git commit -m "feat(chrome): subscribe StatusBar to WorkspaceCwdChanged + redraw on resize"
```

---

## Task 4: `ChangeCwdScreen` modal

**Files:**
- Create: `patchfeld/widgets/change_cwd_screen.py`
- Create: `tests/test_widget_change_cwd_screen.py`

- [ ] **Step 4.1: Write the failing tests**

Create `tests/test_widget_change_cwd_screen.py`:

```python
import pytest
from textual.app import App, ComposeResult
from textual.widgets import Input

from patchfeld.widgets.change_cwd_screen import ChangeCwdScreen


class _Host(App):
    def __init__(self, initial: str) -> None:
        super().__init__()
        self._initial = initial
        self.result: object = "sentinel"

    def compose(self) -> ComposeResult:
        yield Input()  # focus stub

    async def on_mount(self) -> None:
        def _set(value):
            self.result = value
        await self.push_screen(ChangeCwdScreen(initial=self._initial), _set)


@pytest.mark.asyncio
async def test_change_cwd_screen_prefills_initial(tmp_path):
    app = _Host(initial=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        inp = app.screen.query_one("#change-cwd-input", Input)
        assert inp.value == str(tmp_path)


@pytest.mark.asyncio
async def test_change_cwd_screen_submit_returns_trimmed(tmp_path):
    app = _Host(initial="")
    async with app.run_test() as pilot:
        await pilot.pause()
        inp = app.screen.query_one("#change-cwd-input", Input)
        inp.value = "  " + str(tmp_path) + "  "
        await pilot.press("enter")
        await pilot.pause()
        assert app.result == str(tmp_path)


@pytest.mark.asyncio
async def test_change_cwd_screen_escape_returns_none(tmp_path):
    app = _Host(initial=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert app.result is None
```

- [ ] **Step 4.2: Run, confirm failure**

```bash
uv run pytest tests/test_widget_change_cwd_screen.py -v
```

Expected: ModuleNotFoundError.

- [ ] **Step 4.3: Implement the modal**

Create `patchfeld/widgets/change_cwd_screen.py`:

```python
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Static


class ChangeCwdScreen(ModalScreen[str | None]):
    """Tiny modal that asks for a new workspace cwd. Dismisses with the
    trimmed string on submit, or None on escape."""

    DEFAULT_CSS = """
    ChangeCwdScreen { align: center middle; }
    ChangeCwdScreen > Vertical {
        width: 70; height: auto; padding: 1 2;
        background: $surface; border: round $primary;
    }
    """

    BINDINGS = [("escape", "cancel", "cancel")]

    def __init__(self, *, initial: str = "") -> None:
        super().__init__()
        self._initial = initial

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Change workspace cwd:")
            yield Input(
                value=self._initial,
                placeholder="e.g., ~/Developer/other-project",
                id="change-cwd-input",
            )

    def on_input_submitted(self, event: Input.Submitted) -> None:
        path = (event.value or "").strip()
        self.dismiss(path or None)

    def action_cancel(self) -> None:
        self.dismiss(None)
```

- [ ] **Step 4.4: Run, confirm pass**

```bash
uv run pytest tests/test_widget_change_cwd_screen.py -v
```

Expected: 3 passed.

- [ ] **Step 4.5: Commit**

```bash
git add patchfeld/widgets/change_cwd_screen.py tests/test_widget_change_cwd_screen.py
git commit -m "feat(widgets): add ChangeCwdScreen modal"
```

---

## Task 5: `App.change_cwd` happy path

**Files:**
- Modify: `patchfeld/app.py` (add lock, method, reset state)
- Create: `tests/test_app_change_cwd.py`

- [ ] **Step 5.1: Write the failing test**

Create `tests/test_app_change_cwd.py`:

```python
import json
from pathlib import Path

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

from patchfeld.agents.fake_sdk_adapter import FakeSDKAdapter
from patchfeld.agents.manager import AgentManager
from patchfeld.app import PatchfeldApp
from patchfeld.events import EventBus, WorkspaceCwdChanged
from patchfeld.orchestrator.session import OrchestratorSession


def _ok():
    return [
        AssistantMessage(content=[TextBlock(text="ok")], model="fake-model"),
        ResultMessage(
            subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
            num_turns=1, session_id="fake", total_cost_usd=0.0,
            usage={"input_tokens": 1, "output_tokens": 1}, result="ok",
        ),
    ]


def _build_app(cwd):
    bus = EventBus()
    manager = AgentManager(
        cwd=cwd, bus=bus,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_ok()]),
    )
    app = PatchfeldApp(cwd=cwd, manager=manager, global_dir=cwd / ".global")
    app.event_bus = bus
    app.orchestrator = OrchestratorSession(
        cwd=cwd, bus=bus, manager=manager,
        adapter=FakeSDKAdapter(scripts=[_ok()]),
        apply_layout=app._orchestrator_apply_layout,
        layouts_store=app.layouts_store,
        themes_store=app.themes_store,
        config_store=app.config_store,
        actions=app.actions_registry,
        rebind_keys=app._rebind_keys,
        widget_registry=app.registry,
        current_layout=lambda: app._active_layout(),
        app=app,
    )
    return app, bus


@pytest.mark.asyncio
async def test_change_cwd_swaps_cwd_and_publishes_event(tmp_path):
    proj_a = tmp_path / "a"
    proj_b = tmp_path / "b"
    proj_a.mkdir()
    proj_b.mkdir()
    app, bus = _build_app(proj_a)
    received: list[str] = []
    bus.subscribe(WorkspaceCwdChanged, lambda e: received.append(e.cwd))
    async with app.run_test() as pilot:
        await pilot.pause()
        # Re-supply a fresh adapter for the orchestrator restart.
        app.orchestrator._next_adapter_factory = (
            lambda: FakeSDKAdapter(scripts=[_ok()])
        )
        result = await app.change_cwd(proj_b)
        await pilot.pause()
        assert result == {"changed": str(proj_b.resolve())}
        assert app.cwd == proj_b.resolve()
        assert (proj_b / ".patchfeld" / "workspace.json").exists()
        assert received and received[-1] == str(proj_b.resolve())


@pytest.mark.asyncio
async def test_change_cwd_noop_for_same_path(tmp_path):
    proj = tmp_path / "p"
    proj.mkdir()
    app, _ = _build_app(proj)
    async with app.run_test() as pilot:
        await pilot.pause()
        result = await app.change_cwd(proj)
        assert result == {"unchanged": True}


@pytest.mark.asyncio
async def test_change_cwd_rejects_invalid_path(tmp_path):
    proj = tmp_path / "p"
    proj.mkdir()
    app, _ = _build_app(proj)
    async with app.run_test() as pilot:
        await pilot.pause()
        result = await app.change_cwd(tmp_path / "does-not-exist")
        assert "error" in result
        assert app.cwd == proj.resolve() or app.cwd == proj
```

- [ ] **Step 5.2: Run, confirm failure**

```bash
uv run pytest tests/test_app_change_cwd.py -v
```

Expected: AttributeError on `app.change_cwd`.

- [ ] **Step 5.3: Add `_cwd_swap_lock` to `App.__init__`**

In `patchfeld/app.py`, in `PatchfeldApp.__init__` after `self.cwd = Path(cwd) if cwd else Path.cwd()`:

```python
        import asyncio as _asyncio
        self._cwd_swap_lock = _asyncio.Lock()
```

(Use the existing `import asyncio as _asyncio` pattern visible in `action_open_layout_switcher`.)

- [ ] **Step 5.4: Implement `App.change_cwd`**

Append to `PatchfeldApp` (placed between `_apply_to_tab` and `_on_stats_changed` for grouping with workspace lifecycle):

```python
    async def change_cwd(self, new_cwd: "str | Path") -> dict:
        """Re-root the running workspace at `new_cwd`. Stops the
        orchestrator and manager, swaps `self.cwd`, rebuilds both, loads
        (or seeds) the new cwd's workspace, re-applies the active theme,
        and publishes WorkspaceCwdChanged.

        Returns a result dict; never raises on user input.
        """
        from patchfeld.events import WorkspaceCwdChanged
        from patchfeld.agents.sdk_adapter import RealSDKAdapter

        async with self._cwd_swap_lock:
            # Validate.
            try:
                resolved = Path(new_cwd).expanduser().resolve()
            except Exception as e:
                return {"error": "invalid_path", "detail": str(e)}
            if not resolved.exists() or not resolved.is_dir():
                return {"error": "invalid_path", "path": str(resolved)}
            try:
                current = Path(self.cwd).resolve()
            except Exception:
                current = self.cwd
            if resolved == current:
                return {"unchanged": True}

            # Refuse with running children.
            running = [
                {"id": info.id, "name": info.name}
                for info in self.manager.list_infos()
                if not info.state.is_terminal
            ]
            if running:
                return {"error": "agents_running", "agents": running}

            # Save the OLD workspace one last time.
            if self._workspace is not None:
                try:
                    save_local_workspace(self.cwd, self._workspace)
                except Exception:
                    pass

            # Tear down current orchestrator + manager.
            try:
                await self.orchestrator.stop()
            except Exception:
                pass
            try:
                await self.manager.shutdown()
            except Exception:
                pass

            # Swap cwd and reset workspace state.
            self.cwd = resolved
            self._workspace = None
            self._active_tab_id = None
            self._current_layout_name = None
            self._tab_focus_snapshots.clear()

            # Rebuild manager + orchestrator.
            self.manager = AgentManager(
                cwd=self.cwd, bus=self.event_bus,
                adapter_factory=RealSDKAdapter,
            )
            self.orchestrator = OrchestratorSession(
                cwd=self.cwd, bus=self.event_bus, manager=self.manager,
                apply_layout=self._orchestrator_apply_layout,
                layouts_store=self.layouts_store,
                themes_store=self.themes_store,
                config_store=self.config_store,
                actions=self.actions_registry,
                rebind_keys=self._rebind_keys,
                widget_registry=self.registry,
                current_layout=lambda: self._active_layout(),
                app=self,
            )
            self.orchestrator._auto_title_enabled = True
            await self.orchestrator.start()

            # Load (or seed) the new workspace.
            ws = self._load_or_seed_workspace()
            self._workspace = ws
            self._active_tab_id = ws.active
            await self._mount_workspace(ws)
            save_local_workspace(self.cwd, ws)

            # Re-apply theme.
            active_name = (
                ws.active_theme
                or self.config_store.load().ui.active_theme
                or "default"
            )
            try:
                await self._apply_theme_by_name(active_name, persist=False)
            except Exception:
                try:
                    await self._apply_theme_by_name("default", persist=False)
                except Exception:
                    pass

            self.event_bus.publish(WorkspaceCwdChanged(cwd=str(self.cwd)))
            return {"changed": str(self.cwd)}
```

- [ ] **Step 5.5: Run, confirm pass**

```bash
uv run pytest tests/test_app_change_cwd.py -v
```

Expected: 3 passed.

- [ ] **Step 5.6: Commit**

```bash
git add patchfeld/app.py tests/test_app_change_cwd.py
git commit -m "feat(app): App.change_cwd re-roots workspace at runtime"
```

---

## Task 6: `change_cwd` refuses while children are running

**Files:**
- Modify: `tests/test_app_change_cwd.py` (add refusal test)

- [ ] **Step 6.1: Write the failing test**

Append to `tests/test_app_change_cwd.py`:

```python
@pytest.mark.asyncio
async def test_change_cwd_refuses_with_running_children(tmp_path):
    proj_a = tmp_path / "a"
    proj_b = tmp_path / "b"
    proj_a.mkdir()
    proj_b.mkdir()
    app, _ = _build_app(proj_a)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Spawn a child agent that the FakeSDKAdapter will keep "running"
        # because we never feed it a ResultMessage in this script.
        manager = app.manager
        manager._adapter_factory = lambda: FakeSDKAdapter(
            scripts=[[
                AssistantMessage(content=[TextBlock(text="hi")], model="fake-model"),
                # No ResultMessage on purpose — keeps state non-terminal.
            ]],
        )
        await manager.spawn(name="worker", prompt="do thing")
        await pilot.pause()
        result = await app.change_cwd(proj_b)
        assert result.get("error") == "agents_running"
        assert app.cwd == proj_a.resolve() or app.cwd == proj_a
```

- [ ] **Step 6.2: Run, confirm pass**

This should already pass with the implementation from Task 5; the test is the safety guarantee.

```bash
uv run pytest tests/test_app_change_cwd.py::test_change_cwd_refuses_with_running_children -v
```

Expected: pass.

If it does not pass (e.g., the FakeSDKAdapter terminates regardless), inspect `patchfeld/agents/state.py::AgentState.is_terminal` and adapt the script. The intent is to assert that `not is_terminal` agents block the swap.

- [ ] **Step 6.3: Commit**

```bash
git add tests/test_app_change_cwd.py
git commit -m "test(app): change_cwd refuses while children are non-terminal"
```

---

## Task 7: Action registration, `ctrl+shift+d` binding, modal wiring

**Files:**
- Modify: `patchfeld/app.py` (BINDINGS, _register_actions, action handlers)
- Modify: `tests/test_app_change_cwd.py` (modal smoke test)

- [ ] **Step 7.1: Add the binding and action handlers**

In `patchfeld/app.py`, append to the `BINDINGS` list (after the `ctrl+shift+r` line, before the tab bindings):

```python
        Binding("ctrl+shift+d", "open_change_cwd", "change cwd"),
```

In `_register_actions`, append after the `reset_panel_sizes` registration:

```python
        self.actions_registry.register(
            "change_cwd",
            lambda path: self._dispatch_change_cwd(path),
            description="Change the workspace's cwd at runtime.",
            args_schema={"path": str},
        )
        self.actions_registry.register(
            "open_change_cwd", self.action_open_change_cwd,
            description="Open the change-cwd modal.", args_schema={},
        )
```

Append two new handler methods to `PatchfeldApp`:

```python
    def _dispatch_change_cwd(self, path: str) -> None:
        """Action wrapper around change_cwd — schedules the async call."""
        import asyncio as _asyncio
        _asyncio.create_task(self._change_cwd_with_notify(path))

    async def _change_cwd_with_notify(self, path: str) -> None:
        result = await self.change_cwd(path)
        if "error" in result:
            err = result["error"]
            if err == "agents_running":
                names = ", ".join(a["name"] for a in result.get("agents", []))
                self.notify(
                    f"Refusing to change cwd: agents still running ({names}).",
                    severity="warning",
                )
            elif err == "invalid_path":
                self.notify(
                    f"Invalid path: {result.get('path') or result.get('detail')}",
                    severity="warning",
                )
            else:
                self.notify(f"change_cwd failed: {err}", severity="warning")
        elif "unchanged" in result:
            self.notify("cwd unchanged.")
        else:
            self.notify(f"cwd → {result['changed']}")

    def action_open_change_cwd(self) -> None:
        if self._workspace is None:
            return

        def _on_picked(path: str | None) -> None:
            if not path:
                return
            self._dispatch_change_cwd(path)

        from patchfeld.widgets.change_cwd_screen import ChangeCwdScreen
        self.push_screen(
            ChangeCwdScreen(initial=str(self.cwd)),
            _on_picked,
        )
```

- [ ] **Step 7.2: Write the modal smoke test**

Append to `tests/test_app_change_cwd.py`:

```python
@pytest.mark.asyncio
async def test_ctrl_shift_d_opens_change_cwd_modal(tmp_path):
    proj = tmp_path / "p"
    proj.mkdir()
    app, _ = _build_app(proj)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+shift+d")
        await pilot.pause()
        from patchfeld.widgets.change_cwd_screen import ChangeCwdScreen
        assert isinstance(app.screen, ChangeCwdScreen)
```

- [ ] **Step 7.3: Run, confirm pass**

```bash
uv run pytest tests/test_app_change_cwd.py -v
```

Expected: 4 passed.

- [ ] **Step 7.4: Commit**

```bash
git add patchfeld/app.py tests/test_app_change_cwd.py
git commit -m "feat(app): bind ctrl+shift+d to ChangeCwdScreen + register change_cwd action"
```

---

## Task 8: `/cd <path>` slash command in OrchestratorSession

**Files:**
- Modify: `patchfeld/orchestrator/session.py` (add regex, branch in `_on_user_message`)
- Modify: `tests/test_app_change_cwd.py` (slash-command test)

- [ ] **Step 8.1: Write the failing test**

Append to `tests/test_app_change_cwd.py`:

```python
@pytest.mark.asyncio
async def test_slash_cd_changes_cwd(tmp_path):
    proj_a = tmp_path / "a"
    proj_b = tmp_path / "b"
    proj_a.mkdir()
    proj_b.mkdir()
    app, bus = _build_app(proj_a)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.orchestrator._next_adapter_factory = (
            lambda: FakeSDKAdapter(scripts=[_ok()])
        )
        from patchfeld.events import UserMessageToOrchestrator
        bus.publish(UserMessageToOrchestrator(f"/cd {proj_b}"))
        await pilot.pause()
        await pilot.pause()  # second pause: change_cwd creates async tasks
        assert app.cwd == proj_b.resolve()
```

- [ ] **Step 8.2: Run, confirm failure**

```bash
uv run pytest tests/test_app_change_cwd.py::test_slash_cd_changes_cwd -v
```

Expected: assertion failure (cwd unchanged because the SDK ate the `/cd` line as a normal prompt).

- [ ] **Step 8.3: Add the `/cd` regex and branch**

In `patchfeld/orchestrator/session.py`, after the `_RENAME_RE` regex (line ~47):

```python
_CD_RE = re.compile(r"^/cd\s+(.+?)\s*$")
```

Update `_HELP_TEXT` to mention `/cd`:

```python
_HELP_TEXT = (
    "Available commands:\n"
    "  /reset                     Start a fresh orchestrator session\n"
    "  /resume [<session_id>]     Resume a past session (no arg → picker)\n"
    "  /rename [<id>] <title>     Rename the active or a specific session\n"
    "  /cd <path>                 Re-root the workspace at <path>\n"
    "  /help                      Show this list"
)
```

In `_on_user_message`, after the `_RENAME_RE` block, add:

```python
        m = _CD_RE.match(text)
        if m and self._app is not None:
            path = m.group(1).strip()
            self._send_tasks = [t for t in self._send_tasks if not t.done()]
            self._send_tasks.append(
                asyncio.create_task(self._handle_cd_command(path))
            )
            return
```

Append a new method to `OrchestratorSession`:

```python
    async def _handle_cd_command(self, path: str) -> None:
        if self._app is None:
            return
        result = await self._app.change_cwd(path)
        if "error" in result:
            err = result["error"]
            if err == "agents_running":
                names = ", ".join(a["name"] for a in result.get("agents", []))
                self._publish_notice(
                    f"Refusing /cd: agents still running ({names})."
                )
            elif err == "invalid_path":
                self._publish_notice(
                    f"Invalid path: {result.get('path') or result.get('detail')}"
                )
            else:
                self._publish_notice(f"/cd failed: {err}")
        elif "unchanged" in result:
            self._publish_notice("cwd unchanged.")
        else:
            self._publish_notice(f"cwd → {result['changed']}")
```

- [ ] **Step 8.4: Run, confirm pass**

```bash
uv run pytest tests/test_app_change_cwd.py -v
```

Expected: 5 passed.

- [ ] **Step 8.5: Commit**

```bash
git add patchfeld/orchestrator/session.py tests/test_app_change_cwd.py
git commit -m "feat(orchestrator): /cd <path> slash command re-roots the workspace"
```

---

## Task 9: `change_cwd` MCP tool

**Files:**
- Modify: `patchfeld/orchestrator/tools.py` (handler factory, register in `build_orchestrator_tools` and `build_orchestrator_mcp_server`)
- Create: `tests/test_orchestrator_change_cwd_tool.py`

- [ ] **Step 9.1: Write the failing test**

Create `tests/test_orchestrator_change_cwd_tool.py`:

```python
from pathlib import Path

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

from patchfeld.agents.fake_sdk_adapter import FakeSDKAdapter
from patchfeld.agents.manager import AgentManager
from patchfeld.app import PatchfeldApp
from patchfeld.events import EventBus
from patchfeld.orchestrator.session import OrchestratorSession
from patchfeld.orchestrator.tools import build_orchestrator_tools


def _ok():
    return [
        AssistantMessage(content=[TextBlock(text="ok")], model="fake-model"),
        ResultMessage(
            subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
            num_turns=1, session_id="fake", total_cost_usd=0.0,
            usage={"input_tokens": 1, "output_tokens": 1}, result="ok",
        ),
    ]


@pytest.mark.asyncio
async def test_change_cwd_mcp_tool_routes_to_app(tmp_path):
    proj_a = tmp_path / "a"
    proj_b = tmp_path / "b"
    proj_a.mkdir()
    proj_b.mkdir()
    bus = EventBus()
    manager = AgentManager(
        cwd=proj_a, bus=bus,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_ok()]),
    )
    app = PatchfeldApp(cwd=proj_a, manager=manager, global_dir=proj_a / ".g")
    app.event_bus = bus
    app.orchestrator = OrchestratorSession(
        cwd=proj_a, bus=bus, manager=manager,
        adapter=FakeSDKAdapter(scripts=[_ok()]),
        apply_layout=app._orchestrator_apply_layout,
        layouts_store=app.layouts_store,
        themes_store=app.themes_store,
        config_store=app.config_store,
        actions=app.actions_registry,
        rebind_keys=app._rebind_keys,
        widget_registry=app.registry,
        current_layout=lambda: app._active_layout(),
        app=app,
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        app.orchestrator._next_adapter_factory = (
            lambda: FakeSDKAdapter(scripts=[_ok()])
        )
        handlers = build_orchestrator_tools(
            manager, apply_layout=app._orchestrator_apply_layout,
            layouts_store=app.layouts_store, themes_store=app.themes_store,
            config_store=app.config_store, actions=app.actions_registry,
            rebind_keys=app._rebind_keys, widget_registry=app.registry,
            current_layout=lambda: app._active_layout(), app=app,
        )
        result = await handlers["change_cwd"]({"path": str(proj_b)})
        await pilot.pause()
        assert "Re-rooted" in result["content"][0]["text"]
        assert app.cwd == proj_b.resolve()
```

- [ ] **Step 9.2: Run, confirm failure**

```bash
uv run pytest tests/test_orchestrator_change_cwd_tool.py -v
```

Expected: KeyError on `"change_cwd"` handler.

- [ ] **Step 9.3: Add the handler factory to `tools.py`**

In `patchfeld/orchestrator/tools.py`, add a new handler factory above `build_orchestrator_tools`:

```python
def _change_cwd_handler(app):
    async def change_cwd_tool(args: dict) -> dict:
        path = args.get("path")
        if not path:
            return {"content": [{"type": "text", "text": "path is required"}]}
        result = await app.change_cwd(path)
        if "error" in result:
            return {"content": [{"type": "text",
                                 "text": f"change_cwd error: {result}"}]}
        if result.get("unchanged"):
            return {"content": [{"type": "text", "text": "cwd unchanged."}]}
        return {"content": [{"type": "text",
                             "text": f"Re-rooted at {result['changed']}."}]}
    return change_cwd_tool
```

In `build_orchestrator_tools`, in the `if app is not None:` block (after `reorder_tabs`), append:

```python
        handlers["change_cwd"] = _change_cwd_handler(app)
```

In `build_orchestrator_mcp_server`, in the same `if app is not None:` block, append:

```python
        sdk_tools.append(tool(
            "change_cwd",
            "Re-root the workspace at a new working directory. `path` is "
            "expanded for `~` and resolved to absolute. Refuses if any "
            "child agents are still running (kill or wait first). On "
            "success, the previous workspace.json is saved at the OLD cwd, "
            "the orchestrator session is reset, and the new cwd's "
            "workspace.json is loaded (or seeded from the dashboard).",
            {"path": str},
        )(_change_cwd_handler(app)))
```

- [ ] **Step 9.4: Run, confirm pass**

```bash
uv run pytest tests/test_orchestrator_change_cwd_tool.py -v
```

Expected: 1 passed.

- [ ] **Step 9.5: Commit**

```bash
git add patchfeld/orchestrator/tools.py tests/test_orchestrator_change_cwd_tool.py
git commit -m "feat(orchestrator): change_cwd MCP tool routes to App.change_cwd"
```

---

## Task 10: Integration smoke + footer-on-real-cwd-change

**Files:**
- Modify: `tests/test_app_change_cwd.py` (combined assertion that all four surfaces converge on the same App.change_cwd)

- [ ] **Step 10.1: Write the integration test**

Append to `tests/test_app_change_cwd.py`:

```python
@pytest.mark.asyncio
async def test_change_cwd_updates_footer(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    proj_a = tmp_path / "a"
    proj_b = tmp_path / "b" / "deeper"
    proj_a.mkdir()
    proj_b.mkdir(parents=True)
    app, _ = _build_app(proj_a)
    async with app.run_test() as pilot:
        await pilot.pause()
        from patchfeld.widgets.chrome import StatusBar
        from textual.widgets import Static
        bar = app.query_one(StatusBar)
        assert "~/a" in str(bar.query_one("#sb-cwd", Static).renderable)
        app.orchestrator._next_adapter_factory = (
            lambda: FakeSDKAdapter(scripts=[_ok()])
        )
        await app.change_cwd(proj_b)
        await pilot.pause()
        text = str(bar.query_one("#sb-cwd", Static).renderable)
        assert "~/b/deeper" in text
```

- [ ] **Step 10.2: Run, confirm pass**

```bash
uv run pytest tests/test_app_change_cwd.py tests/test_chrome_cwd.py tests/test_widget_change_cwd_screen.py tests/test_orchestrator_change_cwd_tool.py -v
```

Expected: all green.

- [ ] **Step 10.3: Run the full suite to catch regressions**

```bash
uv run pytest -q
```

Expected: same pass/fail count as before the plan (no regressions). Investigate any new failures before continuing.

- [ ] **Step 10.4: Commit**

```bash
git add tests/test_app_change_cwd.py
git commit -m "test(app): integration smoke for runtime cwd change + footer"
```

---

## Task 11: Update help text and `/help` notification

**Files:**
- Modify: `patchfeld/app.py` (action_show_help) — already touched indirectly by `_HELP_TEXT` in Task 8, but the in-app `?` notification is separate.

- [ ] **Step 11.1: Update `action_show_help`**

In `patchfeld/app.py`, replace the `action_show_help` body so it advertises the new bindings:

```python
    def action_show_help(self) -> None:
        self.notify(
            "/ command bar · ctrl-q quit · ctrl-h history · ctrl-l layouts · "
            "ctrl-shift-l themes · ctrl-shift-r reset panel sizes · "
            "ctrl-shift-d change cwd · "
            "ctrl-pgup/pgdn prev/next tab · ctrl-1..9 tab N · ctrl-t new tab · "
            "ctrl-w close tab · /reset new · /resume past · /rename title · "
            "/cd path · /help cmds · ? help",
            title="keybindings",
        )
```

- [ ] **Step 11.2: Run app smoke to confirm nothing broke**

```bash
uv run pytest tests/test_app_smoke.py -v
```

Expected: pass.

- [ ] **Step 11.3: Commit**

```bash
git add patchfeld/app.py
git commit -m "docs(app): mention ctrl-shift-d and /cd in the ? help notification"
```

---

## Self-Review Checklist (run before declaring done)

- [ ] Footer shows cwd at boot, updates after `change_cwd`, and truncates on a 40-col terminal.
- [ ] `App.change_cwd` returns: `{"changed": ...}`, `{"unchanged": True}`, `{"error": "invalid_path", ...}`, `{"error": "agents_running", "agents": [...]}`. Every test asserts one of these shapes.
- [ ] All four trigger surfaces (`ctrl+shift+d`, `change_cwd` action, `/cd <path>`, `change_cwd` MCP tool) call `App.change_cwd` — no surface duplicates the swap logic.
- [ ] `WorkspaceCwdChanged` event is published exactly once per successful swap and never on no-op or error.
- [ ] Old orchestrator session is `await stop()`ed before the new one starts; old manager is `await shutdown()`ed before the new one is constructed.
- [ ] `self.event_bus` is the SAME object before and after a swap (app-level subscriptions stay live).
- [ ] No raw `cwd` Path is shown to the user without going through `_format_cwd`.
- [ ] `?` help notification mentions both `ctrl-shift-d` and `/cd`. `/help` in chat mentions `/cd`.
- [ ] `uv run pytest -q` passes from a clean tree.

---

## Out of Scope (intentionally deferred)

- Forced cwd change (`force=true`) that kills running children. Add when a real workflow needs it.
- Carrying the current Workspace into the new cwd (`carry_layout=true`). Add when users complain.
- Continuing the SDK session across cwd boundaries (`keep_session=true`). Requires SDK-side validation work.
- Auto-rebasing relative `FileTree(path=...)` props after a swap.
- A "recent cwds" picker (à la `/resume`). The modal's pre-fill is enough for v1.
- File-system watcher that notifies the footer if the cwd is renamed or deleted while running.
