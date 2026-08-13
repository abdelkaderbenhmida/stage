"use strict";

const state = { data: null, view: "topology", search: "", timer: null, detail: null, configTab: "helm" };

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

function gaugeSvg(id, pct, color, label, target) {
  const R = 34, C = 2 * Math.PI * R;
  const off = C * (1 - Math.min(pct, 1));
  return `
    <div class="gauge">
      <svg width="96" height="96" viewBox="0 0 96 96">
        <circle cx="48" cy="48" r="${R}" fill="none" stroke="rgba(120,140,170,.15)" stroke-width="8"/>
        <circle cx="48" cy="48" r="${R}" fill="none" stroke="${color}" stroke-width="8"
                stroke-linecap="round" stroke-dasharray="${C}" stroke-dashoffset="${off}"
                transform="rotate(-90 48 48)"/>
        <text x="48" y="46" text-anchor="middle" font-size="14" font-weight="700" fill="#e6ecf5" font-family="var(--mono)">${target}</text>
        <text x="48" y="62" text-anchor="middle" font-size="9" fill="#8a97ad">${esc(label)}</text>
      </svg>
    </div>`;
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

/* ═══════════ TOPOLOGY ═══════════ */

function topoNode(x, y, w, h, label, sub, color, extra) {
  return `
    <g class="topo-node" data-id="${esc(extra.id)}" data-view="${esc(extra.view || "")}"
       data-search="${esc(extra.search || "")}" data-tooltip="${esc(extra.tooltip || "")}">
      <rect x="${x}" y="${y}" width="${w}" height="${h}" rx="10"
            fill="rgba(20,27,41,.9)" stroke="${color}" stroke-width="1.2"/>
      <rect x="${x + 8}" y="${y + (h / 2 - 10)}" width="4" height="20" rx="2" fill="${color}"/>
      <text x="${x + 22}" y="${y + 22}" font-size="13.5" font-weight="700" fill="#e6ecf5">${esc(label)}</text>
      <text x="${x + 22}" y="${y + h - 16}" font-size="10.5" fill="#8a97ad" font-family="var(--mono)">${esc(sub)}</text>
    </g>`;
}

function topoEdge(d, x1, y1, x2, y2, color, bend) {
  const cx = x1 + (x2 - x1) * (bend ?? 0.5);
  const cy = y1;
  return `<path d="M ${x1} ${y1} Q ${cx} ${y1} ${x2} ${y2}"
           fill="none" stroke="${color}" stroke-width="1.2" opacity="0.45"
           class="topo-edge" marker-end="url(#arrow-${color.replace("#", "")}"/>`;
}

function topoArrows() {
  const defs = ["#4cc2ff", "#a78bfa", "#fbbf24", "#f472b6", "#fb923c", "#34d399", "#7a5cff"]
    .map((c) => `
      <marker id="arrow-${c.replace("#", "")}" viewBox="0 0 10 10" refX="9" refY="5"
              markerWidth="6" markerHeight="6" orient="auto-start-reverse">
        <path d="M 0 0 L 10 5 L 0 10 z" fill="${c}"/>
      </marker>`)
    .join("");
  return `<defs>${defs}</defs>`;
}

function renderTopology() {
  const d = state.data;
  const svcs = d.services;
  const ov = d.overview;
  const CI = "#a78bfa", ARGO = "#f472b6", HELM = "#fbbf24", VAULT = "#fb923c", MON = "#34d399", SLO = "#4cc2ff";

  const X_REPO = 30, X_SVC = 330, X_LAYER = 630;
  const W = 250, H = 54;
  const LAYER_GAP = 96;
  const svcH = 62;

  const parts = [];
  parts.push(topoArrows());

  let y = 30;
  parts.push(topoNode(X_REPO, y, W, H, "app/*/main.py", "discovery — source of truth", "#34d399", {
    id: "repo",
    tooltip: `<div class="tt-title">Discovery contract</div>
      <div class="tt-row">repo: ${esc(ov.repo)}</div>
      <div class="tt-row">branch: ${esc(ov.revision.branch)} @ ${esc(ov.revision.commit)}</div>
      <div class="tt-row">any dir with main.py = a service</div>
      <div class="tt-row">0 hardcoded names in the platform</div>`,
  }));

  const repoY = y + H;

  y = 150;
  svcs.forEach((s, i) => {
    const yy = y + i * (svcH + 22);
    parts.push(topoNode(X_SVC, yy, W, svcH, s.name, `v${s.version} · ${s.endpoints.length} routes`, "#4cc2ff", {
      id: `svc-${s.name}`,
      view: "services",
      search: s.name,
      tooltip: `<div class="tt-title">${esc(s.title)}</div>
        <div class="tt-row">${s.loc} LOC · ${s.requirements_num} pinned deps</div>
        <div class="tt-row">vault: ${s.uses_vault ? "yes" : "no"} · endpoints: ${s.endpoints.join(" ")}</div>`,
    }));
    parts.push(topoEdge(d, X_REPO + W, repoY, X_SVC + 10, yy + svcH / 2, "#34d399", 0.35));
  });
  const svcCenters = svcs.map((_, i) => 150 + i * (svcH + 22) + svcH / 2);

  const layers = [
    { key: "ci", label: "CI / CD", sub: `${d.ci.jobs.length} jobs · fromJSON matrix`, color: CI, view: "ci",
      tooltip: `<div class="tt-title">CI/CD</div>
        <div class="tt-row">discover job → matrix per service</div>
        <div class="tt-row">lint · test · build · trivy · deploy</div>
        <div class="tt-row">fromJSON: ${d.ci.uses_fromjson.join(", ")}</div>` },
    { key: "argocd", label: "ArgoCD", sub: `git files app/*/main.py · 1 app/service`, color: ARGO, view: "argocd",
      tooltip: `<div class="tt-title">ApplicationSet</div>
        <div class="tt-row">generator: git files ${esc((d.argocd.files_pattern || []).map((f) => f.path).join(", "))}</div>
        <div class="tt-row">sync: ${esc(JSON.stringify(d.argocd.sync_policy?.automated ?? {}))}</div>
        <div class="tt-row">delete dir → app pruned → cascade delete</div>` },
    { key: "helm", label: "Helm Chart", sub: `${d.helm.formula} · ${d.helm.total} objects`, color: HELM, view: "helm",
      tooltip: `<div class="tt-title">Generic chart</div>
        <div class="tt-row">${d.helm.formula} rendered objects</div>
        <div class="tt-row">per service: ${esc(d.helm.expected_per_service.join(" · "))}</div>
        <div class="tt-row">shared: ${esc(d.helm.expected_shared.join(" · "))}</div>` },
    { key: "vault", label: "Vault", sub: `policies + k8s-auth roles per service`, color: VAULT, view: "vault",
      tooltip: `<div class="tt-title">Secret provisioning</div>
        <div class="tt-row">loop from devops-service-list ConfigMap</div>
        <div class="tt-row">policy: devops-platform-&lt;svc&gt;</div>
        <div class="tt-row">role bound to &lt;svc&gt;-sa, ttl 1h</div>` },
    { key: "monitoring", label: "Prometheus", sub: `ServiceMonitor part-of → ${d.monitoring.rules.length} SLO rules`, color: MON, view: "monitoring",
      tooltip: `<div class="tt-title">Observability</div>
        <div class="tt-row">matchLabels: part-of=devops-platform</div>
        <div class="tt-row">${d.monitoring.rules.length} alert/record rules</div>
        <div class="tt-row">scrape /metrics 15s · relabel part_of</div>` },
  ];

  const layerY0 = 130;
  layers.forEach((L, i) => {
    const yy = layerY0 + i * (H + LAYER_GAP);
    parts.push(topoNode(X_LAYER, yy, W, H, L.label, L.sub, L.color, {
      id: L.key, view: L.view,
      tooltip: L.tooltip,
    }));
    svcCenters.forEach((cy, si) => {
      parts.push(topoEdge(d, X_SVC + W, cy, X_LAYER + 10, yy + H / 2, L.color, 0.4));
    });
    if (L.key === "argocd") {
      const helmY = layerY0 + 1 * (H + LAYER_GAP);
      parts.push(topoEdge(d, X_LAYER + W, yy + H, X_LAYER + W, helmY, ARGO, 0.3));
    }
  });

  const width = X_LAYER + W + 40;
  const height = layerY0 + (layers.length - 1) * (H + LAYER_GAP) + H + 40;

  $("view-topology").innerHTML = `
    ${explainer(`One picture of the whole platform. <b>Left:</b> your code (app/*/main.py). <b>Right:</b> what the platform does automatically for each service — build (CI), deploy (ArgoCD + Helm), secrets (Vault), monitoring (Prometheus). Add a service folder → new boxes appear. Nothing else to change.`)}
    <div class="head">
      <div>
        <h1>Platform Topology</h1>
        <div class="sub">One discovery point (<span class="mono">app/*/main.py</span>) fans out to every platform layer.
        Add a service — every downstream node in this map grows. Delete it — they shrink. Nothing else changes.</div>
      </div>
      <div class="header-actions legend">
        <div class="lg"><span class="sw" style="background:#34d399"></span> source of truth</div>
        <div class="lg"><span class="sw" style="background:#4cc2ff"></span> services</div>
        <div class="lg"><span class="sw" style="background:#a78bfa"></span> pipelines</div>
        <div class="lg"><span class="sw" style="background:#fbbf24"></span> k8s</div>
        <div class="lg"><span class="sw" style="background:#fb923c"></span> secrets</div>
        <div class="lg"><span class="sw" style="background:#34d399"></span> observability</div>
      </div>
    </div>
    <div class="topo-card">
      <svg id="topo" viewBox="0 0 ${width} ${height}">${parts.join("")}</svg>
    </div>`;

  const svg = $("topo");
  if (svg) {
    svg.querySelectorAll(".topo-node").forEach((n) => {
      n.addEventListener("mouseenter", (e) => {
        const tt = $("topo-tooltip");
        tt.innerHTML = n.dataset.tooltip || "";
        tt.style.opacity = "1";
      });
      n.addEventListener("mousemove", (e) => {
        const tt = $("topo-tooltip");
        tt.style.left = Math.min(e.clientX + 14, window.innerWidth - 280) + "px";
        tt.style.top = e.clientY + 14 + "px";
      });
      n.addEventListener("mouseleave", () => {
        $("topo-tooltip").style.opacity = "0";
      });
      n.addEventListener("click", () => {
        if (n.dataset.view) {
          if (n.dataset.search) {
            state.search = n.dataset.search;
            renderServices();
          }
          switchView(n.dataset.view);
          const inp = $("svc-search");
          if (inp && n.dataset.search) inp.value = n.dataset.search;
        }
      });
    });
  }
}

/* ═══════════ APPS (management console) ═══════════ */

function slugify(name) {
  return name.toLowerCase().replace(/[^a-z0-9-]/g, "-").replace(/-+/g, "-").replace(/^-|-$/g, "").slice(0, 40);
}

async function createAppFlow() {
  const raw = prompt("New app name (e.g. checkout):");
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
        <div class="add-svc">
          <input placeholder="new service name…" id="add-svc-${esc(a.name)}" onkeydown="if(event.key==='Enter'){const inp=event.target;addServiceFlow('${esc(a.name)}',inp.value)}">
          <button class="btn ghost sm" data-add-svc="${esc(a.name)}">＋ add service</button>
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
      <div class="header-actions">
        <span class="kbd">created files are local — git add/commit/push to activate</span>
        <button class="btn" onclick="createAppFlow()">＋ New app</button>
      </div>
    </div>
    <div class="grid cards">${cards || `<div class="card empty">no apps</div>`}</div>`;

  $("view-apps").querySelectorAll("[data-del-app]").forEach((b) => {
    b.addEventListener("click", () => deleteAppFlow(b.dataset.delApp));
  });
  $("view-apps").querySelectorAll("[data-add-svc]").forEach((b) => {
    b.addEventListener("click", () => addServiceFlow(b.dataset.addSvc));
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

/* ═══════════ HELM ═══════════ */

function renderHelm() {
  const d = state.data;
  const h = d.helm;
  const kinds = Object.entries(h.counts).sort((a, b) => a[0].localeCompare(b[0]));
  const perSvc = h.expected_per_service.map((k) => `<th>${esc(k)}</th>`).join("");
  const svcRows = h.per_service
    ? Object.entries(h.per_service).map(([name, ks]) => `
      <tr>
        <td><span class="mono" style="color:var(--accent);font-weight:700">${esc(name)}</span></td>
        ${h.expected_per_service.map((k) => `<td class="mono">${ks.includes(k) ? "✓" : "–"}</td>`).join("")}
      </tr>`).join("")
    : "";

  $("view-helm").innerHTML = `
    ${explainer(`The <b>recipe</b> that creates Kubernetes objects per service: Deployment (runs it), Service (network address), HPA (auto-scale), PDB (always available), SA (identity). Every service gets the same 5 — plus 3 shared objects once per platform.`)}
    <div class="head">
      <div>
        <h1>Helm Chart</h1>
        <div class="sub">One generic chart at <span class="mono">k8s/apps/chart</span>. Every template <span class="mono">ranges .Values.services[]</span> — rendering is a live <span class="mono">helm template</span> against the discovered service list.</div>
      </div>
      <div class="header-actions">${h.ok ? pill(`renders ${h.total} objects`, "green") : pill("render failed", "red")}</div>
    </div>

    ${h.ok ? "" : `<div class="card mb" style="border-color:var(--red)"><span class="red">${esc(h.error)}</span></div>`}

    <div class="grid two">
      <div class="card">
        <div class="card-title">Object kind counts (${h.total})</div>
        ${barRows(kinds.map(([k, v]) => ({ l: k, v })), "#fbbf24")}
      </div>
      <div class="card">
        <div class="card-title">Per-service matrix — expected: ${esc(h.expected_per_service.join(", "))}</div>
        <table>
          <thead><tr><th>Service</th>${perSvc}</tr></thead>
          <tbody>${svcRows || `<tr><td colspan="6" class="empty">no services</td></tr>`}</tbody>
        </table>
      </div>
    </div>

    <h2 class="mt">Shared objects — once per namespace</h2>
    <div class="grid cards">
      ${h.shared.map((o) => `
        <div class="card" style="display:flex;align-items:center;gap:12px;padding:14px 16px">
          <span class="status-dot ok"></span>
          <div>
            <div class="mono" style="font-weight:700">${esc(o.kind)}</div>
            <div class="muted small">${esc(o.name)} · ${esc(o.namespace || "–")}</div>
          </div>
        </div>`).join("")}
    </div>

    <h2 class="mt">Validation</h2>
    <pre class="code"><span class="tk-dim"># spec test — any service count, zero config edits</span>
printf 'services:\n  - name: foo\n  - name: users-service\n' > /tmp/t.yaml
helm template t k8s/apps/chart -f /tmp/t.yaml | grep -c '^kind:'   <span class="tk-accent"># → ${h.total}</span></pre>`;
}

/* ═══════════ CI ═══════════ */

function renderCI() {
  const d = state.data;
  const jobs = d.ci.jobs;
  const order = [...jobs.filter((j) => !j.needs.length), ...jobs.filter((j) => j.needs.length)];
  const pipe = order.map((j) => {
    const cls = j.id === "discover" ? "disc" : (j.matrix.has_matrix ? "stage" : "");
    const m = j.matrix.has_matrix
      ? `<div class="pn-meta">${j.matrix.from_json ? pill("fromJSON", "accent") : pill("static", "muted")} ${esc(j.matrix.keys.join(", "))}</div>`
      : `<div class="pn-meta">${esc(j.runs_on || "")}</div>`;
    return `<div class="pnode ${cls}"><div class="pn-name">${esc(j.id)}</div>${m}<div class="pn-meta">${j.steps.length} steps</div></div>`;
  }).join('<span class="muted" style="align-self:center">→</span>');

  const cards = jobs.map((j) => `
    <div class="card">
      <div class="row" style="justify-content:space-between">
        <div class="mono" style="font-weight:800">${esc(j.name)}</div>
        ${j.needs.length ? pill(j.needs.join("+"), "muted") : pill("root", "green")}
      </div>
      <div class="mt1 chips">${j.steps.map((s) => `<span class="chip">${esc(s)}</span>`).join("")}</div>
    </div>`).join("");

  const triggerRows = Object.entries(d.ci.triggers).map(([k, v]) => `
    <tr><td class="mono" style="color:var(--accent)">${esc(k)}</td>
    <td><div class="chips">${v.map((t) => `<span class="chip">${esc(t)}</span>`).join("") || '<span class="muted">–</span>'}</div></td></tr>`).join("");

  $("view-ci").innerHTML = `
    ${explainer(`Your <b>software pipeline</b>: find services → check quality → test → build images → security scan → deploy. Discovery-driven: a new service folder creates new pipeline jobs by itself — the workflow file never changes.`)}
    <div class="head">
      <div>
        <h1>CI / CD Pipeline</h1>
        <div class="sub"><span class="mono">discover</span> is the only source of truth — every matrix job expands from <span class="mono">fromJSON(discover.outputs.services)</span>. New service = new matrix rows, no workflow edits.</div>
      </div>
      <div class="header-actions">${pill("cancel-in-progress concurrency", "muted")}</div>
    </div>

    <div class="card mb"><div class="pipe">${pipe}</div></div>

    <h2 class="mb">Jobs</h2>
    <div class="grid two">${cards}</div>

    <div class="grid two mt">
      <div class="card">
        <div class="card-title">Triggers</div>
        <table><tbody>${triggerRows}</tbody></table>
      </div>
      <div class="card">
        <div class="card-title">Guardrails</div>
        <div class="kv">
          <span class="k">concurrency</span><span class="v mono">${esc(JSON.stringify(d.ci.concurrency ?? "none"))}</span>
          <span class="k">permissions</span><span class="v mono">${esc(JSON.stringify(d.ci.permissions ?? "none"))}</span>
          <span class="k">gates</span><span class="v">${pill("lint", "muted")} ${pill("gitleaks", "muted")} ${pill("pip-audit", "muted")} ${pill("trivy CRIT/HIGH", "red")}</span>
        </div>
      </div>
    </div>`;
}

/* ═══════════ VAULT ═══════════ */

function renderVault() {
  const d = state.data.vault;
  const lp = d.per_service;
  const objRows = d.objects.map((o) => `
    <tr><td class="mono">${esc(o.kind)}</td><td class="mono">${esc(o.name)}</td><td class="mono">${esc(o.namespace ?? "–")}</td></tr>`).join("");

  $("view-vault").innerHTML = `
    ${explainer(`<b>Secret manager.</b> For every discovered service, the setup job auto-creates: 1 read-only policy (its own secrets), 1 login role, bound to the service account. <b>Fail-closed:</b> no secret → service refuses to start, so nothing runs with fake keys.`)}
    <div class="head">
      <div>
        <h1>Vault</h1>
        <div class="sub">Setup job reads <span class="mono">devops-service-list</span> ConfigMap → provisions policy + k8s-auth role per discovered service. Fail-closed: placeholder secrets, apps refuse to start without real values.</div>
      </div>
      <div class="header-actions">${lp.present ? pill("discovery loop", "green") : pill("loop missing", "red")}</div>
    </div>

    <div class="grid two">
      <div class="card">
        <div class="card-title">Per-service provisioning</div>
        <div class="kv">
          <span class="k">policy</span><span class="v mono" style="color:var(--accent)">${esc(lp.policy_template)}</span>
          <span class="k">k8s-auth role</span><span class="v mono">${esc(lp.k8s_role_template)}</span>
          <span class="k">bound SA</span><span class="v mono">${esc(lp.bound_sa_template)}</span>
          <span class="k">KV path</span><span class="v mono">${esc(lp.kv_path_template)}</span>
          <span class="k">ttl</span><span class="v mono">1h</span>
          <span class="k">services list</span><span class="v mono">${esc(d.services_source ?? "not wired")}</span>
        </div>
      </div>
      <div class="card">
        <div class="card-title">Security posture</div>
        <div class="kv">
          <span class="k">token</span><span class="v mono">${esc(d.token_source)}</span>
          <span class="k">fail closed</span><span class="v">${d.fail_closed ? pill("set -e", "green") : pill("no", "red")}</span>
          <span class="k">least priv</span><span class="v">${pill("read-only policy", "green")}</span>
          <span class="k">drop caps</span><span class="v">${pill("ALL", "green")} + IPC_LOCK/NET_RAW</span>
        </div>
      </div>
    </div>

    <h2 class="mt mb">Setup script loop</h2>
    <pre class="code">for svc in $SERVICES; do
  vault policy write <span class="tk-accent">devops-platform-\${svc}</span> -   <span class="tk-dim"># read-only on secret/data/...</span>
  vault write auth/kubernetes/role/<span class="tk-accent">\${svc}</span> \\
    bound_service_account_names=<span class="tk-accent">\${svc}-sa</span> policies=<span class="tk-accent">devops-platform-\${svc}</span> ttl="1h"
  vault kv put secret/devops-platform/<span class="tk-accent">\${svc}</span> DATABASE_URL=""
done</pre>

    <h2 class="mt mb">Manifest objects (${d.objects.length})</h2>
    <div class="card" style="overflow-x:auto"><table>
      <thead><tr><th>Kind</th><th>Name</th><th>Namespace</th></tr></thead>
      <tbody>${objRows}</tbody>
    </table></div>`;
}

/* ═══════════ MONITORING ═══════════ */

function renderMonitoring() {
  const d = state.data.monitoring;
  const sm = d.service_monitor;
  const sloRows = Object.entries(d.slo_detail).map(([k, v]) => `
    <tr>
      <td class="mono" style="color:var(--accent)">${esc(k)}</td>
      <td>${esc(v.target)}</td>
      <td class="mono">${esc(v.rule)}</td>
      <td class="mono small">${esc(v.matcher)}</td>
    </tr>`).join("");

  const ruleRows = d.rules.map((r) => `
    <tr>
      <td>${r.type === "record" ? pill("record", "violet") : pill("alert", r.severity === "critical" ? "red" : "amber")}</td>
      <td class="mono">${esc(r.name)}</td>
      <td class="mono small">${esc(r.group)}</td>
      <td class="mono small" style="max-width:380px;overflow:hidden;text-overflow:ellipsis">${esc(r.expr.slice(0, 110))}${r.expr.length > 110 ? "…" : ""}</td>
      <td>${r.severity ? pill(r.severity, r.severity === "critical" ? "red" : "amber") : ""}</td>
      <td class="mono small">${esc(r.slo ?? "")}</td>
      <td class="mono small">${esc(r.for)}</td>
    </tr>`).join("");

  $("view-monitoring").innerHTML = `
    ${explainer(`<b>Alerts and targets (SLOs):</b> 99.9% availability, P95 latency &lt; 200ms, errors &lt; 1%. All services share one label (part-of=devops-platform), so a new service is watched instantly — the rule file never changes.`)}
    <div class="head">
      <div>
        <h1>Monitoring &amp; SLOs</h1>
        <div class="sub">One ServiceMonitor matches <span class="mono">part-of: devops-platform</span> — every discovered service is scraped automatically. SLO rules select <span class="mono">part_of="devops-platform"</span>, so the rule set never changes with the service count.</div>
      </div>
      <div class="header-actions">${pill(`${sm.scrape_path} every ${sm.scrape_interval}`, "green")}</div>
    </div>

    <div class="grid two">
      <div class="card">
        <div class="card-title">SLO targets</div>
        <table><thead><tr><th>SLI</th><th>Target</th><th>Rule</th><th>Matcher</th></tr></thead>
          <tbody>${sloRows}</tbody></table>
      </div>
      <div class="card">
        <div class="card-title">ServiceMonitor</div>
        <div class="gauge-row">
          ${gaugeSvg("g-av", 0.999, "#34d399", "availability", "99.9%")}
          ${gaugeSvg("g-lt", 0.2, "#4cc2ff", "latency P95", "200ms")}
          ${gaugeSvg("g-er", 0.01, "#f87171", "5xx rate", "1%")}
        </div>
        <div class="mt1 kv">
          <span class="k">matchLabels</span><span class="v mono">${esc(JSON.stringify(sm.match_labels))}</span>
          <span class="k">relabel</span><span class="v small">${esc(sm.relabel)}</span>
        </div>
      </div>
    </div>

    <h2 class="mt mb">PrometheusRules (${d.rules.length})</h2>
    <div class="card" style="overflow-x:auto"><table>
      <thead><tr><th>Type</th><th>Name</th><th>Group</th><th>Expression</th><th>Severity</th><th>SLO/Inc</th><th>For</th></tr></thead>
      <tbody>${ruleRows || `<tr><td colspan="7" class="empty">none</td></tr>`}</tbody>
    </table></div>

    ${d.dashboards.length ? `
    <h2 class="mt mb">Grafana dashboards</h2>
    <div class="chips">${d.dashboards.map((x) => `<span class="chip accent">${esc(x)}</span>`).join("")}</div>` : ""}`;
}

/* ═══════════ ARGOCD ═══════════ */

function renderArgocd() {
  const d = state.data.argocd;
  const auto = d.sync_policy?.automated ?? {};
  const appRows = d.static_applications.map((a) => `
    <tr><td class="mono" style="color:var(--accent);font-weight:700">${esc(a.name)}</td>
    <td class="mono">${esc(a.repo)}</td><td class="mono">${esc(a.path)}</td></tr>`).join("");

  $("view-argocd").innerHTML = `
    ${explainer(`<b>Deploys straight from git (GitOps).</b> Auto-creates one Application per service folder. Delete the folder → ArgoCD removes the app → all its k8s objects are deleted. Zero manual cleanup.`)}
    <div class="head">
      <div>
        <h1>ArgoCD</h1>
        <div class="sub">ApplicationSet with the <span class="mono">git files</span> generator over <span class="mono">app/*/main.py</span> — one Application per discovered service. Deleting a service dir stops the generator, ArgoCD prunes the app, the finalizer cascade-deletes its resources.</div>
      </div>
      <div class="header-actions">${pill("1 app / service", "violet")}</div>
    </div>

    <div class="grid two">
      <div class="card">
        <div class="card-title">ApplicationSet</div>
        <div class="kv">
          <span class="k">name</span><span class="v mono" style="color:var(--accent)">${esc(d.name)}</span>
          <span class="k">generator</span><span class="v">${d.generator_type === "git" ? pill("git files", "violet") : pill("unknown", "red")}</span>
          <span class="k">file pattern</span><span class="v mono">${esc((d.files_pattern || []).map((f) => f.path).join(", "))}</span>
          <span class="k">app name</span><span class="v mono">${esc(d.app_name_template ?? "?")}</span>
          <span class="k">revision</span><span class="v mono">${esc(d.revision ?? "main")}</span>
          <span class="k">part-of</span><span class="v mono">${esc(d.part_of_label ?? "–")}</span>
        </div>
      </div>
      <div class="card">
        <div class="card-title">Sync policy</div>
        <div class="kv">
          <span class="k">automated</span><span class="v mono">${esc(JSON.stringify(auto))}</span>
          <span class="k">options</span><span class="v mono">${esc(JSON.stringify(d.sync_policy?.syncOptions ?? []))}</span>
          <span class="k">retry</span><span class="v mono">${esc(JSON.stringify(d.sync_policy?.retry ?? {}))}</span>
        </div>
        <hr class="hr">
        <div class="small muted">Scale story: add <span class="mono">app/foo/main.py</span> → generator emits a new Application → <span class="mono">helm template</span> renders <span class="mono">services[0].name=foo</span> → Deployment/Service/SA/HPA/PDB appear. Remove it → pruned.</div>
      </div>
    </div>

    <h2 class="mt mb">Static Applications (${d.static_applications.length})</h2>
    <div class="card" style="overflow-x:auto"><table>
      <thead><tr><th>Name</th><th>Repo</th><th>Path</th></tr></thead>
      <tbody>${appRows || `<tr><td colspan="3" class="empty">none</td></tr>`}</tbody>
    </table></div>`;
}

/* ═══════════ CONFIGURATION — live status + real actions ═══════════ */

const liveCache = { ci: null, argocd: null, vault: null, alerts: null, cluster: null };

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

/* ─── shared: open real dashboard via on-demand port-forward ─── */

async function openDashboard(tool, label) {
  try {
    loading(true);
    const r = await api("POST", `/api/live/dashboard/${tool}/open`);
    toast(`✓ ${label} ready at ${r.url}`, true);
    window.open(r.url, "_blank");
  } catch (e) { toast("✕ " + e.message, false); }
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
  liveCache.ci = data;
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
  liveCache.argocd = data;
  if (!data.reachable) { body.innerHTML = offlineCard("ArgoCD / cluster", data.error); return; }

  const syncCls = (s) => s === "Synced" ? "green" : s === "OutOfSync" ? "amber" : "muted";
  const healthCls = (s) => s === "Healthy" ? "green" : s === "Degraded" ? "red" : s === "Progressing" ? "amber" : "muted";

  const rows = (data.apps || []).map((a) => `
    <div class="app-row">
      <div class="app-row-main">
        <div class="app-row-name">${esc(a.name)}</div>
        <div class="app-row-meta">${esc(a.path)} @ ${esc(a.revision || "–")}</div>
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
  liveCache.vault = data;
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
    box.innerHTML = (sec.keys || []).map((k) => `<div class="cfg-item"><span class="mono">${esc(k)}</span></div>`).join("") || `<div class="empty">no keys</div>`;
  } catch (e) { /* leave loading state message */ }
}

/* ─── Monitoring: real firing alerts via Alertmanager ─── */

async function renderMonitoringTab(body) {
  body.innerHTML = `<div class="cfg-loading">checking alertmanager…</div>`;
  const data = await api("GET", "/api/live/alerts");
  liveCache.alerts = data;
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
      <h3>Pod health (monitoring namespace)</h3>
      <div id="mon-pods"><div class="cfg-loading">loading pods…</div></div>
    </div>`;

  loadMonitoringPods();
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
  `;

  $("detail-title").textContent = svc.name;
  $("detail-content").innerHTML = content;
  $("detail-panel").classList.add("show");
  $("detail-overlay").classList.add("show");
}

function closeDetail() {
  state.detail = null;
  $("detail-panel").classList.remove("show");
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
