from patchbai.widgets._terminal_keys import encode_key


def _enc(key: str, character: str | None = None) -> bytes | None:
    return encode_key(key, character)


# --- printable and basic editing ---

def test_printable_letter():
    assert _enc("a", "a") == b"a"


def test_printable_unicode():
    assert _enc("ñ", "ñ") == "ñ".encode("utf-8")


def test_enter_emits_carriage_return():
    assert _enc("enter", None) == b"\r"


def test_tab():
    assert _enc("tab", None) == b"\t"


def test_shift_tab():
    assert _enc("shift+tab", None) == b"\x1b[Z"


def test_backspace():
    assert _enc("backspace", None) == b"\x7f"


def test_escape():
    assert _enc("escape", None) == b"\x1b"


def test_space():
    assert _enc("space", " ") == b" "


# --- arrows ---

def test_arrows():
    assert _enc("up", None) == b"\x1b[A"
    assert _enc("down", None) == b"\x1b[B"
    assert _enc("right", None) == b"\x1b[C"
    assert _enc("left", None) == b"\x1b[D"


# --- navigation ---

def test_home_end():
    assert _enc("home", None) == b"\x1b[H"
    assert _enc("end", None) == b"\x1b[F"


def test_pageup_pagedown():
    assert _enc("pageup", None) == b"\x1b[5~"
    assert _enc("pagedown", None) == b"\x1b[6~"


def test_insert_delete():
    assert _enc("insert", None) == b"\x1b[2~"
    assert _enc("delete", None) == b"\x1b[3~"


# --- F-keys ---

def test_f1_through_f4():
    assert _enc("f1", None) == b"\x1bOP"
    assert _enc("f2", None) == b"\x1bOQ"
    assert _enc("f3", None) == b"\x1bOR"
    assert _enc("f4", None) == b"\x1bOS"


def test_f5_through_f12():
    assert _enc("f5", None) == b"\x1b[15~"
    assert _enc("f6", None) == b"\x1b[17~"
    assert _enc("f7", None) == b"\x1b[18~"
    assert _enc("f8", None) == b"\x1b[19~"
    assert _enc("f9", None) == b"\x1b[20~"
    assert _enc("f10", None) == b"\x1b[21~"
    assert _enc("f11", None) == b"\x1b[23~"
    assert _enc("f12", None) == b"\x1b[24~"


# --- Ctrl combos ---

def test_ctrl_letters_a_through_z():
    for i, ch in enumerate("abcdefghijklmnopqrstuvwxyz"):
        assert _enc(f"ctrl+{ch}", None) == bytes([i + 1]), f"ctrl+{ch}"


def test_ctrl_space_is_null():
    assert _enc("ctrl+space", None) == b"\x00"


def test_ctrl_backslash():
    assert _enc("ctrl+backslash", None) == b"\x1c"


# --- Alt combos ---

def test_alt_letter_prefixes_with_esc():
    assert _enc("alt+a", "a") == b"\x1ba"


def test_alt_arrow():
    assert _enc("alt+up", None) == b"\x1b\x1b[A"


# --- unhandled ---

def test_unknown_key_returns_none():
    assert _enc("super+f", None) is None


def test_no_character_for_printable_key_returns_none():
    # A key string we don't recognize and no character — drop it.
    assert _enc("nonsense_key", None) is None


def test_ctrl_at_is_null_alias_for_ctrl_space():
    assert _enc("ctrl+at", None) == b"\x00"


def test_ctrl_right_square_bracket():
    assert _enc("ctrl+right_square_bracket", None) == b"\x1d"


def test_ctrl_slash():
    assert _enc("ctrl+slash", None) == b"\x1f"


def test_ctrl_underscore():
    assert _enc("ctrl+underscore", None) == b"\x1f"


def test_ctrl_question_mark():
    assert _enc("ctrl+question_mark", None) == b"\x7f"


def test_alt_function_key():
    # alt+f5 should be ESC followed by the F5 sequence.
    assert _enc("alt+f5", None) == b"\x1b\x1b[15~"


def test_alt_space():
    # alt+space should be ESC + space.
    assert _enc("alt+space", None) == b"\x1b "


def test_ctrl_non_ascii_letter_returns_none():
    # Tightened: "ctrl+ñ" used to return junk byte 137; now should return None.
    assert _enc("ctrl+ñ", None) is None


def test_empty_character_string_returns_none():
    # If Textual ever delivers an empty character string for an unknown key,
    # we should drop it rather than emitting empty bytes.
    assert _enc("nonsense_key", "") is None
