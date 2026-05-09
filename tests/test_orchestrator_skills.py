"""Tests for skill discovery and slash-command dispatch in OrchestratorSession.

Locally-installed Claude Code skills (e.g. `~/.claude/skills/<name>/SKILL.md`
and plugin-shipped `~/.claude/plugins/cache/<plugin>/<ver>/skills/<name>/SKILL.md`)
are exposed as `/<name>` slash commands. The orchestrator translates the
slash-command line into a prose prompt that nudges the SDK's LLM to invoke
the matching `Skill` tool — see design decision (a) "translate" in the
implementation plan.
"""

from __future__ import annotations

import pytest
from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    TextBlock,
)

from patchfeld.agents.fake_sdk_adapter import FakeSDKAdapter
from patchfeld.agents.manager import AgentManager
from patchfeld.events import (
    EventBus,
    OrchestratorReply,
    UserMessageToOrchestrator,
)
from patchfeld.orchestrator.session import OrchestratorSession
from patchfeld.orchestrator.skills import SkillEntry, SkillsIndex, discover_skills


def _ok_script(session_id: str = "s-fake"):
    return [
        AssistantMessage(content=[TextBlock(text="ok")], model="fake-model"),
        ResultMessage(
            subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
            num_turns=1, session_id=session_id, total_cost_usd=0.0,
            usage={"input_tokens": 1, "output_tokens": 1}, result="ok",
        ),
    ]


class _RecordingAdapter(FakeSDKAdapter):
    """FakeSDKAdapter that records every prompt string it was asked to query.

    The base adapter exposes `_next_query_index` (count of queries seen), but
    the dispatch tests need to inspect the *prompt strings* themselves, so we
    record them here.
    """

    def __init__(self, scripts):
        super().__init__(scripts)
        self.queries: list[str] = []

    async def query(self, prompt: str) -> None:
        self.queries.append(prompt)
        await super().query(prompt)


def _build_orch(tmp_path, *, adapter, skills: SkillsIndex | None = None):
    bus = EventBus()
    manager = AgentManager(
        cwd=tmp_path, bus=bus,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[]),
    )
    orch = OrchestratorSession(
        cwd=tmp_path, bus=bus, manager=manager, adapter=adapter,
        skills=skills,
    )
    return orch, bus


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def test_discover_skills_walks_user_skills_dir(tmp_path):
    """A plain `~/.claude/skills/<name>/SKILL.md` is picked up."""
    user_skills = tmp_path / "user_skills"
    (user_skills / "kb-query").mkdir(parents=True)
    (user_skills / "kb-query" / "SKILL.md").write_text(
        "---\nname: kb-query\ndescription: Look stuff up.\n---\n# body\n",
        encoding="utf-8",
    )

    index = discover_skills(user_skills_dir=user_skills, plugin_cache_dir=None)
    entry = index.get("kb-query")
    assert entry is not None
    assert entry.name == "kb-query"
    assert entry.source == "user"


def test_discover_skills_walks_plugin_cache(tmp_path):
    """Plugin-shipped skills under
    `<cache>/<vendor>/<plugin>/<version>/skills/<name>/SKILL.md` are picked
    up under their bare name. The 4-level layout matches the real on-disk
    shape (e.g. `claude-plugins-official/superpowers/5.1.0/skills/...`).
    """
    plugin_cache = tmp_path / "plugin_cache"
    skill_dir = (
        plugin_cache
        / "vendor" / "superpowers" / "5.1.0" / "skills" / "writing-plans"
    )
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: writing-plans\ndescription: Plans.\n---\n", encoding="utf-8",
    )

    index = discover_skills(user_skills_dir=None, plugin_cache_dir=plugin_cache)
    entry = index.get("writing-plans")
    assert entry is not None
    assert entry.source == "plugin"


def test_discover_skills_picks_highest_plugin_version(tmp_path):
    """When a plugin has multiple version directories cached side by side
    (e.g. 5.0.7 alongside 5.1.0), discovery walks ONLY the highest sorted
    version. This avoids loading two copies of the same skill from a single
    plugin and matches the user's expectation that the latest release wins.
    """
    plugin_cache = tmp_path / "plugin_cache"
    base = plugin_cache / "vendor" / "superpowers"

    old = base / "5.0.7" / "skills" / "writing-plans"
    old.mkdir(parents=True)
    (old / "SKILL.md").write_text(
        "---\nname: writing-plans\ndescription: old version\n---\n",
        encoding="utf-8",
    )

    new = base / "5.1.0" / "skills" / "writing-plans"
    new.mkdir(parents=True)
    (new / "SKILL.md").write_text(
        "---\nname: writing-plans\ndescription: new version\n---\n",
        encoding="utf-8",
    )

    index = discover_skills(user_skills_dir=None, plugin_cache_dir=plugin_cache)
    entry = index.get("writing-plans")
    assert entry is not None
    # The path of the kept entry should point inside 5.1.0, not 5.0.7.
    assert "5.1.0" in entry.path and "5.0.7" not in entry.path


