"use strict";

// The platform console shares a page (and a DOM) with the main control-plane
// SPA, which defines its own `api`, `esc`, `toast`, `$` … at top level. Both
// files are plain scripts, so this module wraps itself in an IIFE and exports
// exactly one symbol — `window.PlatformConsole` — rather than leaking ~80
// globals that would silently overwrite the SPA's.
window.PlatformConsole = (function () {

const state = { data: null, view: "topology", search: "", timer: null, detail: null, configTab: "ci", runTimer: null, pipelineTimer: null };

const $ = (id) => document.getElementById(id);
const VIEWS = ["topology", "apps", "overview", "services", "config"];

function esc(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

let toastTimer = null;
function toast(msg, ok = true) {
  const t = $("platform-toast");
  t.textContent = msg;
  t.className = `toast show ${ok ? "ok" : "err"}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.className = "toast"; }, 5000);
}

// The spinner is the toast showing "…". It has to clear itself the way a real
// toast does: `loading(false)` used to be a no-op, so the only thing that ever
// took "…" off the screen was a later toast() call. Any handler that returned
// without one — an early `return` on an unreachable backend, a throw the
// caller swallowed — left the console looking busy forever.
function loading(show = true) {
  const t = $("platform-toast");
  clearTimeout(toastTimer);
  if (show) {
    t.textContent = "…";
    t.className = "toast show";
    // Backstop only: a request that neither resolves nor rejects still stops
    // claiming to be in flight.
    toastTimer = setTimeout(() => { t.className = "toast"; }, 30000);
  } else {
    t.className = "toast";
  }
}

// Shared with the main SPA (web/static/app.js) — same origin, same localStorage.
const TOKEN_KEY = "cp.access_token";

function authHeaders(extra) {
  const t = localStorage.getItem(TOKEN_KEY);
  return { ...(t ? { Authorization: `Bearer ${t}` } : {}), ...extra };
}

async function api(method, url, body) {
  const res = await fetch(url, {
    method,
    headers: authHeaders(body ? { "Content-Type": "application/json" } : undefined),
    body: body ? JSON.stringify(body) : undefined,
  });
  if (res.status === 401) {
    location.href = "/";
    throw new Error("Not authenticated");
  }
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
  const res = await fetch("/api/v1/platform", { headers: authHeaders() });
  if (res.status === 401) { location.href = "/"; throw new Error("Not authenticated"); }
  if (!res.ok) throw new Error(`API ${res.status}`);
  state.data = await res.json();
  render();
}

/* ─────────── topbar ─────────── */

function renderTopbar() {
  const d = state.data;
  const ov = d.overview;
  const ready = ov.status === "healthy";
  // Data has arrived, so the badge has something to say.
  document.querySelector("#platform-root .topbar").hidden = false;
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
  // renderTopology()/renderConfig() each kick off their own async fetch
  // beyond what the SSE snapshot already carries (loadHealthBoard, the
  // active Operations tab's own API call) and reset their section to a
  // "loading…" placeholder every time they run. The live stream pushes a
  // frame every ~2s, and render() used to call both on every single one —
  // any fetch slower than that (GitHub Actions' API for the CI tab, the
  // multi-call health board) never won the race against the next tick's
  // reset, so it sat on "loading live status…" / "fetching workflow
  // runs…" forever. Reproduced live: every request involved returned 200
  // with real data, and the page still never showed it. Both keep their
  // own explicit "↻ refresh" button and switchConfigTab already calls
  // renderConfig() directly when the user picks a different tab, so
  // running them once at mount and leaving them alone after that loses
  // nothing a user could reach through the UI.
  if (!state.heavyMounted) {
    renderTopology();
    renderConfig();
    state.heavyMounted = true;
  }
  renderApps();
  renderOverview();
  renderServices();
}

/* ═══════════ PLATFORM HEALTH ═══════════ */

function renderTopology() {
  $("view-topology").innerHTML = `
    <div class="head">
      <div>
        <h1>Platform Health</h1>
        <div class="sub">Real status, right now — pods, CI, ArgoCD sync, and firing alerts per service. Click a service to act on it.</div>
      </div>
      <button class="act-btn" data-act="renderTopology">↻ refresh</button>
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
      api("GET", "/api/v1/platform/live/ci").catch(() => ci),
      api("GET", "/api/v1/platform/live/argocd").catch(() => argo),
      api("GET", "/api/v1/platform/live/alerts").catch(() => alerts),
      api("GET", "/api/v1/platform/live/pods?namespace=devops-platform").catch(() => pods),
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

  const blockers = d.overview.blockers || [];
  const blockerBanner = blockers.length ? `
    <div class="card mb" style="padding:12px 20px;border-color:var(--red)">
      <div class="muted small mb" style="color:var(--red);font-weight:700">${blockers.length} service${blockers.length === 1 ? "" : "s"} blocked from healthy — first blockers:</div>
      ${blockers.map((b) => `<div class="cfg-item"><span><b>${esc(b.service)}</b></span><span class="val">${pill(b.stage, "red")} <span class="muted small">${esc(b.detail || "")}</span></span></div>`).join("")}
    </div>` : "";

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

    const pl = d.overview.pipelines?.[s.name];
    const plBadge = !pl ? pill("n/a", "muted")
      : pl.all_ok ? pill("healthy", "green")
      : pill(`blocked: ${pl.blocking}`, "red");

    return `
      <div class="health-row" data-act="showDetail" data-a1="${esc(s.name)}">
        <div class="health-name">
          <div class="mono" style="font-weight:800;color:var(--accent)">${esc(s.name)}</div>
          <div class="muted small">${esc(s.title)} · v${esc(s.version)}</div>
        </div>
        <div class="health-col"><div class="health-label">pipeline</div>${plBadge}</div>
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

    ${blockerBanner}

    <div class="card mb" style="padding:16px 20px">
      <div style="font-weight:700;margin-bottom:4px">Need to fix something running right now?</div>
      <div class="muted small">Open <b>Operations</b> in the sidebar — trigger a CI rerun, sync an ArgoCD app, restart a pod, or open the real Grafana/Prometheus/ArgoCD dashboards.</div>
    </div>

    <div class="card">
      <div class="health-row health-head">
        <div class="health-name">Service</div>
        <div class="health-col">Pipeline</div>
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
    const r = await api("POST", "/api/v1/platform/apps", { name });
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
    const r = await api("DELETE", `/api/v1/platform/apps/${appName}`);
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
    const r = await api("POST", `/api/v1/platform/apps/${appName}/services`, { name });
    toast(r.message || `✓ service '${name}' added`, true);
    await refresh();
    const inp = $(`add-svc-${appName}`);
    if (inp) inp.value = "";
  } catch (e) { toast("✕ " + e.message, false); }
}

/* ─── Ship flow: create → branch → push → PR → live stage tracker ─── */

function pipelinePill(st) {
  return st === "ok" ? pill("ok", "green")
    : st === "pending" ? pill("pending", "amber")
    : st === "blocked" ? pill("blocked", "muted")
    : pill("failed", "red");
}

function renderPipeline(r) {
  const rows = r.stages.map((s) => {
    const isBlock = s.stage === r.blocking;
    return `
      <div class="cfg-item" style="${isBlock ? "border-color:var(--red)" : ""}">
        <span><b>${esc(s.stage)}</b> ${pipelinePill(s.state)} <span class="muted small">${esc(s.detail || "")}</span></span>
        ${s.stage === "vault" && s.state !== "ok" ? `<button class="act-btn" data-act="seedSecretsFlow" data-a1="${esc(r.service)}">⚙ seed secrets</button>` : ""}
      </div>`;
  }).join("");
  const blockPill = r.all_ok ? pill("all stages ok — healthy", "green") : pill(`blocked at: ${esc(r.blocking)}`, "red");
  return `
    <div class="cfg-toolbar">
      <span class="cfg-repo">${esc(r.service)} — end-to-end pipeline · ${blockPill}</span>
      <span class="sp"></span>
      <button class="act-btn" data-act="showPipeline" data-a1="${esc(r.service)}">↻ refresh</button>
    </div>
    <div class="run-list">${rows}</div>`;
}

async function showPipeline(service) {
  const panel = $("pipeline-panel");
  if (!panel) return;
  panel.style.display = "";
  if (state.pipelineTimer) clearInterval(state.pipelineTimer);
  const poll = async () => {
    try {
      const r = await api("GET", `/api/v1/platform/ship/${encodeURIComponent(service)}/pipeline`);
      panel.innerHTML = renderPipeline(r);
      if (r.all_ok && state.pipelineTimer) { clearInterval(state.pipelineTimer); state.pipelineTimer = null; }
    } catch (e) { panel.innerHTML = offlineCard("pipeline", e.message); }
  };
  poll();
  state.pipelineTimer = setInterval(poll, 3000);
}

async function shipServiceFlow(appName, raw) {
  if (!raw) {
    raw = prompt(`Ship a new service in app '${appName}' (e.g. cart):`);
    if (!raw) return;
  }
  const name = slugify(raw);
  if (!name) { toast("invalid service name", false); return; }
  const k8s = appName === "default" ? name : `${appName}-${name}`;
  if (!confirm(`Create branch service/${k8s}, push to origin, and open a PR against secondary? This writes to your GitHub repository.`)) return;
  try {
    loading(true);
    const r = await api("POST", "/api/v1/platform/ship/service", { app: appName, name, open_pr: true });
    toast("✓ " + r.message, true);
    const inp = $(`add-svc-${appName}`);
    if (inp) inp.value = "";
    await refresh();
    showPipeline(k8s);
  } catch (e) { toast("✕ " + e.message, false); }
}

async function seedSecretsFlow(service) {
  if (!confirm(`Generate and write DATABASE_URL and JWT_SECRET_KEY into Vault at secret/devops-platform/${service}? Existing values will be overwritten.`)) return;
  try {
    loading(true);
    const r = await api("POST", `/api/v1/platform/ship/${encodeURIComponent(service)}/secrets`);
    toast("✓ " + r.message, true);
    setTimeout(() => showPipeline(service), 1500);
  } catch (e) { toast("✕ " + e.message, false); }
}

async function deleteServiceFlow(appName, svcName) {
  if (!confirm(`Delete service '${svcName}'? CI stops building it, ArgoCD prunes its app, Vault role idles.`)) return;
  try {
    loading(true);
    const r = await api("DELETE", `/api/v1/platform/apps/${appName}/services/${svcName}`);
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
          <button class="act-btn" data-act="showPipeline" data-a1="${esc(s.name)}">▤ pipeline</button>
          <button class="act-btn danger" data-del-svc="${esc(a.name)}" data-svc="${esc(s.key.split("/").pop())}">✕ delete</button>
        </div>`).join("");

    return `
      <div class="card app-card" style="cursor:pointer" data-act="gotoServices">
        <div class="app-head">
          <div class="app-icon">${esc(a.name[0].toUpperCase())}</div>
          <div>
            <div class="app-name">${isDefault ? "default <span class='muted small'>(flat app/*/services)</span>" : esc(a.name)}</div>
            <div class="app-path">${esc(a.path)}/</div>
          </div>
          <span class="sp"></span>
          ${pill(`${a.services.length} service${a.services.length === 1 ? "" : "s"}`, isDefault ? "muted" : "accent")}
          <button class="btn danger sm" data-stop data-del-app="${esc(a.name)}" ${isDefault ? "disabled title='legacy flat group — not deletable'" : ""}>delete app</button>
        </div>
        ${svcList || `<div class="empty">no services yet</div>`}
        <div class="add-svc-panel" data-stop>
          <div class="add-svc-label">＋ Add or 🚀 ship a service to <b>${esc(a.name)}</b></div>
          <div class="add-svc">
            <input placeholder="e.g. cart, payments…" id="add-svc-${esc(a.name)}" data-enter="addServiceFromSelf" data-a1="${esc(a.name)}">
            <button class="btn sm" data-act="addServiceFromInput" data-a1="${esc(a.name)}" data-input="add-svc-${esc(a.name)}">Add</button>
            <button class="btn sm" style="background:var(--accent);color:#0a0e14" data-act="shipServiceFromInput" data-a1="${esc(a.name)}" data-input="add-svc-${esc(a.name)}">🚀 Ship (branch + PR)</button>
          </div>
        </div>
      </div>`;
  }).join("");

  $("view-apps").innerHTML = `
    ${explainer(`Your <b>management console</b>. Create an app, add services inside it — the UI writes real files (app/&lt;app&gt;/&lt;service&gt;/main.py). The platform is discovery-driven: CI builds the new service, ArgoCD deploys it, Vault provisions secrets, Prometheus watches it. <b>Ship</b> = create + branch <span class="mono">service/&lt;name&gt;</span> + push + open a PR against <span class="mono">secondary</span> — nothing lands unreviewed. Delete → everything shrinks back. Flat legacy services live in the "default" group.`)}
    <div class="head">
      <div>
        <h1>Apps &amp; Services</h1>
        <div class="sub">Create, ship, and track services end to end. The pipeline tracker names the FIRST stage any service is stuck on.</div>
      </div>
    </div>

    <div class="card new-app-panel mb">
      <div class="new-app-step">
        <div class="step-num">1</div>
        <div class="step-body">
          <div class="step-title">Create a new app</div>
          <div class="step-sub">A folder under <span class="mono">app/</span> that groups related services.</div>
          <div class="add-svc mt1">
            <input placeholder="e.g. checkout, billing…" id="new-app-input" data-enter="createAppFromSelf">
            <button class="btn" data-act="createAppFromInput" data-input="new-app-input">＋ Create app</button>
          </div>
        </div>
      </div>
      <div class="new-app-step">
        <div class="step-num">2</div>
        <div class="step-body">
          <div class="step-title">Add or Ship services inside it</div>
          <div class="step-sub">Scroll down to any app card — <b>Add</b> writes files locally; <b>Ship</b> also branches, pushes, and opens a PR. <b>▤ pipeline</b> tracks any service to pods-ready.</div>
        </div>
      </div>
      <div class="kbd" style="align-self:flex-start;margin-top:4px">Add = local files only. Ship = branch + PR against secondary (CI, ArgoCD, Vault, monitoring follow)</div>
    </div>

    <div id="pipeline-panel" class="card mb" style="display:none"></div>

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
      <div class="header-actions">${ov.status === "healthy" ? pill("PLATFORM HEALTHY", "green") : pill("DEGRADED", "red")}</div>
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
  // Whether the filter box is being typed into has to be read BEFORE the
  // view is replaced: tearing down a focused element fires its blur handler,
  // so anything the blur sets is already stale by the time the replacement
  // exists.
  const wasTyping = document.activeElement && document.activeElement.id === "svc-search";
  const q = state.search.toLowerCase();
  const list = d.services.filter((s) => !q || s.name.toLowerCase().includes(q) || s.title.toLowerCase().includes(q));

  const rows = list.map((s) => `
    <tr data-act="showDetail" data-a1="${esc(s.name)}" style="cursor:pointer">
      <td><span class="mono" style="color:var(--accent);font-weight:700">${esc(s.name)}</span></td>
      <td><span class="chip">${esc(s.app)}</span></td>
      <td>${esc(s.title)}</td>
      <td class="mono">${esc(s.version)}</td>
      <td class="mono">${s.loc}</td>
      <td class="mono">${s.requirements_num}</td>
      <td>${s.has_requirements ? pill("pinned", "green") : pill("none", "red")}</td>
      <td>${s.uses_vault ? pill("vault", "accent") : pill("none", "muted")}</td>
      <td><div class="chips" style="gap:4px">${s.endpoints.map((e) => `<span class="chip">${esc(e)}</span>`).join("")}</div></td>
      <td data-stop><button class="act-btn danger" data-del-svc-row="${esc(s.app)}" data-svc-row="${esc(s.key.split("/").pop())}">✕</button></td>
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
    // renderServices() replaces this whole view, including the input being
    // typed into, so the fresh one starts unfocused: the first keystroke
    // filtered and every later one went nowhere, which reads as a search box
    // that only accepts one character. Restore focus and caret on the
    // replacement element.
    if (wasTyping) {
      input.focus();
      const end = input.value.length;
      input.setSelectionRange(end, end);
    }
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
  const tabs = ["ci", "argocd", "vault", "monitoring", "run", "infra", "drift", "logs", "graph"];
  const tabHtml = tabs.map((t) => `
    <button class="cfg-tab ${t === state.configTab ? "active" : ""}" data-act="switchConfigTab" data-a1="${t}">
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
  if (state.runTimer) { clearInterval(state.runTimer); state.runTimer = null; }
  if (state.graphTimer) { clearInterval(state.graphTimer); state.graphTimer = null; }
  state.configTab = t;
  renderConfig();
}

async function loadConfigTab(tab) {
  const body = $("cfg-body");
  if (!body) return;
  // Leaving a tab clears its timer; renderConfig -> switchConfigTab handles it,
  // but be defensive: if we re-enter the same tab via refreshConfigTab,
  // the timer might be stale.
  if (state.graphTimer) { clearInterval(state.graphTimer); state.graphTimer = null; }
  try {
    if (tab === "ci") await renderCiTab(body);
    else if (tab === "argocd") await renderArgocdTab(body);
    else if (tab === "vault") await renderVaultTab(body);
    else if (tab === "monitoring") await renderMonitoringTab(body);
    else if (tab === "run") await renderRunTab(body);
    else if (tab === "infra") await renderInfraTab(body);
    else if (tab === "drift") await renderDriftTab(body);
    else if (tab === "logs") await renderLogsTab(body);
    else if (tab === "graph") await renderGraphTab(body);
  } catch (e) {
    body.innerHTML = offlineCard(tab, e.message);
  }
}

function refreshConfigTab() { loadConfigTab(state.configTab); }

/* ─── shared: embed real dashboard inline where the tool allows it ─── */

async function openDashboard(tool, label) {
  try {
    loading(true);
    const r = await api("POST", `/api/v1/platform/live/dashboard/${tool}/open`);
    toast(`✓ ${label} live`, true);
    showEmbeddedDashboard(tool, label, r.url, r.embeddable !== false);
  } catch (e) { toast("✕ " + e.message, false); }
}

// `embeddable` is the backend's read of the tool's own frame-blocking headers
// (platform_ops._probe_forward), not a preference. A refused iframe paints an
// empty grey rectangle and reports nothing, so a tool that says no gets a link
// panel instead of a frame that would only ever be blank — Vault hardcodes
// frame-ancestors 'none' and is permanently in that category.
function showEmbeddedDashboard(tool, label, url, embeddable = true) {
  $("detail-title").textContent = label;
  $("detail-content").innerHTML = `
    <div class="embed-bar">
      <span class="mono small muted">${esc(url)}</span>
      <a class="act-btn" href="${esc(url)}" target="_blank" rel="noopener">↗ full tab</a>
    </div>
    ${embeddable
      ? `<iframe class="embed-frame" src="${esc(url)}" sandbox="allow-scripts allow-same-origin allow-forms allow-popups"></iframe>`
      : `<div class="cfg-offline">
           <span class="status-dot warn"></span>
           <div>
             <div class="cfg-offline-title">${esc(label)} refuses to be embedded</div>
             <div class="cfg-offline-err">It sends frame-blocking headers of its own, so it can only
               be opened in its own tab. The forward above is live and stays up.</div>
           </div>
         </div>`}`;
  $("detail-panel").classList.add("show", "wide");
  $("detail-overlay").classList.add("show");
  // Opening the panel IS the completion of whatever was loading. Several
  // callers end here with no toast() of their own, and the spinner has no
  // other way to learn the request finished.
  loading(false);
}

/* ─── shared: log viewer modal ─── */

function showLogs(title, logText) {
  $("detail-title").textContent = title;
  $("detail-content").innerHTML = `<pre class="cfg-code" style="white-space:pre-wrap;max-height:70vh">${esc(logText || "(empty)")}</pre>`;
  $("detail-panel").classList.add("show");
  $("detail-overlay").classList.add("show");
  loading(false);
}

async function viewPodLogs(namespace, pod, label) {
  try {
    loading(true);
    const r = await api("GET", `/api/v1/platform/live/pods/${namespace}/${pod}/logs?tail=200`);
    if (!r.reachable) { toast("✕ " + r.error, false); return; }
    showLogs(label || pod, r.log);
  } catch (e) { toast("✕ " + e.message, false); }
}

async function restartPod(namespace, pod) {
  if (!confirm(`Restart pod '${pod}' in namespace '${namespace}'? It will be deleted and recreated by its controller.`)) return;
  try {
    loading(true);
    const r = await api("POST", "/api/v1/platform/live/pods/restart", { namespace, pod });
    toast("✓ " + r.message, true);
  } catch (e) { toast("✕ " + e.message, false); }
}

async function viewCiLogs(runId) {
  try {
    loading(true);
    const r = await api("GET", `/api/v1/platform/live/ci/${runId}/logs`);
    if (!r.reachable) { toast("✕ " + r.error, false); return; }
    showLogs(`Run ${runId} logs`, r.log);
  } catch (e) { toast("✕ " + e.message, false); }
}

/* ─── CI/CD: real GitHub Actions ─── */

async function renderCiTab(body) {
  body.innerHTML = `<div class="cfg-loading">fetching workflow runs…</div>`;
  const data = await api("GET", "/api/v1/platform/live/ci");
  if (!data.reachable) { body.innerHTML = offlineCard("GitHub Actions", data.error); return; }

  const statusPill = (r) => {
    if (r.status === "in_progress" || r.status === "queued") return pill(r.status, "amber");
    if (r.conclusion === "success") return pill("success", "green");
    if (r.conclusion === "failure") return pill("failure", "red");
    if (r.conclusion === "cancelled") return pill("cancelled", "muted");
    return pill(r.conclusion || r.status || "unknown", "muted");
  };

  const isTerminal = (r) => r.status === "completed" && (r.conclusion === "success" || r.conclusion === "failure" || r.conclusion === "cancelled");

  const rows = data.runs.map((r) => `
    <div class="run-row" data-run-id="${r.databaseId}">
      <div class="run-main">
        ${statusPill(r)}
        <div>
          <div class="run-title">
            ${esc(r.displayTitle)}
            <button class="act-btn sm" data-act="showCiGraph" data-a1="${r.databaseId}" title="Show pipeline graph">⧉ graph</button>
          </div>
          <div class="run-meta">${esc(r.workflowName)} · ${esc(r.headBranch)} · ${esc(r.event)} · ${esc(new Date(r.createdAt).toLocaleString())}</div>
        </div>
      </div>
      <div class="run-actions">
        <a class="act-btn" href="${esc(r.url)}" target="_blank" rel="noopener">↗ open in GitHub</a>
        ${r.status === "completed" ? `<button class="act-btn" data-act="viewCiLogs" data-a1="${r.databaseId}">▤ logs</button>` : ""}
        ${r.conclusion === "failure" ? `<button class="act-btn" data-act="ciRerun" data-a1="${r.databaseId}">↻ rerun failed</button>` : ""}
        ${r.status === "in_progress" || r.status === "queued" ? `<button class="act-btn danger" data-act="ciCancel" data-a1="${r.databaseId}">✕ cancel</button>` : ""}
      </div>
      <div class="ci-graph-panel" id="ci-graph-${r.databaseId}" style="display:none;"></div>
    </div>`).join("");

  body.innerHTML = `
    <div class="cfg-toolbar">
      <span class="cfg-repo">${esc(data.repo)}</span>
      <span class="sp"></span>
      <button class="btn sm" data-act="ciTrigger">▶ run workflow</button>
      <button class="act-btn" data-act="refreshConfigTab">↻ refresh</button>
    </div>
    <div class="run-list">${rows || `<div class="empty">no runs found</div>`}</div>`;

  // Poll live runs: only refresh the visible per-run graphs every 8s.
  if (state.graphTimer) clearInterval(state.graphTimer);
  state.graphTimer = setInterval(async () => {
    if (!state.mounted || !$("autorefresh").checked) return;
    const liveRuns = data.runs.filter(r => !isTerminal(r));
    if (!liveRuns.length) { clearInterval(state.graphTimer); state.graphTimer = null; return; }
    for (const r of liveRuns) {
      const panel = $(`ci-graph-${r.databaseId}`);
      if (!panel || panel.style.display === "none") continue;
      try {
        const graph = await api("GET", `/api/v1/platform/live/ci/${r.databaseId}/graph`);
        if (window.PipelineGraph) {
          panel.innerHTML = window.PipelineGraph.render(graph);
        }
      } catch (e) { /* ignore */ }
    }
  }, 8000);
}

async function ciTrigger() {
  try {
    loading(true);
    const r = await api("POST", "/api/v1/platform/live/ci/trigger", { workflow: "ci-cd.yml" });
    toast("✓ " + r.message, true);
    setTimeout(refreshConfigTab, 2000);
  } catch (e) { toast("✕ " + e.message, false); }
}

async function ciRerun(runId) {
  if (!confirm(`Re-run failed jobs for run ${runId}?`)) return;
  try {
    loading(true);
    const r = await api("POST", "/api/v1/platform/live/ci/rerun", { run_id: runId });
    toast("✓ " + r.message, true);
    setTimeout(refreshConfigTab, 2000);
  } catch (e) { toast("✕ " + e.message, false); }
}

async function ciCancel(runId) {
  if (!confirm(`Cancel run ${runId}?`)) return;
  try {
    loading(true);
    const r = await api("POST", "/api/v1/platform/live/ci/cancel", { run_id: runId });
    toast("✓ " + r.message, true);
    setTimeout(refreshConfigTab, 2000);
  } catch (e) { toast("✕ " + e.message, false); }
}

/**
 * renderCiGraphTab(runId) — draws the pipeline graph for a single CI run
 * and starts an 8s poll while the run is running/pending. The poll is
 * stored on state.graphTimer and cleared when the run completes, when the
 * user leaves the config view, or when the panel is closed.
 */
async function renderCiGraphTab(runId, elementId = `ci-graph-${runId}`) {
  const panel = $(elementId);
  if (!panel) return;
  panel.style.display = "block";
  const row = panel.closest(".run-row");
  const btn = row ? row.querySelector('[data-act="showCiGraph"]') : null;
  if (btn) btn.textContent = "⧍ hide graph";

  async function fetchAndRender() {
    try {
      const graph = await api("GET", `/api/v1/platform/live/ci/${runId}/graph`);
      if (window.PipelineGraph) {
        panel.innerHTML = window.PipelineGraph.render(graph);
      }
    } catch (e) {
      panel.innerHTML = `<div class="error">${esc(e.message)}</div>`;
    }
  }
  await fetchAndRender();

  // Start per-run 8s poll, guarded like the 30s timer.
  if (state.graphTimer) clearInterval(state.graphTimer);
  state.graphTimer = setInterval(async () => {
    if (!state.mounted || !$("autorefresh").checked) return;
    const run = (await api("GET", "/api/v1/platform/live/ci")).runs.find(r => r.databaseId === runId);
    if (!run || run.status === "completed") {
      clearInterval(state.graphTimer);
      state.graphTimer = null;
      return;
    }
    await fetchAndRender();
  }, 8000);
}

/**
 * renderGraphTab(body) — the "graph" config tab. Shows the latest run's
 * pipeline graph with live polling.
 */
async function renderGraphTab(body) {
  const data = await api("GET", "/api/v1/platform/live/ci");
  if (!data.reachable) { body.innerHTML = offlineCard("GitHub Actions", data.error); return; }
  const latest = data.runs[0];
  if (!latest) { body.innerHTML = `<div class="empty">no CI runs yet</div>`; return; }

  body.innerHTML = `
    <div class="cfg-toolbar">
      <span class="cfg-repo">${esc(data.repo)}</span>
      <span class="sp"></span>
      <button class="btn sm" data-act="ciTrigger">▶ run workflow</button>
      <button class="act-btn" data-act="refreshConfigTab">↻ refresh</button>
    </div>
    <div class="ci-graph-panel" id="ci-graph-latest" style="display:block;"></div>`;

  await renderCiGraphTab(latest.databaseId, "ci-graph-latest");
}

/* ─── ArgoCD: real Application CRDs + sync ─── */

async function renderArgocdTab(body) {
  body.innerHTML = `<div class="cfg-loading">fetching applications…</div>`;
  const data = await api("GET", "/api/v1/platform/live/argocd");
  if (!data.reachable) { body.innerHTML = offlineCard("ArgoCD / cluster", data.error); return; }

  const syncCls = (s) => s === "Synced" ? "green" : s === "OutOfSync" ? "amber" : "muted";
  const healthCls = (s) => s === "Healthy" ? "green" : s === "Degraded" ? "red" : s === "Progressing" ? "amber" : "muted";

  const rows = (data.apps || []).map((a) => `
    <div class="app-row">
      <div class="app-row-main clickable" data-act="showArgoResources" data-a1="${esc(a.name)}">
        <div class="app-row-name">${esc(a.name)}</div>
        <div class="app-row-meta">${esc(a.path)} @ ${esc(a.revision || "–")} · click for resources</div>
      </div>
      ${pill(a.sync_status, syncCls(a.sync_status))}
      ${pill(a.health_status, healthCls(a.health_status))}
      <div class="run-actions">
        <button class="act-btn" data-act="argoRefresh" data-a1="${esc(a.name)}">↻ refresh</button>
        <button class="act-btn" data-act="argoSync" data-a1="${esc(a.name)}">⇌ sync</button>
      </div>
    </div>`).join("");

  body.innerHTML = `
    <div class="cfg-toolbar">
      <span class="cfg-repo">${data.apps.length} applications</span>
      <span class="sp"></span>
      <button class="btn sm" data-act="openDashboard" data-a1="argocd" data-a2="ArgoCD UI">⧉ open ArgoCD dashboard</button>
      <button class="act-btn" data-act="showArgoCreds">🔑 admin password</button>
      <button class="act-btn" data-act="refreshConfigTab">↻ refresh all</button>
    </div>
    <div class="run-list">${rows || `<div class="empty">no applications found</div>`}</div>`;
}

async function argoSync(name) {
  if (!confirm(`Trigger sync for '${name}'? This applies the latest git state to the cluster.`)) return;
  try {
    loading(true);
    const r = await api("POST", "/api/v1/platform/live/argocd/sync", { name });
    toast("✓ " + r.message, true);
    setTimeout(refreshConfigTab, 1500);
  } catch (e) { toast("✕ " + e.message, false); }
}

async function argoRefresh(name) {
  try {
    loading(true);
    const r = await api("POST", "/api/v1/platform/live/argocd/refresh", { name });
    toast("✓ " + r.message, true);
    setTimeout(refreshConfigTab, 1500);
  } catch (e) { toast("✕ " + e.message, false); }
}

async function showArgoResources(appName) {
  try {
    loading(true);
    const r = await api("GET", `/api/v1/platform/live/argocd/${appName}/resources`);
    if (!r.reachable) { toast("✕ " + r.error, false); return; }
    const lines = r.resources.map((res) =>
      `${(res.kind || "").padEnd(24)} ${(res.name || "").padEnd(30)} ${res.status || ""}  ${res.health || ""}`);
    showLogs(`${appName} — resources (${r.resources.length})`, lines.join("\n"));
  } catch (e) { toast("✕ " + e.message, false); }
}

async function showVaultSecretMeta(service) {
  try {
    loading(true);
    const r = await api("GET", `/api/v1/platform/live/vault/secrets/${service}`);
    if (!r.reachable) { toast("✕ " + r.error, false); return; }
    showLogs(`${service} — secret metadata`,
      `version: ${r.version}\ncreated: ${r.created_time}\nfields: ${r.field_names.join(", ")}\n\n(values are never shown here — read-only metadata for audit)`);
  } catch (e) { toast("✕ " + e.message, false); }
}

async function showArgoCreds() {
  try {
    loading(true);
    const r = await api("GET", "/api/v1/platform/live/argocd/admin-password");
    if (!r.reachable) { toast("✕ " + r.error, false); return; }
    showLogs("ArgoCD admin credentials", `username: ${r.username}\npassword: ${r.password}`);
  } catch (e) { toast("✕ " + e.message, false); }
}

/* ─── Vault: real seal status + secret listing ─── */

async function renderVaultTab(body) {
  body.innerHTML = `<div class="cfg-loading">checking vault…</div>`;
  const data = await api("GET", "/api/v1/platform/live/vault");
  if (!data.reachable) { body.innerHTML = offlineCard("Vault", data.error); return; }

  let secretsHtml = `<div class="cfg-loading">listing secrets…</div>`;
  body.innerHTML = `
    <div class="cfg-toolbar">
      <span class="status-dot ${data.sealed ? "bad" : "ok"}"></span>
      <span class="cfg-repo">${data.sealed ? "SEALED" : "UNSEALED"} · v${esc(data.version)}</span>
      <span class="sp"></span>
      <button class="btn sm" data-act="syncServiceList">⇌ sync service list</button>
      <button class="btn sm" data-act="rerunVaultSetup">▶ re-run setup job</button>
      <button class="btn sm" data-act="openDashboard" data-a1="vault" data-a2="Vault UI">⧉ open Vault dashboard</button>
      <button class="act-btn" data-act="refreshConfigTab">↻ refresh</button>
    </div>
    <div class="grid two mb">
      <div class="cfg-target"><div class="cfg-item"><span>Initialized</span><span class="val">${data.initialized ? "✓ yes" : "✗ no"}</span></div></div>
      <div class="cfg-target"><div class="cfg-item"><span>HA enabled</span><span class="val">${data.ha_enabled ? "✓ yes" : "– no"}</span></div></div>
    </div>
    <div class="cfg-section"><h3>Secrets at secret/devops-platform</h3><div id="vault-secrets">${secretsHtml}</div></div>`;

  try {
    const sec = await api("GET", "/api/v1/platform/live/vault/secrets");
    const box = $("vault-secrets");
    if (!box) return;
    if (!sec.reachable) { box.innerHTML = offlineCard("secret listing", sec.error); return; }
    box.innerHTML = (sec.keys || []).map((k) => `
      <div class="cfg-item">
        <span class="mono clickable" data-act="showVaultSecretMeta" data-a1="${esc(k)}">${esc(k)}</span>
        <span class="val">
          <button class="act-btn" data-act="seedSecretsFlow" data-a1="${esc(k)}">⚙ seed secrets</button>
          <button class="act-btn" data-act="showPipeline" data-a1="${esc(k)}">▤ pipeline</button>
          <span class="muted small clickable" data-act="showVaultSecretMeta" data-a1="${esc(k)}">metadata →</span>
        </span>
      </div>`).join("") || `<div class="empty">no keys — run setup job after syncing the service list</div>`;
  } catch (e) { /* leave loading state message */ }
}

async function syncServiceList() {
  if (!confirm(`Rewrite the devops-service-list ConfigMap (vault ns) from the discovered services? The vault-setup Job reads this list to provision per-service secrets.`)) return;
  try {
    loading(true);
    const r = await api("POST", "/api/v1/platform/ship/vault/resync");
    toast("✓ " + r.message, true);
  } catch (e) { toast("✕ " + e.message, false); }
}

async function rerunVaultSetup() {
  if (!confirm(`Force the vault-setup Job to re-run? It deletes the finished job (TTL-expired) and re-applies k8s/vault/manifests.yaml — seeding secrets for every service in the list.`)) return;
  try {
    loading(true);
    const r = await api("POST", "/api/v1/platform/ship/vault/setup");
    toast("✓ " + r.message, true);
    setTimeout(refreshConfigTab, 1500);
  } catch (e) { toast("✕ " + e.message, false); }
}

/* ─── Monitoring: real firing alerts via Alertmanager ─── */

async function renderMonitoringTab(body) {
  body.innerHTML = `<div class="cfg-loading">checking alertmanager…</div>`;
  const data = await api("GET", "/api/v1/platform/live/alerts");
  if (!data.reachable) { body.innerHTML = offlineCard("Alertmanager", data.error); return; }

  const sevCls = (s) => s === "critical" ? "red" : s === "warning" ? "amber" : "muted";
  window._alertsData = data.alerts || [];
  const rows = (data.alerts || []).map((a, i) => `
    <div class="app-row clickable" data-act="showFiringAlertDetail" data-a1="${i}">
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
      <button class="btn sm" data-act="openDashboard" data-a1="grafana" data-a2="Grafana">⧉ open Grafana</button>
      <button class="btn sm" data-act="openDashboard" data-a1="prometheus" data-a2="Prometheus">⧉ open Prometheus</button>
      <button class="btn sm" data-act="openDashboard" data-a1="alertmanager" data-a2="Alertmanager">⧉ open Alertmanager</button>
      <button class="act-btn" data-act="refreshConfigTab">↻ refresh</button>
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
    const r = await api("GET", "/api/v1/platform/live/alerts/history?limit=50");
    if (!r.reachable) { box.innerHTML = offlineCard("alert-sink", r.error); return; }
    window._alertHistory = r.alerts;
    const sevCls = (s) => s === "critical" ? "red" : s === "warning" ? "amber" : "muted";
    box.innerHTML = r.alerts.map((a, i) => `
      <div class="cfg-item clickable" data-act="showAlertHistoryDetail" data-a1="${i}">
        <span>${pill(a.status, a.status === "firing" ? "red" : "green")} ${pill(a.severity || "info", sevCls(a.severity))} ${esc(a.name)} <span class="muted small">${esc(a.service || "")}</span></span>
        <span class="val small">${esc(new Date(a.received_at).toLocaleString())}</span>
      </div>`).join("") || `<div class="empty">no history yet</div>`;
  } catch (e) { box.innerHTML = offlineCard("alert-sink", e.message); }
}

