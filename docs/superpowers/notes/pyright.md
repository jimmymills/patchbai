# Pyright in `patchbai`

## TL;DR — How to run pyright

```bash
./scripts/typecheck.sh                  # full project
./scripts/typecheck.sh patchbai/widgets  # one subtree
./scripts/typecheck.sh --watch          # watch mode
```

The script always runs `uv sync --extra dev` first, then `uv run pyright`. Use
it instead of calling pyright directly — that's what makes runs deterministic
across worktrees, agents, and machines.

If you must call pyright manually (e.g. from your editor), the equivalent is:

```bash
uv sync --extra dev
uv run pyright [args]
```

## Recovering from "stale cache" symptoms

Symptom: pyright reports errors that don't match the current source — for
example, dozens of `Import "pytest" could not be resolved` in `tests/`, or
errors against attributes/types that you've already fixed.

**This is almost never an actual stale cache.** Pyright's CLI does not keep a
persistent on-disk AST cache between invocations; each run is fresh.

The real cause is almost always **an incomplete `.venv`**. Recovery, in order:

1. **Sync dev extras.** This is the actual fix 99% of the time:
   ```bash
   uv sync --extra dev
   ```
   Worktrees and fresh checkouts don't have `.venv/`, and `uv run pyright`
   only installs the project's base dependencies — not the `dev` extras
   (pytest, pytest-asyncio, pyright itself). Pyright then can't resolve those
   imports and reports phantom errors that look like a stale cache.

2. **Verify `uv run pyright` is using the venv pyright, not a system pyright:**
   ```bash
   uv run which pyright
   # → .../<repo>/.venv/bin/pyright   ✓
   # → /opt/homebrew/bin/pyright      ✗ (uv fell through to PATH)
   ```
   If it's falling through to PATH, `pyright` isn't in `[project.optional-dependencies] dev` — re-add it.

3. **As a true belt-and-suspenders only**, clear the user-level cache:
   ```bash
   rm -rf ~/.cache/pyright
   # or, equivalently:
   ./scripts/typecheck.sh --clear-cache
   ```
   Note: pyright's CLI does **not** have a `--clearcache` flag (despite some
   stale advice on the internet). The only on-disk cache knob is the
   `~/.cache/pyright` directory. In practice it rarely even exists on a
   given machine, and clearing it has never been the fix in this repo. Keep
   this in your back pocket but don't reach for it first.

## Root cause investigation (May 2026)

Several agents reported that pyright surfaced "stale" errors that didn't match
the source. Systematic debugging in worktree `patchbai-pyright-cache` showed
the symptom was a misdiagnosis. Findings:

- **There is no persistent on-disk AST cache.** An edit-and-retest experiment
  (inject `-> int` into a string-returning function, run pyright, revert,
  re-run) showed pyright catches the error and clears it on revert with no
  caching artifacts. Each invocation is deterministic.
- **`~/.cache/pyright/` did not exist** on the affected machine, ruling out
  the user-level cache as a vector.
- **The actual driver was an incomplete `.venv`.** `uv run pyright` was
  bootstrapping a venv with only the base project deps; `dev` extras
  (pytest, pytest-asyncio) were absent. That produced 79 phantom
  `Import "X" could not be resolved` errors against `tests/` — exactly the
  "errors that don't match the source" symptom. After `uv sync --extra dev`
  the count dropped from 169 → 90, and the remaining 90 were all real,
  pre-existing type errors.
- **Pyright was not pinned in the project.** `uv run pyright` was falling
  through to `/opt/homebrew/bin/pyright` (1.1.399). On a different machine
  it could be a different version, missing entirely, or a different build —
  enough drift to produce machine-dependent error sets that compounded the
  "stale" feeling.

## Fix

1. **Pinned `pyright>=1.1.395,<1.2` in `[project.optional-dependencies] dev`.**
   Now `uv sync --extra dev` installs pyright into `.venv/bin/pyright`, and
   `uv run pyright` resolves to the venv-local binary. No more PATH fallthrough,
   no more version drift between machines.
2. **Added `scripts/typecheck.sh`** that runs `uv sync --extra dev` before
   pyright. This makes the venv-completeness step impossible to forget.
3. **Documented the recovery runbook** above.

## Known pre-existing issues (not addressed by this fix)

- `[tool.pyright].pythonVersion = "3.11"` does not match the actual venv
  Python (`uv` resolves to 3.12.10 on this machine because `requires-python`
  has no upper bound). Pyright analyzes against 3.11 stdlib stubs while the
  interpreter is 3.12. This causes minor stub-vs-runtime drift but is not
  the source of the phantom-errors symptom. If you want to fix it, either
  bump `pythonVersion` to `"3.12"` or pin `requires-python = ">=3.11,<3.12"`.
- 89 genuine type errors remain in the project after the fix. These are
  pre-existing and unrelated to the cache investigation; addressing them is
  a separate piece of work.
- Shell `VIRTUAL_ENV` set to a different venv (e.g. the main repo's `.venv`
  while you're in a worktree) produces a noisy uv warning but does not affect
  results — uv ignores it.
