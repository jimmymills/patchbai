# Patchfeld — Design Spec

**Date:** 2026-05-06
**Status:** Approved for implementation planning

## Overview

`patchfeld` is a Textual-based terminal application for managing multiple Claude
Code agent sessions. The defining property: the entire interface is mutable by
the agent itself. A top-level "orchestrator" Claude Code session runs inside
the TUI, talks to the user, spawns and supervises child agents, and reshapes
the UI on demand by emitting a declarative layout spec.

The application starts simple — a dashboard with the orchestrator chat, an
agent table, and an activity feed — and grows in complexity only when the
orchestrator decides it should, including the option to register new
custom-built widgets at runtime.

## Goals

- Run and supervise multiple concurrent Claude Code sessions in one terminal.
- Let the orchestrator answer questions about, message, and interrupt children
  using structured introspection (not screen scraping).
- Let the orchestrator reshape the UI conversationally — pick widgets, arrange
  panels, save/load named layouts, even register custom widgets when the
  built-in library isn't enough.
- Let the orchestrator adjust user-facing config (keybindings, theme, defaults)
  in response to user requests.
- Persist what matters (current layout, transcripts, agent history) across
  restarts without trying to keep zombie processes alive.

## Non-goals (v1)

- Auto-resuming half-finished agents across restarts (`resume=session_id`).
  Agents are re-runnable from history, not auto-resumed.
- Sandboxing custom widgets in subprocesses. Mode C trusts the orchestrator's
  emitted Python in-process; a try/except boundary at mount time prevents
  full-app crashes.
- Peer-to-peer messaging between child agents. All cross-agent traffic is
  orchestrator-mediated.
- Adapters for non-Claude harnesses (Codex, Aider, Gemini CLI). The agent
  abstraction is designed to accommodate them later, but only Claude Code via
  the Claude Agent SDK ships in v1.
- A modal "approve this tool call?" UX for child agents. Children inherit
  permission policy from the user's `~/.claude/settings.json`, optionally
  narrowed at spawn time.

## Architecture

One Python process, one asyncio event loop. Six components.

```
                                                          ┌────────────────────────┐
                                                          │      EventBus          │
                                                          │  (async pub/sub)       │
                                                          └────────────┬───────────┘
                                                                       │ events
                          ┌─────────────────┐    layout spec           ▼
       user keyboard ───▶ │   Textual App   │◀───────────  ┌──────────────────────┐
                          │  (shell + chrome)               │   LayoutEngine       │
                          │  - CommandBar   │ ◀── mounts ── │  diff(old, new)      │
                          │  - StatusBar    │   widgets     │  WidgetRegistry      │
                          └────────┬────────┘               │  (curated + custom)  │
                                   │ render                 └──────────┬───────────┘
                                   ▼                                   │
                         ┌──────────────────┐                          │
                         │    Panels (N)    │ ◀── widget classes ──────┘
                         └──────────────────┘

                ┌────────────────────────┐         spawn / send / interrupt
                │   Orchestrator         │ ───────────────────────────────────────┐
                │   ClaudeSDKClient      │                                        ▼
                │   + injected tools     │                          ┌──────────────────────────┐
                └─────────┬──────────────┘                          │      AgentManager        │
                          │                                         │   ClaudeSDKClient × N    │
                          │                                         │   + injected tools       │
                          └────────── EventBus ◀──── transcript ────┴──────────────────────────┘
                                                  state changes
                                                  push notifications
```

### Components

1. **Textual App** — owns the screen, the persistent chrome (`CommandBar`,
   `StatusBar`), and renders whatever the LayoutEngine mounts. Knows nothing
   about agents.
2. **LayoutEngine** — receives a `LayoutSpec` (pydantic model), diffs it
   against current state, and applies the minimum set of mount / unmount /
   swap-props / re-parent operations.
3. **WidgetRegistry** — maps widget-type strings to Textual widget classes.
   Bootstrapped with the curated library. Its internal
   `register_custom_widget(name, source)` method `exec`s source into an
   isolated module namespace and adds the resulting class. The orchestrator
   never calls this method directly — it ships custom widgets in the
   `custom_widgets` block of a `set_layout` call, and the LayoutEngine invokes
   the registry as part of applying the spec.
4. **Orchestrator session** — a `ClaudeSDKClient` instance with an in-process
   MCP server providing the orchestrator-only tools (spawn/send/layout/config),
   layered on top of standard Claude Code tools.
5. **AgentManager** — owns child `ClaudeSDKClient` lifecycles. Holds state
   machines, transcripts, and per-agent input queues. Injects
   `notify_orchestrator` and `ask_orchestrator` MCP tools into every child.
