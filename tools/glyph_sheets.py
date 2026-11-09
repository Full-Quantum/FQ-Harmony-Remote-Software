"""Render unknown glyph shapes as numbered contact sheets for manual identification."""

import json
import sys
from collections import Counter
from pathlib import Path

from PIL import Image, ImageDraw

from harmony_one.config_reader import parse_config
from harmony_one.fonts import read_font_sets
from harmony_one.screens import read_mode_pages
from harmony_one.text_decode import _keys, learn_alphabet

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
names = sys.argv[2:]
alphabet, used, cfg = {}, Counter(), []
for name in names:
    data = Path(name).read_bytes()
    config = parse_config(data)
    cfg.append((data, config))
    learn_alphabet(data, config, alphabet)
for data, config in cfg:
    fonts = read_font_sets(data, config)
    for page in read_mode_pages(data, config):
        for draw in page.draws:
            for key in _keys(data, fonts, draw):
                if key and any(key[2]):
                    used[key] += 1
unknown = sorted((k for k in used if k not in alphabet), key=lambda k: (k[0], k[1], k[2]))
index = {}
by_height = {}
for key in unknown:
    by_height.setdefault(key[0], []).append(key)
scale, cols = 5, 10
for height, keys in by_height.items():
    rows = (len(keys) + cols - 1) // cols
    cell_w, cell_h = 20 * scale + 8, height * scale + 24
    sheet = Image.new("RGB", (cols * cell_w, rows * cell_h), "white")
    pen = ImageDraw.Draw(sheet)
    for n, key in enumerate(keys):
        index[f"{height}:{n}"] = {"height": key[0], "width": key[1], "mask": "".join(map(str, key[2]))}
        x0, y0 = (n % cols) * cell_w, (n // cols) * cell_h
        pen.text((x0 + 2, y0 + 1), str(n), fill="red")
        for i, bit in enumerate(key[2]):
            if bit:
                px, py = i % key[1], i // key[1]
                pen.rectangle(
                    [x0 + 2 + px * scale, y0 + 14 + py * scale, x0 + 2 + px * scale + scale - 1, y0 + 14 + py * scale + scale - 1],
                    fill="black",
                )
    sheet.save(out / f"unknown-h{height}.png")
(out / "unknown-index.json").write_text(json.dumps(index))
print({h: len(k) for h, k in by_height.items()})
