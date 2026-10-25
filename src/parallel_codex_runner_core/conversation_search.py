"""Bounded-memory, literal search of saved conversation text."""

from __future__ import annotations

import codecs
from pathlib import Path


def matching_excerpt(text: str, query: str) -> str | None:
    folded = text.casefold()
    offset = folded.find(query.casefold())
    if offset < 0:
        return None
    # Case folding can expand characters. Map the match back to original text.
    start = 0
    count = 0
    for start, character in enumerate(text):
        if count >= offset:
            break
        count += len(character.casefold())
    excerpt = text[max(0, start - 60):start + len(query) + 120]
    return " ".join(excerpt.split())


def search_file(path: Path, query: str) -> str | None:
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    overlap = ""
    try:
        with path.open("rb") as stream:
            while True:
                data = stream.read(64 * 1024)
                text = overlap + decoder.decode(data, final=not data)
                excerpt = matching_excerpt(text, query)
                if excerpt is not None:
                    return excerpt
                if not data:
                    return None
                overlap = text[-(len(query) + 120):]
    except OSError:
        return None
