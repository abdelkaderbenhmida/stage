#!/usr/bin/env python3
"""stress-panel.py — web UI to drive scripts/stress-hpa.sh.

Start/stop sustained load, live HPA + pod + VM status, Grafana hint.
Deps: stdlib only; needs kubectl, virsh, ab, jq on PATH.
"""

import json
import os
import signal
import subprocess
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
STRESS = os.path.join(HERE, "stress-hpa.sh")
NS = "devops-platform"
PORT = 8080

STATE = {"proc": None, "c": None, "n": None, "watch": None, "log": ""}


def run(args, timeout=15):
    try:
        return subprocess.run(args, capture_output=True, text=True,
                              timeout=timeout).stdout.strip()
    except subprocess.TimeoutExpired:
        return "(timeout)"
    except Exception:
        return ""


def status():
    running = STATE["proc"] is not None and STATE["proc"].poll() is None
    hpa = run(["kubectl", "-n", NS, "get", "hpa", "-o", "wide"])
    if not hpa:
        hpa = run(["kubectl", "get", "hpa", "-A"])
    pods = run(["kubectl", "-n", NS, "get", "pods",
                "-l", "app.kubernetes.io/name=users-service", "-o", "wide"])
    vms = run(["virsh", "list", "--all"])
    grafana_pf = run(["bash", "-c",
                      "pgrep -af 'port-forward.*grafana' | head -1"])
    return {
        "running": running,
        "pid": STATE["proc"].pid if running else None,
        "c": STATE["c"], "n": STATE["n"], "watch": STATE["watch"],
        "hpa": hpa, "pods": pods, "vms": vms,
        "grafana_pf": grafana_pf or "port-forward DOWN",
        "log": STATE["log"],
    }


def start_stress(body):
    if STATE["proc"] is not None and STATE["proc"].poll() is None:
        return {"ok": False, "error": "already running — stop first"}
    c = body.get("c") or "200"
    n = body.get("n") or "40000"
    watch = body.get("watch") or "240"
    logpath = os.path.join(HERE, "stress-run.log")
    logf = open(logpath, "w")
    argv = ["bash", STRESS, "-c", c, "-n", n, "--watch", watch]
    proc = subprocess.Popen(argv, stdout=logf, stderr=subprocess.STDOUT,
                            start_new_session=True)
    STATE.update(proc=proc, c=c, n=n, watch=watch)
    return {"ok": True, "cmd": " ".join(argv)}