function showFiringAlertDetail(i) {
  const a = (window._alertsData || [])[i];
  if (!a) return;
  const lines = [
    `alert: ${a.name}`, `state: ${a.state}`, `severity: ${a.severity || "–"}`,
    `service: ${a.service || "–"}`, `firing since: ${a.starts_at}`, "",
    "labels:", ...Object.entries(a.labels || {}).map(([k, v]) => `  ${k} = ${v}`), "",
    "annotations:", ...Object.entries(a.annotations || {}).map(([k, v]) => `  ${k}: ${v}`),
  ];
  showLogs(a.name, lines.join("\n"));
}

function showAlertHistoryDetail(i) {
  const a = (window._alertHistory || [])[i];
  if (!a) return;
  showLogs(`${a.name} — history entry`, [
    `status: ${a.status}`, `severity: ${a.severity || "–"}`, `service: ${a.service || "–"}`,
    `starts_at: ${a.starts_at}`, `ends_at: ${a.ends_at}`, `received_at: ${a.received_at}`,
  ].join("\n"));
}

async function loadMonitoringPods() {
  try {
    const r = await api("GET", "/api/v1/platform/live/pods?namespace=monitoring");
    const box = $("mon-pods");
    if (!box) return;
    if (!r.reachable) { box.innerHTML = offlineCard("pods", r.error); return; }
    box.innerHTML = r.pods.map((p) => `
      <div class="app-row">
        <div class="app-row-main clickable" data-act="showMonitoringPodDetail" data-a1="${esc(p.name)}">
          <div class="app-row-name">${esc(p.name)}</div>
          <div class="app-row-meta">${esc(p.phase)} · restarts: ${p.restarts}${p.waiting_reason ? " · " + esc(p.waiting_reason) : ""}</div>
        </div>
        ${pill(p.ready ? "ready" : "not ready", p.ready ? "green" : "red")}
        <div class="run-actions">
          <button class="act-btn" data-act="viewPodLogs" data-a1="monitoring" data-a2="${esc(p.name)}" data-a3="${esc(p.name)} logs">▤ logs</button>
          <button class="act-btn danger" data-act="restartPod" data-a1="monitoring" data-a2="${esc(p.name)}">↻ restart</button>
        </div>
      </div>`).join("") || `<div class="empty">no pods</div>`;
  } catch (e) { /* ignore */ }
}

