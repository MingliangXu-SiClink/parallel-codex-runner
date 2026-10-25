"""Unicode cursor and word-boundary helpers used by text editors."""

from __future__ import annotations

import unicodedata
from bisect import bisect_left, bisect_right
from functools import lru_cache
from typing import Iterator


_ZWJ = "\u200d"


def _is_variation_selector(character: str) -> bool:
    codepoint = ord(character)
    return 0xFE00 <= codepoint <= 0xFE0F or 0xE0100 <= codepoint <= 0xE01EF


def _is_emoji_modifier(character: str) -> bool:
    return 0x1F3FB <= ord(character) <= 0x1F3FF


def _is_regional_indicator(character: str) -> bool:
    return 0x1F1E6 <= ord(character) <= 0x1F1FF


def _extends_grapheme(character: str) -> bool:
    codepoint = ord(character)
    return (
        unicodedata.category(character) in {"Mn", "Mc", "Me"}
        or character == "\u200c"
        or _is_variation_selector(character)
        or _is_emoji_modifier(character)
        or 0xE0020 <= codepoint <= 0xE007F
    )


@lru_cache(maxsize=4096)
def grapheme_boundaries(text: str) -> tuple[int, ...]:
    """Return practical extended-grapheme boundaries for terminal editing.

    This covers combining marks, variation selectors, emoji modifiers, ZWJ
    sequences, regional-indicator pairs, and CRLF. It intentionally has no
    third-party dependency and keeps cursor/delete operations from splitting
    the sequences commonly emitted by IMEs.
    """
    if not text:
        return (0,)

    boundaries = [0]
    regional_run = 1 if _is_regional_indicator(text[0]) else 0

    for index in range(1, len(text)):
        previous = text[index - 1]
        character = text[index]
        regional = _is_regional_indicator(character)
        joins_previous = (
            (previous == "\r" and character == "\n")
            or _extends_grapheme(character)
            or character == _ZWJ
            or previous == _ZWJ
            or (
                regional
                and _is_regional_indicator(previous)
                and regional_run % 2 == 1
            )
        )

        if not joins_previous:
            boundaries.append(index)

        if regional:
            regional_run = regional_run + 1 if _is_regional_indicator(previous) else 1
        elif not _extends_grapheme(character) and character != _ZWJ:
            regional_run = 0

    boundaries.append(len(text))
    return tuple(boundaries)


def iter_graphemes(text: str) -> Iterator[tuple[int, int, str]]:
    """Yield ``(start, end, grapheme)`` tuples for *text*."""
    boundaries = grapheme_boundaries(text)
    for start, end in zip(boundaries, boundaries[1:]):
        yield start, end, text[start:end]


def previous_grapheme_boundary(text: str, index: int) -> int:
    """Return the grapheme boundary immediately before *index*."""
    index = max(0, min(index, len(text)))
    boundaries = grapheme_boundaries(text)
    return boundaries[max(0, bisect_left(boundaries, index) - 1)]


def next_grapheme_boundary(text: str, index: int) -> int:
    """Return the grapheme boundary immediately after *index*."""
    index = max(0, min(index, len(text)))
    boundaries = grapheme_boundaries(text)
    return boundaries[min(len(boundaries) - 1, bisect_right(boundaries, index))]


_CJK_RANGES = (
    (0x1100, 0x11FF),
    (0x2E80, 0x2FFF),
    (0x3040, 0x30FF),
    (0x3100, 0x318F),
    (0x31A0, 0x31BF),
    (0x31C0, 0x31EF),
    (0x31F0, 0x31FF),
    (0x3400, 0x4DBF),
    (0x4E00, 0x9FFF),
    (0xA960, 0xA97F),
    (0xAC00, 0xD7AF),
    (0xF900, 0xFAFF),
    (0x20000, 0x323AF),
)


@lru_cache(maxsize=2048)
def _is_cjk_codepoint(character: str) -> bool:
    codepoint = ord(character)
    return any(start <= codepoint <= end for start, end in _CJK_RANGES)


def _grapheme_kind(grapheme: str) -> str:
    base = next(
        (
            character
            for character in grapheme
            if not _extends_grapheme(character) and character != _ZWJ
        ),
        grapheme[0],
    )
    if _is_cjk_codepoint(base):
        return "cjk"
    if base == "_" or base.isalnum():
        return "word"
    return "other"


def _text_area_word_stops(text: str) -> list[int]:
    graphemes = list(iter_graphemes(text))
    stops: set[int] = set()
    previous_kind: str | None = None

    for start, end, grapheme in graphemes:
        kind = _grapheme_kind(grapheme)
        if kind == "cjk":
            stops.update((start, end))
        if previous_kind is not None and (kind == "other") != (
            previous_kind == "other"
        ):
            stops.add(start)
        previous_kind = kind

    return sorted(stops)


def text_area_word_left(text: str, index: int) -> int:
    """Return TextArea's previous word stop, with one stop per CJK grapheme."""
    search_text = text[: max(0, min(index, len(text)))].rstrip()
    stops = [stop for stop in _text_area_word_stops(search_text) if stop < len(search_text)]
    return stops[-1] if stops else 0


def text_area_word_right(text: str, index: int) -> int:
    """Return TextArea's next word stop, with one stop per CJK grapheme."""
    index = max(0, min(index, len(text)))
    suffix = text[index:]
    stripped = suffix.lstrip()
    strip_offset = len(suffix) - len(stripped)
    stops = [stop for stop in _text_area_word_stops(stripped) if stop > 0]
    return index + strip_offset + (stops[0] if stops else len(stripped))


def _input_word_stops(text: str) -> list[int]:
    stops = {0, len(text)}
    previous_kind: str | None = None
    for start, end, grapheme in iter_graphemes(text):
        kind = _grapheme_kind(grapheme)
        if previous_kind == "other" and kind != "other":
            stops.add(start)
        if kind == "cjk":
            stops.update((start, end))
        previous_kind = kind
    return sorted(stops)


def input_word_left(text: str, index: int) -> int:
    """Return Input's previous word stop, with one stop per CJK grapheme."""
    index = max(0, min(index, len(text)))
    stops = _input_word_stops(text)
    return stops[max(0, bisect_left(stops, index) - 1)]


def input_word_right(text: str, index: int) -> int:
    """Return Input's next word stop, with one stop per CJK grapheme."""
    index = max(0, min(index, len(text)))
    stops = _input_word_stops(text)
    return stops[min(len(stops) - 1, bisect_right(stops, index))]