def stop_stress():
    if STATE["proc"] is None or STATE["proc"].poll() is not None:
        return {"ok": False, "error": "nothing running"}
    try:
        os.killpg(os.getpgid(STATE["proc"].pid), signal.SIGTERM)
        for _ in range(20):
            if STATE["proc"].poll() is not None:
                break
            time.sleep(0.2)
        if STATE["proc"].poll() is None:
            os.killpg(os.getpgid(STATE["proc"].pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    STATE["proc"].wait()
    STATE["proc"] = None
    return {"ok": True}


class Handler(BaseHTTPRequestHandler):
    def _json(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, body):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if urllib.parse.urlparse(self.path).path == "/api/status":
            return self._json(status())
        return self._html(PAGE)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        body = {}
        try:
            raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            body = json.loads(raw) if raw else {}
        except Exception:
            body = {}
        if path == "/api/start":
            return self._json(start_stress(body))
        if path == "/api/stop":
            return self._json(stop_stress())
        return self._json({"ok": False, "error": "not found"})

    def log_message(self, *a):
        pass


PAGE = """<!doctype html><html lang=en><head>
<meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Stress &amp; HPA Panel</title>
<style>
  *{box-sizing:border-box}
  body{font-family:system-ui,sans-serif;margin:0;background:#0f1115;color:#e6e8ee}
  header{padding:16px 24px;background:#161a22;border-bottom:1px solid #2a2f3a;
         display:flex;gap:16px;align-items:center;flex-wrap:wrap}
  h1{font-size:20px;margin:0;color:#7aa3ff}
  .badge{font-size:12px;padding:4px 12px;border-radius:12px;font-weight:700}
  .idle{background:#333a48;color:#aab4c8}
  .run{background:#1b5e2a;color:#c8ffd0}
  main{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:16px;padding:20px}
  .card{background:#161a22;border:1px solid #2a2f3a;border-radius:10px;padding:16px}
  h2{font-size:13px;color:#9aa2b4;text-transform:uppercase;letter-spacing:1px;margin:0 0 10px}
  .row{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:14px}
  label{font-size:12px;color:#98a0b0;display:block;margin-bottom:4px}
  input{width:100%;padding:8px;border:1px solid #2f3644;border-radius:6px;
        background:#0e1117;color:#e6e8ee;font-size:14px}
  .btn{width:48%;padding:12px;border:0;border-radius:8px;font-size:15px;font-weight:800;cursor:pointer}
  #go{background:#1b6e2a;color:#fff} #stop{background:#8c2323;color:#fff}
  .btn:disabled{opacity:.35;cursor:not-allowed}
  pre{background:#0e1117;border:1px solid #262c38;border-radius:6px;padding:10px;
      font-size:12px;white-space:pre-wrap;max-height:300px;overflow:auto}
  a{color:#7aa3ff}
  .meta{font-size:12px;color:#8a92a3;margin-top:10px}
</style></head><body>
<header>
  <h1>DevOps Stress / HPA Panel</h1>
  <span id=badge class="badge idle">IDLE</span>
  <span id=pf class=meta></span>
</header>
<main>
  <div class=card>
    <h2>Load control</h2>
    <div class=row>
      <div><label>Concurrency (-c)</label><input id=c value=200></div>
      <div><label>Requests (-n)</label><input id=n value=40000></div>
      <div><label>Watch sec</label><input id=watch value=240></div>
    </div>
    <button id=go class=btn>GO (ramp up)</button>
    <button id=stop class=btn>STOP (ramp down)</button>
    <p class=meta>Runs stress-hpa.sh with sustained load for the watch window,
      then stops itself. Stop kills the whole process group instantly.</p>
  </div>
  <div class=card>
    <h2>HPA</h2>
    <pre id=hpa>loading…</pre>
    <h2>users-service pods</h2>
    <pre id=pods>loading…</pre>
  </div>
  <div class=card>
    <h2>Hypervisor VMs</h2>
    <pre id=vms>loading…</pre>
  </div>
  <div class=card>
    <h2>Run log</h2>
    <pre id=log>waiting…</pre>
  </div>
</main>
<script>
const $=id=>document.getElementById(id);
async function refresh(){
  const s=await (await fetch('/api/status')).json();
  $('hpa').textContent=s.hpa||'no data';
  $('pods').textContent=s.pods||'no data';
  $('vms').textContent=s.vms||'no data';
  $('meta').textContent='grafana: '+(s.grafana_pf||'down')+
    (s.pid? ' | stress pid '+s.pid+' | c='+s.c+' n='+s.n+' watch='+s.watch+'s':'');
  const b=$('badge');
  b.textContent=s.running?'RUNNING':'IDLE';
  b.className='badge '+(s.running?'running':'idle');
  $('go').disabled=s.running; $('stop').disabled=!s.running;
  if(s.log)$('log').textContent=s.log.trim();
}
async function post(p,b){return (await fetch(p,{method:'POST',
  headers:{'Content-Type':'application/json'},body:JSON.stringify(b||{})})).json();}
$('go').onclick=async()=>{
  const r=await post('/api/start',{c:$('c').value,n:$('n').value,watch:$('watch').value});
  alert(r.ok?('started: '+r.cmd):('FAIL: '+(r.error||''))); refresh();
};
$('stop').onclick=async()=>{const r=await post('/api/stop');refresh();};
refresh();setInterval(refresh,3000);
</script></body></html>
"""


def main():
    print(f"Stress panel: http://127.0.0.1:{PORT}  (Ctrl+C to quit)")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()