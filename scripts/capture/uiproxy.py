#!/usr/bin/env python3
"""Proxy local d'authentification pour les captures d'interfaces web.

Evite de saisir des identifiants a la main : le proxy lit les Secrets Kubernetes,
puis injecte l'en-tete d'authentification (Basic ou cookie de session ArgoCD)
dans chaque requete transmise a l'interface cible.

  grafana   -> http://127.0.0.1:8801   (Basic admin)
  kibana    -> http://127.0.0.1:8802   (Basic elastic)
  argocd    -> http://127.0.0.1:8803   (cookie argocd.token)
"""
import base64, json, os, ssl, subprocess, sys, threading, urllib.request, urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

NOVERIFY = ssl.create_default_context()
NOVERIFY.check_hostname = False
NOVERIFY.verify_mode = ssl.CERT_NONE

ARGO = "http://127.0.0.1:8480"

def secret(ns, name, key):
    out = subprocess.run(["kubectl","get","secret",name,"-n",ns,"-o","jsonpath={.data.%s}" % key],
                         capture_output=True, text=True).stdout
    return base64.b64decode(out).decode()

def basic(user, pw):
    return "Basic " + base64.b64encode(("%s:%s" % (user, pw)).encode()).decode()

def argocd_cookie():
    pw = secret("argocd", "argocd-initial-admin-secret", "password")
    req = urllib.request.Request(ARGO + "/api/v1/session",
                                 data=json.dumps({"username":"admin","password":pw}).encode(),
                                 headers={"Content-Type":"application/json"})
    tok = json.load(urllib.request.urlopen(req, context=NOVERIFY))["token"]
    return "argocd.token=" + tok

COOKIES = {}

BOOTSTRAP = """<!doctype html><meta charset=utf-8><title>bootstrap</title>
<script>
document.cookie = %s + "; path=/";
location.replace(%s);
</script>"""

def grafana_cookie():
    """Ouvre une session Grafana cote serveur et renvoie le cookie a deposer."""
    user = secret("monitoring","grafana-admin-credentials","admin-user")
    pw   = secret("monitoring","grafana-admin-credentials","admin-password")
    req = urllib.request.Request("http://127.0.0.1:3000/login",
                                 data=json.dumps({"user":user,"password":pw}).encode(),
                                 headers={"Content-Type":"application/json"})
    r = urllib.request.urlopen(req, timeout=30)
    for k, v in r.headers.items():
        if k.lower() == "set-cookie" and v.startswith("grafana_session="):
            return v.split(";")[0]
    return ""