6. **EventBus** — async pub/sub. Single channel for transcript appends, agent
   state changes, push notifications, layout-applied / layout-failed events.
   Widgets subscribe; nothing reaches into AgentManager directly.

### Boundaries that matter

- **App ↔ LayoutEngine** is "spec in, mounted widgets out." The App never
  builds widget trees by hand.
- **Orchestrator ↔ AgentManager** is the only path that creates or controls
  children. Widgets cannot spawn agents.
- **EventBus** is the only path async events take to widgets. No callbacks
  passed through the layout.

## LayoutSpec

The whole "agent reshapes the UI" surface is one schema, validated by pydantic
v2, persisted as JSON.

```json
{
  "version": 1,
  "layout": {
    "type": "horizontal",
    "children": [
      { "id": "orch", "size": "60%", "widget": "OrchestratorChat", "props": {} },
      {
        "type": "vertical",
        "size": "40%",
        "children": [
          { "id": "agents", "size": "50%", "widget": "AgentTable",   "props": {} },
          { "id": "feed",   "size": "50%", "widget": "ActivityFeed", "props": {} }
        ]
      }
    ]
  },
  "focus": "orch",
  "custom_widgets": []
}
```

### Rules

- **Two node kinds.** Containers (`{ type: "horizontal" | "vertical",
  children: [...], size? }`) and panels (`{ id, widget, props?, size? }`).
- **`id` is stable.** Diff is identity-based: same `id` and same `widget` → reuse
  the mounted widget and push new props (no re-mount, no scroll-jump).
  Different `widget` at the same `id` → tear down and re-mount. Missing `id`
  in the new spec → unmount.
- **`size`** is a CSS-like string (`"40%"`, `"30"` cells, `"1fr"`).
- **`focus`** names the panel that gets keyboard focus on apply (optional).
- **`custom_widgets`** is the mode-C escape hatch: `[{ name, source }]`. Source
  is `exec`d into an isolated module namespace; the resulting class is
  registered, then panels can reference `widget: name` like any built-in.
- **Chrome is not in the spec.** `CommandBar` and `StatusBar` are always
  present; the agent cannot remove them. This guarantees the app stays usable
  even if the spec is broken.
- **`OrchestratorChat` is required.** Validation rejects any spec that doesn't
  contain exactly one panel with `widget: "OrchestratorChat"`. The orchestrator
  cannot accidentally hide its own input. (To temporarily reclaim screen real
  estate, the orchestrator can shrink its panel via `size`, not remove it.)
- **Atomic apply.** If validation or any custom-widget instantiation fails,
  the entire apply is rolled back, the previous layout stays mounted, and a
  `layout-failed` event is fired so the orchestrator can react.

### Default landing layout

The dashboard above is the built-in default applied when there is no
`<cwd>/.patchfeld/layout.json`.

## Curated widget library

