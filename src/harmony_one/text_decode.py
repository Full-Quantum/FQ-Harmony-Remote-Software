"""Turn glyph codes into text by matching glyph shapes (read-only).

A glyph code is assigned per configuration, but a character's pixels are the same
in every configuration of a skin (public reference, screens.md). So the alphabet is
keyed by (font height, width, pixel mask) and learned from device names, whose ASCII
is also stored in the name tree.
"""

from __future__ import annotations

from collections import Counter

from harmony_one.config_reader import RemoteConfig
from harmony_one.fonts import FontSet, decode_glyph, read_font_sets
from harmony_one.screens import ModePage, TextDraw, read_mode_pages

ShapeKey = tuple[int, int, tuple[int, ...]]


def glyph_key(data: bytes, font: FontSet, code: int) -> ShapeKey | None:
    slot = code - font.first_code
    if not 0 <= slot < len(font.glyph_offsets) or font.glyph_offsets[slot] is None:
        return None
    width, rows = decode_glyph(data, font.glyph_offsets[slot], font.height)
    mask = tuple(1 if pixel is not None else 0 for row in rows for pixel in row)
    return font.height, width, mask


def _keys(data: bytes, fonts: list[FontSet], draw: TextDraw) -> list[ShapeKey | None]:
    if draw.font >= len(fonts):
        return [None] * len(draw.codes)
    return [glyph_key(data, fonts[draw.font], code) for code in draw.codes]


def learn_alphabet(
    data: bytes, config: RemoteConfig, alphabet: dict[ShapeKey, str] | None = None
) -> dict[ShapeKey, str]:
    """Add what this configuration's device names teach to `alphabet`."""
    alphabet = alphabet if alphabet is not None else {}
    fonts = read_font_sets(data, config)
    first_draw: dict[tuple[int, tuple[int, ...]], TextDraw] = {}
    counts: Counter = Counter()
    for page in read_mode_pages(data, config):
        for draw in page.draws:
            first_draw.setdefault((draw.font, draw.codes), draw)
            counts[(draw.font, draw.codes)] += 1
    for label in config.device_labels:
        candidates = [
            item for item in counts.most_common() if len(item[0][1]) == len(label)
        ]
        for (font, codes), _ in candidates[:40]:
            draw = first_draw[(font, codes)]
            keys = _keys(data, fonts, draw)
            pairs = list(zip(keys, label))
            if any(key is None for key, _ in pairs):
                continue
            if any(alphabet.get(key, char) != char for key, char in pairs):
                continue
            if any(
                other != key and other_char == char and other[0] == key[0]
                for key, char in pairs
                for other, other_char in alphabet.items()
                if char != " " and any(key[2])
            ):
                continue
            for key, char in pairs:
                alphabet[key] = char
            break
    return alphabet


def load_bundled_alphabet() -> dict[ShapeKey, str]:
    import json
    from pathlib import Path

    path = Path(__file__).parent / "data" / "alphabet.json"
    if not path.exists():
        return {}
    return {
        (item["height"], item["width"], tuple(int(bit) for bit in item["mask"])): item["char"]
        for item in json.loads(path.read_text())
    }


def decode_text(
    data: bytes, config: RemoteConfig, draw: TextDraw, alphabet: dict[ShapeKey, str], fonts=None
) -> str:
    fonts = fonts if fonts is not None else read_font_sets(data, config)
    out = []
    for key in _keys(data, fonts, draw):
        if key is None:
            out.append("?")
        elif not any(key[2]):
            out.append(" ")
        else:
            out.append(alphabet.get(key, "?"))
    return "".join(out)