def kibana_cookie():
    """Ouvre une session Kibana cote serveur et renvoie le cookie a deposer."""
    pw = secret("monitoring","elasticsearch-credentials","ELASTIC_PASSWORD")
    body = {"providerType":"basic","providerName":"basic","currentURL":"/",
            "params":{"username":"elastic","password":pw}}
    req = urllib.request.Request("http://127.0.0.1:5601/internal/security/login",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type":"application/json","kbn-xsrf":"true"})
    r = urllib.request.urlopen(req, timeout=30)
    for k, v in r.headers.items():
        if k.lower() == "set-cookie" and v.startswith("sid="):
            return v.split(";")[0]
    return ""


def make(upstream, headers):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"
        def log_message(self, *a): pass
        def note(self, code):
            open(os.path.join(os.path.expanduser("~"),"capshots","proxy-access.log"),"a").write(
                "%d %s %s\n" % (code, self.command, self.path[:150]))
        def collect(self, body):
            """Recoit les canvas d'une page (graphiques Grafana) pour recomposition."""
            import base64
            payload = json.loads(body.decode())
            base = os.path.join(os.path.expanduser("~"), "capshots", "collect", payload["name"])
            os.makedirs(base, exist_ok=True)
            meta = []
            for i, im in enumerate(payload["images"]):
                raw = base64.b64decode(im["data"].split(",", 1)[1])
                open(os.path.join(base, "%02d.png" % i), "wb").write(raw)
                meta.append({k: im[k] for k in ("x", "y", "w", "h")})
            json.dump({"scale": payload.get("scale", 1), "images": meta},
                      open(os.path.join(base, "meta.json"), "w"))
            out = json.dumps({"recu": len(meta)}).encode()
            self.send_response(200)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.send_header("Connection", "close")
            self.end_headers(); self.wfile.write(out)

        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "content-type")
            self.send_header("Content-Length", "0")
            self.send_header("Connection", "close")
            self.end_headers()

        def proxy(self, body=None):
            if self.path.startswith("/collect"):
                return self.collect(body or b"{}")
            # Depot du cookie de session puis redirection vers l'interface reelle :
            # le navigateur dialogue ensuite directement avec ArgoCD (flux temps reel inclus).
            if self.path.startswith("/bootstrap"):
                import urllib.parse as up
                q = up.parse_qs(up.urlparse(self.path).query)
                ck = COOKIES.get(q.get("use", [""])[0], headers.get("Cookie",""))
                page = BOOTSTRAP % (json.dumps(ck),
                                    json.dumps(q.get("next", ["/"])[0]))
                b = page.encode()
                self.send_response(200); self.send_header("Content-Type","text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(b)))
                self.send_header("Connection","close"); self.end_headers()
                self.wfile.write(b); return
            # Les flux temps reel (watch) monopolisent les 6 connexions HTTP/1.x
            # du navigateur et empechent le chargement initial : on les neutralise.
            if "/stream" in self.path or "watch=true" in self.path:
                self.note(204)
                self.send_response(204); self.send_header("Content-Length","0")
                self.send_header("Connection","close"); self.end_headers(); return
            url = upstream + self.path
            hdrs = {k: v for k, v in self.headers.items()
                    if k.lower() not in ("host","connection","accept-encoding","cookie","authorization","content-length")}
            hdrs.update(headers)
            req = urllib.request.Request(url, data=body, headers=hdrs, method=self.command)
            try:
                r = urllib.request.urlopen(req, context=NOVERIFY, timeout=300)
                code, rh = r.status, r.headers
            except urllib.error.HTTPError as e:
                r, code, rh = e, e.code, e.headers
            except Exception as e:
                msg = str(e).encode()
                self.send_response(502); self.send_header("Content-Length", str(len(msg)))
                self.end_headers(); self.wfile.write(msg); return
            self.note(code)
            self.send_response(code)
            for k in ("Content-Type","Cache-Control","Location","Set-Cookie"):
                if rh and rh.get(k): self.send_header(k, rh.get(k))
            # les endpoints de flux (ArgoCD, Kibana) n'ont pas de Content-Length :
            # on relaie en continu et on ferme la connexion en fin de flux.
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                while True:
                    chunk = r.read(8192)
                    if not chunk: break
                    self.wfile.write(chunk); self.wfile.flush()
            except Exception:
                pass
            self.close_connection = True
        def do_GET(self): self.proxy()
        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            self.proxy(self.rfile.read(n) if n else None)
        do_PUT = do_POST
    return H

def serve(port, upstream, headers):
    srv = ThreadingHTTPServer(("127.0.0.1", port), make(upstream, headers))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print("proxy %d -> %s" % (port, upstream))

serve(8801, "http://127.0.0.1:3000",
      {"Authorization": basic(secret("monitoring","grafana-admin-credentials","admin-user"),
                              secret("monitoring","grafana-admin-credentials","admin-password"))})
serve(8802, "http://127.0.0.1:5601",
      {"Authorization": basic("elastic", secret("monitoring","elasticsearch-credentials","ELASTIC_PASSWORD")),
       "kbn-xsrf": "true"})
try:
    serve(8803, ARGO, {"Cookie": argocd_cookie()})
except Exception as e:
    print("argocd: %s" % e)
for nom, fn in (("grafana", grafana_cookie), ("kibana", kibana_cookie)):
    try:
        COOKIES[nom] = fn()
        print("session %s : %s" % (nom, "ok" if COOKIES[nom] else "vide"))
    except Exception as exc:
        print("session %s : %s" % (nom, exc))
print("pret")
threading.Event().wait()