| Name | Purpose |
|---|---|
| `OrchestratorChat` | The orchestrator session: messages + input. Always somewhere. |
| `AgentTable` | Sortable list: name, status, elapsed, last action, cost. |
| `ActivityFeed` | Cross-agent chronological event stream. |
| `AgentTranscript` | One agent's full conversation, collapsible tool calls, syntax-highlighted code/diff. Includes a bottom input box that sends straight to the bound child when focused. Bound to `agent_id`. |
| `AgentInput` | Standalone send-message text area, bound to `agent_id`. For layouts that want input without the transcript above it. |
| `DiffViewer` | Unified or split diff for a file edit; bindable to a tool call. |
| `FileTree` | Directory tree of an agent's working directory. |
| `FileViewer` | Read-only syntax-highlighted file display. |
| `LogTail` | Tails an arbitrary file. |
| `Markdown` | Renders text/markdown. |
| `Notebook` | Scratch space the orchestrator writes to (todos, plans). |
| `Terminal` | A real PTY in a panel — runs an arbitrary command (defaults to user's `$SHELL`). Lets the user open a normal shell, or drop into the actual `claude` CLI when they want the colorful native TUI instead of the SDK-managed transcript. Props: `command`, `cwd`, `env`. |

Plus the always-on chrome: `CommandBar`, `StatusBar`.

Mode-C custom widgets aren't a row in this table — they're whatever class the
orchestrator ships in the `custom_widgets` block of a `set_layout` call,
referenced by name from a panel.

### `Terminal` panels are not agents

A `Terminal` panel is a raw PTY the user drives directly with their keyboard.
It is **not** owned or introspected by the AgentManager — the orchestrator
has no `notify_orchestrator` channel into it, can't `read_agent_transcript`
on it, and won't see its tool calls in the activity feed. Anything the user
does in a `Terminal` panel (including running `claude` interactively) is
opaque to the orchestrator. That's the point: the SDK path gives you
introspection, the `Terminal` widget gives you raw access when you need it.

The orchestrator can `set_layout` a `Terminal` panel into existence (e.g.
"open a terminal pointed at `~/Developer/foo`") and `kill_agent`-equivalent
removal happens by simply omitting the panel from a future `set_layout`.

## Tool surfaces

Both surfaces are layered on top of standard Claude Code tools (Bash, Read,
Edit, etc.) using the SDK's in-process MCP support
(`create_sdk_mcp_server` + `@tool`).

### Orchestrator tools

```
# Agents
spawn_agent(name, prompt, *, cwd=None, allowed_tools=None,
            disallowed_tools=None, model=None, system_prompt=None) -> agent_id
send_to_agent(agent_id, message)                                   -> None
interrupt_agent(agent_id)                                          -> None
kill_agent(agent_id)                                               -> None
list_agents()       -> [{id, name, status, started_at, last_activity, cost}]
read_agent_transcript(agent_id, since_message=None, limit=None)
                                                                   -> [Message]

# Layout
set_layout(spec)                                                   -> None
save_layout(name)                                                  -> None
load_layout(name)                                                  -> None
list_layouts()                                                     -> [str]

# Config (hot-reloads on success)
list_bindings()             -> [{key, action, description}]
list_actions()              -> [{name, signature, description}]
bind_key(key, action, *, args=None) -> None
unbind_key(key)             -> None
set_config(path, value)     -> None
get_config(path=None)       -> dict | value
```

`spawn_agent` returns a new `agent_id`. `allowed_tools` / `disallowed_tools`
default to inheriting `~/.claude/settings.json`; the orchestrator can narrow
per-spawn (e.g. a "researcher" with only `Read` and `WebSearch`).

### Child tools (always injected)

```
notify_orchestrator(message)              -> None
ask_orchestrator(question, timeout_s=300) -> str
```

`notify_orchestrator` is fire-and-forget — appears in the orchestrator's chat
as `[child_name → orchestrator] message`. `ask_orchestrator` blocks the
child's tool call until the orchestrator's eventual `send_to_agent` reply
arrives, then returns it as the tool result.

### Permission flow

The SDK's `can_use_tool` hook routes through AgentManager, which checks the
child's per-spawn allowlist and the inherited settings.json defaults. No user
modal. The user can always interrupt via `ctrl-c` on a focused agent panel.

## Cross-communication

- **User → Orchestrator** — typing in the `CommandBar` or in
  `OrchestratorChat`'s input.
- **User → Child** — when an `AgentTranscript` panel is focused, its bottom
  input box sends straight to that child. The orchestrator sees the message
  arrive in its event stream so it stays informed without mediating.
- **Orchestrator → Child** — `send_to_agent` tool.
- **Child → Orchestrator** — `notify_orchestrator` (push) and
  `ask_orchestrator` (push + wait). Plus the orchestrator can pull at any
  time via `read_agent_transcript`.
- **Child ↔ Child** — not supported in v1 (orchestrator-mediated only).

## Persistence

### Per-project: `<cwd>/.patchfeld/`

```
layout.json              # current LayoutSpec; restored on next launch in this cwd
agents.json              # [{id, name, cwd, started_at, ended_at, status}, ...]
transcripts/
  <agent_id>.jsonl       # append-only message log per child
  orchestrator.jsonl     # the orchestrator's own transcript
scratch/
  <free-form notes>      # for the Notebook widget
```

Suggested addition to project `.gitignore`: `.patchfeld/`.

### Global: `~/.config/patchfeld/`

```
config.toml              # bindings, theme, default model, default tool allowlist
layouts/
  <name>.json            # named layout presets (save_layout / load_layout)
```

### Behavior

- Launch reads `<cwd>/.patchfeld/layout.json`; falls back to built-in dashboard.
- Transcripts are append-only JSONL — cheap to tail and re-read.
- `agents.json` records every agent that ever existed in this cwd. The
  History view (built-in screen, `ctrl-h`) lists them. The orchestrator can
  re-run any past task by reading the original prompt + transcript and
  spawning a fresh agent.
- No background processes between sessions. Quit sends an interrupt to every
  running child, waits up to 5s for clean shutdown, then terminates.
- All file writes are atomic: write-to-temp, fsync, rename.

## Chrome and keyboard

Always-on, outside the LayoutSpec:

- **CommandBar** (top, 1 row) — `/` to focus. Submits to the orchestrator.
  Up-arrow recalls history.
- **StatusBar** (bottom, 1 row) — `tokens in/out · cost · N active agents ·
  current layout name (or "default" if unsaved) · [E] error indicator if last
  set_layout failed`.

### Default keybindings (all rebindable via `bind_key`)

| Key | Action |
|-----|--------|
| `/` | Focus command bar |
| `tab` / `shift-tab` | Cycle focus across panels |
| `ctrl-c` | Interrupt focused agent (or orchestrator); confirm quit if nothing running |
| `ctrl-q` | Quit |
| `ctrl-h` | Open History view |
| `ctrl-l` | Open layout switcher |
| `?` | Show keybindings overlay |

Mouse: standard Textual support — click to focus a panel, scroll, click
`AgentTable` cells.

### Action registry

A fixed enumerable set the app exposes for binding. Examples:
`focus_panel(panel_id)`, `focus_orchestrator`, `focus_command_bar`,
`cycle_focus`, `interrupt_focused`, `quit`, `load_layout(name)`,
`open_history`. `list_actions()` returns names + signatures so the
orchestrator can map user-spoken intents to bindings without guessing.

## Tech stack

- Python 3.11+
- [Textual](https://textual.textualize.io/) — UI
- [`claude-agent-sdk`](https://docs.claude.com/en/api/agent-sdk/python) —
  orchestrator + child sessions, in-process MCP for custom tools
- `pydantic` v2 — `LayoutSpec` validation
- `orjson` — atomic JSON writes
- `tomllib` (stdlib read) + `tomli-w` (write) — `config.toml`
- `rich` (transitive via Textual) — diff/syntax rendering
- A Textual-compatible PTY widget for the `Terminal` panel (e.g.
  `textual-terminal` or equivalent — final pick at implementation time, must
  support custom `command`/`cwd` and survive widget unmount cleanly)

### Packaging

Single package `patchfeld`, entry point `patchfeld` (and short `mt`).
`uv` / `pipx` installable wheel. Does not bundle `claude` — relies on the
user's installed CLI for parity with their `~/.claude/settings.json`.

### Module layout

```
patchfeld/
  app.py                # Textual App, chrome, key bindings
  layout/
    spec.py             # pydantic models
    engine.py           # diff + mount
    registry.py         # widget registry, custom-widget exec sandbox
  agents/
    manager.py          # AgentManager, state machine
    session.py          # one ClaudeSDKClient + transcript
    tools.py            # notify_orchestrator / ask_orchestrator MCP server
  orchestrator/
    session.py          # the orchestrator ClaudeSDKClient
    tools.py            # spawn_agent, set_layout, bind_key, etc. MCP server
  widgets/              # one file per curated widget
  events.py             # EventBus
  persistence.py        # layout, transcripts, agents.json, config.toml
  config.py             # config schema + hot reload
  actions.py            # action registry
```

## Testing strategy

- **Unit**
  - `LayoutEngine.diff` — pure function; golden tests of (old, new) →
    operations.
  - `WidgetRegistry` — custom-widget `exec` does not pollute globals;
    instantiation failures are caught and returned as errors.
  - `AgentManager` — state-machine transitions and event emission.
  - Config — round-trip read/write, hot-reload triggers.
  - Action dispatch — argument binding and validation.
- **Integration** (Textual `App.run_test()`)
  - Boot, apply default layout, assert mounted widget tree and focus.
  - Apply several `set_layout` calls in sequence; assert diff produced
    expected mounts/unmounts.
  - Apply an invalid spec; assert rollback and `layout-failed` event.
- **End-to-end**
  - Replace `ClaudeSDKClient` with a fake that replays a recorded message
    stream from a JSON fixture.
  - Scripted scenario: orchestrator spawns a child → child runs a tool →
    child calls `notify_orchestrator` → orchestrator replies via
    `send_to_agent` → child completes. Assert events fired and final state.

## Open questions / parking lot

These are intentionally deferred past v1:

- Mode-C generative widgets in a subprocess sandbox.
- Full session resume (`resume=session_id`) for half-finished children
  surviving restart.
- Peer-to-peer messaging between child agents.
- A "remote attach" PTY adapter for non-SDK harnesses.
- Adapters for Codex / Aider / Gemini CLI (the AgentManager interface is
  designed to accommodate them, but the only implementation in v1 is the
  Claude Agent SDK adapter).
- A modal approval UX as an opt-in for paranoid configs.
