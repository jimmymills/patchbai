"""Regression tests for tab-switching after a Multica-style tab is mounted.

User-reported bug:

    "Whenever the Multica tab is made, after I switch to it, I can't click back
     into the Agents tab. The other tabs work fine, but something about that
     first tab vs created ones has a bug."

The Agents tab is the seeded "default" tab containing OrchestratorChat. Multica
is a custom local widget loaded from ~/.config/patchbai/widgets/MulticaIssues.py
that exposes a DataTable populated by an async subprocess.

Hypothesis: MulticaIssues has an `on_data_table_row_selected` handler that
opens the issue in a browser but does NOT call event.stop(). The DataTable
RowSelected message therefore bubbles up to the App's global
`on_data_table_row_selected` handler at patchbai/app.py:1264, which pushes a
TranscriptScreen modal onto the screen stack. With a modal screen on top, all
subsequent tab-strip clicks are intercepted by the modal — so the Agents tab
strip click is silently swallowed instead of activating the seeded tab.

These tests reproduce that path with a faithful MulticaIssues stub, then
confirm the fix (calling `event.stop()` in the user widget) keeps the modal
out of the picture.
"""
import textwrap

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock
from textual.widgets import DataTable, TabbedContent

from patchbai.agents.fake_sdk_adapter import FakeSDKAdapter
from patchbai.agents.manager import AgentManager
from patchbai.app import PatchbaiApp
from patchbai.events import EventBus
from patchbai.layout.spec import LayoutSpec


def _ok():
    return [
        AssistantMessage(content=[TextBlock(text="ok")], model="fake-model"),
        ResultMessage(
            subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
            num_turns=1, session_id="fake", total_cost_usd=0.0,
            usage={"input_tokens": 1, "output_tokens": 1}, result="ok",
        ),
    ]


# A MulticaIssues stub that mirrors the salient shape of the real widget at
# ~/.config/patchbai/widgets/MulticaIssues.py — a Container holding a DataTable
# with cursor_type="row", an async subprocess worker (we point it at /bin/echo
# returning empty JSON so the test doesn't depend on the multica CLI), and an
# on_data_table_row_selected handler that does NOT stop the event. The exact
# reproduction does not depend on the subprocess succeeding; it depends on the
# bubbling RowSelected message and on a non-trivial widget that auto-focuses.
_MULTICA_STUB_SRC = textwrap.dedent('''
    import asyncio
    import json

    from textual.app import ComposeResult
    from textual.binding import Binding
    from textual.containers import Container
    from textual.widgets import DataTable

    __patchbai_widget__ = {
        "name": "MulticaIssues",
        "description": "stub for tests",
    }


    class MulticaIssues(Container):
        BINDINGS = [Binding("r", "refresh", "Refresh")]

        DEFAULT_CSS = """
        MulticaIssues { height: 1fr; }
        """

        def __init__(self, **kw) -> None:
            super().__init__(**kw)
            self._issues = {}

        def compose(self) -> ComposeResult:
            yield DataTable(id="mi-table", zebra_stripes=True, cursor_type="row")

        def on_mount(self) -> None:
            table = self.query_one(DataTable)
            table.add_columns("ID", "Status", "Title")
            table.add_row("BUO-1", "open", "demo issue", key="BUO-1")
            self._issues["BUO-1"] = {"id": "stub-id", "identifier": "BUO-1"}
            self.run_worker(self._refresh(), exclusive=True)

        def action_refresh(self) -> None:
            self.run_worker(self._refresh(), exclusive=True)

        async def _refresh(self) -> None:
            # Mirror the real widget: spawn an async subprocess. Use /bin/echo so
            # the test is self-contained and doesn't need the multica CLI.
            try:
                proc = await asyncio.create_subprocess_exec(
                    "/bin/echo", "{}",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await proc.communicate()
            except Exception:
                return

        # NOTE: this reproduces the buggy shape — handler does NOT call
        # event.stop(), so the RowSelected message bubbles to the App's
        # global handler and pushes a TranscriptScreen modal.
        def on_data_table_row_selected(self, event) -> None:
            try:
                ident = (
                    str(event.row_key.value)
                    if event.row_key is not None and event.row_key.value is not None
                    else None
                )
            except Exception:
                ident = None
            if ident is None:
                return
            # Real widget calls webbrowser.open here; the test doesn't care.
            try:
                self.app.notify(f"Opening {ident}", title="multica")
            except Exception:
                pass
''')


def _write_widget(global_dir, source: str) -> None:
    wdir = global_dir / "widgets"
    wdir.mkdir(parents=True, exist_ok=True)
    (wdir / "MulticaIssues.py").write_text(source, encoding="utf-8")


def _build_app(tmp_path):
    bus = EventBus()
    manager = AgentManager(
        cwd=tmp_path, bus=bus,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_ok()]),
    )
    app = PatchbaiApp(cwd=tmp_path, manager=manager, global_dir=tmp_path)
    app.event_bus = bus
    from patchbai.orchestrator.session import OrchestratorSession
    app.orchestrator = OrchestratorSession(
        cwd=tmp_path, bus=bus, manager=manager,
        adapter=FakeSDKAdapter(scripts=[_ok()]),
        apply_layout=app._orchestrator_apply_layout,
        layouts_store=app.layouts_store,
        config_store=app.config_store,
        actions=app.actions_registry,
        rebind_keys=app._rebind_keys,
    )
    return app