def test_discover_skills_collision_logs_warning(tmp_path, caplog):
    """If the same skill name is found in both the user dir and the plugin
    cache, the user-installed copy wins and a warning is logged."""
    user_skills = tmp_path / "user_skills"
    (user_skills / "garden").mkdir(parents=True)
    (user_skills / "garden" / "SKILL.md").write_text(
        "---\nname: garden\ndescription: user copy\n---\n", encoding="utf-8",
    )
    plugin_cache = tmp_path / "plugin_cache"
    pdir = (
        plugin_cache / "vendor" / "garden-pack" / "1.0.0" / "skills" / "garden"
    )
    pdir.mkdir(parents=True)
    (pdir / "SKILL.md").write_text(
        "---\nname: garden\ndescription: plugin copy\n---\n", encoding="utf-8",
    )

    import logging as _logging
    with caplog.at_level(_logging.WARNING):
        index = discover_skills(
            user_skills_dir=user_skills, plugin_cache_dir=plugin_cache,
        )
    entry = index.get("garden")
    assert entry is not None
    assert entry.source == "user"  # user wins
    assert any("garden" in rec.getMessage() and "collision" in rec.getMessage().lower()
               for rec in caplog.records), \
        f"expected collision warning, got: {[r.getMessage() for r in caplog.records]}"


def test_discover_skills_collision_with_builtin_logs_warning(tmp_path, caplog):
    """A skill named `cd` collides with the built-in /cd slash command. The
    skill must still be in the index (so callers can decide), but the
    discovery layer logs a warning so it's visible in production logs."""
    user_skills = tmp_path / "user_skills"
    (user_skills / "cd").mkdir(parents=True)
    (user_skills / "cd" / "SKILL.md").write_text(
        "---\nname: cd\ndescription: collides with built-in\n---\n",
        encoding="utf-8",
    )

    import logging as _logging
    with caplog.at_level(_logging.WARNING):
        index = discover_skills(
            user_skills_dir=user_skills, plugin_cache_dir=None,
            builtin_command_names={"cd", "help", "reset", "resume", "rename"},
        )
    assert index.get("cd") is not None
    assert any("cd" in rec.getMessage() and "built-in" in rec.getMessage().lower()
               for rec in caplog.records)


def test_skills_index_names_is_sorted():
    """`names()` returns names in stable lexicographic order so `/help` output
    is deterministic."""
    idx = SkillsIndex(entries={
        "zebra": SkillEntry(name="zebra", path="/x", source="user"),
        "apple": SkillEntry(name="apple", path="/y", source="user"),
        "mango": SkillEntry(name="mango", path="/z", source="plugin"),
    })
    assert idx.names() == ["apple", "mango", "zebra"]


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_skill_slash_command_translates_to_skill_invocation_prompt(tmp_path):
    """`/kb-query python type hints` must NOT be sent verbatim to the SDK.
    The orchestrator rewrites it into a prompt that explicitly tells the LLM
    to invoke the `kb-query` Skill tool with the trailing argument string —
    that's design decision (a) "translate"."""
    skills = SkillsIndex(entries={
        "kb-query": SkillEntry(name="kb-query", path="/skills/kb-query/SKILL.md",
                                source="user"),
    })
    adapter = _RecordingAdapter(scripts=[_ok_script()])
    orch, bus = _build_orch(tmp_path, adapter=adapter, skills=skills)
    await orch.start()
    try:
        bus.publish(UserMessageToOrchestrator("/kb-query python type hints"))
        await orch.wait_idle()

        assert len(adapter.queries) == 1, \
            f"expected exactly one query, got {adapter.queries!r}"
        prompt = adapter.queries[0]
        # Verbatim slash-line must NOT be the prompt.
        assert prompt != "/kb-query python type hints"
        # The rewritten prompt must reference the skill name and the args.
        assert "kb-query" in prompt
        assert "python type hints" in prompt
        # And it must direct the model to call the Skill tool.
        assert "Skill" in prompt
    finally:
        await orch.stop()


@pytest.mark.asyncio
async def test_skill_slash_command_with_no_args(tmp_path):
    """`/garden` (bare, no args) still translates."""
    skills = SkillsIndex(entries={
        "garden": SkillEntry(name="garden", path="/x", source="user"),
    })
    adapter = _RecordingAdapter(scripts=[_ok_script()])
    orch, bus = _build_orch(tmp_path, adapter=adapter, skills=skills)
    await orch.start()
    try:
        bus.publish(UserMessageToOrchestrator("/garden"))
        await orch.wait_idle()

        assert len(adapter.queries) == 1
        prompt = adapter.queries[0]
        assert prompt != "/garden"
        assert "garden" in prompt
        assert "Skill" in prompt
    finally:
        await orch.stop()


