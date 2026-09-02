#!/usr/bin/env python3
"""Importe une capture prise dans le navigateur vers images/<nom>.png."""
import sys
from PIL import Image
src, name = sys.argv[1], sys.argv[2]
im = Image.open(src).convert("RGB")
out = "images/%s.png" % name
im.save(out)
print("OK %s (%dx%d)" % (out, im.width, im.height))