def _multica_layout() -> LayoutSpec:
    return LayoutSpec.model_validate({
        "version": 1,
        "layout": {"id": "multica", "widget": "MulticaIssues"},
    })


@pytest.mark.asyncio
async def test_multica_loads_into_local_registry(tmp_path):
    """Sanity check: the stub widget is picked up by the local-widgets loader."""
    _write_widget(tmp_path, _MULTICA_STUB_SRC)
    app = _build_app(tmp_path)
    assert "MulticaIssues" in app.registry.known()


@pytest.mark.asyncio
async def test_can_switch_back_to_agents_after_multica_visit(tmp_path):
    """The bug: switch to the Multica tab, click a row in its DataTable, then
    switch back to the seeded "Agents" (default) tab. With the bug, clicking
    the Agents tab is intercepted by a TranscriptScreen modal that the App's
    global on_data_table_row_selected handler pushed when the RowSelected
    bubbled up from MulticaIssues.
    """
    _write_widget(tmp_path, _MULTICA_STUB_SRC)
    app = _build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app._active_tab_id == "default"

        # Add a Multica tab via the production code path.
        new_id = await app.add_tab("Multica", _multica_layout(), activate=True)
        await pilot.pause()
        assert app._active_tab_id == new_id

        # Simulate a user click on a row in the Multica DataTable. This is
        # exactly the path that fires DataTable.RowSelected on a real click.
        widget = app.query_one(f"#panel-multica")
        table = widget.query_one(DataTable)
        # cursor_type="row" + a single row → selecting fires RowSelected.
        table.action_select_cursor()
        await pilot.pause()

        # The bug surface: a TranscriptScreen modal has been pushed on top of
        # the screen stack, so subsequent tab-strip clicks would be swallowed.
        # Assert no modal was pushed — that's what the fix guarantees.
        from patchbai.widgets.transcript_screen import TranscriptScreen
        screen_stack = list(app.screen_stack)
        assert not any(isinstance(s, TranscriptScreen) for s in screen_stack), (
            "MulticaIssues' on_data_table_row_selected let RowSelected bubble "
            "to the App, which pushed a TranscriptScreen modal — that modal "
            "intercepts subsequent tab-strip clicks and breaks the 'click "
            "back into Agents' flow the user reported."
        )

        # And, sanity, switching back to the seeded tab still works.
        tc = app.query_one("#app-tabs", TabbedContent)
        tc.active = "tab-default"
        await pilot.pause()
        assert app._active_tab_id == "default"
        # The seeded panel must still be in the DOM and reachable.
        assert app.query_one("#panel-orch") is not None


@pytest.mark.asyncio
async def test_pure_tab_switch_to_multica_and_back_works(tmp_path):
    """A control: with no row interaction, switching to Multica and back to
    the seeded tab must work. Confirms the failure mode in the test above is
    specifically the bubbling RowSelected, not something else (focus chain,
    worker leak, etc.)."""
    _write_widget(tmp_path, _MULTICA_STUB_SRC)
    app = _build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        new_id = await app.add_tab("Multica", _multica_layout(), activate=True)
        await pilot.pause()
        assert app._active_tab_id == new_id

        tc = app.query_one("#app-tabs", TabbedContent)
        tc.active = "tab-default"
        await pilot.pause()
        assert app._active_tab_id == "default"
        assert app.query_one("#panel-orch") is not None


@pytest.mark.asyncio
async def test_app_global_row_handler_does_not_fire_for_user_widget_rows(tmp_path):
    """A targeted assertion on the same root cause: when a user-authored
    widget owns a DataTable whose row keys are NOT agent ids, the App's
    global row handler must not run for that selection. Otherwise it pushes
    a TranscriptScreen with a bogus agent_id, blocking all UI input.
    """
    _write_widget(tmp_path, _MULTICA_STUB_SRC)
    app = _build_app(tmp_path)

    # Spy on push_screen — that's how the App handler propagates the bug.
    pushed: list[object] = []
    real_push = app.push_screen

    def _spy_push_screen(screen, *args, **kw):
        pushed.append(screen)
        return real_push(screen, *args, **kw)

    app.push_screen = _spy_push_screen  # type: ignore[assignment]

    async with app.run_test() as pilot:
        await pilot.pause()
        await app.add_tab("Multica", _multica_layout(), activate=True)
        await pilot.pause()
        widget = app.query_one("#panel-multica")
        table = widget.query_one(DataTable)
        table.action_select_cursor()
        await pilot.pause()

    # Ignore any modals pushed during normal app boot (none expected here),
    # focus on whether a TranscriptScreen was pushed in response to the row.
    from patchbai.widgets.transcript_screen import TranscriptScreen
    assert not any(isinstance(s, TranscriptScreen) for s in pushed), (
        f"App.on_data_table_row_selected fired for a non-agent row. "
        f"pushed screens: {[type(s).__name__ for s in pushed]}"
    )