// A pod may carry sidecars — Vault's injector adds `vault-agent` to every
// annotated pod and it sorts first, so reading containers[0] reported the
// sidecar's image, readiness and restart count as if they were the service's.
// Prefer the container named after the workload, else the first non-sidecar.
const SIDECAR_PREFIXES = ["vault-", "istio-proxy", "linkerd-proxy", "envoy", "filebeat"];
function appContainer(containers, workload) {
  const list = containers || [];
  if (!list.length) return {};
  const named = workload && list.find((c) => c.name === workload);
  if (named) return named;
  return list.find((c) => !SIDECAR_PREFIXES.some((p) => (c.name || "").startsWith(p))) || list[0];
}

async function showMonitoringPodDetail(podName) {
  try {
    loading(true);
    const [detail, events] = await Promise.all([
      api("GET", `/api/v1/platform/live/pods/monitoring/${podName}/detail`),
      api("GET", `/api/v1/platform/live/pods/monitoring/${podName}/events?limit=15`),
    ]);
    if (!detail.reachable) { toast("✕ " + detail.error, false); return; }
    const c = appContainer(detail.containers, podName.replace(/-[a-z0-9]+-[a-z0-9]+$/, ""));
    const lines = [
      `pod: ${detail.name}`, `phase: ${detail.phase}`, `node: ${detail.node}`, `started: ${detail.start_time}`, "",
      `container: ${c.name}`, `image: ${c.image}`, `ready: ${c.ready}`, `restarts: ${c.restart_count}`,
      c.waiting_reason ? `waiting: ${c.waiting_reason} — ${c.waiting_message || ""}` : "",
      c.last_terminated_reason ? `last terminated: ${c.last_terminated_reason} (exit ${c.last_terminated_exit_code})` : "",
      "", "recent events:",
      ...(events.reachable ? events.events.map((e) => `  [${e.type}] ${e.reason}: ${e.message}`) : ["  (unavailable)"]),
    ].filter(Boolean);
    showLogs(podName, lines.join("\n"));
  } catch (e) { toast("✕ " + e.message, false); }
}

