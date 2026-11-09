"""Build the bundled glyph alphabet from learned device names plus hand-identified shapes."""

import json
import sys
from pathlib import Path

from harmony_one.config_reader import parse_config
from harmony_one.text_decode import learn_alphabet

H15 = {0: ".", 1: ",", 2: ":", 3: ":", 4: "i", 5: "!", 6: "l", 7: "-", 8: "-", 9: "r", 10: "(", 11: "t",
       12: ")", 13: '"', 14: "]", 15: "[", 17: "f", 18: "\\", 19: "c", 20: "z", 21: "f", 22: "k", 23: "F",
       24: "q", 25: "g", 26: "a", 27: "s", 28: "o", 29: "e", 30: "x", 31: "v", 32: "y", 33: "u", 34: "n",
       35: "p", 36: "d", 37: "1", 38: "4", 39: "1", 40: "2", 41: "?", 42: "6", 43: "L", 44: "h", 45: "b",
       46: "2", 47: "3", 48: "8", 49: "9", 50: "0", 51: "F", 52: "5", 53: "7", 54: "T", 55: "S", 56: "K",
       57: "P", 58: "B", 59: "E", 60: "H", 61: "B", 62: "A", 63: "C", 64: "G", 65: "Q", 66: "V", 67: "U",
       68: "H", 69: "N", 70: "M", 71: "D", 72: "R", 73: "w", 74: "M", 75: "m", 76: "W", 77: "W"}
H14 = {0: ".", 1: ",", 2: ":", 3: "i", 6: "!", 7: "-", 8: "(", 10: ")", 11: "]", 12: "[", 13: "r", 15: "t",
       16: "t", 17: "f", 19: "c", 20: "4", 21: "1", 22: "2", 23: "3", 24: "6", 25: "8", 26: "9", 27: "0",
       28: "5", 29: "7", 30: "E", 31: "F", 32: "E", 33: "x", 34: "x", 35: "v", 36: "o", 39: "z", 40: "q",
       41: "g", 42: "n", 43: "p", 44: "j", 45: "d", 47: "S", 48: "L", 49: "k", 50: "h", 51: "b", 52: "U",
       53: "H", 54: "H", 55: "D", 56: "P", 57: "T", 58: "O", 59: "Q", 60: "C", 61: "G", 62: "Z", 63: "K",
       64: "A", 65: "V", 66: "Y", 67: "M", 68: "w", 69: "m", 70: "W"}

glyph_dir, out, *backups = sys.argv[1:]
alphabet = {}
for name in backups:
    data = Path(name).read_bytes()
    learn_alphabet(data, parse_config(data), alphabet)
index = json.loads((Path(glyph_dir) / "unknown-index.json").read_text())
for height, table in ((15, H15), (14, H14)):
    for number, char in table.items():
        entry = index[f"{height}:{number}"]
        key = (entry["height"], entry["width"], tuple(int(bit) for bit in entry["mask"]))
        alphabet.setdefault(key, char)
payload = [
    {"height": k[0], "width": k[1], "mask": "".join(map(str, k[2])), "char": c}
    for k, c in sorted(alphabet.items(), key=lambda item: (item[0][0], item[1], item[0][1]))
]
Path(out).write_text(json.dumps(payload, indent=0))
print(len(payload), "shapes written")
