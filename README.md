# patchbai

A Textual terminal app for running and supervising multiple Claude Code
sessions in one place — with a twist: the **agent itself can reshape the UI**.
A top-level "orchestrator" Claude Code session lives inside the TUI, talks to
you, spawns child agents, and rearranges panels, switches themes, rebinds
keys, or even ships custom widgets at runtime by emitting a declarative
layout spec.

Entry points: `patchbai` and the short alias `mt`.

```
┌─ Orchestrator ──────────────────────┐ ┌─ Agents ─────────────────────────┐
│ > spawn an agent to refactor auth.py│ │ name      status   elapsed  cost │
│ ✓ spawned `auth-refactor` (id …)    │ │ auth-ref  running  0:42     $0.03│
│ > switch to the file-tree layout    │ │ readme    waiting  0:11     $0.01│
│ ✓ loaded layout "file-tree"         │ └──────────────────────────────────┘
│ ▎                                   │ ┌─ ActivityFeed ───────────────────┐
│ Message orchestrator… (/reset, /res…│ │ auth-ref  Edit  src/auth.py      │
└─────────────────────────────────────┘ │ auth-ref  Read  src/jwt.py       │
                                        └──────────────────────────────────┘
 / cmd · ctrl-q quit · ctrl-h history · ctrl-l layouts · ctrl-shift-l themes
 tokens 12,431↑ 3,210↓ · $0.04 · 2 active · layout: dashboard
```

## Why use it

- **Run several Claude sessions side by side.** The orchestrator spawns
  children with their own prompts, allowed tools, and cwd. Each gets a
  transcript you can scroll, a state machine you can introspect, and a
  direct-message input box when you want to bypass the orchestrator.
- **Talk to the UI like you talk to the agent.** "Open a diff viewer for
  the last edit", "give me a 3-pane layout with file tree on the left",
  "save that as `review`" — all of it routes through orchestrator tools
  (`set_layout`, `save_layout`, `bind_key`, `set_theme`, …) and persists.
- **Structured introspection, not screen scraping.** The orchestrator
  reads a child's transcript, sends messages, interrupts, kills — all via
  in-process MCP tools layered on top of the Claude Agent SDK. No PTY
  parsing, no fragile regex.
- **Persistent everything.** Workspaces (per-cwd), layouts, themes,
  keybindings, transcripts, and a full agent history are stored on disk
  and restored on next launch.
- **Real escape hatches.** A `Terminal` widget is a real PTY — drop into
  the actual `claude` CLI, or your shell, in any panel. Mode-C custom
  widgets let the orchestrator ship Python at runtime when the curated
  widget library isn't enough.

## Concept

```
                ┌─────────────────────┐         spawn / send / interrupt
   you ───────▶ │  Orchestrator       │ ──────────────────────────────┐
   (cmd bar     │  ClaudeSDKClient    │                               ▼
   or chat)     │  + injected tools   │                    ┌──────────────────┐
                └──────────┬──────────┘                    │  AgentManager    │
                           │                               │  ClaudeSDKClient │
                           │                               │  × N children    │
                           ▼                               └────────┬─────────┘
                ┌─────────────────────┐                             │
                │   LayoutEngine      │ ◀── transcripts, state ─────┘
                │   diff(old, new)    │     events via EventBus
                │   WidgetRegistry    │
                └──────────┬──────────┘
                           ▼
                ┌─────────────────────┐
                │   Textual App       │
                │   tabs · panels     │
                │   chrome (always-on)│
                └─────────────────────┘
```

A `LayoutSpec` is a tree of containers (`horizontal` / `vertical` splits)
and panels (`{ id, widget, props, size }`). The engine diffs the new spec
against the live tree by `id` — same id + same widget reuses the mounted
widget (no scroll-jump); a different widget at the same id swaps it; a
missing id unmounts. The chrome (`CommandBar`, `StatusBar`) is always
mounted and cannot be removed. `OrchestratorChat` must be present in
exactly one panel — the agent can shrink it but cannot hide its own input.

## Built-in widgets

| Widget | Purpose |
|---|---|
| `OrchestratorChat` | The orchestrator session: rich transcript + input. |
| `AgentTable` | Sortable list of children: name, status, elapsed, cost. |
| `AgentTranscript` | One agent's full conversation, with a direct-message input box. |
| `ActivityFeed` | Cross-agent chronological event stream. |
| `FileTree` | Directory tree; emits `FileSelected` events on the bus. |
| `FileViewer` | Read-only syntax-highlighted file display; can follow `FileTree` selection. |
| `FileEditor` | Editable, syntax-highlighted; ctrl-s saves; warns on external changes. |
| `DiffViewer` | Unified-diff viewer (precomputed `diff` or `before` + `after`). |
| `LogTail` | Tails an arbitrary file (250 ms poll). |
| `Markdown` | Renders markdown from a string or file. |
| `Notebook` | Editable scratch buffer; persists to `<cwd>/.patchbai/scratch/<name>.md`. |
| `Terminal` | Real PTY — drop into `claude`, `$SHELL`, or any command. Opaque to the orchestrator. |

The orchestrator can also register **custom widgets** by emitting Python
source in the `custom_widgets` block of a `set_layout` call. The source
runs in an isolated module namespace; instantiation failures roll the
apply back so a broken widget can't brick the app.

## Tabs, layouts, and themes

- **Tabs.** `ctrl-t` to add, `ctrl-w` to close, `ctrl-1`..`ctrl-9` to jump,
  `ctrl-pgup` / `ctrl-pgdn` to cycle. Each tab has its own `LayoutSpec` and
  remembers which panel was last focused.
