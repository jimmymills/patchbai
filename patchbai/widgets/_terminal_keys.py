"""Pure mapping from Textual key events to xterm-compatible byte sequences.

Default xterm cursor-key mode (no DECCKM application mode) is assumed —
that's what real shells expect by default. If we later support DECCKM,
we'll route through here too.
"""

from __future__ import annotations

ESC = b"\x1b"

_SIMPLE: dict[str, bytes] = {
    "enter": b"\r",
    "tab": b"\t",
    "shift+tab": b"\x1b[Z",
    "backspace": b"\x7f",
    "escape": b"\x1b",
    "up": b"\x1b[A",
    "down": b"\x1b[B",
    "right": b"\x1b[C",
    "left": b"\x1b[D",
    "home": b"\x1b[H",
    "end": b"\x1b[F",
    "pageup": b"\x1b[5~",
    "pagedown": b"\x1b[6~",
    "insert": b"\x1b[2~",
    "delete": b"\x1b[3~",
    "f1": b"\x1bOP",
    "f2": b"\x1bOQ",
    "f3": b"\x1bOR",
    "f4": b"\x1bOS",
    "f5": b"\x1b[15~",
    "f6": b"\x1b[17~",
    "f7": b"\x1b[18~",
    "f8": b"\x1b[19~",
    "f9": b"\x1b[20~",
    "f10": b"\x1b[21~",
    "f11": b"\x1b[23~",
    "f12": b"\x1b[24~",
}

_CTRL_NAMED: dict[str, bytes] = {
    "ctrl+space": b"\x00",
    "ctrl+at": b"\x00",
    "ctrl+backslash": b"\x1c",
    "ctrl+right_square_bracket": b"\x1d",
    "ctrl+slash": b"\x1f",
    "ctrl+underscore": b"\x1f",
    "ctrl+question_mark": b"\x7f",
}


def encode_key(key: str, character: str | None) -> bytes | None:
    """Map a Textual key+character to xterm-style bytes; None if unhandled.

    Args:
        key: Textual's key descriptor (e.g. "up", "ctrl+c", "alt+x", "f5").
        character: The typed character if any (Textual provides this for
            printable keys including Unicode).
    """
    # Alt+X → ESC + (recursively encoded X).
    if key.startswith("alt+"):
        rest = key[len("alt+") :]
        sub = encode_key(rest, character if rest == character else None)
        if sub is None and character is not None:
            sub = character.encode("utf-8")
        return None if sub is None else ESC + sub

    if key in _SIMPLE:
        return _SIMPLE[key]

    if key.startswith("ctrl+"):
        suffix = key[len("ctrl+") :]
        if len(suffix) == 1 and suffix.isalpha():
            return bytes([ord(suffix.lower()) - ord("a") + 1])
        if key in _CTRL_NAMED:
            return _CTRL_NAMED[key]
        return None

    if key == "space" and character == " ":
        return b" "

    if character is not None and len(character) >= 1:
        return character.encode("utf-8")

    return None
