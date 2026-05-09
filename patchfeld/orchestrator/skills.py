"""Discovery + indexing of locally-installed Claude Code skills.

A "skill" lives at one of:

  - `~/.claude/skills/<name>/SKILL.md`
      User-installed skills. Highest priority on collisions.
  - `~/.claude/plugins/cache/<plugin>/<version>/skills/<name>/SKILL.md`
      Plugin-shipped skills. Walked second; first occurrence per bare name
      wins (subsequent duplicates log a `collision` warning).

The orchestrator uses this index to expose `/<skill-name>` slash commands in
the chat input. Discovery is performed once at orchestrator-session start
(see `OrchestratorSession.__init__`) — re-scans require a restart, same as
the existing widget loader. Per the implementation plan we picked design (a)
"translate": when the user types `/<skill> <args>`, the orchestrator
synthesizes a prose prompt that nudges the LLM to invoke the matching `Skill`
tool. Discovery only needs the skill's *name* (not the body) — we never read
SKILL.md content here.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

log = logging.getLogger(__name__)

# Reused for the slash-command dispatch — only names matching this pattern
# are exposed. Keeping it tight avoids weird shell-escaping edge cases. The
# orchestrator's _SKILL_RE is anchored on this same alphabet.
_SKILL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]*$")


@dataclass(frozen=True)
class SkillEntry:
    """A discovered skill the orchestrator can route slash commands to."""

    name: str
    """Bare skill name (the directory name, e.g. `kb-query`)."""

    path: str
    """Absolute path to the `SKILL.md` file. Stored for diagnostics; we don't
    need to read the body to invoke a skill via the `Skill` tool."""

    source: str
    """Where the skill was found: `"user"` for `~/.claude/skills/<name>` or
    `"plugin"` for `~/.claude/plugins/cache/.../<name>`. Used for logging
    and for collision-resolution rules (user wins)."""


@dataclass
class SkillsIndex:
    """Bare-name → SkillEntry, populated by `discover_skills`."""

    entries: dict[str, SkillEntry] = field(default_factory=dict)

    def get(self, name: str) -> SkillEntry | None:
        return self.entries.get(name)

    def names(self) -> list[str]:
        """Lexicographically-sorted skill names. Used by `/help` so output is
        deterministic regardless of filesystem walk order."""
        return sorted(self.entries.keys())

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self.entries


def discover_skills(
    *,
    user_skills_dir: Path | None,
    plugin_cache_dir: Path | None,
    builtin_command_names: Iterable[str] = (),
) -> SkillsIndex:
    """Walk known locations, return a `SkillsIndex`.

    Parameters
    ----------
    user_skills_dir : Path | None
        Typically `~/.claude/skills`. If None or missing, skipped.
    plugin_cache_dir : Path | None
        Typically `~/.claude/plugins/cache`. If None or missing, skipped.
    builtin_command_names : iterable of str
        Names of orchestrator-internal slash commands (e.g. `cd`, `help`).
        These do NOT cause the skill to be excluded — built-ins win at
        dispatch time and the skill remains reachable via prose. We just
        log a warning here so it's visible in production logs.

    Notes
    -----
    - Each skill must live in its own directory containing a `SKILL.md` file.
      We do not read the SKILL.md frontmatter — Claude Code's `Skill` tool
      uses the directory name as the canonical identifier, and that's what
      the orchestrator passes through.
    - Walk order: user dir first (so user-installed copies win on collision),
      then plugin cache. The plugin walk is bounded to depth 4 from the
      cache root: `<plugin>/<version>/skills/<name>/SKILL.md`.
    - Plugin-namespaced skills (e.g. `Notion:search`) are exposed under
      their bare name (`search`). On collisions the first occurrence wins
      — typically driven by directory iteration order, which is filesystem
      dependent. A warning identifies the loser so a user can rename if
      they care which copy is reachable via slash.
    """
    builtin_set = set(builtin_command_names)
    entries: dict[str, SkillEntry] = {}

    def _try_add(name: str, path: Path, source: str) -> None:
        if not _SKILL_NAME_RE.match(name):
            log.debug("skipping skill with non-slash-safe name: %r at %s",
                      name, path)
            return
        if name in entries:
            existing = entries[name]
            log.warning(
                "skill name collision: %r (kept %s copy at %s; "
                "ignored %s copy at %s)",
                name, existing.source, existing.path, source, path,
            )
            return
        if name in builtin_set:
            log.warning(
                "skill name %r collides with a built-in slash command; the "
                "built-in wins at dispatch but the skill remains reachable "
                "via prose. (path=%s, source=%s)",
                name, path, source,
            )
        entries[name] = SkillEntry(
            name=name, path=str(path), source=source,
        )

    # --- user dir ---------------------------------------------------------
    if user_skills_dir is not None and user_skills_dir.is_dir():
        for child in sorted(user_skills_dir.iterdir()):
            if not child.is_dir():
                continue
            skill_md = child / "SKILL.md"
            if not skill_md.is_file():
                continue
            _try_add(child.name, skill_md, source="user")

    # --- plugin cache -----------------------------------------------------
    if plugin_cache_dir is not None and plugin_cache_dir.is_dir():
        # Layout: <cache>/<vendor>/<plugin>/<version>/skills/<skill_name>/SKILL.md
        # Example: ~/.claude/plugins/cache/claude-plugins-official/superpowers/
        #          5.1.0/skills/writing-plans/SKILL.md
        # Per-plugin we pick the *highest lexicographic version directory*
        # — for typical semver this lines up with the latest release (5.1.0
        # > 5.0.7 lexicographically). Edge cases (5.10.0 vs 5.2.0) sort
        # incorrectly under this rule; that's a known v1 limitation, and
        # the workaround is to delete stale cache versions.
        for vendor_dir in sorted(plugin_cache_dir.iterdir()):
            if not vendor_dir.is_dir():
                continue
            for plugin_dir in sorted(vendor_dir.iterdir()):
                if not plugin_dir.is_dir():
                    continue
                version_dirs = sorted(
                    [d for d in plugin_dir.iterdir() if d.is_dir()],
                )
                if not version_dirs:
                    continue
                # Highest version wins.
                version_dir = version_dirs[-1]
                skills_root = version_dir / "skills"
                if not skills_root.is_dir():
                    continue
                for skill_dir in sorted(skills_root.iterdir()):
                    if not skill_dir.is_dir():
                        continue
                    skill_md = skill_dir / "SKILL.md"
                    if not skill_md.is_file():
                        continue
                    _try_add(skill_dir.name, skill_md, source="plugin")

    log.info("discovered %d skill(s): %s",
             len(entries), sorted(entries.keys()))
    return SkillsIndex(entries=entries)


def default_skills_index(builtin_command_names: Iterable[str] = ()) -> SkillsIndex:
    """Convenience wrapper that scans the canonical user paths.

    Used by production wiring; tests pass a hand-built `SkillsIndex` instead.
    """
    home = Path.home()
    return discover_skills(
        user_skills_dir=home / ".claude" / "skills",
        plugin_cache_dir=home / ".claude" / "plugins" / "cache",
        builtin_command_names=builtin_command_names,
    )