- **Named layouts.** `ctrl-l` opens the switcher. Layouts save to
  `~/.config/patchbai/layouts/<name>.json`; the orchestrator can list /
  load / save them via tools.
- **Named themes.** `ctrl-shift-l` opens the theme switcher. Themes are a
  palette + extra Textual CSS; the orchestrator can author and apply them
  with `set_theme`.
- **Drag to resize.** Dragging a splitter persists new sizes back to the
  workspace; `ctrl-shift-r` resets the active tab to its named source.
- **Runtime cwd swap.** `ctrl-shift-d` (or `/cd <path>`) re-roots the app:
  stops orchestrator + manager, swaps cwd, loads (or seeds) the new
  workspace, re-applies the active theme. Refuses while children run.

## Slash commands (orchestrator chat)

| Command | Action |
|---|---|
| `/reset` | Start a fresh orchestrator session. |
| `/resume` | Open the resume picker for a past orchestrator session. |
| `/rename` | Rename the current session's title. |
| `/cd <path>` | Change the workspace cwd. |
| `/help` | Show the slash-command list. |

`ctrl-c` while the chat is focused interrupts the orchestrator without
quitting the app.

## Default keybindings

| Key | Action |
|---|---|
| `/` | Focus command bar |
| `?` | Show keybindings overlay |
| `ctrl-q` | Quit |
| `ctrl-h` | Open agent history |
| `ctrl-l` | Open layout switcher |
| `ctrl-shift-l` | Open theme switcher |
| `ctrl-shift-r` | Reset panel sizes for active tab |
| `ctrl-shift-d` | Change cwd |
| `ctrl-t` / `ctrl-w` | New tab / close active tab |
| `ctrl-1`..`ctrl-9` | Jump to tab N |

All of these are rebindable from inside the app — ask the orchestrator to
"bind ctrl-r to focus_orchestrator" and it will, via the `bind_key` tool.

## Persistence

```
<cwd>/.patchbai/
  workspace.json          # tabs + layouts for this directory
  agents.json             # every child agent ever spawned here
  transcripts/
    <agent_id>.jsonl      # append-only message log per child
    orchestrator.jsonl    # the orchestrator's own transcript
  scratch/                # for the Notebook widget

~/.config/patchbai/
  config.toml             # bindings, theme, default model, tool allowlist
  layouts/<name>.json     # named layout presets
  themes/<name>.json      # named themes
```

All writes are atomic (temp + fsync + rename). `.patchbai/` is in this
repo's `.gitignore` and you should add it to yours.

## Installation

### Requirements

- **Python 3.11+**
- The Claude CLI installed and authenticated (`claude --version`). patchbai
  uses your `~/.claude/settings.json` for permissions and tool allowlists.
- A terminal with TrueColor support (any modern macOS / Linux terminal).

### With `uv` (recommended, used by this repo)

```bash
git clone <repo> patchbai
cd patchbai
uv sync                  # install runtime deps into .venv
uv run patchbai          # or: uv run mt
```

For development, sync the dev extras (pyright, pytest):

```bash
uv sync --extra dev
uv run pytest
./scripts/typecheck.sh   # canonical pyright invocation
```

### With `pipx`

```bash
pipx install .
patchbai                 # or: mt
```

### Editable install with `pip`

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
patchbai
```

## Running

```bash
patchbai               # use the current directory as the workspace cwd
mt                     # short alias
```

First launch in a directory seeds the built-in dashboard (orchestrator
chat + agent table + activity feed) and creates `<cwd>/.patchbai/`. Type
in the orchestrator chat or hit `/` to focus the command bar and start
talking to it.

Try:

- `spawn an agent that audits this repo for unused code`
- `give me a layout with the orchestrator on the left and a file tree + viewer on the right`
- `save that as "review"`
- `bind ctrl-r to focus_orchestrator`
- `make a theme called dim with a dark slate palette`

## Project layout

```
patchbai/
  app.py                   # Textual App, tabs, chrome, key bindings, lifecycle
  config.py                # config.toml schema + atomic IO
  events.py                # EventBus + event types
  actions.py               # action registry (bindable verbs)
  agents/                  # AgentManager, child sessions, SDK adapter
  orchestrator/            # orchestrator session + MCP tool surface
  layout/                  # LayoutSpec, diff/mount engine, widget registry
  workspace/               # Workspace + Tab models
  widgets/                 # one file per built-in widget
  theme/                   # ThemeSpec + apply
  persistence/             # per-cwd and global stores (atomic)
docs/superpowers/
  specs/                   # design docs (start here for the v1 design spec)
  plans/                   # implementation plans
tests/                     # pytest, including App.run_test() integration tests
```

The canonical design spec is
[`docs/superpowers/specs/2026-05-06-patchbai-design.md`](docs/superpowers/specs/2026-05-06-patchbai-design.md).

## Limitations (v1)

- **No auto-resume of in-flight children** across restarts. Past agents
  are visible in History (`ctrl-h`) and can be re-run from their original
  prompt; they are not silently revived.
- **Custom widgets run in-process.** A `try/except` boundary at mount
  time catches crashes, but there is no subprocess sandbox.
- **No peer-to-peer messaging between children.** All cross-agent traffic
  is orchestrator-mediated.
- **Claude Agent SDK only.** The agent abstraction is designed to
  accommodate other harnesses (Codex, Aider, Gemini CLI), but only the
  Claude adapter ships.
- **No modal "approve this tool call?" UX.** Children inherit your
  `~/.claude/settings.json` permissions, optionally narrowed at spawn.

## License

TBD.
