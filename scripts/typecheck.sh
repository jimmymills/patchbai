#!/usr/bin/env bash
# Run pyright with a guaranteed-complete venv.
#
# This script is the canonical way to typecheck mod_tui. It ensures the
# `dev` extras (pytest, pyright, etc.) are synced before running pyright,
# which is what avoids the "phantom errors look like a stale cache" trap.
#
# Optional flags:
#   --clear-cache    rm -rf the user-level pyright cache at ~/.cache/pyright
#                    (almost never needed; pyright's CLI does not expose a
#                    --clearcache option, so this is the only on-disk cache
#                    knob that exists. Belt-and-suspenders recovery only.)
#
# All other args are forwarded to pyright. Examples:
#   ./scripts/typecheck.sh                       # full project
#   ./scripts/typecheck.sh mod_tui/widgets       # just one tree
#   ./scripts/typecheck.sh --watch               # watch mode
#   ./scripts/typecheck.sh --clear-cache         # nuke cache then typecheck
set -euo pipefail

cd "$(dirname "$0")/.."

CLEAR_CACHE=0
PYRIGHT_ARGS=()
for arg in "$@"; do
  case "$arg" in
    --clear-cache)
      CLEAR_CACHE=1
      ;;
    *)
      PYRIGHT_ARGS+=("$arg")
      ;;
  esac
done

if [[ "$CLEAR_CACHE" == "1" ]]; then
  rm -rf "${HOME}/.cache/pyright" 2>/dev/null || true
  # Note: pyright CLI has no --clearcache flag; clearing ~/.cache/pyright is
  # the only on-disk cache knob, and we just did that.
fi

# Always sync dev extras before invoking pyright. This is the actual fix for
# the "stale cache" symptom: phantom Import errors come from missing dev deps
# in `.venv`, not from pyright caching.
uv sync --extra dev

# `${PYRIGHT_ARGS[@]+...}` guards against `set -u` complaining when the array
# is empty (no extra args passed). With `set -u`, plain `"${PYRIGHT_ARGS[@]}"`
# on an empty array errors out with "unbound variable".
exec uv run pyright ${PYRIGHT_ARGS[@]+"${PYRIGHT_ARGS[@]}"}
