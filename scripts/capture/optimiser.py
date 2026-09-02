#!/usr/bin/env python3
"""Allege les figures sans les rendre illisibles a l'impression.

1800 px sur une largeur utile d'environ 15 cm donnent ~300 dpi. Les captures
n'utilisent qu'une poignee de couleurs : une palette de 256 teintes divise le
poids par deux sans difference visible sur le rendu final.
"""
import glob, os
from PIL import Image

LARGEUR_MAX = 1800
avant = apres = 0
for f in sorted(glob.glob("images/*.png")):
    avant += os.path.getsize(f)
    im = Image.open(f).convert("RGB")
    if im.width > LARGEUR_MAX:
        im = im.resize((LARGEUR_MAX, round(im.height * LARGEUR_MAX / im.width)), Image.LANCZOS)
    im.quantize(colors=256, method=Image.MEDIANCUT,
                dither=Image.FLOYDSTEINBERG).save(f, optimize=True)
    apres += os.path.getsize(f)
print("images/ : %.1f Mo -> %.1f Mo" % (avant / 1e6, apres / 1e6))