/* ─── Ops scripts: run + stream live output ─── */

async function renderRunTab(body) {
  body.innerHTML = `<div class="cfg-loading">loading scripts…</div>`;
  const data = await api("GET", "/api/v1/platform/live/scripts");
  const rows = data.scripts.map((s) => `
    <div class="run-row">
      <div class="run-main">
        ${s.running ? pill("running", "amber") : s.last_exit_code === null ? pill("never run", "muted") : s.last_exit_code === 0 ? pill("last: ok", "green") : pill("last: failed", "red")}
        <div>
          <div class="run-title">${esc(s.label)}${s.destructive ? " ⚠️" : ""}</div>
          <div class="run-meta">${s.key}${s.destructive ? " · generates real load" : ""}</div>
        </div>
      </div>
      <div class="run-actions">
        <button class="act-btn" data-act="runScript" data-a1="${s.key}" data-a2="${s.destructive}" ${s.running ? "disabled" : ""}>▶ run</button>
        ${s.running ? `<button class="act-btn danger" data-act="stopScript" data-a1="${s.key}">✕ stop</button>` : ""}
        <button class="act-btn" data-act="viewScriptOutput" data-a1="${s.key}">▤ output</button>
      </div>
    </div>`).join("");

  body.innerHTML = `
    <div class="cfg-toolbar">
      <span class="cfg-repo">scripts/*.sh — non-interactive --ci mode</span>
      <span class="sp"></span>
      <button class="act-btn" data-act="refreshConfigTab">↻ refresh</button>
    </div>
    <div class="run-list">${rows}</div>
    <div class="cfg-section mt" id="run-output-section" style="display:none">
      <h3 id="run-output-title">Output</h3>
      <pre class="cfg-code" id="run-out" style="max-height:55vh;overflow:auto"></pre>
    </div>`;
}

