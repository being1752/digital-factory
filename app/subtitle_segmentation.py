from __future__ import annotations

import math
from functools import lru_cache


PUNCTUATION = "。！？!?；;，,、：:…"
STRONG_PUNCTUATION = "。！？!?；;"
SOFT_PUNCTUATION = "，,、：:"
SUBTITLE_SEGMENTATION_VERSION = 2


def _is_visible(char: str) -> bool:
    return char.isalnum() or "\u4e00" <= char <= "\u9fff"


def visible_length(value: str) -> int:
    return sum(1 for char in value if _is_visible(char))


def balanced_chunk_ranges(text: str, max_chars: int) -> list[tuple[int, int]]:
    """Split one spoken sentence without leaving short fragments at its tail.

    Punctuation is attached to the text before it.  The number of chunks is
    derived from the configured maximum, then all legal boundaries are scored
    together so that a locally full chunk cannot leave a one-character cue.
    """
    if not text:
        return []
    max_chars = min(32, max(6, int(max_chars)))
    total_visible = visible_length(text)
    if total_visible <= max_chars:
        return [(0, len(text))]

    chunk_count = max(2, math.ceil(total_visible / max_chars))
    target = total_visible / chunk_count
    minimum = min(5, max(2, total_visible // (chunk_count * 2)))
    prefix = [0]
    for char in text:
        prefix.append(prefix[-1] + int(_is_visible(char)))

    def count(start: int, end: int) -> int:
        return prefix[end] - prefix[start]

    # A boundary immediately before punctuation would create punctuation-led
    # cues, so punctuation runs always remain attached to the previous text.
    boundaries = [0]
    boundaries.extend(
        index
        for index in range(1, len(text))
        if text[index] not in PUNCTUATION and count(0, index) > 0
    )
    boundaries.append(len(text))
    boundaries = sorted(set(boundaries))

    def boundary_cost(end: int) -> float:
        if end >= len(text):
            return 0.0
        previous = text[end - 1]
        if previous in STRONG_PUNCTUATION:
            return -14.0
        if previous in SOFT_PUNCTUATION:
            return -10.0
        if previous.isspace():
            return -4.0
        return 3.0

    @lru_cache(maxsize=None)
    def solve(start: int, remaining: int) -> tuple[float, tuple[int, ...]] | None:
        if remaining == 1:
            length = count(start, len(text))
            if minimum <= length <= max_chars:
                return (2.0 * (length - target) ** 2, (len(text),))
            return None

        best: tuple[float, tuple[int, ...]] | None = None
        for end in boundaries:
            if end <= start or end >= len(text):
                continue
            length = count(start, end)
            rest = count(end, len(text))
            if length < minimum or length > max_chars:
                continue
            if rest < minimum * (remaining - 1) or rest > max_chars * (remaining - 1):
                continue
            tail = solve(end, remaining - 1)
            if tail is None:
                continue
            score = 2.0 * (length - target) ** 2 + boundary_cost(end) + tail[0]
            candidate = (score, (end, *tail[1]))
            if best is None or candidate[0] < best[0]:
                best = candidate
        return best

    solution = solve(0, chunk_count)
    if solution is None:
        # Defensive fallback. In normal text there is always a legal balanced
        # partition, but returning the whole sentence is safer than an orphan.
        return [(0, len(text))]

    ranges: list[tuple[int, int]] = []
    start = 0
    for end in solution[1]:
        ranges.append((start, end))
        start = end
    return ranges


def balanced_text_chunks(text: str, max_chars: int) -> list[str]:
    return [text[start:end] for start, end in balanced_chunk_ranges(text, max_chars)]
