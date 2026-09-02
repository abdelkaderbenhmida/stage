#!/usr/bin/env python3
"""Rend la sortie reelle d'une commande shell en PNG facon terminal clair.

Usage: render.py NOM "commande" [--cwd DIR] [--host devops@master-01] [--width 1180]
Le PNG est ecrit dans images/NOM.png .
"""
import argparse, html, os, re, subprocess, sys, tempfile, shutil

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
IMAGES = os.path.join(ROOT, "images")
CHROMIUM = shutil.which("chromium") or shutil.which("chromium-browser") or shutil.which("google-chrome")

# palette claire, lisible en impression
FG = {30:"#3b3b3b",31:"#c0392b",32:"#1e8449",33:"#a06a00",34:"#1f5fbf",35:"#8e44ad",36:"#0e7c86",37:"#3b3b3b",
      90:"#7a7a7a",91:"#e74c3c",92:"#27ae60",93:"#c98a00",94:"#3572d3",95:"#a55fc4",96:"#12a2ad",97:"#3b3b3b"}
SGR = re.compile(r"\x1b\[([0-9;]*)m")
OSC = re.compile(r"\x1b\][^\x07\x1b]*(\x07|\x1b\\)")
CTL = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\r")

def ansi_to_html(text):
    text = OSC.sub("", text)
    out, open_spans = [], 0
    pos = 0
    for m in SGR.finditer(text):
        out.append(html.escape(CTL.sub("", text[pos:m.start()])))
        pos = m.end()
        codes = [int(c) for c in m.group(1).split(";") if c.isdigit()] or [0]
        style = []
        for c in codes:
            if c == 0:
                out.append("</span>" * open_spans); open_spans = 0
            elif c == 1: style.append("font-weight:700")
            elif c == 3: style.append("font-style:italic")
            elif c == 4: style.append("text-decoration:underline")
            elif c in FG: style.append("color:%s" % FG[c])
        if style:
            out.append('<span style="%s">' % ";".join(style)); open_spans += 1
    out.append(html.escape(CTL.sub("", text[pos:])))
    out.append("</span>" * open_spans)
    return "".join(out)

TPL = """<style>
@page{margin:0}
html,body{margin:0;padding:0;background:#e9edf2}
.win{margin:14px;border-radius:9px;overflow:hidden;border:1px solid #c3ccd7;
     box-shadow:0 2px 10px rgba(20,30,50,.13);background:#fff}
.bar{display:flex;align-items:center;gap:7px;padding:8px 12px;background:#eef2f7;
     border-bottom:1px solid #d6dee8}
.dot{width:11px;height:11px;border-radius:50%%}
.t{margin-left:8px;font:600 12.5px/1 -apple-system,Segoe UI,Ubuntu,sans-serif;color:#5b6673}
.st{margin-left:14px;font:12.5px/1 -apple-system,Segoe UI,Ubuntu,sans-serif;color:#8b96a4}
.ts{margin-left:auto;font:12px/1 -apple-system,Segoe UI,Ubuntu,sans-serif;color:#93a0af}
pre{margin:0;padding:16px 18px;font:15px/1.48 "DejaVu Sans Mono","Ubuntu Mono",monospace;
    color:#2b2f36;white-space:pre-wrap;word-break:break-word;tab-size:4}
.p{color:#1e8449;font-weight:700}.d{color:#1f5fbf;font-weight:700}.s{color:#7a7a7a}
</style>
<div class="win">
 <div class="bar"><span class="dot" style="background:#ff5f57"></span>
  <span class="dot" style="background:#febc2e"></span>
  <span class="dot" style="background:#28c840"></span>
  <span class="t">%(title)s</span><span class="st">%(sub)s</span><span class="ts">%(stamp)s</span></div>
 <pre>%(body)s</pre>
</div>"""

def work_racine():
    return os.path.join(os.path.expanduser("~"), "capshots")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name"); ap.add_argument("command")
    ap.add_argument("--cwd", default=ROOT); ap.add_argument("--host", default="devops@homelab")
    ap.add_argument("--width", type=int, default=1180)
    ap.add_argument("--title", default=None)
    ap.add_argument("--subtitle", default="")
    ap.add_argument("--text", default=None, help="utiliser ce texte au lieu d'executer")
    ap.add_argument("--timeout", type=int, default=180)
    a = ap.parse_args()

    if a.text is not None:
        out = a.text
    else:
        cibin = os.path.join(os.path.expanduser("~"), "capshots", "bin")
        env = dict(os.environ, PATH=cibin + os.pathsep + os.environ["PATH"],
                   TERM="xterm-256color", COLUMNS="120",
                   FORCE_COLOR="1", CLICOLOR_FORCE="1", PY_COLORS="1", ANSIBLE_FORCE_COLOR="1")
        try:
            r = subprocess.run(["bash","-lc",a.command], cwd=a.cwd, env=env, timeout=a.timeout,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            out = r.stdout.decode("utf-8","replace")
        except subprocess.TimeoutExpired as e:
            out = (e.output or b"").decode("utf-8","replace") + "\n[timeout]"

    out = out.rstrip("\n")
    short = os.path.basename(a.cwd) if a.cwd != ROOT else "rapport"
    prompt = ('<span class="p">%s</span>:<span class="d">~/%s</span>$ '
              % (html.escape(a.host), html.escape(short)))
    lignes, courant = [], ""
    for morceau in a.command.split("; "):
        if courant and len(courant) + len(morceau) > 96:
            lignes.append(courant); courant = morceau
        else:
            courant = (courant + "; " + morceau) if courant else morceau
    lignes.append(courant)
    cmd_html = ("\n" + " " * 2).join(html.escape(l) + ("" if l is lignes[-1] else " ;")
                                      for l in lignes)
    body = prompt + cmd_html + "\n" + ansi_to_html(out)

    textes = os.path.join(work_racine(), "text")
    os.makedirs(textes, exist_ok=True)
    open(os.path.join(textes, a.name + ".txt"), "w", encoding="utf-8").write(out)

    import datetime
    page = TPL % {"title": html.escape(a.title or a.name), "body": body,
                  "sub": html.escape(a.subtitle),
                  "stamp": datetime.datetime.now().strftime("%d/%m/%Y %H:%M")}
    affichee = "\n".join(lignes) + "\n" + out
    nlines = sum(max(1, (len(l) // (a.width // 9)) + 1) for l in affichee.split("\n"))
    height = min(30000, int(28 + 33 + 32 + nlines * 22.2 + 6))

    work = os.path.join(os.path.expanduser("~"), "capshots")
    os.makedirs(work, exist_ok=True); os.makedirs(IMAGES, exist_ok=True)
    fh = os.path.join(work, a.name + ".html"); png = os.path.join(work, a.name + ".png")
    open(fh, "w", encoding="utf-8").write(page)
    subprocess.run([CHROMIUM,"--headless","--disable-gpu","--no-sandbox","--hide-scrollbars",
                    "--force-device-scale-factor=2",
                    "--window-size=%d,%d" % (a.width, height),
                    "--screenshot=" + png, fh],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
    if not os.path.exists(png):
        print("ECHEC %s" % a.name); return 1
    shutil.move(png, os.path.join(IMAGES, a.name + ".png"))
    print("OK images/%s.png (%d lignes)" % (a.name, nlines))
    return 0

sys.exit(main())