async function runScript(key, destructive) {
  if (destructive && !confirm(`'${key}' generates real load against the cluster. Run it?`)) return;
  try {
    loading(true);
    const r = await api("POST", "/api/v1/platform/live/scripts/run", { script: key });
    toast("✓ " + r.message, true);
    viewScriptOutput(key);
  } catch (e) { toast("✕ " + e.message, false); }
}

async function stopScript(key) {
  if (!confirm(`Stop '${key}'?`)) return;
  try {
    const r = await api("POST", `/api/v1/platform/live/scripts/${key}/stop`);
    toast("✓ " + r.message, true);
    refreshConfigTab();
  } catch (e) { toast("✕ " + e.message, false); }
}

function viewScriptOutput(key) {
  if (state.runTimer) clearInterval(state.runTimer);
  const section = $("run-output-section");
  const out = $("run-out");
  if (!section || !out) return;
  section.style.display = "";
  $("run-output-title").textContent = `Output — ${key}`;
  out.textContent = "";
  let offset = 0;

  const poll = async () => {
    try {
      const r = await api("GET", `/api/v1/platform/live/scripts/${key}/output?offset=${offset}`);
      if (r.lines.length) {
        out.textContent += r.lines.join("\n") + "\n";
        out.scrollTop = out.scrollHeight;
        offset = r.offset;
      }
      if (!r.running) {
        clearInterval(state.runTimer);
        state.runTimer = null;
        const code = r.exit_code;
        // A script nobody has run yet reports exit_code null and no lines.
        // Rendering that through the finished-run footer produced
        // "✕ exit=null · 0s", which reads as a failed run of a script that
        // never started — the one thing the panel must not claim.
        out.textContent += code === null && !out.textContent
          ? "(never run — press ▶ run to start it)\n"
          : `\n${code === 0 ? "✓" : "✕"} exit=${code} · ${fmtDur(Math.round(r.duration_s || 0))}\n`;
        out.scrollTop = out.scrollHeight;
        // Not refreshConfigTab(): that rebuilds the whole RUN tab from
        // scratch, including the very #run-output-section this just wrote
        // to — for a script that had already finished before "output" was
        // clicked, poll()'s first tick sees running:false immediately, so
        // the refresh used to fire within milliseconds and hide the output
        // again before anyone could read it. The row's "last: X" pill goes
        // stale until the tab's own "↻ refresh" is clicked, which is a far
        // smaller cost than the output never being visible at all.
      }
    } catch (e) { /* keep last output */ }
  };
  poll();
  state.runTimer = setInterval(poll, 1200);
}

