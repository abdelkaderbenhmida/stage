#!/usr/bin/env python3
"""Repeint la barre de titre des figures terminal deja produites.

Evite de rejouer une commande longue (ou un incident) juste pour changer le
libelle affiche : seule la bande superieure de l'image est redessinee.
"""
import os, sys
from PIL import Image, ImageDraw
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from frame import police
from titres import TITRES

MARGE, BARRE = 28, 66          # geometrie du gabarit terminal, rendu 2x
BARRE_FOND, BARRE_TRAIT = (238, 242, 247), (214, 222, 232)
TITRE_COUL, SOUS_COUL, TS_COUL = (60, 72, 88), (139, 150, 164), (147, 160, 175)

def retitrer(chemin, titre, sous_titre, horodatage):
    im = Image.open(chemin).convert("RGB")
    d = ImageDraw.Draw(im)
    x0, x1 = MARGE, im.width - MARGE
    d.rectangle([x0, MARGE, x1 - 1, MARGE + BARRE], fill=BARRE_FOND)
    d.line([x0, MARGE + BARRE, x1 - 1, MARGE + BARRE], fill=BARRE_TRAIT)
    for i, coul in enumerate(((255, 95, 87), (254, 188, 46), (40, 200, 64))):
        cx = x0 + 30 + i * 30
        d.ellipse([cx, MARGE + BARRE // 2 - 9, cx + 18, MARGE + BARRE // 2 + 9], fill=coul)
    ft, fs = police(23, True), police(21)
    d.text((x0 + 132, MARGE + BARRE // 2 - 13), titre, font=ft, fill=TITRE_COUL)
    if sous_titre:
        d.text((x0 + 132 + d.textlength(titre, font=ft) + 22, MARGE + BARRE // 2 - 11),
               sous_titre, font=fs, fill=SOUS_COUL)
    if horodatage:
        larg = d.textlength(horodatage, font=fs)
        d.text((x1 - 24 - larg, MARGE + BARRE // 2 - 11), horodatage, font=fs, fill=TS_COUL)
    im.save(chemin)
    return True

if __name__ == "__main__":
    import datetime
    ts = datetime.datetime.now().strftime("%d/%m/%Y")
    noms = sys.argv[1:] or sorted(TITRES)
    for n in noms:
        f = "images/%s.png" % n
        if not os.path.exists(f):
            print("absent   %s" % f); continue
        titre, sous = TITRES.get(n, (n, ""))
        retitrer(f, titre, sous, ts)
        print("OK       %s" % f)
