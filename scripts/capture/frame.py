#!/usr/bin/env python3
"""Met les captures d'interfaces web au format des figures du rapport.

Rogne les barres de defilement, agrandit a la resolution des figures terminal
et pose le meme cadre (barre de titre + bordure) pour une planche homogene.

  frame.py <image-source> <nom-figure> "<titre>" ["<sous-titre>"]
"""
import os, sys
from PIL import Image, ImageDraw, ImageFilter, ImageFont

LARGEUR = 2360          # largeur des figures terminal (rendu 2x)
MARGE, BARRE, RAYON = 28, 66, 18
FOND, CADRE = (233, 237, 242), (195, 204, 215)
BARRE_FOND, BARRE_TRAIT = (238, 242, 247), (214, 222, 232)
TITRE_COUL, SOUS_COUL = (60, 72, 88), (122, 132, 146)

def police(taille, gras=False):
    noms = (["DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf"] if gras
            else ["DejaVuSans.ttf", "LiberationSans-Regular.ttf"])
    for rep in ("/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/truetype/liberation"):
        for n in noms:
            c = os.path.join(rep, n)
            if os.path.exists(c):
                return ImageFont.truetype(c, taille)
    return ImageFont.load_default()

def rogner_barres(im):
    """Supprime les barres de defilement uniformes a droite et en bas."""
    px = im.convert("RGB")
    w, h = px.size
    droite = w
    for x in range(w - 1, w - 26, -1):
        col = {px.getpixel((x, y)) for y in range(0, h, max(1, h // 40))}
        if len(col) <= 2:
            droite = x
        else:
            break
    bas = h
    for y in range(h - 1, h - 26, -1):
        lig = {px.getpixel((x, y)) for x in range(0, w, max(1, w // 40))}
        if len(lig) <= 2:
            bas = y
        else:
            break
    # marge fixe : les barres de defilement de Chrome ne sont pas uniformes
    # (fleches, curseur) et resistent a la detection ci-dessus.
    return im.crop((0, 0, max(1, droite - 14), max(1, bas - 14)))

def encadrer(src, nom, titre, sous_titre=""):
    im = rogner_barres(Image.open(src).convert("RGB"))
    utile = LARGEUR - 2 * MARGE
    ech = utile / im.width
    im = im.resize((utile, max(1, round(im.height * ech))), Image.LANCZOS)
    im = im.filter(ImageFilter.UnsharpMask(radius=1.2, percent=55, threshold=3))

    W = LARGEUR
    H = im.height + BARRE + 2 * MARGE
    fond = Image.new("RGB", (W, H), FOND)

    # ombre portee douce sous la fenetre
    ombre = Image.new("L", (W, H), 0)
    ImageDraw.Draw(ombre).rounded_rectangle(
        [MARGE, MARGE + 6, W - MARGE, H - MARGE + 6], RAYON, fill=70)
    fond.paste(Image.new("RGB", (W, H), (150, 160, 175)),
               (0, 0), ombre.filter(ImageFilter.GaussianBlur(9)))

    fenetre = Image.new("RGB", (W - 2 * MARGE, im.height + BARRE), (255, 255, 255))
    d = ImageDraw.Draw(fenetre)
    d.rectangle([0, 0, fenetre.width, BARRE], fill=BARRE_FOND)
    d.line([0, BARRE, fenetre.width, BARRE], fill=BARRE_TRAIT)
    for i, coul in enumerate(((255, 95, 87), (254, 188, 46), (40, 200, 64))):
        cx = 30 + i * 30
        d.ellipse([cx, BARRE // 2 - 9, cx + 18, BARRE // 2 + 9], fill=coul)
    d.text((132, BARRE // 2 - 13), titre, font=police(23, True), fill=TITRE_COUL)
    if sous_titre:
        larg = d.textlength(titre, font=police(23, True))
        d.text((132 + larg + 22, BARRE // 2 - 11), sous_titre,
               font=police(21), fill=SOUS_COUL)
    fenetre.paste(im, (0, BARRE))

    masque = Image.new("L", fenetre.size, 0)
    ImageDraw.Draw(masque).rounded_rectangle([0, 0, fenetre.width - 1, fenetre.height - 1],
                                             RAYON, fill=255)
    fond.paste(fenetre, (MARGE, MARGE), masque)
    contour = ImageDraw.Draw(fond)
    contour.rounded_rectangle([MARGE, MARGE, W - MARGE - 1, MARGE + fenetre.height - 1],
                              RAYON, outline=CADRE, width=2)

    dst = "images/%s.png" % nom
    fond.save(dst)
    print("OK %s (%dx%d)" % (dst, fond.width, fond.height))

if __name__ == "__main__":
    encadrer(sys.argv[1], sys.argv[2], sys.argv[3],
             sys.argv[4] if len(sys.argv) > 4 else "")