/* ─── Infra control: capacity + IaC drift + reconcile + preflight ─── */

async function renderInfraTab(body) {
  body.innerHTML = `<div class="cfg-loading">checking capacity + terraform…</div>`;
  let capacity = { reachable: false, error: "loading" };
  let tf = { reachable: false, error: "loading" };
  try {
    [capacity, tf] = await Promise.all([
      api("GET", "/api/v1/platform/infra/capacity").catch(() => capacity),
      api("GET", "/api/v1/platform/infra/terraform").catch(() => tf),
    ]);
  } catch (e) { /* keep defaults */ }

  const capHtml = capacity.reachable ? `
    <div class="grid two mb">
      <div class="cfg-target"><div class="cfg-item"><span>CPU allocatable / requested</span><span class="val">${capacity.allocatable.cpu_m}m / ${capacity.requested.cpu_m}m (${capacity.used_pct.cpu}%)</span></div></div>
      <div class="cfg-target"><div class="cfg-item"><span>RAM allocatable / requested</span><span class="val">${capacity.allocatable.memory_mib}Mi / ${capacity.requested.memory_mib}Mi (${capacity.used_pct.memory}%)</span></div></div>
    </div>
    <div class="cfg-item"><span>${esc(capacity.summary)}</span><span class="val">${pill(`${capacity.room_now} now`, "green")} ${pill(`${capacity.room_burst} burst`, "amber")}</span></div>
    <div class="cfg-item"><span>Tainted nodes</span><span class="val">${capacity.tainted_nodes.length ? capacity.tainted_nodes.join(", ") : "none"}</span></div>
    <div class="kbd mt" style="margin-bottom:0">No cluster-autoscaler exists — on libvirt, capacity is a human action. That is exactly why this control lives in the platform.</div>`
    : offlineCard("capacity", capacity.error);

  const findingsHtml = (tf.findings || []).map((f) => `<div class="cfg-item"><span class="val" style="color:var(--red)">⚠</span><span>${esc(f)}</span></div>`).join("") || `<div class="cfg-item"><span>no findings — state matches reality</span><span class="val">✓</span></div>`;

  body.innerHTML = `
    <div class="cfg-toolbar">
      <span class="cfg-repo">capacity + libvirt terraform</span>
      <span class="sp"></span>
      <button class="act-btn" data-act="refreshConfigTab">↻ refresh</button>
    </div>

    <div class="cfg-section"><h3>Cluster capacity</h3>${capHtml}</div>

    <div class="cfg-section">
      <h3>Terraform drift (state vs. running VMs)</h3>
      <div class="grid two mb">
        <div class="cfg-target"><div class="cfg-item"><span>Domains in state</span><span class="val">${tf.reachable ? tf.domains_in_state.length : "–"}</span></div></div>
        <div class="cfg-target"><div class="cfg-item"><span>VMs running (virsh)</span><span class="val">${tf.reachable ? tf.vms_running.length : "–"}</span></div></div>
      </div>
      <div class="run-list mb">${tf.reachable ? findingsHtml : offlineCard("terraform", tf.error)}</div>
      <div class="add-svc">
        <button class="btn" data-act="reconcileTerraform">⚙ Reconcile state (lock + tfvars + import)</button>
        <button class="btn sm" data-act="preflightFlow">🔍 preflight node-add</button>
      </div>
      <div id="preflight-out" class="mt"></div>
    </div>

    <div class="cfg-section">
      <h3>Provision / remove workers</h3>
      <div class="cfg-item"><span>Add next worker VM (terraform apply + ansible join, streams in Run tab)</span><span class="val"><button class="btn sm" data-act="workerProvision">🚀 Provision</button></span></div>
      <div class="cfg-item"><span>Drain + remove last worker VM (order matters: drain → delete node → apply)</span><span class="val"><button class="btn sm danger" data-act="workerDeprovision">✕ Remove last</button></span></div>
      <div class="kbd mt" style="margin-bottom:0">These are the scripts/*.sh entries — see the <b>run</b> tab for streamed live output.</div>
    </div>`;
}

async function reconcileTerraform() {
  if (!confirm(`Reconcile terraform state? Removes the stale lock, rewrites terraform.tfvars to libvirt-correct values, and imports every missing resource. The plan must come out EMPTY afterwards.`)) return;
  try {
    loading(true);
    const r = await api("POST", "/api/v1/platform/infra/terraform/reconcile");
    toast("✓ " + r.message, true);
    setTimeout(refreshConfigTab, 1500);
  } catch (e) { toast("✕ " + e.message, false); }
}

async function preflightFlow() {
  const box = $("preflight-out");
  if (!box) return;
  box.innerHTML = `<div class="cfg-loading">running preflight…</div>`;
  try {
    const r = await api("GET", "/api/v1/platform/infra/preflight");
    const rows = (r.problems || []).map((p) => `<div class="cfg-item"><span class="val" style="color:var(--red)">⚠</span><span>${esc(p)}</span></div>`).join("");
    box.innerHTML = `
      <div class="cfg-item"><span>disk: ${r.disk_gb}GB · mem: ${r.mem_mb}MB · host free disk: ${r.free_disk_gb != null ? r.free_disk_gb + "GB" : "unknown"}</span>${r.ok ? pill("ready to provision", "green") : pill("refused", "red")}</div>
      ${rows || `<div class="cfg-item"><span>all checks pass — a new VM can be provisioned</span><span class="val">✓</span></div>`}
    `;
  } catch (e) { box.innerHTML = offlineCard("preflight", e.message); }
}

