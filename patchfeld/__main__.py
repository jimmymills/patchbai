import argparse
import sys

from patchfeld.app import PatchfeldApp


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="patchfeld",
        description="Multi-agent Textual TUI for Claude Agent SDK.",
    )
    parser.add_argument(
        "--bypass-permissions",
        action="store_true",
        help=(
            "Run all sessions (orchestrator + child agents) with "
            "permission_mode=bypassPermissions. Default behavior is to "
            "ask for confirmation via a Textual modal before every "
            "tool call."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    PatchfeldApp(bypass_permissions=args.bypass_permissions).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
