"""Token estimates for a CJK/ASCII mixed prompt budget.

Upstream models publish their windows in tokens while the prompt assembler
trims in characters, and no tokenizer ships with either side. Every number the
settings UI shows goes through this module so the two views cannot drift apart.
"""

from __future__ import annotations

from math import ceil


# Mixed Chinese/English companion text lands between the two per-script rates,
# so a single factor converts a user-set token window into the char budget.
CHARS_PER_TOKEN = 2.0

_CJK_RANGES = (
    (0x2000, 0x206F),  # general punctuation: em dash, curly quotes
    (0x3000, 0x303F),  # CJK symbols and punctuation
    (0x3400, 0x4DBF),
    (0x4E00, 0x9FFF),
    (0xAC00, 0xD7AF),
    (0xF900, 0xFAFF),
    (0xFF00, 0xFFEF),  # fullwidth forms
)

_ASCII_TOKEN_CHARS = 4


def _is_cjk(char: str) -> bool:
    code = ord(char)
    for low, high in _CJK_RANGES:
        if low <= code <= high:
            return True
    return False


def estimate_tokens(text) -> int:
    cjk = 0
    other = 0
    for char in str(text or ""):
        if _is_cjk(char):
            cjk += 1
        else:
            other += 1
    return cjk + int(ceil(other / _ASCII_TOKEN_CHARS))


def tokens_to_chars(tokens) -> int:
    return int(round(max(0, int(tokens or 0)) * CHARS_PER_TOKEN))


def chars_to_tokens(chars) -> int:
    return int(ceil(max(0, int(chars or 0)) / CHARS_PER_TOKEN))