async function workerProvision() {
  if (!confirm(`Provision the next worker VM (N GB disk, M MB RAM) and join it to the cluster. This allocates real host resources and takes several minutes.`)) return;
  try {
    const pre = await api("GET", "/api/v1/platform/infra/preflight");
    if (!pre.ok) {
      toast("✕ preflight refuses: " + (pre.problems[0] || "unknown"), false);
      return;
    }
    loading(true);
    const r = await api("POST", "/api/v1/platform/live/scripts/run", { script: "worker-add" });
    toast("✓ " + r.message, true);
    setTimeout(() => switchConfigTab("run"), 800);
  } catch (e) { toast("✕ " + e.message, false); }
}

async function workerDeprovision() {
  if (!confirm(`Drain and remove the LAST worker VM from the cluster and from terraform. This deletes real infrastructure.`)) return;
  try {
    loading(true);
    const r = await api("POST", "/api/v1/platform/live/scripts/run", { script: "worker-remove" });
    toast("✓ " + r.message, true);
    setTimeout(() => switchConfigTab("run"), 800);
  } catch (e) { toast("✕ " + e.message, false); }
}

/* ─── Cluster drift: what's running vs. what git declares ─── */

async function renderDriftTab(body) {
  body.innerHTML = `<div class="cfg-loading">comparing cluster vs. git…</div>`;
  const data = await api("GET", "/api/v1/platform/live/drift");
  if (!data.reachable) { body.innerHTML = offlineCard("drift check", data.error); return; }

  const statusPill = (s) =>
    s === "untracked" ? pill("not in git", "red") :
    s === "in-git-not-gitops" ? pill("in git · not GitOps", "amber") :
    s === "argocd-annotated" ? pill("argocd (annotated)", "green") :
    pill("argocd", "green");

  const rows = data.objects.map((o) => `
    <div class="app-row">
      <div class="app-row-main">
        <div class="app-row-name">${esc(o.kind)} / ${esc(o.name)}</div>
        <div class="app-row-meta">ns: ${esc(o.namespace)}</div>
      </div>
      ${statusPill(o.status)}
    </div>`).join("");

  const untracked = data.counts["untracked"] || 0;
  body.innerHTML = `
    <div class="cfg-toolbar">
      <span class="status-dot ${untracked ? "bad" : "ok"}"></span>
      <span class="cfg-repo">${untracked} object${untracked === 1 ? "" : "s"} running but not in git</span>
      <span class="sp"></span>
      <button class="act-btn" data-act="refreshConfigTab">↻ refresh</button>
    </div>
    <div class="cfg-stat">Checked: ${esc(data.namespaces.join(", "))} · ${pill(data.counts["argocd"] || 0, "green")} argocd-managed · ${pill(data.counts["in-git-not-gitops"] || 0, "amber")} in-git-not-gitops · ${pill(untracked, "red")} untracked</div>
    <div class="run-list mt">${rows || `<div class="empty">nothing found</div>`}</div>`;
}

/* ─── Logs: pipeline health + native ES search + Kibana embed ─── */

async function renderLogsTab(body) {
  body.innerHTML = `<div class="cfg-loading">checking log pipeline…</div>`;
  const health = await api("GET", "/api/v1/platform/live/logs/pipeline");

  const links = health.reachable ? health.links.map((l) => `
    <div class="cfg-item"><span>${esc(l.name)}</span><span class="val ${l.ok ? "" : "critical"}">${l.ok ? "✓" : "✕"} ${esc(l.detail)}</span></div>
  `).join("") : offlineCard("log pipeline", health.error);

  const svcOptions = (state.data.services || []).map((s) => `<option value="${esc(s.name)}">${esc(s.name)}</option>`).join("");

  body.innerHTML = `
    <div class="cfg-toolbar">
      <span class="cfg-repo">log pipeline status</span>
      <span class="sp"></span>
      <button class="btn sm" data-act="openDashboard" data-a1="kibana" data-a2="Kibana">⧉ open Kibana</button>
      <button class="act-btn" data-act="refreshConfigTab">↻ refresh</button>
    </div>
    <div class="cfg-section"><h3>Pipeline health</h3>${links}</div>

    <div class="cfg-section">
      <h3>Search logs (Elasticsearch, direct — no Kibana login needed)</h3>
      <div class="add-svc mb">
        <select id="log-svc" style="background:var(--panel2);border:1px solid var(--border);border-radius:8px;padding:7px 12px;color:var(--text);font-family:var(--mono);font-size:12.5px">
          <option value="">all services</option>
          ${svcOptions}
        </select>
        <input id="log-query" placeholder="filter text (optional)…" data-enter="searchLogs">
        <select id="log-since" style="background:var(--panel2);border:1px solid var(--border);border-radius:8px;padding:7px 12px;color:var(--text);font-family:var(--mono);font-size:12.5px">
          <option value="now-1h">last hour</option>
          <option value="now-24h">last 24h</option>
          <option value="now-7d">last 7 days</option>
          <option value="now-90d">last 90 days</option>
        </select>
        <button class="btn sm" data-act="searchLogs">Search</button>
      </div>
      <div id="log-results" class="empty">enter a query and press Search</div>
    </div>`;
}

// The window is part of the answer, not a hidden default. It used to be
// fixed at now-1h with nothing on screen saying so, so a search over an index
// the same panel reports as holding 363k documents came back "0 matches" and
// looked broken — filebeat had stopped, and the newest document was days old.
const SINCE_LABELS = { "now-1h": "the last hour", "now-24h": "the last 24 hours", "now-7d": "the last 7 days", "now-90d": "the last 90 days" };