# ---------------------------------------------------------------------------
# Collision: built-ins beat skills
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_builtin_slash_command_beats_colliding_skill(tmp_path):
    """A skill named `cd` exists in the registry, but `/cd <path>` MUST still
    route to the built-in cwd-change handler — built-ins win."""
    skills = SkillsIndex(entries={
        "cd": SkillEntry(name="cd", path="/x", source="user"),
    })

    class _AppStub:
        def __init__(self):
            self.change_cwd_calls: list[str] = []

        async def change_cwd(self, path):
            self.change_cwd_calls.append(path)
            return {"changed": path}

        def notify(self, *args, **kwargs):
            pass

    app = _AppStub()
    adapter = _RecordingAdapter(scripts=[_ok_script()])
    bus = EventBus()
    manager = AgentManager(
        cwd=tmp_path, bus=bus,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[]),
    )
    orch = OrchestratorSession(
        cwd=tmp_path, bus=bus, manager=manager, adapter=adapter,
        app=app, skills=skills,
    )

    await orch.start()
    try:
        bus.publish(UserMessageToOrchestrator("/cd /tmp/elsewhere"))
        await orch.wait_idle()

        # Built-in handler ran.
        assert app.change_cwd_calls == ["/tmp/elsewhere"]
        # The SDK was NOT prompted to invoke the cd skill.
        assert all("Skill" not in q for q in adapter.queries), \
            f"adapter.queries={adapter.queries!r}"
    finally:
        await orch.stop()


# ---------------------------------------------------------------------------
# /help integration
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_help_lists_discovered_skills(tmp_path):
    """`/help` must include a `Skills:` section listing the names of all
    discovered skills (sorted, no descriptions)."""
    skills = SkillsIndex(entries={
        "kb-query": SkillEntry(name="kb-query", path="/x", source="user"),
        "garden": SkillEntry(name="garden", path="/y", source="user"),
        "last30days": SkillEntry(name="last30days", path="/z", source="plugin"),
    })
    adapter = _RecordingAdapter(scripts=[_ok_script()])
    orch, bus = _build_orch(tmp_path, adapter=adapter, skills=skills)
    replies: list[OrchestratorReply] = []
    bus.subscribe(OrchestratorReply, replies.append)

    await orch.start()
    try:
        bus.publish(UserMessageToOrchestrator("/help"))
        await orch.wait_idle()
        joined = "\n".join(r.text for r in replies)
        assert "Skills:" in joined
        for name in ("kb-query", "garden", "last30days"):
            assert f"/{name}" in joined, f"missing skill in /help: {name}"
        # Built-in commands still listed.
        assert "/reset" in joined and "/help" in joined
    finally:
        await orch.stop()


@pytest.mark.asyncio
async def test_help_with_no_skills_omits_skills_section(tmp_path):
    """When the index is empty, /help shouldn't have a dangling 'Skills:' line."""
    skills = SkillsIndex(entries={})
    adapter = _RecordingAdapter(scripts=[_ok_script()])
    orch, bus = _build_orch(tmp_path, adapter=adapter, skills=skills)
    replies: list[OrchestratorReply] = []
    bus.subscribe(OrchestratorReply, replies.append)

    await orch.start()
    try:
        bus.publish(UserMessageToOrchestrator("/help"))
        await orch.wait_idle()
        joined = "\n".join(r.text for r in replies)
        assert "Skills:" not in joined
    finally:
        await orch.stop()


# ---------------------------------------------------------------------------
# Unknown command UX
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unknown_slash_command_replies_with_error_not_forwarded(tmp_path):
    """A slash command that matches neither a built-in nor a skill must
    publish an OrchestratorReply naming the command and pointing at /help —
    it must NOT be forwarded to the SDK as a prompt (otherwise typos waste
    tokens and confuse the model)."""
    skills = SkillsIndex(entries={
        "garden": SkillEntry(name="garden", path="/x", source="user"),
    })
    adapter = _RecordingAdapter(scripts=[_ok_script()])
    orch, bus = _build_orch(tmp_path, adapter=adapter, skills=skills)
    replies: list[OrchestratorReply] = []
    bus.subscribe(OrchestratorReply, replies.append)

    await orch.start()
    try:
        bus.publish(UserMessageToOrchestrator("/foobarbaz"))
        await orch.wait_idle()

        # SDK was NOT queried with the unknown slash text.
        assert all("/foobarbaz" not in q for q in adapter.queries), \
            f"adapter.queries={adapter.queries!r}"
        # User got a clear error reply.
        joined = "\n".join(r.text for r in replies)
        assert "foobarbaz" in joined
        assert "unknown" in joined.lower()
        assert "/help" in joined
    finally:
        await orch.stop()


@pytest.mark.asyncio
async def test_non_slash_message_still_forwards_to_sdk(tmp_path):
    """The unknown-slash error must NOT block ordinary user messages —
    anything not starting with `/` should still flow through to the SDK as a
    regular prompt."""
    skills = SkillsIndex(entries={})
    adapter = _RecordingAdapter(scripts=[_ok_script()])
    orch, bus = _build_orch(tmp_path, adapter=adapter, skills=skills)
    await orch.start()
    try:
        bus.publish(UserMessageToOrchestrator("hello orchestrator"))
        await orch.wait_idle()
        assert adapter.queries == ["hello orchestrator"]
    finally:
        await orch.stop()
