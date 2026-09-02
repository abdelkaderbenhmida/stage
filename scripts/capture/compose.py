#!/usr/bin/env python3
"""Recompose une capture d'interface : capture ecran + graphiques canvas collectes.

Les navigateurs pilotes ne rendent pas les <canvas> dans leurs captures ; la page
envoie donc ses canvas au proxy (/collect), et ce script les recolle a leur place.

Usage: compose.py <capture.jpg> <nom-collecte> <nom-figure>
"""
import json, os, sys
from PIL import Image

WORK = os.path.join(os.path.expanduser("~"), "capshots", "collect")
shot, name, out = sys.argv[1], sys.argv[2], sys.argv[3]

base = Image.open(shot).convert("RGB")
d = json.load(open(os.path.join(WORK, name, "meta.json")))
# la capture ecran est en pixels CSS x facteur d'echelle
sx = base.width / (d["images"] and d["scale"] or 1) if False else d["scale"]

for i, im in enumerate(d["images"]):
    f = os.path.join(WORK, name, "%02d.png" % i)
    if not os.path.exists(f):
        continue
    c = Image.open(f).convert("RGBA")
    w, h = max(1, int(im["w"] * sx)), max(1, int(im["h"] * sx))
    c = c.resize((w, h), Image.LANCZOS)
    base.paste(c, (int(im["x"] * sx), int(im["y"] * sx)), c)

dst = "images/%s.png" % out
base.save(dst)
print("OK %s (%dx%d, %d graphiques)" % (dst, base.width, base.height, len(d["images"])))
