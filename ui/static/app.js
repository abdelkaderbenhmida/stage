"use strict";

const state = { data: null, view: "topology", search: "", timer: null, detail: null, configTab: "ci", runTimer: null };

const $ = (id) => document.getElementById(id);
const VIEWS = ["topology", "apps", "overview", "services", "config"];

function esc(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

let toastTimer = null;
function toast(msg, ok = true) {
  const t = $("toast");
  t.textContent = msg;
  t.className = `toast show ${ok ? "ok" : "err"}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.className = "toast"; }, 5000);
}

function loading(show = true) {
  const t = $("toast");
  if (show) {
    t.textContent = "…";
    t.className = "toast show";
  }
}

async function api(method, url, body) {
  const res = await fetch(url, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || data.message || `HTTP ${res.status}`);
  return data;
}

async function refresh() {
  try {
    await fetchData();
  } catch (e) {
    toast(`refresh failed: ${e.message}`, false);
  }
}

function pill(text, cls) {
  return `<span class="pill ${cls}">${esc(text)}</span>`;
}

function explainer(text) {
  return `<div class="explainer"><span class="ex-tag">?</span><div>${text}</div></div>`;
}

function fmtDur(secs) {
  if (secs < 60) return `${secs}s`;
  const m = Math.floor(secs / 60);
  if (m < 60) return `${m}m ${secs % 60}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

/* ─────────── tiny charts ─────────── */

function sparkline(canvas, vals, color) {
  if (!canvas) return;
  const dpr = window.devicePixelRatio || 1;
  const w = (canvas.clientWidth || 320) * dpr;
  const h = 20 * dpr;
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext("2d");
  if (!vals || vals.length < 2) return;
  const max = Math.max(...vals, 1);
  const step = w / (vals.length - 1);
  ctx.beginPath();
  vals.forEach((v, i) => {
    const x = i * step;
    const y = h - 3 - (v / max) * (h - 8);
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.strokeStyle = color || "#4cc2ff";
  ctx.lineWidth = 1.6 * dpr;
  ctx.stroke();
  ctx.lineTo(w, h);
  ctx.lineTo(0, h);
  ctx.closePath();
  ctx.fillStyle = (color || "#4cc2ff") + "22";
  ctx.fill();
}

function barRows(rows, maxW) {
  const max = Math.max(...rows.map((r) => r.v), 1);
  return rows
    .map((r) => `
      <div class="bar-row">
        <span class="lbl">${esc(r.l)}</span>
        <div class="bar-track"><div class="bar-fill" style="width:${Math.round((r.v / max) * 100)}%"></div></div>
        <span class="stat-mini" style="width:44px;text-align:right">${r.v}</span>
      </div>`)
    .join("");
}

/* ─────────── data load ─────────── */

async function fetchData() {
  const res = await fetch("/api/platform");
  if (!res.ok) throw new Error(`API ${res.status}`);
  state.data = await res.json();
  render();
}

/* ─────────── topbar ─────────── */

function renderTopbar() {
  const d = state.data;
  const ov = d.overview;
  const ready = ov.status === "ready";
  const dot = $("top-status");
  dot.className = `status-dot ${ready ? "ok" : "bad"} pulse`;
  $("top-repo").textContent = `${ov.revision.branch} @ ${ov.revision.commit} — ${ov.revision.message} (${ov.revision.author}, ${ov.revision.date})`;
  $("top-updated").textContent = `updated ${d.server_time} · up ${fmtDur(d.uptime_s)}`;
  const b = $("nav-services");
  if (b) b.textContent = ov.service_count;
  const ba = $("nav-apps");
  if (ba) ba.textContent = ov.app_count ?? state.data.apps.length;
}

function render() {
  renderTopbar();
  renderTopology();
  renderApps();
  renderOverview();
  renderServices();
  renderConfig();
}

/* ═══════════ PLATFORM HEALTH ═══════════ */

function renderTopology() {
  $("view-topology").innerHTML = `
    <div class="head">
      <div>
        <h1>Platform Health</h1>
        <div class="sub">Real status, right now — pods, CI, ArgoCD sync, and firing alerts per service. Click a service to act on it.</div>
      </div>
      <button class="act-btn" onclick="renderTopology()">↻ refresh</button>
    </div>
    <div id="health-body"><div class="cfg-loading">loading live status…</div></div>`;
  loadHealthBoard();
}

async function loadHealthBoard() {
  const body = $("health-body");
  if (!body) return;
  const d = state.data;
  const svcs = d.services;

  let ci = { reachable: false, runs: [] };
  let argo = { reachable: false, apps: [] };
  let alerts = { reachable: false, alerts: [] };
  let pods = { reachable: false, pods: [] };
  try {
    [ci, argo, alerts, pods] = await Promise.all([
      api("GET", "/api/live/ci").catch(() => ci),
      api("GET", "/api/live/argocd").catch(() => argo),
      api("GET", "/api/live/alerts").catch(() => alerts),
      api("GET", "/api/live/pods?namespace=devops-platform").catch(() => pods),
    ]);
  } catch (e) { /* keep defaults, render offline state per-source */ }

  const lastRun = ci.reachable ? ci.runs[0] : null;
  const ciBadge = !ci.reachable ? pill("offline", "muted")
    : lastRun.status !== "completed" ? pill(lastRun.status, "amber")
    : lastRun.conclusion === "success" ? pill("passing", "green") : pill("failing", "red");

  const alertsByService = {};
  (alerts.alerts || []).forEach((a) => {
    const k = a.service || "platform";
    alertsByService[k] = (alertsByService[k] || 0) + 1;
  });

  const podsByService = {};
  (pods.pods || []).forEach((p) => {
    const svc = p.name.replace(/-[a-f0-9]+-[a-z0-9]{5}$/, "");
    (podsByService[svc] = podsByService[svc] || []).push(p);
  });

  const argoByService = {};
  (argo.apps || []).forEach((a) => { argoByService[a.name] = a; });

  const rows = svcs.map((s) => {
    const myPods = Object.entries(podsByService).find(([k]) => k.includes(s.name) || s.name.includes(k))?.[1] || [];
    const podsUp = myPods.filter((p) => p.ready).length;
    const podHealth = !pods.reachable ? pill("cluster offline", "muted")
      : myPods.length === 0 ? pill("no pods", "red")
      : podsUp === myPods.length ? pill(`${podsUp}/${myPods.length} up`, "green") : pill(`${podsUp}/${myPods.length} up`, "amber");

    const argoApp = argoByService[s.name] || argoByService[s.name.replace(/^default-/, "")];
    const argoBadge = !argo.reachable ? pill("offline", "muted")
      : !argoApp ? pill("not deployed", "muted")
      : argoApp.sync_status === "Synced" ? pill("synced", "green") : pill(argoApp.sync_status, "amber");

    const nAlerts = alertsByService[s.name] || 0;
    const alertBadge = nAlerts > 0 ? pill(`${nAlerts} firing`, "red") : pill("clear", "green");

    return `
      <div class="health-row" onclick="showDetail('${esc(s.name)}')">
        <div class="health-name">
          <div class="mono" style="font-weight:800;color:var(--accent)">${esc(s.name)}</div>
          <div class="muted small">${esc(s.title)} · v${esc(s.version)}</div>
        </div>
        <div class="health-col"><div class="health-label">pods</div>${podHealth}</div>
        <div class="health-col"><div class="health-label">argocd</div>${argoBadge}</div>
        <div class="health-col"><div class="health-label">alerts</div>${alertBadge}</div>
      </div>`;
  }).join("");

  body.innerHTML = `
    <div class="grid kpis mb">
      <div class="kpi">
        <div class="k"><span>CI/CD</span></div>
        <div class="v">${ciBadge}</div>
        <div class="d">${lastRun ? esc(lastRun.workflowName) + " · " + esc(lastRun.headBranch) : "no data"}</div>
      </div>
      <div class="kpi">
        <div class="k"><span>ArgoCD apps</span></div>
        <div class="v ${argo.reachable ? "green" : ""}">${argo.reachable ? argo.apps.filter((a) => a.sync_status === "Synced").length + "/" + argo.apps.length : "–"}</div>
        <div class="d">synced</div>
      </div>
      <div class="kpi">
        <div class="k"><span>Alerts firing</span></div>
        <div class="v ${alerts.reachable && alerts.alerts.length ? "" : "green"}" style="${alerts.reachable && alerts.alerts.length ? "color:var(--red)" : ""}">${alerts.reachable ? alerts.alerts.length : "–"}</div>
        <div class="d">${alerts.reachable ? (alerts.alerts.length ? "needs attention" : "all clear") : "alertmanager offline"}</div>
      </div>
      <div class="kpi">
        <div class="k"><span>Pods running</span></div>
        <div class="v">${pods.reachable ? pods.pods.filter((p) => p.ready).length + "/" + pods.pods.length : "–"}</div>
        <div class="d">devops-platform ns</div>
      </div>
    </div>

    <div class="card mb" style="padding:16px 20px">
      <div style="font-weight:700;margin-bottom:4px">Need to fix something running right now?</div>
      <div class="muted small">Open <b>Operations</b> in the sidebar — trigger a CI rerun, sync an ArgoCD app, restart a pod, or open the real Grafana/Prometheus/ArgoCD dashboards.</div>
    </div>

    <div class="card">
      <div class="health-row health-head">
        <div class="health-name">Service</div>
        <div class="health-col">Pods</div>
        <div class="health-col">ArgoCD</div>
        <div class="health-col">Alerts</div>
      </div>
      ${rows || `<div class="empty">no services discovered</div>`}
    </div>`;
}

/* ═══════════ APPS (management console) ═══════════ */

function slugify(name) {
  return name.toLowerCase().replace(/[^a-z0-9-]/g, "-").replace(/-+/g, "-").replace(/^-|-$/g, "").slice(0, 40);
}

async function createAppFlow(rawInput) {
  const raw = rawInput ?? prompt("New app name (e.g. checkout):");
  if (!raw) return;
  const name = slugify(raw);
  if (!name) { toast("invalid app name", false); return; }
  try {
    loading(true);
    const r = await api("POST", "/api/apps", { name });
    toast(r.message || `✓ app '${name}' created`, true);
    await refresh();
    state.search = "";
    switchView("apps");
    const inp = $("new-app-input");
    if (inp) inp.value = "";
  } catch (e) { toast("✕ " + e.message, false); }
}

async function deleteAppFlow(appName) {
  if (!confirm(`Delete app '${appName}' and ALL its services? The platform will prune everything downstream.`)) return;
  try {
    loading(true);
    const r = await api("DELETE", `/api/apps/${appName}`);
    toast(r.message || `✓ app '${appName}' deleted`, true);
    await refresh();
  } catch (e) { toast("✕ " + e.message, false); }
}

async function addServiceFlow(appName, raw) {
  if (!raw) {
    raw = prompt(`New service in app '${appName}' (e.g. cart):`);
    if (!raw) return;
  }
  const name = slugify(raw);
  if (!name) { toast("invalid service name", false); return; }
  try {
    loading(true);
    const r = await api("POST", `/api/apps/${appName}/services`, { name });
    toast(r.message || `✓ service '${name}' added`, true);
    await refresh();
    const inp = $(`add-svc-${appName}`);
    if (inp) inp.value = "";
  } catch (e) { toast("✕ " + e.message, false); }
}

async function deleteServiceFlow(appName, svcName) {
  if (!confirm(`Delete service '${svcName}'? CI stops building it, ArgoCD prunes its app, Vault role idles.`)) return;
  try {
    loading(true);
    const r = await api("DELETE", `/api/apps/${appName}/services/${svcName}`);
    toast(r.message || `✓ service '${svcName}' deleted`, true);
    await refresh();
  } catch (e) { toast("✕ " + e.message, false); }
}

function renderApps() {
  const d = state.data;
  const apps = d.apps;

  const cards = apps.map((a) => {
    const isDefault = a.name === "default";
    const svcList = d.services
      .filter((s) => s.app === a.name)
      .map((s) => `
        <div class="svc-row">
          <span class="svc-name" style="color:var(--accent)">${esc(s.name)}</span>
          <span class="svc-meta">v${esc(s.version)} · ${s.endpoints.length} routes · ${s.loc} LOC</span>
          ${s.uses_vault ? pill("vault", "accent") : pill("no vault", "muted")}
          <span class="sp"></span>
          <button class="act-btn danger" data-del-svc="${esc(a.name)}" data-svc="${esc(s.key.split("/").pop())}">✕ delete</button>
        </div>`).join("");

    return `
      <div class="card app-card" style="cursor:pointer" onclick="switchView('services')">
        <div class="app-head">
          <div class="app-icon">${esc(a.name[0].toUpperCase())}</div>
          <div>
            <div class="app-name">${isDefault ? "default <span class='muted small'>(flat app/*/services)</span>" : esc(a.name)}</div>
            <div class="app-path">${esc(a.path)}/</div>
          </div>
          <span class="sp"></span>
          ${pill(`${a.services.length} service${a.services.length === 1 ? "" : "s"}`, isDefault ? "muted" : "accent")}
          <button class="btn danger sm" onclick="event.stopPropagation()" data-del-app="${esc(a.name)}" ${isDefault ? "disabled title='legacy flat group — not deletable'" : ""}>delete app</button>
        </div>
        ${svcList || `<div class="empty">no services yet</div>`}
        <div class="add-svc-panel" onclick="event.stopPropagation()">
          <div class="add-svc-label">＋ Add a service to <b>${esc(a.name)}</b></div>
          <div class="add-svc">
            <input placeholder="e.g. cart, payments…" id="add-svc-${esc(a.name)}" onkeydown="if(event.key==='Enter'){addServiceFlow('${esc(a.name)}',event.target.value)}">
            <button class="btn sm" onclick="addServiceFlow('${esc(a.name)}',$('add-svc-${esc(a.name)}').value)">Add</button>
          </div>
        </div>
      </div>`;
  }).join("");

  $("view-apps").innerHTML = `
    ${explainer(`Your <b>management console</b>. Create an app, add services inside it — the UI writes real files (app/&lt;app&gt;/&lt;service&gt;/main.py). The platform is discovery-driven: CI builds the new service, ArgoCD deploys it, Vault provisions secrets, Prometheus watches it. Delete → everything shrinks back. Flat legacy services live in the "default" group.`)}
    <div class="head">
      <div>
        <h1>Apps &amp; Services</h1>
        <div class="sub">Create and delete apps + services live. Every change lands in the repo — commit and push to trigger the full pipeline.</div>
      </div>
    </div>

    <div class="card new-app-panel mb">
      <div class="new-app-step">
        <div class="step-num">1</div>
        <div class="step-body">
          <div class="step-title">Create a new app</div>
          <div class="step-sub">A folder under <span class="mono">app/</span> that groups related services.</div>
          <div class="add-svc mt1">
            <input placeholder="e.g. checkout, billing…" id="new-app-input" onkeydown="if(event.key==='Enter'){createAppFlow(event.target.value)}">
            <button class="btn" onclick="createAppFlow($('new-app-input').value)">＋ Create app</button>
          </div>
        </div>
      </div>
      <div class="new-app-step">
        <div class="step-num">2</div>
        <div class="step-body">
          <div class="step-title">Add services inside it</div>
          <div class="step-sub">Scroll down to any app card below — type a service name in its box and press Enter.</div>
        </div>
      </div>
      <div class="kbd" style="align-self:flex-start;margin-top:4px">created files are local — git add/commit/push to activate CI, ArgoCD, Vault, monitoring</div>
    </div>

    <div class="grid cards">${cards || `<div class="card empty">no apps</div>`}</div>`;

  $("view-apps").querySelectorAll("[data-del-app]").forEach((b) => {
    b.addEventListener("click", () => deleteAppFlow(b.dataset.delApp));
  });
  $("view-apps").querySelectorAll("[data-del-svc]").forEach((b) => {
    b.addEventListener("click", () => deleteServiceFlow(b.dataset.delSvc, b.dataset.svc));
  });
}

/* ═══════════ OVERVIEW ═══════════ */

function renderOverview() {
  const d = state.data;
  const ov = d.overview;
  const locs = d.services.map((s) => s.loc);
  const steps = d.ci.jobs.map((j) => j.steps.length);
  const ruleCounts = {};
  d.monitoring.rules.forEach((r) => { ruleCounts[r.group] = (ruleCounts[r.group] || 0) + 1; });
  const rules = Object.values(ruleCounts);
  const kinds = Object.entries(d.helm.counts).map(([k, v]) => ({ l: k, v }));

  const checks = Object.entries(ov.layer_checks).map(([k, ok]) => `
    <div class="card" style="display:flex;align-items:center;gap:12px;padding:14px 16px">
      <span class="status-dot ${ok ? "ok" : "bad"}"></span>
      <div>
        <div class="mono" style="font-weight:700;font-size:13px">${esc(k)}</div>
        <div class="muted small">${ok ? "configured — auto-adapts" : "missing"}</div>
      </div>
    </div>`).join("");

  $("view-overview").innerHTML = `
    ${explainer(`Health dashboard. Numbers tell you what exists <b>today</b>: services discovered, k8s objects the Helm chart would render, CI jobs, SLO rules, Vault objects, uptime. Green dot = layer present and wired.`)}
    <div class="head">
      <div>
        <h1>Overview</h1>
        <div class="sub">Discovery-driven platform · ${esc(ov.repo)} · "${esc(ov.revision.message)}"</div>
      </div>
      <div class="header-actions">${ov.status === "ready" ? pill("PLATFORM READY", "green") : pill("PARTIAL", "amber")}</div>
    </div>

    <div class="grid kpis mb">
      <div class="kpi">
        <div class="k"><span>Services</span><span class="status-dot ok"></span></div>
        <div class="v accent">${ov.service_count}</div>
        <div class="d">app/*/main.py</div>
        <canvas data-spark="services"></canvas>
      </div>
      <div class="kpi">
        <div class="k"><span>Helm objects</span>${pill("live", "accent")}</div>
        <div class="v">${d.helm.total}</div>
        <div class="d">${esc(d.helm.formula)}</div>
        <canvas data-spark="helm"></canvas>
      </div>
      <div class="kpi">
        <div class="k"><span>CI jobs</span></div>
        <div class="v violet">${d.ci.jobs.length}</div>
        <div class="d">${d.ci.uses_fromjson.length} dynamic matrices</div>
        <canvas data-spark="ci"></canvas>
      </div>
      <div class="kpi">
        <div class="k"><span>SLO rules</span></div>
        <div class="v green">${d.monitoring.rules.length}</div>
        <div class="d">${d.monitoring.slo_labels.join(" · ")}</div>
        <canvas data-spark="rules"></canvas>
      </div>
      <div class="kpi">
        <div class="k"><span>Vault objects</span></div>
        <div class="v" style="color:#fb923c">${d.vault.objects.length}</div>
        <div class="d">setup job loops per service</div>
      </div>
      <div class="kpi">
        <div class="k"><span>Uptime</span></div>
        <div class="v">${fmtDur(d.uptime_s).split(" ")[0]}</div>
        <div class="d">since ${d.server_time}</div>
      </div>
    </div>

    <h2 class="mt mb">Platform layers</h2>
    <div class="grid cards mb">${checks}</div>

    <h2 class="mb">Services</h2>
    <div class="grid cards">
      ${d.services.map((s) => `
      <div class="card" style="cursor:pointer" data-ovc-svc="${esc(s.name)}">
        <div class="row" style="justify-content:space-between">
          <div>
            <div class="mono" style="font-weight:800;font-size:15px;color:var(--accent)">${esc(s.name)}</div>
            <div class="muted small">${esc(s.title)} · v${esc(s.version)}</div>
          </div>
          ${s.uses_vault ? pill("vault", "accent") : pill("no vault", "muted")}
        </div>
        <div class="chips mt">${s.endpoints.map((e) => `<span class="chip accent">${esc(e)}</span>`).join("")}</div>
        <div class="mt1 muted small">${s.loc} LOC · ${s.requirements_num} pinned deps${s.has_requirements ? "" : " · no requirements.txt"}</div>
      </div>`).join("")}
    </div>`;

  $("view-overview").querySelectorAll("[data-ovc-svc]").forEach((c) => {
    c.addEventListener("click", () => {
      showDetail(c.dataset.ovcSvc);
    });
  });

  $("view-overview").querySelectorAll("canvas[data-spark]").forEach((cv) => {
    const m = {
      services: [locs, "#4cc2ff"],
      helm: [kinds.map((k) => k.v), "#fbbf24"],
      ci: [steps, "#a78bfa"],
      rules: [rules, "#34d399"],
    }[cv.dataset.spark];
    if (m) sparkline(cv, m[0], m[1]);
  });
}

/* ═══════════ SERVICES ═══════════ */

function renderServices() {
  const d = state.data;
  const q = state.search.toLowerCase();
  const list = d.services.filter((s) => !q || s.name.toLowerCase().includes(q) || s.title.toLowerCase().includes(q));

  const rows = list.map((s) => `
    <tr onclick="showDetail('${esc(s.name)}')" style="cursor:pointer">
      <td><span class="mono" style="color:var(--accent);font-weight:700">${esc(s.name)}</span></td>
      <td><span class="chip">${esc(s.app)}</span></td>
      <td>${esc(s.title)}</td>
      <td class="mono">${esc(s.version)}</td>
      <td class="mono">${s.loc}</td>
      <td class="mono">${s.requirements_num}</td>
      <td>${s.has_requirements ? pill("pinned", "green") : pill("none", "red")}</td>
      <td>${s.uses_vault ? pill("vault", "accent") : pill("none", "muted")}</td>
      <td><div class="chips" style="gap:4px">${s.endpoints.map((e) => `<span class="chip">${esc(e)}</span>`).join("")}</div></td>
      <td onclick="event.stopPropagation()"><button class="act-btn danger" data-del-svc-row="${esc(s.app)}" data-svc-row="${esc(s.key.split("/").pop())}">✕</button></td>
    </tr>`).join("");

  $("view-services").innerHTML = `
    ${explainer(`List of your microservices — flat ones in the <b>default</b> group, grouped ones under their app. Each is just a folder with main.py. Delete a service here and every layer adapts.`)}
    <div class="head">
      <div>
        <h1>Services</h1>
        <div class="sub">Discovered live from <span class="mono">app/*/main.py</span> (flat) and <span class="mono">app/*/*/main.py</span> (grouped apps).</div>
      </div>
      <div class="header-actions">
        <div class="search"><span class="ico">⌕</span>
          <input id="svc-search" placeholder="filter services…" value="${esc(state.search)}">
        </div>
      </div>
    </div>

    <div class="grid two mb">
      <div class="card">
        <div class="card-title">Code volume (LOC)</div>
        ${barRows(d.services.map((s) => ({ l: s.name, v: s.loc })))}
      </div>
      <div class="card">
        <div class="card-title">Pinned dependencies</div>
        ${barRows(d.services.map((s) => ({ l: s.name, v: s.requirements_num })))}
      </div>
    </div>

    <div class="card" style="overflow-x:auto">
      <table>
        <thead><tr>
          <th>Name</th><th>App</th><th>Title</th><th>Version</th><th>LOC</th><th>Deps</th>
          <th>Requirements</th><th>Vault</th><th>Endpoints</th><th></th>
        </tr></thead>
        <tbody>${rows || `<tr><td colspan="10" class="empty">No services match “${esc(state.search)}”.</td></tr>`}</tbody>
      </table>
    </div>`;

  $("view-services").querySelectorAll("[data-del-svc-row]").forEach((b) => {
    b.addEventListener("click", () => deleteServiceFlow(b.dataset.delSvcRow, b.dataset.svcRow));
  });

  const input = $("svc-search");
  if (input) {
    input.oninput = () => { state.search = input.value; renderServices(); };
  }
}

/* ═══════════ CONFIGURATION — live status + real actions ═══════════ */


function offlineCard(label, err) {
  return `<div class="cfg-offline">
    <span class="status-dot bad"></span>
    <div>
      <div class="cfg-offline-title">${esc(label)} unreachable</div>
      <div class="cfg-offline-err mono">${esc(err || "connection failed")}</div>
    </div>
  </div>`;
}

function renderConfig() {
  const tabs = ["ci", "argocd", "vault", "monitoring"];
  const tabHtml = tabs.map((t) => `
    <button class="cfg-tab ${t === state.configTab ? "active" : ""}" onclick="switchConfigTab('${t}')">
      ${t.toUpperCase()}
    </button>`).join("");

  $("view-config").innerHTML = `
    <div class="head" style="padding:0 0 16px">
      <h1>Operations</h1>
      <div class="sub">Real status from GitHub Actions, ArgoCD, Vault and the cluster — with buttons that actually do things.</div>
    </div>
    <div class="cfg-tabs">${tabHtml}</div>
    <div class="cfg-content" id="cfg-body"><div class="cfg-loading">loading live status…</div></div>`;

  loadConfigTab(state.configTab);
}

function switchConfigTab(t) {
  state.configTab = t;
  renderConfig();
}

async function loadConfigTab(tab) {
  const body = $("cfg-body");
  if (!body) return;
  try {
    if (tab === "ci") await renderCiTab(body);
    else if (tab === "argocd") await renderArgocdTab(body);
    else if (tab === "vault") await renderVaultTab(body);
    else if (tab === "monitoring") await renderMonitoringTab(body);
  } catch (e) {
    body.innerHTML = offlineCard(tab, e.message);
  }
}

function refreshConfigTab() { loadConfigTab(state.configTab); }

/* ─── shared: embed real dashboard inline, no login, no new tab ─── */

async function openDashboard(tool, label) {
  try {
    loading(true);
    const r = await api("POST", `/api/live/dashboard/${tool}/open`);
    toast(`✓ ${label} live`, true);
    showEmbeddedDashboard(tool, label, r.url);
  } catch (e) { toast("✕ " + e.message, false); }
}

function showEmbeddedDashboard(tool, label, url) {
  $("detail-title").textContent = label;
  $("detail-content").innerHTML = `
    <div class="embed-bar">
      <span class="mono small muted">${esc(url)}</span>
      <a class="act-btn" href="${esc(url)}" target="_blank" rel="noopener">↗ full tab</a>
    </div>
    <iframe class="embed-frame" src="${esc(url)}" sandbox="allow-scripts allow-same-origin allow-forms allow-popups"></iframe>`;
  $("detail-panel").classList.add("show", "wide");
  $("detail-overlay").classList.add("show");
}

/* ─── shared: log viewer modal ─── */

function showLogs(title, logText) {
  $("detail-title").textContent = title;
  $("detail-content").innerHTML = `<pre class="cfg-code" style="white-space:pre-wrap;max-height:70vh">${esc(logText || "(empty)")}</pre>`;
  $("detail-panel").classList.add("show");
  $("detail-overlay").classList.add("show");
}

async function viewPodLogs(namespace, pod, label) {
  try {
    loading(true);
    const r = await api("GET", `/api/live/pods/${namespace}/${pod}/logs?tail=200`);
    if (!r.reachable) { toast("✕ " + r.error, false); return; }
    showLogs(label || pod, r.log);
  } catch (e) { toast("✕ " + e.message, false); }
}

async function restartPod(namespace, pod) {
  if (!confirm(`Restart pod '${pod}' in namespace '${namespace}'? It will be deleted and recreated by its controller.`)) return;
  try {
    loading(true);
    const r = await api("POST", "/api/live/pods/restart", { namespace, pod });
    toast("✓ " + r.message, true);
  } catch (e) { toast("✕ " + e.message, false); }
}

async function viewCiLogs(runId) {
  try {
    loading(true);
    const r = await api("GET", `/api/live/ci/${runId}/logs`);
    if (!r.reachable) { toast("✕ " + r.error, false); return; }
    showLogs(`Run ${runId} logs`, r.log);
  } catch (e) { toast("✕ " + e.message, false); }
}

/* ─── CI/CD: real GitHub Actions ─── */

async function renderCiTab(body) {
  body.innerHTML = `<div class="cfg-loading">fetching workflow runs…</div>`;
  const data = await api("GET", "/api/live/ci");
  if (!data.reachable) { body.innerHTML = offlineCard("GitHub Actions", data.error); return; }

  const statusPill = (r) => {
    if (r.status === "in_progress" || r.status === "queued") return pill(r.status, "amber");
    if (r.conclusion === "success") return pill("success", "green");
    if (r.conclusion === "failure") return pill("failure", "red");
    if (r.conclusion === "cancelled") return pill("cancelled", "muted");
    return pill(r.conclusion || r.status || "unknown", "muted");
  };

  const rows = data.runs.map((r) => `
    <div class="run-row">
      <div class="run-main">
        ${statusPill(r)}
        <div>
          <div class="run-title">${esc(r.displayTitle)}</div>
          <div class="run-meta">${esc(r.workflowName)} · ${esc(r.headBranch)} · ${esc(r.event)} · ${esc(new Date(r.createdAt).toLocaleString())}</div>
        </div>
      </div>
      <div class="run-actions">
        <a class="act-btn" href="${esc(r.url)}" target="_blank" rel="noopener">↗ open in GitHub</a>
        ${r.status === "completed" ? `<button class="act-btn" onclick="viewCiLogs('${r.databaseId}')">▤ logs</button>` : ""}
        ${r.conclusion === "failure" ? `<button class="act-btn" onclick="ciRerun('${r.databaseId}')">↻ rerun failed</button>` : ""}
        ${r.status === "in_progress" || r.status === "queued" ? `<button class="act-btn danger" onclick="ciCancel('${r.databaseId}')">✕ cancel</button>` : ""}
      </div>
    </div>`).join("");

  body.innerHTML = `
    <div class="cfg-toolbar">
      <span class="cfg-repo">${esc(data.repo)}</span>
      <span class="sp"></span>
      <button class="btn sm" onclick="ciTrigger()">▶ run workflow</button>
      <button class="act-btn" onclick="refreshConfigTab()">↻ refresh</button>
    </div>
    <div class="run-list">${rows || `<div class="empty">no runs found</div>`}</div>`;
}

async function ciTrigger() {
  try {
    loading(true);
    const r = await api("POST", "/api/live/ci/trigger", { workflow: "ci-cd.yml" });
    toast("✓ " + r.message, true);
    setTimeout(refreshConfigTab, 2000);
  } catch (e) { toast("✕ " + e.message, false); }
}

async function ciRerun(runId) {
  if (!confirm(`Re-run failed jobs for run ${runId}?`)) return;
  try {
    loading(true);
    const r = await api("POST", "/api/live/ci/rerun", { run_id: runId });
    toast("✓ " + r.message, true);
    setTimeout(refreshConfigTab, 2000);
  } catch (e) { toast("✕ " + e.message, false); }
}

async function ciCancel(runId) {
  if (!confirm(`Cancel run ${runId}?`)) return;
  try {
    loading(true);
    const r = await api("POST", "/api/live/ci/cancel", { run_id: runId });
    toast("✓ " + r.message, true);
    setTimeout(refreshConfigTab, 2000);
  } catch (e) { toast("✕ " + e.message, false); }
}

/* ─── ArgoCD: real Application CRDs + sync ─── */

async function renderArgocdTab(body) {
  body.innerHTML = `<div class="cfg-loading">fetching applications…</div>`;
  const data = await api("GET", "/api/live/argocd");
  if (!data.reachable) { body.innerHTML = offlineCard("ArgoCD / cluster", data.error); return; }

  const syncCls = (s) => s === "Synced" ? "green" : s === "OutOfSync" ? "amber" : "muted";
  const healthCls = (s) => s === "Healthy" ? "green" : s === "Degraded" ? "red" : s === "Progressing" ? "amber" : "muted";

  const rows = (data.apps || []).map((a) => `
    <div class="app-row">
      <div class="app-row-main clickable" onclick="showArgoResources('${esc(a.name)}')">
        <div class="app-row-name">${esc(a.name)}</div>
        <div class="app-row-meta">${esc(a.path)} @ ${esc(a.revision || "–")} · click for resources</div>
      </div>
      ${pill(a.sync_status, syncCls(a.sync_status))}
      ${pill(a.health_status, healthCls(a.health_status))}
      <div class="run-actions">
        <button class="act-btn" onclick="argoRefresh('${esc(a.name)}')">↻ refresh</button>
        <button class="act-btn" onclick="argoSync('${esc(a.name)}')">⇌ sync</button>
      </div>
    </div>`).join("");

  body.innerHTML = `
    <div class="cfg-toolbar">
      <span class="cfg-repo">${data.apps.length} applications</span>
      <span class="sp"></span>
      <button class="btn sm" onclick="openDashboard('argocd','ArgoCD UI')">⧉ open ArgoCD dashboard</button>
      <button class="act-btn" onclick="showArgoCreds()">🔑 admin password</button>
      <button class="act-btn" onclick="refreshConfigTab()">↻ refresh all</button>
    </div>
    <div class="run-list">${rows || `<div class="empty">no applications found</div>`}</div>`;
}

async function argoSync(name) {
  if (!confirm(`Trigger sync for '${name}'? This applies the latest git state to the cluster.`)) return;
  try {
    loading(true);
    const r = await api("POST", "/api/live/argocd/sync", { name });
    toast("✓ " + r.message, true);
    setTimeout(refreshConfigTab, 1500);
  } catch (e) { toast("✕ " + e.message, false); }
}

async function argoRefresh(name) {
  try {
    loading(true);
    const r = await api("POST", "/api/live/argocd/refresh", { name });
    toast("✓ " + r.message, true);
    setTimeout(refreshConfigTab, 1500);
  } catch (e) { toast("✕ " + e.message, false); }
}

async function showArgoResources(appName) {
  try {
    loading(true);
    const r = await api("GET", `/api/live/argocd/${appName}/resources`);
    if (!r.reachable) { toast("✕ " + r.error, false); return; }
    const lines = r.resources.map((res) =>
      `${(res.kind || "").padEnd(24)} ${(res.name || "").padEnd(30)} ${res.status || ""}  ${res.health || ""}`);
    showLogs(`${appName} — resources (${r.resources.length})`, lines.join("\n"));
  } catch (e) { toast("✕ " + e.message, false); }
}

async function showVaultSecretMeta(service) {
  try {
    loading(true);
    const r = await api("GET", `/api/live/vault/secrets/${service}`);
    if (!r.reachable) { toast("✕ " + r.error, false); return; }
    showLogs(`${service} — secret metadata`,
      `version: ${r.version}\ncreated: ${r.created_time}\nfields: ${r.field_names.join(", ")}\n\n(values are never shown here — read-only metadata for audit)`);
  } catch (e) { toast("✕ " + e.message, false); }
}

async function showArgoCreds() {
  try {
    loading(true);
    const r = await api("GET", "/api/live/argocd/admin-password");
    if (!r.reachable) { toast("✕ " + r.error, false); return; }
    showLogs("ArgoCD admin credentials", `username: ${r.username}\npassword: ${r.password}`);
  } catch (e) { toast("✕ " + e.message, false); }
}

/* ─── Vault: real seal status + secret listing ─── */

async function renderVaultTab(body) {
  body.innerHTML = `<div class="cfg-loading">checking vault…</div>`;
  const data = await api("GET", "/api/live/vault");
  if (!data.reachable) { body.innerHTML = offlineCard("Vault", data.error); return; }

  let secretsHtml = `<div class="cfg-loading">listing secrets…</div>`;
  body.innerHTML = `
    <div class="cfg-toolbar">
      <span class="status-dot ${data.sealed ? "bad" : "ok"}"></span>
      <span class="cfg-repo">${data.sealed ? "SEALED" : "UNSEALED"} · v${esc(data.version)}</span>
      <span class="sp"></span>
      <button class="btn sm" onclick="openDashboard('vault','Vault UI')">⧉ open Vault dashboard</button>
      <button class="act-btn" onclick="refreshConfigTab()">↻ refresh</button>
    </div>
    <div class="grid two mb">
      <div class="cfg-target"><div class="cfg-item"><span>Initialized</span><span class="val">${data.initialized ? "✓ yes" : "✗ no"}</span></div></div>
      <div class="cfg-target"><div class="cfg-item"><span>HA enabled</span><span class="val">${data.ha_enabled ? "✓ yes" : "– no"}</span></div></div>
    </div>
    <div class="cfg-section"><h3>Secrets at secret/devops-platform</h3><div id="vault-secrets">${secretsHtml}</div></div>`;

  try {
    const sec = await api("GET", "/api/live/vault/secrets");
    const box = $("vault-secrets");
    if (!box) return;
    if (!sec.reachable) { box.innerHTML = offlineCard("secret listing", sec.error); return; }
    box.innerHTML = (sec.keys || []).map((k) => `<div class="cfg-item clickable" onclick="showVaultSecretMeta('${esc(k)}')"><span class="mono">${esc(k)}</span><span class="val">view metadata →</span></div>`).join("") || `<div class="empty">no keys</div>`;
  } catch (e) { /* leave loading state message */ }
}

/* ─── Monitoring: real firing alerts via Alertmanager ─── */

async function renderMonitoringTab(body) {
  body.innerHTML = `<div class="cfg-loading">checking alertmanager…</div>`;
  const data = await api("GET", "/api/live/alerts");
  if (!data.reachable) { body.innerHTML = offlineCard("Alertmanager", data.error); return; }

  const sevCls = (s) => s === "critical" ? "red" : s === "warning" ? "amber" : "muted";
  const rows = (data.alerts || []).map((a) => `
    <div class="app-row">
      <div class="app-row-main">
        <div class="app-row-name">${esc(a.name)}</div>
        <div class="app-row-meta">${esc(a.service || "–")} · firing since ${esc(new Date(a.starts_at).toLocaleString())}</div>
      </div>
      ${pill(a.severity || "info", sevCls(a.severity))}
      ${pill(a.state, "amber")}
    </div>`).join("");

  body.innerHTML = `
    <div class="cfg-toolbar">
      <span class="status-dot ${data.alerts.length ? "bad" : "ok"}"></span>
      <span class="cfg-repo">${data.alerts.length} alerts firing</span>
      <span class="sp"></span>
      <button class="btn sm" onclick="openDashboard('grafana','Grafana')">⧉ open Grafana</button>
      <button class="btn sm" onclick="openDashboard('prometheus','Prometheus')">⧉ open Prometheus</button>
      <button class="btn sm" onclick="openDashboard('alertmanager','Alertmanager')">⧉ open Alertmanager</button>
      <button class="act-btn" onclick="refreshConfigTab()">↻ refresh</button>
    </div>
    <div class="run-list">${rows || `<div class="empty">no alerts firing — all green</div>`}</div>

    <div class="cfg-section mt">
      <h3>Alert history (received by alert-sink)</h3>
      <div id="alert-history"><div class="cfg-loading">loading history…</div></div>
    </div>

    <div class="cfg-section mt">
      <h3>Pod health (monitoring namespace)</h3>
      <div id="mon-pods"><div class="cfg-loading">loading pods…</div></div>
    </div>`;

  loadMonitoringPods();
  loadAlertHistory();
}

async function loadAlertHistory() {
  const box = $("alert-history");
  if (!box) return;
  try {
    const r = await api("GET", "/api/live/alerts/history?limit=50");
    if (!r.reachable) { box.innerHTML = offlineCard("alert-sink", r.error); return; }
    const sevCls = (s) => s === "critical" ? "red" : s === "warning" ? "amber" : "muted";
    box.innerHTML = r.alerts.map((a) => `
      <div class="cfg-item">
        <span>${pill(a.status, a.status === "firing" ? "red" : "green")} ${pill(a.severity || "info", sevCls(a.severity))} ${esc(a.name)} <span class="muted small">${esc(a.service || "")}</span></span>
        <span class="val small">${esc(new Date(a.received_at).toLocaleString())}</span>
      </div>`).join("") || `<div class="empty">no history yet</div>`;
  } catch (e) { box.innerHTML = offlineCard("alert-sink", e.message); }
}

async function loadMonitoringPods() {
  try {
    const r = await api("GET", "/api/live/pods?namespace=monitoring");
    const box = $("mon-pods");
    if (!box) return;
    if (!r.reachable) { box.innerHTML = offlineCard("pods", r.error); return; }
    box.innerHTML = r.pods.map((p) => `
      <div class="app-row">
        <div class="app-row-main">
          <div class="app-row-name">${esc(p.name)}</div>
          <div class="app-row-meta">${esc(p.phase)} · restarts: ${p.restarts}</div>
        </div>
        ${pill(p.ready ? "ready" : "not ready", p.ready ? "green" : "red")}
        <div class="run-actions">
          <button class="act-btn" onclick="viewPodLogs('monitoring','${esc(p.name)}','${esc(p.name)} logs')">▤ logs</button>
          <button class="act-btn danger" onclick="restartPod('monitoring','${esc(p.name)}')">↻ restart</button>
        </div>
      </div>`).join("") || `<div class="empty">no pods</div>`;
  } catch (e) { /* ignore */ }
}

/* ═══════════ DETAIL PANEL ═══════════ */

function showDetail(serviceName) {
  const svc = state.data.services.find((s) => s.name === serviceName);
  if (!svc) return;

  state.detail = serviceName;
  const helm = state.data.helm.per_service?.[serviceName] || [];
  const ci = state.data.ci.jobs.filter((j) => j.matrix.has_matrix && j.matrix.keys.includes("service"));
  const vault = state.data.vault.per_service || {};

  let content = `
    <div class="detail-section">
      <div class="detail-section-title">Service info</div>
      <div class="detail-item">
        <div class="detail-item-label">Name</div>
        <div class="detail-item-value">${esc(svc.name)}</div>
      </div>
      <div class="detail-item">
        <div class="detail-item-label">Path</div>
        <div class="detail-item-value">${esc(svc.key)}</div>
      </div>
      <div class="detail-item">
        <div class="detail-item-label">Version</div>
        <div class="detail-item-value">${esc(svc.version)}</div>
      </div>
      <div class="detail-item">
        <div class="detail-item-label">LOC</div>
        <div class="detail-item-value">${svc.loc}</div>
      </div>
    </div>

    <div class="detail-section">
      <div class="detail-section-title">Endpoints</div>
      <div class="chips" style="gap:6px">${svc.endpoints.map((e) => `<span class="chip">${esc(e)}</span>`).join("")}</div>
    </div>

    <div class="detail-section">
      <div class="detail-section-title">Dependencies</div>
      <div class="detail-item">
        <div class="detail-item-label">Pinned deps</div>
        <div class="detail-item-value">${svc.requirements_num}</div>
      </div>
      <div class="detail-item">
        <div class="detail-item-label">Vault integration</div>
        <div class="detail-item-value">${svc.uses_vault ? "✓ enabled" : "– disabled"}</div>
      </div>
    </div>

    <div class="detail-section">
      <div class="detail-section-title">Kubernetes</div>
      ${helm.length ? helm.map((k) => `<span class="chip">${esc(k)}</span>`).join("") : "<span class='muted'>–</span>"}
    </div>

    <div class="detail-section">
      <div class="detail-section-title">CI Pipeline</div>
      ${ci.length ? `<span class="chip">${ci[0].name}</span> ${ci.length} matrix jobs` : "<span class='muted'>– no CI</span>"}
    </div>

    <div class="detail-section">
      <div class="detail-section-title">Live runtime</div>
      <div id="detail-runtime"><div class="cfg-loading">loading pods, events, rollout history…</div></div>
    </div>
  `;

  $("detail-title").textContent = svc.name;
  $("detail-content").innerHTML = content;
  $("detail-panel").classList.add("show");
  $("detail-overlay").classList.add("show");

  loadServiceRuntime(svc.name);
}

async function loadServiceRuntime(service) {
  const box = $("detail-runtime");
  if (!box) return;
  try {
    const r = await api("GET", `/api/live/services/${service}/drilldown`);
    if (!r.reachable) { box.innerHTML = offlineCard("cluster", r.error); return; }

    const podRows = r.pods.map((p) => {
      if (!p.reachable) return `<div class="app-row"><div class="app-row-main">${offlineCard(p.name || "pod", p.error)}</div></div>`;
      const c = p.containers[0] || {};
      const m = r.metrics[p.name];
      const notReady = c.waiting_reason && `<span class="val" style="color:var(--red)">${esc(c.waiting_reason)}${c.waiting_message ? ": " + esc(c.waiting_message) : ""}</span>`;
      return `
        <div class="app-row">
          <div class="app-row-main">
            <div class="app-row-name">${esc(p.name)}</div>
            <div class="app-row-meta mono small">${esc(c.image || "–")}</div>
          </div>
          ${pill(p.phase, p.phase === "Running" ? "green" : "amber")}
          ${notReady || pill(c.ready ? "ready" : "not ready", c.ready ? "green" : "red")}
          <span class="mono small muted">${m ? `${m.cpu} · ${m.memory}` : "–"}</span>
          <span class="mono small muted">restarts: ${c.restart_count ?? 0}</span>
          <div class="run-actions">
            <button class="act-btn" onclick="viewPodLogs('devops-platform','${esc(p.name)}','${esc(p.name)} logs')">▤ logs</button>
            <button class="act-btn danger" onclick="restartPod('devops-platform','${esc(p.name)}')">↻ restart</button>
          </div>
        </div>`;
    }).join("") || `<div class="empty">no pods running</div>`;

    const eventRows = r.events.map((e) => `
      <div class="cfg-item"><span>${pill(e.reason, e.type === "Warning" ? "red" : "muted")} ${esc(e.message)}</span><span class="val small">${e.count > 1 ? `×${e.count}` : ""}</span></div>
    `).join("") || `<div class="empty">no recent events</div>`;

    const revRows = (r.rollout.reachable ? r.rollout.revisions : []).slice().reverse().map((rev) => `
      <div class="cfg-item">
        <span>rev ${rev.revision} <span class="mono small muted">${esc(rev.image || "")}</span></span>
        <button class="act-btn" onclick="rollbackDeployment('devops-platform','${esc(service)}',${rev.revision})">↩ rollback here</button>
      </div>`).join("") || `<div class="empty">no rollout history</div>`;

    box.innerHTML = `
      <div class="detail-section"><div class="detail-section-title">Pods (${r.pods.length})</div>${podRows}</div>
      <div class="detail-section"><div class="detail-section-title">Recent events</div>${eventRows}</div>
      <div class="detail-section"><div class="detail-section-title">Rollout history</div>${revRows}</div>`;
  } catch (e) {
    box.innerHTML = offlineCard("cluster", e.message);
  }
}

async function rollbackDeployment(namespace, deployment, revision) {
  if (!confirm(`Roll back '${deployment}' to revision ${revision}? This changes what's running right now.`)) return;
  try {
    loading(true);
    const r = await api("POST", "/api/live/rollout/undo", { namespace, deployment, to_revision: revision });
    toast("✓ " + r.message, true);
    setTimeout(() => loadServiceRuntime(deployment), 2000);
  } catch (e) { toast("✕ " + e.message, false); }
}

function closeDetail() {
  state.detail = null;
  $("detail-panel").classList.remove("show", "wide");
  $("detail-overlay").classList.remove("show");
}

/* ═══════════ NAV / TIMER / TOOLTIP ═══════════ */

function switchView(name) {
  document.querySelectorAll(".nav-item").forEach((n) => n.classList.toggle("active", n.dataset.view === name));
  document.querySelectorAll(".view").forEach((v) => v.classList.toggle("active", v.id === `view-${name}`));
  state.view = name;
  window.location.hash = name;
}

function bindNav() {
  document.querySelectorAll(".nav-item").forEach((el) => {
    el.addEventListener("click", () => switchView(el.dataset.view));
  });
}

function startTimer() {
  if (state.timer) clearInterval(state.timer);
  state.timer = setInterval(async () => {
    if (!$("autorefresh").checked) return;
    try { await fetchData(); } catch { /* keep last data */ }
  }, 30000);
}

(async function init() {
  bindNav();
  const hash = window.location.hash.replace("#", "");
  if (VIEWS.includes(hash)) switchView(hash);
  try {
    await fetchData();
  } catch (e) {
    $("view-topology").innerHTML = `<div class="card" style="border-color:var(--red)"><span class="red">Failed to load: ${esc(e.message)}</span></div>`;
  }
  startTimer();
})();
