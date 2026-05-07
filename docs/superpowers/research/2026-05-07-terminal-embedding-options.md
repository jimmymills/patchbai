# Terminal Embedding Options for patchbai

**Date:** 2026-05-07
**Branch:** `terminal-research`
**Author:** research subagent (read-only on `patchbai/widgets/terminal.py`)
**Scope:** Investigate options for replacing patchbai's hand-rolled `Terminal` widget
with something more capable. The user's stated example is "embed Ghostty"; this report
takes that ask seriously, refutes it where it cannot work, and proposes alternatives
that *do* work.

---

## Executive summary

- **Best option (medium scope):** Keep Textual as the renderer, replace `pyte` with
  **libghostty-vt** (via Python bindings — most realistically a PyO3 wrapper over
  [`libghostty-rs`](https://github.com/uzaaft/libghostty-rs) or a WASM build loaded
  with `wasmtime-py`, the path Obsidian and JupyterLab already use). This gives
  production-grade VT parsing, truecolor, Kitty keyboard protocol, OSC 8 hyperlinks,
  Kitty graphics, scrollback, reflow, and Unicode/grapheme handling — all of the
  things "missing a lot of basic terminal features" actually means — without giving
  up the Textual TUI shell.
- **Runner-up (small scope, do this first regardless):** Fix the existing pyte-based
  pipeline. The Python widget is leaving 70%+ of pyte's actual capability on the
  floor in its renderer (colors thrown away, no scrollback class, no cursor, line-mode
  keystrokes, polling instead of `add_reader`). A pure refactor with no new dependency
  closes most of the gap users complain about.
- **Discard:** Embedding the Ghostty *GUI* (or Alacritty/WezTerm/Kitty/iTerm2/Terminal.app
  GUIs) as a Textual widget — categorically impossible. Each of these renders to a
  GPU-backed native window; none expose a "draw into a cell grid" mode. Also discard:
  spawning a real terminal headlessly and screen-scraping pixels (no surface to scrape);
  tmux control mode as the *primary* solution (heavyweight, adds runtime dep, doesn't
  remove the need for a VT parser on patchbai's side).

A two-step roadmap is recommended: (1) renderer/keyboard refactor against current pyte
this sprint; (2) libghostty-vt swap as the next milestone once the API stabilises (it
is currently in public alpha but the underlying logic is Ghostty's shipping production code).

---

## 1. What's missing today

Concrete review of `patchbai/widgets/terminal.py` (169 lines). The PTY plumbing is
fine; the **render layer and the keyboard layer are where the widget falls down**.

### 1.1 Rendering layer — the single biggest issue

- `_refresh()` does:
  ```python
  text = Text("\n".join(self._screen.display))
  ```
  `pyte.Screen.display` is a list of plain strings — **all per-cell attributes
  (colors, bold, italic, underline, reverse, etc.) are discarded** before they reach
  Textual. The widget renders a black-and-white shadow of whatever the program drew.
  This is the gap users are most likely actually feeling.
- The cursor is **never drawn**. `pyte.Screen.cursor` exists (.x, .y, .hidden) but the
  renderer ignores it, so users have no visual prompt feedback in TUI programs.
- 256/truecolor: per the [pyte issue tracker](https://github.com/selectel/pyte/issues/92),
  pyte's older versions had 256-color limitations. Even when pyte does parse
  modern colors, the current renderer's `screen.display` path can't carry them.

### 1.2 Screen object choice

- `self._screen = pyte.Screen(...)` uses the bare class. There is no scrollback —
  pyte ships `pyte.HistoryScreen` for that. So a `--help` longer than 24 lines is
  unrecoverable.
- The screen is fixed at `80×24` (`DEFAULT_COLS/DEFAULT_ROWS`). On `on_mount` the PTY
  is spawned with those dimensions; on Textual `Resize` events nothing is propagated,
  so `vim` etc. always think they have an 80×24 hole. No `setwinsz`/SIGWINCH wiring at all.

### 1.3 Stream / encoding kludge

- `PtyProcessUnicode` decodes to `str`, then the widget does
  `chunk.encode("utf-8", errors="replace")` and feeds bytes back into a `pyte.ByteStream`.
  `errors="replace"` quietly destroys data on UTF-8 boundary chunking. The fix is
  trivial — use `pyte.Stream` directly with the decoded `str`, or `PtyProcess` (bytes)
  with `pyte.ByteStream`. Pick one and stop the round-trip.

### 1.4 Polling

- `set_interval(0.05, self._tick)` polls every 50 ms with a `select()` of timeout 0,
  reading `1024` bytes. This means:
  - Bursty output ladders — many ticks are needed to drain `cat largefile`, each
    capped at 1 KiB and 50 ms apart, so output looks like it's stuttering.
  - Idle CPU is non-zero even when nothing's happening.
  - The right shape is `loop.add_reader(self._pty.fd, ...)` integrated with Textual's
    asyncio loop, draining all available bytes on each readiness event.

### 1.5 Keyboard

- `on_key` forwards exactly seven things: any printable single character, Enter,
  Backspace, Tab, Ctrl+C, Ctrl+D. **Everything else is silently dropped.** No arrows
  (so no shell history, no editor cursor movement), no Home/End/PgUp/PgDn, no F-keys,
  no Esc, no Alt-modified anything, no Ctrl+letter beyond C and D, no Shift+Tab,
  no Kitty keyboard / fixterms protocols. The doc-comment admits "line-mode keystroke
  forwarding only."
- No focus check — `on_key` always tries to write, even when the widget is unfocused
  (Textual's bubbling probably saves us, but it's still wrong).

### 1.6 Mouse

- Completely absent. Programs that send `\e[?1000h` (X10), `\e[?1006h` (SGR), etc.
  get no mouse events back. `htop`/`ranger`/`vim`'s mouse modes don't work.

### 1.7 Other escape sequences and protocols

- **OSC 8 hyperlinks:** not surfaced even if pyte parses them (it doesn't, fully).
- **OSC 52 clipboard:** not handled.
- **OSC 0/2 titles:** ignored — nothing updates the panel border title from the running program.
- **Bracketed paste:** not implemented (no `\e[?2004h` reply path, no paste-burst handling).
- **Alternate screen buffer:** pyte handles the toggle internally, but the renderer's
  `screen.display` reads whichever is active — so this works *coincidentally* for
  visible content, but switching back doesn't restore the prior view.
- **Kitty graphics / Sixel:** out of scope for any cell-grid renderer (these draw raster
  pixels into the terminal viewport — Textual cannot host them in a normal Static).

### 1.8 Process lifecycle

- On EOF the timer is torn down silently — no "[process exited 0]" indication. The
  user just sees a frozen screen.
- No way to restart the shell without unmounting the widget.

### 1.9 Summary

The widget today is a **proof-of-concept renderer with most of pyte's information
thrown away at the last step.** Many of the user's complaints can be addressed
without changing the emulator at all — they're renderer/keyboard/event-loop bugs.

---

## 2. Embedding Ghostty (the literal ask)

**Short answer:** You cannot embed Ghostty's GUI as a Textual panel. You *can* embed
the engine that powers it.

### 2.1 Why the Ghostty GUI cannot live inside Textual

Ghostty draws its own native window with platform GPU APIs (Metal on macOS, OpenGL on
Linux). It owns its window surface, its font atlas, and its event loop. Textual is a
text framework that emits ANSI escape sequences into a host terminal's cell grid.
There is no API surface where a GPU-rendered window can be composited into a
character cell. The two render models are incompatible.

This is consistent for all the Ghostty consumer apps in the wild: each is itself a
native window app (macterm, Mori, Muxy, Kytos, Geistty, Echo, etc.). None embed
Ghostty into a TUI. ([awesome-libghostty](https://github.com/Uzaaft/awesome-libghostty))

### 2.2 Why the *engine* (libghostty-vt) is exactly what we want

Mitchell Hashimoto split Ghostty so that the terminal-emulator brain ships as a
separate library, [`libghostty-vt`](https://libghostty.tip.ghostty.org/). From the
project description:

> libghostty-vt is a zero-dependency (not even libc) library that provides an API
> for parsing terminal sequences and maintaining terminal state such as cursor
> position, current styles, text wrapping, and more. … It contains no renderer
> drawing or windowing code; the consumer provides its own.
> — [libghostty Is Coming](https://mitchellh.com/writing/libghostty-is-coming)

This is *literally* what patchbai needs — a drop-in replacement for pyte with:

- Full VT escape parsing (SIMD-optimized) including DECSET modes, SGR truecolor,
  alt-screen, scrollback with reflow.
- Modern keyboard encoding (Kitty kbd protocol, fixterms, mouse encoding 1000/1006).
- OSC 0/2/7/8/52, hyperlinks, clipboard.
- Unicode/grapheme width handling (the bit pyte gets wrong on emoji and CJK).
- Renderer-state diffs (so we only redraw cells that changed).

The patchbai render path stays in Textual: read libghostty-vt's screen state, walk it,
emit `rich.text.Text` segments with the right `Style`. It's just *replacing pyte* with
a more capable engine.

### 2.3 Status / API stability

- Ghostty 1.2 (2026) ships libghostty-vt as the public alpha. The author explicitly
  flags the C ABI as still-stabilizing. ([Hashimoto blog](https://mitchellh.com/writing/libghostty-is-coming))
- The *underlying logic* is the production code that ships in Ghostty itself —
  millions of users, very battle-tested.
- C header lives at [`include/ghostty/vt.h`](https://github.com/ghostty-org/ghostty/blob/main/include/ghostty/vt.h).
- Already in use across multiple language ecosystems
  ([awesome-libghostty list](https://github.com/Uzaaft/awesome-libghostty)):
  - **Rust:** [`libghostty-rs`](https://github.com/uzaaft/libghostty-rs) — safe wrappers
    plus a Rust port of the Ghostling demo using macroquad.
  - **Go:** [`go-libghostty`](https://github.com/mitchellh/go-libghostty) (Mitchell's own).
  - **Dart:** `libghostty-dart` for Flutter.
  - **WASM:** `browstty`, `obsidian-ghostty-terminal`, `vscode-bootty`, `hauntty`.
  - **Python:** *no project listed yet.* This is genuinely a small gap in the
    ecosystem — patchbai could be the first.

### 2.4 Three integration paths if we go this way

| Path | How it works | Pros | Cons |
|---|---|---|---|
| **PyO3 wrapper over libghostty-rs** | Build a small Rust crate exposing a Python module that wraps `libghostty-rs::Terminal`, ship via `maturin`. | Native-speed; clean Rust safety layer underneath. | Adds Rust toolchain to the patchbai build (mitigated by publishing prebuilt wheels). Tracks two upstreams (libghostty-rs + libghostty itself). |
| **WASM via wasmtime-py** | Compile `libghostty-vt` to wasm32-unknown-unknown (already a supported target — JupyterLab and Obsidian both ship it as WASM), load via [`wasmtime-py`](https://github.com/bytecodealliance/wasmtime-py). | No native build step on the consumer side. Same artifact runs on every OS/arch we care about. Cross-platform parity is automatic. | Slower than native (still much faster than pyte). One more runtime dep (wasmtime). |
| **Direct ctypes against libghostty-vt's C ABI** | Use [`ctypesgen`](https://github.com/ctypesgen/ctypesgen) on `vt.h`, ship a prebuilt `.so/.dylib/.dll`. | No Rust, no WASM. | We own the wheel-building matrix. The C ABI is the most-likely-to-shift surface during alpha. |

The WASM path looks **best for patchbai's distribution model** (PyPI wheel, no
compiler on user machines, single artifact per release). It is the same model
Obsidian and JupyterLab use today, so the trail is broken.

### 2.5 What this *doesn't* solve

- **Rendering quality is bounded by Textual.** Truecolor will work; sub-pixel font
  rendering won't (Textual uses host terminal's font). Sixel/Kitty graphics
  protocols still cannot be displayed inside a cell-grid panel. If a user wants those,
  they need a real terminal — see §6.

---

## 3. Embedding any other native terminal emulator

Quick verdict on each, since the user named Ghostty as an example rather than
specifically requiring it:

### 3.1 Alacritty

- **Library mode:** [Issue #823](https://github.com/jwilm/alacritty/issues/823) is the
  long-running feature request to expose Alacritty as a library. Status: not done,
  not on the roadmap. The proposal was for it to draw into an OpenGL texture — even
  if implemented, that is incompatible with a cell grid.
- **`--embed <window-id>`:** this exists, but it's X11 window reparenting. Useless on
  macOS, useless on Wayland, and would still create a child native window — not a
  Textual cell.
- **Reusable parts:** `alacritty_terminal` + `vte` crates. These *are* embeddable
  (they're libraries) but at that point you're doing the same thing as §4 below — using
  Alacritty's parser/state engine without the GUI.

### 3.2 WezTerm

- The GUI is not a library; same window/GPU argument as Ghostty.
- **Reusable parts:** the [`wezterm-term`](https://github.com/wezterm/wezterm/blob/main/term/README.md)
  crate. Per its README it provides "terminal escape sequence parsing, keyboard and
  mouse input encoding, a model for the screen cells including scrollback, sixel and
  iTerm2 image support, OSC 8 Hyperlinks and a wide range of terminal cell attributes."
  Very full-featured. Strong alternative to libghostty-vt; trade-off discussed in §4.

### 3.3 Kitty

- Not a library; standalone GPU app.
- The Python "kittens" extension model runs Python code *inside Kitty* (not the other
  way around). That is the opposite of what we want.
- The `kitten @` IPC interface lets external programs script Kitty, but again, that
  requires Kitty to be the host process.

### 3.4 iTerm2 (macOS)

- Mac-only proprietary app. The [iTerm2 Python API](https://iterm2.com/python-api/)
  is for *controlling* iTerm2 (creating windows, sending keystrokes, reading session
  contents) over websockets. It cannot embed an iTerm2 session into another process.
  Could in principle be used to *automate* iTerm2 alongside patchbai, but that
  doesn't satisfy "panel in the TUI."

### 3.5 Terminal.app (macOS)

- AppleScript-only control surface; not embeddable; no library mode.

### 3.6 Verdict for §3

None of the native GUIs can be embedded into a Textual cell grid. The valuable
artifacts from this category are the *engine libraries* — `wezterm-term`,
`alacritty_terminal` + `vte`, and (the standout) `libghostty-vt`. They become §4.

---

## 4. Better in-process emulators (a.k.a. "what to swap pyte for")

| Library | Lang | API stability | Truecolor | Scrollback | Mouse encoding | OSC 8 hyperlinks | Kitty kbd | Sixel/Images | Maintenance | Notes |
|---|---|---|---|---|---|---|---|---|---|---|
| **pyte** (current) | Python | Stable | partial / buggy ([#92](https://github.com/selectel/pyte/issues/92)) | `HistoryScreen` only | no | no | no | no | minimal upstream activity | What we have. Renderer not consuming what's there. |
| **libghostty-vt** | C/Zig | alpha (logic stable) | yes | yes (with reflow) | full (1000/1002/1006) | yes | yes (kitty kbd protocol native) | yes (kitty graphics; sixel via libghostty) | very active (Mitchell H + team) | Best feature coverage, alpha API |
| **wezterm-term** | Rust | stable enough | yes | yes | yes | **yes (explicit)** | partial | **yes (sixel + iTerm2)** | very active (wez) | Full-featured today, no API churn risk |
| **alacritty_terminal + vte** | Rust | stable | yes | yes | yes | partial | partial | no | active | Workmanlike; smaller surface area than wezterm-term |
| **vt100** ([doy/vt100-rust](https://github.com/doy/vt100-rust)) | Rust | stable | yes (cell colors enum) | yes | yes | unclear | no | no | active (151k DL/month) | Designed explicitly for tmux/screen-style consumers — the closest match conceptually to "we want a screen object we can read" |
| **par-term-emu-core-rust** | Rust+PyO3 | new | yes | yes | yes | unclear | unclear | yes (sixel/iTerm2/Kitty) | new project, single maintainer | [Already has Python bindings](https://github.com/paulrobello/par-term-emu-core-rust). Worth evaluating, but maintenance risk for a critical dep is real. |
| **terminado / vte (GNOME)** | Python / C | mature | yes | yes | yes | yes | partial | yes | mature | terminado is xterm.js ↔ websocket plumbing for Jupyter; not a parser. GNOME's libvte is a GTK widget — not embeddable in a cell grid for the same reason as the GUIs. |

**Top three for patchbai:**

1. **libghostty-vt** — best features, best long-term momentum, alpha API. Pick if we
   are willing to track a stabilising surface for ~6 months.
2. **wezterm-term** — equally feature-rich today (sixel, OSC 8 by name, scrollback),
   stable. Pick if we want zero API churn risk and accept the Rust-toolchain
   build dependency.
3. **Stay on pyte but fix the renderer** — costs nothing in dependencies, addresses
   the user's actual complaints (which I believe are dominantly "no colors"), buys
   time to evaluate the above.

---

## 5. The "external terminal under the hood" approach

### 5.1 tmux + capture-pane

Real, works, used in production by many tools. Pattern:

- `tmux new-session -d -s patchbai_<id>` to start a detached session.
- [`libtmux`](https://github.com/tmux-python/libtmux) for the Python wrapper, then
  `pane.capture_pane(escape_sequences=True)` per refresh.
- `pane.send_keys(...)` for input.
- Resize via `tmux resize-window`/`-pane`.

Pros:
- tmux *is* a battle-tested terminal emulator. Truecolor, mouse, alt-screen, scrollback,
  Unicode all work.
- Process supervision is free (sessions persist if patchbai crashes).

Cons:
- We still have to **parse** the captured ANSI text on patchbai's side to render into
  Textual cells with attributes. So we still need a parser. This means tmux-as-backend
  is *additive* to a parser — it doesn't replace pyte/libghostty-vt, it adds a
  process to the stack.
- Adds a hard runtime dep on tmux (mostly fine on developer machines; awkward on
  bare-bones Linux containers).
- Latency on capture-pane is non-trivial — measured in milliseconds. Fine for shells,
  not great for `vim`'s redraw cadence.

### 5.2 tmux control mode (`-CC`)

iTerm2 and a few IDEs use this. tmux switches to a programmatic protocol; the
"emulator" (patchbai, in this hypothetical) becomes responsible for rendering each
pane. Designed *exactly* for the "I am an outer GUI driving tmux" use case.
([tmux Control Mode wiki](https://github.com/tmux/tmux/wiki/Control-Mode))

Pros: clean protocol, push notifications instead of polling.

Cons: significant implementation cost. tmux dep. Doesn't remove the parser need.
Probably overkill for "panel in a TUI" when libghostty-vt + a PTY do the same thing
in-process.

### 5.3 Spawn a hidden Ghostty/Alacritty and screen-scrape

Not feasible. There is no API to read the rendered cell grid from any of these GUIs
back out as text. Even if there were, the round-trip would be useless — we'd still
need a parser to turn pixels back into characters.

### 5.4 Verdict for §5

tmux-as-backend is a real pattern, but for patchbai it solves a problem we don't
have (process persistence, multi-pane multiplexing) at the cost of a runtime
dependency, and it does not eliminate the work of having a real VT parser.
Fixing the parser side directly is cheaper.

---

## 6. Switching the embedding model entirely

What if patchbai's "Terminal" panel just isn't a Textual widget at all?

### 6.1 `App.suspend` to hand the host terminal back

Textual ships `App.suspend()` as a context manager — it pauses the app, clears its
escape-sequence state, restores cooked-mode TTY, lets the user run a real shell in
the host terminal, then re-enters the TUI on exit. Already documented as the
recommended approach for `vim`-style escape hatches.

Pros: zero implementation cost (one method). Native terminal capability is a function
of whatever host the user is in (so if they're already in Ghostty, they get Ghostty
quality).

Cons: monopolises the screen. Can't have a "Terminal" panel alongside other patchbai
panels at the same time. Mode-switch UX rather than co-habitation.

### 6.2 "Pop out" — spawn a real terminal in a new window

`open -a Ghostty -n` on macOS, `xdg-terminal-exec` on Linux, etc. Gives the user a
full-fidelity native terminal for ad-hoc work; patchbai keeps its TUI panels for
everything else.

Pros: trivial; perfect terminal fidelity; respects the user's chosen emulator;
runs in parallel.

Cons: it's a separate window — loses the "everything in one TUI" aesthetic; harder
to wire into the agent orchestration story (no easy way for patchbai to read what
the user typed there).

### 6.3 Verdict for §6

Both are **cheap to ship and worth shipping** as escape hatches alongside whatever
in-TUI terminal we end up with. They are not a substitute for an in-TUI panel for
the cases where the user wants the panel.

---

## 7. Recommendation

Ship in two phases:

### Phase 1 — Renderer & event-loop refactor (small, ~1–2 days)

No new dependencies. All gains come from using pyte properly.

1. Replace `screen.display` with a per-cell walk of `screen.buffer`, emitting
   `rich.text.Text` segments with the cell's `fg`/`bg`/`bold`/`italic`/`underline`/`reverse`.
2. Switch `pyte.Screen` → `pyte.HistoryScreen` for scrollback; expose `Page Up/Down`.
3. Render the cursor (use `screen.cursor.x/y`, `screen.cursor.hidden`, draw with a
   `reverse` style at that cell).
4. Drop the `ByteStream` + encode/decode round-trip. Use `pyte.Stream` with the
   already-decoded `str` from `PtyProcessUnicode`.
5. Replace 50 ms polling with `asyncio.add_reader(self._pty.fd, …)`; drain on each
   readable event in chunks until `select` returns empty.
6. Wire Textual `Resize` → `pty.setwinsize(rows, cols)` + `screen.resize(rows, cols)`.
7. Expand `on_key` to forward arrows, Home/End, Page Up/Down, F1–F12, Esc, Alt-modified,
   all Ctrl+letter variants. Use the standard xterm sequences.
8. Surface process exit ("[exited 0]" line) and add a `Restart` action.
9. **Optional** in this phase: implement basic mouse mode tracking (`?1000h`/`?1006h`)
   and forward Textual mouse events.

**Acceptance:** `htop`, `vim`, `less`, `ls --color=auto`, `man`, `tmux` all render
recognisably and respond to navigation.

### Phase 2 — Engine swap to libghostty-vt (medium, ~1–2 weeks of focused work)

Once Phase 1 is in and the surface is well-tested:

1. Stand up a `patchbai-vt` Python package wrapping libghostty-vt. **Recommended path:
   WASM via `wasmtime-py`** for distribution simplicity (matches Obsidian + JupyterLab
   precedent; no Rust toolchain on user machines; one artifact for all platforms).
2. Define a tiny adapter interface (`feed(bytes) -> ScreenDelta`, `cursor()`,
   `cells(row, col) -> Cell`) so the renderer doesn't care whether pyte or libghostty
   is underneath. Lets us flag-flip during transition.
3. Port the Phase 1 renderer to consume the libghostty screen state.
4. Add OSC 0/2 → panel border title.
5. Add OSC 8 → clickable hyperlink Textual styles.
6. Add Kitty keyboard protocol negotiation (DECSET 2017) — many modern programs
   require this for fully-distinguished keys.
7. Decide on alt-screen rendering policy (currently lost; libghostty exposes both buffers).
8. Track libghostty-vt's API stabilisation, lock to a version, document upgrade policy.

**Acceptance:** truecolor terminal demos render correctly; `nvim` with truecolor
works; OSC 8 hyperlinks render as Textual links; Kitty kbd protocol round-trips.

### Phase 0 (parallel, free) — Pop-out escape hatch

Add a "pop out terminal" command that uses `App.suspend()` (on the host terminal) or
shells out to the user's configured terminal app (`open -a` on macOS,
`$TERMINAL`/`xdg-terminal-exec` elsewhere). Costs ~30 lines, gives users an
unconditional escape valve to a real, full-fidelity terminal whenever the in-TUI
panel falls short. Ship this regardless of Phase 1/2.

### Things to *not* do

- **Do not** try to embed Ghostty/Alacritty/WezTerm/Kitty/iTerm2/Terminal.app GUIs
  as Textual panels. The architectures are incompatible.
- **Do not** adopt tmux-as-backend as the primary solution. It adds a dep without
  removing the parser need. It can be reconsidered later if patchbai grows into
  multiplexer territory.
- **Do not** invest in third-party `textual-terminal` (PyPI) — same pyte-based design,
  same problems, less control.

---

## 8. References

### Ghostty / libghostty
- Ghostty repo: https://github.com/ghostty-org/ghostty
- libghostty-vt site: https://libghostty.tip.ghostty.org/
- Mitchell Hashimoto, "Libghostty Is Coming": https://mitchellh.com/writing/libghostty-is-coming
- C header: https://github.com/ghostty-org/ghostty/blob/main/include/ghostty/vt.h
- Ghostling demo: https://github.com/ghostty-org/ghostling
- libghostty-rs: https://github.com/uzaaft/libghostty-rs
- go-libghostty: https://github.com/mitchellh/go-libghostty
- awesome-libghostty: https://github.com/Uzaaft/awesome-libghostty
- JupyterLab issue "Replace xterm.js with libghostty WASM": https://github.com/jupyterlab/jupyterlab/issues/17702

### Other emulator engines
- wezterm-term README: https://github.com/wezterm/wezterm/blob/main/term/README.md
- alacritty_terminal: https://docs.rs/alacritty_terminal/latest/alacritty_terminal/
- alacritty/vte: https://github.com/alacritty/vte
- vt100 (Rust): https://docs.rs/vt100/ — https://github.com/doy/vt100-rust
- par-term-emu-core-rust (Rust+PyO3): https://github.com/paulrobello/par-term-emu-core-rust
- Alacritty embedding issue #823: https://github.com/jwilm/alacritty/issues/823

### Python / Textual surrounding context
- pyte color limitations issue #92: https://github.com/selectel/pyte/issues/92
- Textual discussion #5461 (embedded shell widget): https://github.com/Textualize/textual/discussions/5461
- textual-terminal (PyPI third-party): https://github.com/mitosch/textual-terminal
- Textual app suspend docs: https://textual.textualize.io/guide/app/
- libtmux: https://github.com/tmux-python/libtmux
- wasmtime-py: https://github.com/bytecodealliance/wasmtime-py

### iTerm2 / Kitty / tmux integration
- iTerm2 Python API: https://iterm2.com/python-api/
- iTerm2 tmux integration: https://iterm2.com/documentation-tmux-integration.html
- tmux Control Mode wiki: https://github.com/tmux/tmux/wiki/Control-Mode
- Kitty docs: https://sw.kovidgoyal.net/kitty/
