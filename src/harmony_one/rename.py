"""Same-length text rename inside a configuration (activity and device labels).

Text is stored as per-configuration glyph codes drawn by screen programs. A rename
re-spells the old text with codes whose glyph shapes spell the new text, keeping the
byte length identical so no address moves, then refreshes the trailer checksum.
"""

from __future__ import annotations

from harmony_one.config_reader import parse_config
from harmony_one.fonts import read_font_sets
from harmony_one.screens import read_mode_pages
from harmony_one.slideshow import refresh_checksum
from harmony_one.text_decode import decode_text, glyph_key, load_bundled_alphabet

_SAME_SHAPE = {"I": "l", "l": "l"}


class RenameError(ValueError):
    pass


def _norm(char: str) -> str:
    return _SAME_SHAPE.get(char, char)


def _encode(codes) -> bytes:
    out = bytearray()
    for code in codes:
        if code > 0xFF:
            out += bytes([code >> 8, code & 0xFF])
        else:
            out.append(code)
    return bytes(out)


def _code_for(data, font, alphabet, char, prefer):
    """A glyph code of `font` that draws `char`, preferring codes already in use."""
    wanted = _norm(char)
    found = []
    for slot, offset in enumerate(font.glyph_offsets):
        if offset is None:
            continue
        code = font.first_code + slot
        key = glyph_key(data, font, code)
        if key is None:
            continue
        shown = " " if not any(key[2]) else alphabet.get(key)
        if shown is not None and _norm(shown) == wanted:
            found.append(code)
    for code in found:
        if code in prefer:
            return code
    return found[0] if found else None


def rename_text(blob: bytes, old: str, new: str) -> tuple[bytes, int]:
    """Replace `old` with `new` everywhere it is drawn; return (new blob, places changed)."""
    if len(old) != len(new):
        raise RenameError("only same-length renames are supported")
    config = parse_config(blob)
    fonts = read_font_sets(blob, config)
    alphabet = load_bundled_alphabet()
    out = bytearray(blob)
    seen: set[int] = set()
    changed = 0
    for page in read_mode_pages(blob, config):
        for draw in page.draws:
            if draw.address in seen or draw.font >= len(fonts):
                continue
            seen.add(draw.address)
            text = decode_text(blob, config, draw, alphabet, fonts)
            index = text.find(old)
            if index < 0 and _norm_text(text).find(_norm_text(old)) >= 0:
                index = _norm_text(text).find(_norm_text(old))
            if index < 0:
                continue
            font = fonts[draw.font]
            codes = list(draw.codes)
            prefer = set(codes)
            for offset, char in enumerate(new):
                code = _code_for(blob, font, alphabet, char, prefer)
                if code is None:
                    raise RenameError(f"font {draw.font} has no glyph for {char!r}")
                codes[index + offset] = code
            before, after = _encode(draw.codes), _encode(codes)
            if len(before) != len(after):
                raise RenameError("new glyph codes change the byte length")
            out[draw.address : draw.address + len(after)] = after
            changed += 1
    if not changed:
        raise RenameError(f"{old!r} was not found")
    refresh_checksum(out)
    return bytes(out), changed


def _norm_text(text: str) -> str:
    return "".join(_norm(c) for c in text)