async function searchLogs() {
  const service = $("log-svc")?.value || "";
  const q = $("log-query")?.value || "";
  const since = $("log-since")?.value || "now-1h";
  const box = $("log-results");
  if (!box) return;
  box.innerHTML = `<div class="cfg-loading">searching…</div>`;
  try {
    const r = await api("GET", `/api/v1/platform/live/logs/search?service=${encodeURIComponent(service)}&q=${encodeURIComponent(q)}&limit=100&since=${encodeURIComponent(since)}`);
    if (!r.reachable) { box.innerHTML = offlineCard("elasticsearch", r.error); return; }
    const window_ = SINCE_LABELS[since] || since;
    if (!r.lines.length) {
      box.innerHTML = `<div class="empty">No log line matches this search in ${esc(window_)}.
        ${r.newest ? `The newest document in the index is from ${esc(r.newest)} — widen the range above.` : ""}</div>`;
      return;
    }
    box.innerHTML = `
      <div class="muted small mb">${r.hits} matches in ${esc(window_)} (showing up to 100)</div>
      <pre class="cfg-code" style="max-height:50vh;overflow:auto">${esc(r.lines.join("\n"))}</pre>`;
  } catch (e) { box.innerHTML = offlineCard("elasticsearch", e.message); }
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
    const r = await api("GET", `/api/v1/platform/live/services/${service}/drilldown`);
    if (!r.reachable) { box.innerHTML = offlineCard("cluster", r.error); return; }

    const podRows = r.pods.map((p) => {
      if (!p.reachable) return `<div class="app-row"><div class="app-row-main">${offlineCard(p.name || "pod", p.error)}</div></div>`;
      const c = appContainer(p.containers, service);
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
            <button class="act-btn" data-act="viewPodLogs" data-a1="devops-platform" data-a2="${esc(p.name)}" data-a3="${esc(p.name)} logs">▤ logs</button>
            <button class="act-btn danger" data-act="restartPod" data-a1="devops-platform" data-a2="${esc(p.name)}">↻ restart</button>
          </div>
        </div>`;
    }).join("") || `<div class="empty">no pods running</div>`;

    const eventRows = r.events.map((e) => `
      <div class="cfg-item"><span>${pill(e.reason, e.type === "Warning" ? "red" : "muted")} ${esc(e.message)}</span><span class="val small">${e.count > 1 ? `×${e.count}` : ""}</span></div>
    `).join("") || `<div class="empty">no recent events</div>`;

    const revRows = (r.rollout.reachable ? r.rollout.revisions : []).slice().reverse().map((rev) => `
      <div class="cfg-item">
        <span>rev ${rev.revision} <span class="mono small muted">${esc(rev.image || "")}</span></span>
        <button class="act-btn" data-act="rollbackDeployment" data-a1="devops-platform" data-a2="${esc(service)}" data-a3="${rev.revision}">↩ rollback here</button>
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
    const r = await api("POST", "/api/v1/platform/live/rollout/undo", { namespace, deployment, to_revision: revision });
    toast("✓ " + r.message, true);
    setTimeout(() => loadServiceRuntime(deployment), 2000);
  } catch (e) { toast("✕ " + e.message, false); }
}

function closeDetail() {
  state.detail = null;
  $("detail-panel").classList.remove("show", "wide");
  $("detail-overlay").classList.remove("show");
}

/* ═══════════ ACTION DISPATCH ═══════════ */

// Every button/row in this console used to carry an inline `onclick="fn(…)"`.
// Sharing a page with the main SPA means sharing its Content-Security-Policy,
// and inline handlers are the one thing a CSP nonce cannot whitelist — only
// 'unsafe-inline' would, for the whole document. So markup now emits
// `data-act="name"` plus `data-a1…data-a3`, and this table is the only place
// that maps a name to a call. Arguments arrive as strings; entries that need
// a number or a boolean coerce it here.
const ACTIONS = {
  renderTopology: () => renderTopology(),
  refreshConfigTab: () => refreshConfigTab(),
  ciTrigger: () => ciTrigger(),
  showArgoCreds: () => showArgoCreds(),
  syncServiceList: () => syncServiceList(),
  rerunVaultSetup: () => rerunVaultSetup(),
  reconcileTerraform: () => reconcileTerraform(),
  preflightFlow: () => preflightFlow(),
  workerProvision: () => workerProvision(),
  workerDeprovision: () => workerDeprovision(),
  searchLogs: () => searchLogs(),
  closeDetail: () => closeDetail(),
  gotoServices: () => switchView("services"),

  showDetail: (el) => showDetail(el.dataset.a1),
  showPipeline: (el) => showPipeline(el.dataset.a1),
  seedSecretsFlow: (el) => seedSecretsFlow(el.dataset.a1),
  switchConfigTab: (el) => switchConfigTab(el.dataset.a1),
  viewCiLogs: (el) => viewCiLogs(el.dataset.a1),
  ciRerun: (el) => ciRerun(el.dataset.a1),
  ciCancel: (el) => ciCancel(el.dataset.a1),
  showCiGraph: (el) => renderCiGraphTab(el.dataset.a1),
  showArgoResources: (el) => showArgoResources(el.dataset.a1),
  argoRefresh: (el) => argoRefresh(el.dataset.a1),
  argoSync: (el) => argoSync(el.dataset.a1),
  showVaultSecretMeta: (el) => showVaultSecretMeta(el.dataset.a1),
  showMonitoringPodDetail: (el) => showMonitoringPodDetail(el.dataset.a1),
  stopScript: (el) => stopScript(el.dataset.a1),
  viewScriptOutput: (el) => viewScriptOutput(el.dataset.a1),
  showFiringAlertDetail: (el) => showFiringAlertDetail(Number(el.dataset.a1)),
  showAlertHistoryDetail: (el) => showAlertHistoryDetail(Number(el.dataset.a1)),

  openDashboard: (el) => openDashboard(el.dataset.a1, el.dataset.a2),
  restartPod: (el) => restartPod(el.dataset.a1, el.dataset.a2),
  viewPodLogs: (el) => viewPodLogs(el.dataset.a1, el.dataset.a2, el.dataset.a3),
  runScript: (el) => runScript(el.dataset.a1, el.dataset.a2 === "true"),
  rollbackDeployment: (el) =>
    rollbackDeployment(el.dataset.a1, el.dataset.a2, Number(el.dataset.a3)),

  // Buttons that read the value out of a named input next to them.
  addServiceFromInput: (el) => addServiceFlow(el.dataset.a1, inputValue(el.dataset.input)),
  shipServiceFromInput: (el) => shipServiceFlow(el.dataset.a1, inputValue(el.dataset.input)),
  createAppFromInput: (el) => createAppFlow(inputValue(el.dataset.input)),
};

// Enter pressed inside an input, reading that same input's value.
const ENTER_ACTIONS = {
  addServiceFromSelf: (el) => addServiceFlow(el.dataset.a1, el.value),
  createAppFromSelf: (el) => createAppFlow(el.value),
  searchLogs: () => searchLogs(),
};

function inputValue(id) {
  const el = $(id);
  return el ? el.value : "";
}

function bindDispatch(root) {
  root.addEventListener("click", (event) => {
    // Nearest match wins, which reproduces what the old inline
    // `event.stopPropagation()` barriers did: a [data-stop] ancestor shields
    // its subtree from the clickable row or card wrapping it.
    const hit = event.target.closest("[data-act],[data-stop]");
    if (!hit || !root.contains(hit)) return;
    const name = hit.dataset.act;
    if (!name) return;
    const fn = ACTIONS[name];
    if (!fn) {
      console.warn(`[platform] unknown action: ${name}`);
      return;
    }
    fn(hit);
  });

  root.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    const el = event.target.closest("[data-enter]");
    if (!el || !root.contains(el)) return;
    const fn = ENTER_ACTIONS[el.dataset.enter];
    if (fn) fn(el);
  });
}

/* ═══════════ NAV / TIMER / MOUNT ═══════════ */

function switchView(name) {
  if (state.pipelineTimer) { clearInterval(state.pipelineTimer); state.pipelineTimer = null; }
  if (state.graphTimer) { clearInterval(state.graphTimer); state.graphTimer = null; }
  const root = $("platform-root");
  if (!root) return;
  root.querySelectorAll(".nav-item").forEach((n) => n.classList.toggle("active", n.dataset.view === name));
  root.querySelectorAll(".view").forEach((v) => v.classList.toggle("active", v.id === `view-${name}`));
  state.view = name;
  if (window.location.hash !== `#/platform/${name}`) {
    window.location.hash = `#/platform/${name}`;
  }
}

/**
 * Live console summary: instead of every open tab polling `GET /platform`
 * on its own 30s clock, one long-lived request reads
 * `GET /platform/live/stream` (server pushes a fresh snapshot every ~2s,
 * see platform.py) and re-renders the instant a frame arrives. Native
 * `EventSource` can't send an Authorization header, so this reads the same
 * `text/event-stream` body by hand through `fetch()`, which can.
 */
function startLiveStream() {
  stopLiveStream();
  const box = $("autorefresh");
  if (box && !box.checked) return;
  // Native EventSource rather than a hand-rolled fetch reader: it handles
  // frame parsing and reconnection itself, and it is the transport the job
  // log stream already uses successfully. It cannot send an Authorization
  // header, so — exactly as that stream does — the token travels as a query
  // parameter, and the endpoint accepts it through get_current_user_sse.
  const token = localStorage.getItem(TOKEN_KEY);
  const source = new EventSource(
    `/api/v1/platform/live/stream?token=${encodeURIComponent(token || "")}`
  );
  state.liveSource = source;

  source.addEventListener("update", (event) => {
    try {
      state.data = JSON.parse(event.data);
      render();
    } catch (e) {
      console.error("[live/stream] frame handling failed:", e);
    }
  });

  // A server-side read failure arrives as its own event; keep the last good
  // snapshot on screen rather than blanking the console.
  source.addEventListener("error", () => { /* keep last good data */ });

  source.onerror = () => {
    // EventSource reconnects on its own, but not after the server closes the
    // stream deliberately; recreate it in that case so "live updates" stays
    // live rather than silently stopping.
    if (source.readyState === EventSource.CLOSED && state.mounted && (!box || box.checked)) {
      setTimeout(startLiveStream, 3000);
    }
  };
}

function stopLiveStream() {
  if (state.liveSource) { state.liveSource.close(); state.liveSource = null; }
}

let booted = false;

/** Called by the shell router whenever a #/platform/* route becomes active. */
async function mount(view) {
  state.mounted = true;
  switchView(VIEWS.includes(view) ? view : state.view);
  if (booted) {
    // Returning to the console after unmount(), which aborted the stream.
    // Restarting here is what keeps "live updates" live across a round trip
    // through a control-plane route — without it the console came back
    // frozen on whatever snapshot it held when the user left, until a full
    // page reload. startLiveStream() aborts any existing reader first, so
    // calling it again cannot stack two streams on one console.
    startLiveStream();
    return;
  }
  booted = true;
  bindDispatch($("platform-root"));
  try {
    await fetchData();
  } catch (e) {
    $("view-topology").innerHTML = `<div class="card" style="border-color:var(--red)"><span class="red">Failed to load: ${esc(e.message)}</span></div>`;
  }
  startLiveStream();
  const box = $("autorefresh");
  if (box) box.addEventListener("change", () => (box.checked ? startLiveStream() : stopLiveStream()));
}

/** Called by the shell router when the user leaves for a control-plane route. */
function unmount() {
  state.mounted = false;
  stopLiveStream();
  if (state.pipelineTimer) { clearInterval(state.pipelineTimer); state.pipelineTimer = null; }
  if (state.runTimer) { clearInterval(state.runTimer); state.runTimer = null; }
  if (state.graphTimer) { clearInterval(state.graphTimer); state.graphTimer = null; }
  closeDetail();
}

return { mount, unmount, views: VIEWS };
})();
