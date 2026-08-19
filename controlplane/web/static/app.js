/* Control-plane UI.
 *
 * Buildless SPA: hash routing, vanilla DOM, talks to /api/v1 with the same
 * bearer token any API client would use. Kept in one file deliberately so the
 * UI ships with the API container and needs no bundler.
 */

const API = "/api/v1";
const TOKEN_KEY = "cp.access_token";
const REFRESH_KEY = "cp.refresh_token";

const $ = (sel, root = document) => root.querySelector(sel);
const view = () => $("#view");

/* ---------------------------------------------------------------- utils */

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

function pill(status) {
  return `<span class="pill ${esc(status)}">${esc(status)}</span>`;
}

/** Mark an input invalid and show an inline message under it. */
function setFieldError(input, message) {
  if (!input) return;
  input.setAttribute("aria-invalid", "true");
  let err = input.parentElement?.querySelector(".field-error");
  if (!err && input.parentElement) {
    err = document.createElement("p");
    err.className = "field-error";
    err.id = `${input.id}-error`;
    input.setAttribute("aria-describedby", err.id);
    input.parentElement.appendChild(err);
  }
  if (err) err.textContent = message;
}

/** Clear the inline error (and aria state) of an input. */
function clearFieldError(input) {
  if (!input) return;
  input.removeAttribute("aria-invalid");
  input.removeAttribute("aria-describedby");
  const err = input.parentElement?.querySelector(".field-error");
  if (err) err.remove();
}

/** Map a server-side validation message to the field it names, falling back
 *  to a top-level error when the message names no known field. */
function applyServerError(form, message, fields = {}) {
  const first = Object.entries(fields).find(([, value]) =>
    message.toLowerCase().includes(value.toLowerCase()));
  if (first) {
    const input = form.querySelector(`#${first[0]}`);
    setFieldError(input, message);
  } else if (form) {
    const top = form.querySelector(".error");
    if (top) top.textContent = message;
  }
}

function fmtDate(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

/** Human countdown to an environment's automatic destruction. */
function fmtRemaining(iso) {
  if (!iso) return null;
  const ms = new Date(iso) - Date.now();
  if (ms <= 0) return "expired";
  const hours = Math.floor(ms / 3600000);
  const minutes = Math.floor((ms % 3600000) / 60000);
  return hours > 0 ? `${hours}h ${minutes}m left` : `${minutes}m left`;
}

function ttlBadge(project) {
  if (!project.expires_at || project.auto_destroy === false) return "";
  const remaining = fmtRemaining(project.expires_at);
  const urgent = project.expiry_warned || remaining === "expired";
  return `<span class="pill ${urgent ? "failed" : "draft"}" title="Automatically destroyed at ${esc(fmtDate(project.expires_at))}">${esc(remaining)}</span>`;
}

let toastTimer;
function toast(message, isError = false) {
  const el = $("#toast");
  el.textContent = message;
  el.classList.toggle("err", isError);
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 4000);
}

function token() { return localStorage.getItem(TOKEN_KEY); }

/** Role claim off the access token. Display only — every /platform route is
 *  gated server-side by require_platform_admin (api/rbac.py), so hiding the
 *  nav is a UX fix, never the access control itself. */
function currentRole() {
  const raw = token();
  if (!raw) return null;
  try { return JSON.parse(atob(raw.split(".")[1])).role || null; } catch { return null; }
}

function isPlatformAdmin() { return currentRole() === "admin"; }

/* -------------------------------------------------------------- modals
 * Task 6.2: real dialogs instead of prompt()/confirm(). Close on Escape,
 * focus the first field, return focus to the trigger. */
/** Where a modal must be mounted.
 *
 * The modal styles are scoped to `#cp-root`, so appending to document.body —
 * as this used to — put the dialog outside the selector's reach and it
 * rendered as unstyled HTML at the bottom of the page, below the fold. The
 * toast had the same problem and was fixed the same way.
 */
function modalHost() {
  return document.getElementById("cp-root") || document.body;
}

function modalShell(title, body) {
  const backdrop = document.createElement("div");
  backdrop.className = "modal-backdrop";
  backdrop.innerHTML = `
    <div class="modal" role="dialog" aria-modal="true" aria-label="${esc(title)}">
      <h3>${esc(title)}</h3>
      ${body}
      <div class="modal-actions">
        <button type="button" class="small" data-action="cancel">Cancel</button>
        <button type="button" class="small primary" data-action="ok">OK</button>
      </div>
    </div>`;
  const close = () => {
    backdrop.remove();
    document.removeEventListener("keydown", onKey);
  };
  const onKey = (event) => {
    if (event.key === "Escape") close();
    if (event.key === "Tab" && backdrop.querySelector(".modal").contains(event.target) === false) {
      event.preventDefault();
      backdrop.querySelector(".modal").focus();
    }
  };
  document.addEventListener("keydown", onKey);
  return { backdrop, close, shell: backdrop.querySelector(".modal") };
}

/** Ask a free-form question; resolves with the value or null on cancel. */
function modalPrompt(title, message, options = {}) {
  const value = options.value ?? "";
  const { backdrop, close, shell } = modalShell(title, `
    <p>${esc(message)}</p>
    <input type="${options.type || "text"}" value="${esc(value)}" placeholder="${esc(options.placeholder || "")}">`);
  return new Promise((resolve) => {
    const input = backdrop.querySelector("input");
    backdrop.querySelector('[data-action="ok"]').onclick = () => {
      const answer = input.value.trim();
      close();
      resolve(answer);
    };
    backdrop.querySelector('[data-action="cancel"]').onclick = () => { close(); resolve(null); };
    modalHost().appendChild(backdrop);
    input.focus();
    input.select();
  });
}

/** Confirm an irreversible action; resolves true/false. */
function modalConfirm(title, message, okLabel = "Confirm") {
  const { backdrop, close, shell } = modalShell(title, `<p>${esc(message)}</p>`);
  shell.querySelector('[data-action="ok"]').textContent = okLabel;
  return new Promise((resolve) => {
    backdrop.querySelector('[data-action="ok"]').onclick = () => { close(); resolve(true); };
    backdrop.querySelector('[data-action="cancel"]').onclick = () => { close(); resolve(false); };
    modalHost().appendChild(backdrop);
    backdrop.querySelector('[data-action="ok"]').focus();
  });
}

function setTokens(access, refresh) {
  if (access) localStorage.setItem(TOKEN_KEY, access);
  if (refresh) localStorage.setItem(REFRESH_KEY, refresh);
}

function clearTokens() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(REFRESH_KEY);
}

/* ------------------------------------------------------------ API client */

/** Turn a FastAPI error body into one readable line. */
function errorText(body, status) {
  const detail = body?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((e) => `${(e.loc || []).filter((p) => p !== "body").join(".")}: ${e.msg}`)
      .join("; ");
  }
  return `Request failed (HTTP ${status})`;
}

async function api(path, { method = "GET", body, auth = true } = {}) {
  const headers = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (auth && token()) headers["Authorization"] = `Bearer ${token()}`;

  const resp = await fetch(API + path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  if (resp.status === 401 && auth) {
    // Try one silent refresh before bouncing the user to the login screen.
    if (await tryRefresh()) return api(path, { method, body, auth });
    clearTokens();
    location.hash = "#/login";
    throw new Error("Session expired — please log in again.");
  }

  if (resp.status === 204) return null;

  const text = await resp.text();
  let data = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = null;
    }
  }
  if (!resp.ok) throw new Error(errorText(data, resp.status));
  return data;
}

async function tryRefresh() {
  const refresh = localStorage.getItem(REFRESH_KEY);
  if (!refresh) return false;
  try {
    const resp = await fetch(`${API}/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refresh }),
    });
    if (!resp.ok) return false;
    const data = await resp.json();
    setTokens(data.access_token, data.refresh_token);
    return true;
  } catch {
    return false;
  }
}

/* --------------------------------------------------------------- chrome */

/** Highlight a control-plane entry in the shared sidebar.
 *
 * The sidebar lists both halves of the console, so this also clears the
 * platform entries — the mirror of what PlatformConsole.switchView does when
 * a platform view takes over. `active === null` means "logged out": the whole
 * sidebar goes away and the login form owns the page.
 */
function setNav(active) {
  const shell = $("#platform-root");
  // The login form renders into #view, which lives inside the shell, so the
  // shell itself stays mounted when logged out — only its chrome hides.
  const signedIn = Boolean(token());
  $("#sidebar").hidden = !signedIn;
  // Not un-hidden here: the topbar is operator data, revealed by
  // platform/app.js once it actually has a revision to show.
  if (!signedIn) $(".topbar", shell).hidden = true;

  // The operator console is admin-only (Phase 0). It used to be listed for
  // everyone and simply 404 on click, which read as a broken platform rather
  // than one the user has no business in — hide it instead.
  const admin = isPlatformAdmin();
  shell.querySelectorAll("[data-operator]").forEach((el) => { el.hidden = !admin; });

  shell.querySelectorAll(".nav-item").forEach((item) => {
    item.classList.toggle("active", active != null && item.dataset.nav === active);
  });
}

/** Show the control-plane pane and stand the platform half down. */
function showControlPlanePane() {
  const shell = $("#platform-root");
  shell.querySelectorAll(".view").forEach((section) => {
    section.classList.toggle("active", section.id === "view-cp");
  });
  window.PlatformConsole.unmount();
}

function loading(message = "Loading…") {
  // Skeleton rows instead of bare text (docs/TODO.md Task 6.3).
  view().innerHTML = `
    <div class="panel">
      <div class="skeleton-line w60"></div>
      <div class="skeleton-line w90"></div>
      <div class="skeleton-line w80"></div>
    </div>`;
}

function showError(err, retry) {
  view().innerHTML = `<div class="panel">
    <p class="error">${esc(err.message)}</p>
    ${retry ? `<button class="small" id="retry-view">Try again</button>` : ""}
  </div>`;
  const btn = $("#retry-view");
  if (btn) btn.onclick = () => { route(); };
}

/* ---------------------------------------------------------------- login */

function renderAuth() {
  setNav(null);
  // Test build: no real password storage on a lab machine, so the form is
  // username-only and the API receives a fixed development password.
  const TEST_PASSWORD = "Test!Passw0rd123";
  // Bare usernames are a convenience for the lab, not a rule about which
  // accounts exist: the platform stores real addresses and accounts on other
  // domains are perfectly valid. Appending this unconditionally turned
  // "ana@scale.example.com" into "ana@scale.example.com@example.com", which
  // the API then rejected with "the part after the @-sign contains invalid
  // characters: '@'" — an error that blamed the address rather than the form.
  const DEFAULT_DOMAIN = "example.com";
  view().innerHTML = `
    <div class="auth-wrap">
      <div class="panel">
        <h1>Central Platform</h1>
        <p class="subtitle">Self-service infrastructure, deployments and security scanning.<br><span class="muted small">Test mode — a bare username is completed to @${DEFAULT_DOMAIN}; a full email is used as typed.</span></p>
        <form id="auth-form">
          <div class="field">
            <label for="email">Username or email</label>
            <input id="email" type="text" autocomplete="username" placeholder="alice or alice@example.com" required>
          </div>
          <div class="row">
            <button class="primary" type="submit" id="submit-btn">Log in</button>
            <button class="link" type="button" id="toggle-mode">Create an account</button>
          </div>
          <div class="row" id="sso-row" hidden>
            <button class="primary" type="button" id="sso-btn">Sign in with SSO</button>
          </div>
          <p class="error" id="auth-error"></p>
        </form>
      </div>
    </div>`;

  let mode = "login";

  // SSO availability (docs/TODO.md Task 3.3). When the platform is
  // SSO-only, the email/password form is pointless — hide it.
  api("/auth/config", { auth: false }).then((cfg) => {
    if (cfg.oidc_enabled) {
      $("#sso-row").hidden = false;
      if (!cfg.local_auth_enabled) {
        $("#sso-row").classList.add("sso-only");
      }
    }
  }).catch(() => {});

  $("#sso-btn").onclick = () => {
    window.open("/api/v1/auth/oidc/login", "controlplane-sso", "popup,width=520,height=620");
  };

  $("#toggle-mode").onclick = () => {
    mode = mode === "login" ? "register" : "login";
    $("#submit-btn").textContent = mode === "register" ? "Create account" : "Log in";
    $("#toggle-mode").textContent = mode === "register" ? "I already have an account" : "Create an account";
    $("#auth-error").textContent = "";
  };

  $("#auth-form").onsubmit = async (e) => {
    e.preventDefault();
    const emailInput = $("#email");
    clearFieldError(emailInput);
    const typed = emailInput.value.trim();
    const email = typed.includes("@") ? typed : `${typed}@${DEFAULT_DOMAIN}`;
    $("#auth-error").textContent = "";
    $("#submit-btn").disabled = true;
    try {
      if (mode === "register") {
        await api("/auth/register", {
          auth: false,
          method: "POST",
          body: { email, password: TEST_PASSWORD, password_confirm: TEST_PASSWORD },
        });
      }
      const tokens = await api("/auth/login", {
        auth: false,
        method: "POST",
        body: { email, password: TEST_PASSWORD },
      });
      setTokens(tokens.access_token, tokens.refresh_token);
      refreshWhoami();
      location.hash = "#/projects";
    } catch (err) {
      applyServerError($("#auth-form"), err.message, { email: "email" });
      if (!emailInput.hasAttribute("aria-invalid")) $("#auth-error").textContent = err.message;
    } finally {
      $("#submit-btn").disabled = false;
    }
  };
}

/* ------------------------------------------------------------- projects */

async function renderProjects() {
  setNav("projects");
  loading();
  try {
    const projects = await api("/projects");
    view().innerHTML = `
      <div class="between">
        <div>
          <h1>Projects</h1>
          <p class="subtitle">Each project is one cluster you own.</p>
        </div>
        <button class="primary" id="new-project">New project</button>
      </div>
      ${projects.length === 0
        ? `<div class="panel"><div class="empty">
            <h2>No environments yet</h2>
            <p>An environment is a Kubernetes cluster (or a shared-cluster namespace) where you test a branch of a service.
               Pick a size and the platform provisions it, destroys it when the time is up, and scans deployments before they go live.</p>
            <button class="primary" id="empty-create">Create your first environment</button>
          </div></div>`
        : `<div class="panel table-wrap"><table>
             <thead><tr><th>Name</th><th>Status</th><th>Lifetime</th><th>Description</th><th>Created</th></tr></thead>
             <tbody>${projects.map((p) => `
               <tr>
                 <td><a href="#/projects/${p.id}">${esc(p.name)}</a></td>
                 <td>${pill(p.status)}</td>
                 <td>${ttlBadge(p) || '<span class="muted">no expiry</span>'}</td>
                 <td class="muted">${esc(p.description || "—")}</td>
                 <td class="muted">${fmtDate(p.created_at)}</td>
               </tr>`).join("")}
           </tbody></table></div>`}`;
    $("#new-project").onclick = () => { location.hash = "#/projects/new"; };
    const emptyCreate = $("#empty-create");
    if (emptyCreate) emptyCreate.onclick = () => { location.hash = "#/projects/new"; };
  } catch (err) {
    showError(err, route);
  }
}

/* ------------------------------------------- infrastructure designer form */

const ROLES = ["k8s_master", "k8s_worker", "docker_host"];
const CAPS = { nodes: 10, vcpu: 8, memory_mb: 16384, disk_gb: 200, totalVcpu: 24, totalMemory: 49152 };

function nodeRowHtml(node, index) {
  return `
    <div class="node-row" data-index="${index}">
      <div><label>Name</label><input class="n-name" value="${esc(node.name)}" pattern="[a-z0-9-]{1,20}" required></div>
      <div><label>vCPU</label><input class="n-vcpu" type="number" min="1" max="8" value="${node.vcpu}" required></div>
      <div><label>Memory (MB)</label><input class="n-mem" type="number" min="1024" max="16384" step="512" value="${node.memory_mb}" required></div>
      <div><label>Disk (GB)</label><input class="n-disk" type="number" min="20" max="200" value="${node.disk_gb}" required></div>
      <div><label>Role</label><select class="n-role">
        ${ROLES.map((r) => `<option value="${r}"${r === node.role ? " selected" : ""}>${r}</option>`).join("")}
      </select></div>
      <div><button type="button" class="small danger remove-node">Remove</button></div>
    </div>`;
}

// The isolation selector defaults to the shared cluster: it is ready in
// seconds and costs a namespace, where dedicated VMs take about ten minutes
// and roughly 12 GB of RAM — of which a developer machine fits one or two.
// It used to default to VMs, so anyone clicking through the form got the
// slowest possible environment and could exhaust the host by doing it twice.
function renderNewProject() {
  setNav("projects");
  const nodes = [
    { name: "master", vcpu: 4, memory_mb: 8192, disk_gb: 50, role: "k8s_master" },
    { name: "worker-1", vcpu: 2, memory_mb: 4096, disk_gb: 30, role: "k8s_worker" },
  ];

  view().innerHTML = `
    <h1>New environment</h1>
    <p class="subtitle">Pick a size and the platform generates the infrastructure for you. Environments are destroyed automatically when their time is up.</p>
    <form id="project-form">
      <div class="panel">
        <div class="grid">
          <div class="field"><label for="p-name">Project name</label>
            <input id="p-name" pattern="[a-z0-9-]{3,30}" placeholder="my-cluster" required></div>
          <div class="field"><label for="p-desc">Description (optional)</label>
            <input id="p-desc" placeholder="What is this for?"></div>
        </div>
        <div class="grid">
          <div class="field"><label for="p-mode">Isolation</label>
            <select id="p-mode">
              <option value="namespace" selected>Shared cluster — ready in seconds</option>
              <option value="vm">Dedicated VMs — stronger isolation, ~10 minutes</option>
            </select></div>
          <div class="field"><label for="p-ttl">Destroy after</label>
            <select id="p-ttl">
              <option value="4">4 hours</option>
              <option value="24" selected>24 hours</option>
              <option value="72">3 days</option>
              <option value="168">7 days (maximum)</option>
            </select></div>
        </div>
        <p class="muted" id="mode-note" style="margin-bottom:0"></p>
      </div>

      <div class="panel">
        <div class="between"><h2 style="margin:0">Size</h2>
          <button type="button" class="link" id="toggle-advanced">Configure nodes individually</button></div>
        <div class="grid" id="preset-picker">
          <label class="preset"><input type="radio" name="preset" value="small" checked> <b>Small</b><br><span class="muted">1 node · 2 vCPU · 4 GB</span></label>
          <label class="preset"><input type="radio" name="preset" value="medium"> <b>Medium</b><br><span class="muted">2 nodes · 4 vCPU · 8 GB each</span></label>
          <label class="preset"><input type="radio" name="preset" value="large"> <b>Large</b><br><span class="muted">3 nodes · 4 vCPU · 8 GB each</span></label>
        </div>
      </div>

      <div class="panel" id="advanced" hidden>
        <div class="grid">
          <div class="field"><label for="p-cidr">Network CIDR</label>
            <input id="p-cidr" value="192.168.56.0/24"></div>
          <div class="field"><label for="p-domain">Domain</label>
            <input id="p-domain" value="devops.local"></div>
        </div>
        <div class="between"><h2 style="margin:0">Nodes</h2>
          <button type="button" class="small" id="add-node">Add node</button></div>
        <div id="nodes"></div>
        <div class="totals" id="totals"></div>
      </div>

      <div class="panel">
        <h2 style="margin-top:0">Configuration</h2>
        <div class="grid">
          <div class="field"><label for="c-k8s">Kubernetes version</label>
            <select id="c-k8s"><option>1.27</option><option selected>1.28</option><option>1.29</option></select></div>
          <div class="field"><label for="c-runtime">Container runtime</label>
            <select id="c-runtime"><option selected>containerd</option><option>crio</option></select></div>
          <div class="field"><label for="c-cni">CNI plugin</label>
            <select id="c-cni"><option selected>calico</option><option>flannel</option></select></div>
          <div class="field"><label for="c-docker">Docker version</label>
            <select id="c-docker"><option selected>24.0</option><option>25.0</option><option>26.1</option></select></div>
        </div>
      </div>

      <div class="row">
        <button class="primary" type="submit" id="create-btn">Create environment</button>
        <button type="button" class="link" id="cancel">Cancel</button>
      </div>
      <p class="error" id="form-error"></p>
    </form>`;

  let advanced = false;

  const MODE_NOTES = {
    namespace:
      "Runs in an isolated, quota-bounded namespace on a shared cluster. " +
      "Fast, but isolation is weaker than a dedicated VM — avoid for sensitive workloads.",
    vm: "Provisions dedicated virtual machines. Strongest isolation; takes several minutes.",
  };

  function updateModeNote() {
    $("#mode-note").textContent = MODE_NOTES[$("#p-mode").value];
  }
  $("#p-mode").onchange = updateModeNote;
  updateModeNote();

  $("#toggle-advanced").onclick = () => {
    advanced = !advanced;
    $("#advanced").hidden = !advanced;
    $("#preset-picker").hidden = advanced;
    $("#toggle-advanced").textContent = advanced
      ? "Use a standard size instead"
      : "Configure nodes individually";
    if (advanced) paint();
  };

  function paint() {
    $("#nodes").innerHTML = nodes.map(nodeRowHtml).join("");
    $("#nodes").querySelectorAll(".remove-node").forEach((btn) => {
      btn.onclick = () => {
        if (nodes.length === 1) return toast("A project needs at least one node.", true);
        nodes.splice(Number(btn.closest(".node-row").dataset.index), 1);
        paint();
      };
    });
    $("#nodes").querySelectorAll("input, select").forEach((el) => {
      el.oninput = () => { syncFromDom(); updateTotals(); };
    });
    updateTotals();
  }

  function syncFromDom() {
    $("#nodes").querySelectorAll(".node-row").forEach((row, i) => {
      nodes[i] = {
        name: $(".n-name", row).value.trim(),
        vcpu: Number($(".n-vcpu", row).value),
        memory_mb: Number($(".n-mem", row).value),
        disk_gb: Number($(".n-disk", row).value),
        role: $(".n-role", row).value,
      };
    });
  }

  function updateTotals() {
    const vcpu = nodes.reduce((s, n) => s + (n.vcpu || 0), 0);
    const mem = nodes.reduce((s, n) => s + (n.memory_mb || 0), 0);
    const disk = nodes.reduce((s, n) => s + (n.disk_gb || 0), 0);
    const over = vcpu > CAPS.totalVcpu || mem > CAPS.totalMemory || nodes.length > CAPS.nodes;
    const el = $("#totals");
    el.classList.toggle("over", over);
    el.textContent =
      `${nodes.length}/${CAPS.nodes} nodes · ${vcpu}/${CAPS.totalVcpu} vCPU · ` +
      `${(mem / 1024).toFixed(1)}/${(CAPS.totalMemory / 1024).toFixed(0)} GB memory · ${disk} GB disk` +
      (over ? "  — exceeds the allowed limits" : "");
  }

  $("#add-node").onclick = () => {
    syncFromDom();
    if (nodes.length >= CAPS.nodes) return toast(`Limit is ${CAPS.nodes} nodes per project.`, true);
    nodes.push({
      name: `worker-${nodes.length}`, vcpu: 2, memory_mb: 4096, disk_gb: 30, role: "k8s_worker",
    });
    paint();
  };

  $("#cancel").onclick = () => { location.hash = "#/projects"; };

  $("#project-form").onsubmit = async (e) => {
    e.preventDefault();
    $("#form-error").textContent = "";
    const nameInput = $("#p-name");
    clearFieldError(nameInput);
    $("#create-btn").disabled = true;
    const name = nameInput.value.trim();

    // The API takes exactly one of preset / infra_spec.
    const body = {
      name,
      description: $("#p-desc").value.trim() || null,
      mode: $("#p-mode").value,
      ttl_hours: Number($("#p-ttl").value),
    };

    if (advanced) {
      syncFromDom();
      body.infra_spec = {
        version: 1,
        project: name,
        network: { cidr: $("#p-cidr").value.trim(), domain: $("#p-domain").value.trim() },
        nodes,
        config: {
          kubernetes_version: $("#c-k8s").value,
          container_runtime: $("#c-runtime").value,
          cni_plugin: $("#c-cni").value,
          docker_version: $("#c-docker").value,
        },
      };
    } else {
      body.preset = view().querySelector('input[name="preset"]:checked').value;
    }

    try {
      const project = await api("/projects", { method: "POST", body });
      toast("Environment created.");
      location.hash = `#/projects/${project.id}`;
    } catch (err) {
      applyServerError($("#project-form"), err.message, { "p-name": "name" });
      if (!nameInput.hasAttribute("aria-invalid")) $("#form-error").textContent = err.message;
    } finally {
      $("#create-btn").disabled = false;
    }
  };
}

/* ------------------------------------------------- namespace quota panel */

/**
 * What namespace mode actually provisions: a namespace bounded by a
 * ResourceQuota, a LimitRange and a default-deny NetworkPolicy. The numbers
 * mirror renderers/namespace.py — the spec's per-node sizing is reinterpreted
 * as the total budget for the namespace, with requests at half the ceiling.
 *
 * The namespace's own name is deliberately not derived here: it comes from
 * k8s_namespace() server-side and is shown by the monitoring panel below, so
 * there is exactly one source of truth for it.
 */
function namespaceQuotaPanel(project) {
  const nodes = project.nodes || [];
  const vcpu = nodes.reduce((a, n) => a + n.vcpu, 0);
  const memMb = nodes.reduce((a, n) => a + n.memory_mb, 0);
  const diskGb = nodes.reduce((a, n) => a + n.disk_gb, 0);
  const row = (k, v) => `<tr><td>${esc(k)}</td><td class="mono">${esc(v)}</td></tr>`;

  return `
      <h2>Namespace quota</h2>
      <div class="panel table-wrap">
        <p class="subtitle" style="margin:.2rem 0 .8rem">
          A quota-bounded slice of the shared cluster — no virtual machines, so
          no node addresses. Pods are capped by this quota and isolated by a
          default-deny NetworkPolicy.
        </p>
        <table>
          <thead><tr><th>Resource</th><th>Limit</th></tr></thead>
          <tbody>
            ${row("CPU", `${vcpu} cores (requests ${Math.floor(vcpu / 2) || 1})`)}
            ${row("Memory", `${(memMb / 1024).toFixed(1)} GB (requests ${(memMb / 2 / 1024).toFixed(1)} GB)`)}
            ${row("Storage", `${diskGb} GB`)}
            ${row("Pods", "40")}
            ${row("Services", "20 (no NodePort or LoadBalancer)")}
          </tbody>
        </table>
      </div>`;
}

/* -------------------------------------------------------- project detail */

async function renderProject(id) {
  setNav("projects");
  loading();
  try {
    const [project, deployments, scans] = await Promise.all([
      api(`/projects/${id}`),
      api(`/projects/${id}/deployments`).catch(() => []),
      api(`/projects/${id}/scans`).catch(() => []),
    ]);

    // Namespace mode carves a quota-bounded slice out of a shared cluster:
    // there are no VMs, no IPs and no Terraform state. Showing a node table
    // and a "terraform plan" button for it describes infrastructure that
    // does not exist — the plan call even fails, because there is no
    // workspace to plan (see renderers/namespace.py vs render_terraform).
    const isNamespace = (project.infra_spec || {}).mode === "namespace";

    view().innerHTML = `
      <div class="between">
        <div>
          <h1>${esc(project.name)} ${pill(project.status)} ${ttlBadge(project)}</h1>
          <p class="subtitle">${esc(project.description || "No description")}</p>
        </div>
        <div class="row">
          ${project.expires_at ? `<button id="extend-btn">Extend</button>` : ""}
          ${isNamespace ? "" : `<button id="plan-btn">Preview plan</button>`}
          <button class="primary" id="provision-btn">Provision</button>
          <button class="danger" id="destroy-btn">Destroy</button>
        </div>
      </div>
      ${project.expiry_warned
        ? `<div class="panel"><p class="error">This environment expires soon and will be destroyed automatically. Extend it if you still need it.</p></div>`
        : ""}

      ${isNamespace ? namespaceQuotaPanel(project) : `
      <h2>Infrastructure</h2>
      <div class="panel table-wrap">
        <table>
          <thead><tr><th>Node</th><th>Role</th><th>vCPU</th><th>Memory</th><th>Disk</th><th>IP address</th><th>Status</th></tr></thead>
          <tbody>${project.nodes.map((n) => `
            <tr>
              <td>${esc(n.name)}</td>
              <td class="mono">${esc(n.role)}</td>
              <td>${n.vcpu}</td>
              <td>${(n.memory_mb / 1024).toFixed(1)} GB</td>
              <td>${n.disk_gb} GB</td>
              <td class="mono">${esc(n.ip_address || "—")}</td>
              <td>${pill(n.status)}</td>
            </tr>`).join("")}
          </tbody>
        </table>
      </div>`}

      <div class="between"><h2>Deployments</h2>
        <button class="small primary" id="deploy-btn">Deploy an app</button></div>
      <div class="panel table-wrap" id="deployments">
        ${deployments.length === 0
          ? `<div class="empty">Nothing deployed yet.</div>`
          : `<table>
              <thead><tr><th>Service</th><th>Branch</th><th>Status</th><th>Live URL</th><th></th></tr></thead>
              <tbody>${deployments.map((d) => `
                <tr>
                  <td>${esc(d.service_name)}</td>
                  <td class="mono">${esc(d.branch)}</td>
                  <td>${pill(d.status)}</td>
                  <td>${d.live_url ? `<a href="${esc(d.live_url)}" target="_blank" rel="noopener">${esc(d.live_url)}</a>` : "—"}</td>
                  <td><button class="small redeploy" data-id="${d.id}">Redeploy</button></td>
                </tr>`).join("")}
              </tbody></table>`}
      </div>

      <div class="between"><h2>Security scans</h2>
        <div class="row">
          <select id="scan-tool" style="width:auto">
            <option value="all">All tools</option>
            <option value="trivy">Trivy only</option>
            <option value="gitleaks">Gitleaks only</option>
            <option value="pip_audit">pip-audit only</option>
          </select>
          <button class="small" id="scan-btn">Run scan</button>
          <a href="#/security/${project.id}">View report</a>
        </div></div>
      <div class="panel table-wrap">
        ${scans.length === 0
          ? `<div class="empty">No scans run yet.</div>`
          : `<table>
              <thead><tr><th>Tool</th><th>Target</th><th>Status</th><th>Findings</th><th>When</th></tr></thead>
              <tbody>${scans.slice(0, 10).map((s) => `
                <tr>
                  <td class="mono">${esc(s.tool)}</td>
                  <td class="mono">${esc(s.target)}</td>
                  <td>${pill(s.status)}</td>
                  <td>${s.summary ? severityInline(s.summary) : "—"}</td>
                  <td class="muted">${fmtDate(s.created_at)}</td>
                </tr>`).join("")}
              </tbody></table>`}
      </div>

      <div class="between"><h2>Running workloads</h2>
        <button class="small" id="workloads-btn">Refresh</button></div>
      <div class="panel" id="project-workloads">
        <div class="empty">Loading what is running in this environment…</div>
      </div>

      <div class="between"><h2>CI — your repositories</h2>
        <button class="small" id="ci-btn">Refresh</button></div>
      <div class="panel" id="project-ci">
        <div class="empty">Loading GitHub Actions runs for this project's repositories…</div>
      </div>

      <div class="between"><h2>Configuration &amp; secrets</h2>
        <button class="small" id="secrets-btn">Refresh</button></div>
      <div class="panel" id="project-secrets">
        <div class="empty">Loading configuration…</div>
      </div>

      <div class="between"><h2>Monitoring</h2>
        <div class="row">
          <select id="metrics-window">
            <option value="60">last hour</option>
            <option value="360">last 6 hours</option>
            <option value="1440">last 24 hours</option>
          </select>
          <button class="small" id="metrics-btn">Refresh</button>
        </div></div>
      <div class="panel" id="project-metrics">
        <div class="empty">Loading metrics for this environment…</div>
      </div>

      <div class="between"><h2>Logs</h2>
        <div class="row">
          <input id="logs-search" placeholder="search term (e.g. error)" style="width:16rem">
          <button class="small" id="logs-btn">Fetch logs</button>
        </div></div>
      <div class="panel" id="project-logs">
        <div class="empty">Fetch recent log lines from Loki for this project's namespace.</div>
      </div>`;

    /* ------------------------------------------- running workloads panel */

    /* What is actually running in this project's namespace. The operator
       console answers this for the platform's own services via ArgoCD;
       tenant apps are applied with plain kubectl, so their answer comes
       from the namespace itself. */
    const loadWorkloads = async () => {
      const box = $("#project-workloads");
      if (!box) return;
      box.innerHTML = `<div class="empty">Loading…</div>`;
      try {
        const data = await api(`/projects/${id}/workloads`);
        if (!data.reachable) {
          box.innerHTML = `<div class="empty">Cluster unreachable: ${esc(data.error || "unknown error")}</div>`;
          return;
        }
        if (!data.deployments.length && !data.pods.length) {
          box.innerHTML = `<div class="empty">Nothing running yet in <span class="mono">${esc(data.namespace)}</span> — deploy a service to see it here.</div>`;
          return;
        }
        const deployRows = data.deployments.map((d) => `
          <tr>
            <td class="mono">${esc(d.name)}</td>
            <td>${pill(d.healthy ? "healthy" : "degraded")}</td>
            <td>${d.ready}/${d.desired} ready</td>
            <td class="mono small">${esc((d.images[0] || "").split("/").pop())}</td>
          </tr>`).join("");
        const podRows = data.pods.map((p) => `
          <tr>
            <td class="mono">${esc(p.name)}</td>
            <td>${pill(p.ready ? "running" : (p.waiting_reason || p.phase || "pending"))}</td>
            <td>${p.restarts} restarts</td>
          </tr>`).join("");
        box.innerHTML = `
          <table class="table"><thead><tr>
            <th>Deployment</th><th>Health</th><th>Replicas</th><th>Image</th>
          </tr></thead><tbody>${deployRows}</tbody></table>
          <h3 style="margin-top:1rem">Pods</h3>
          <table class="table"><thead><tr>
            <th>Pod</th><th>State</th><th>Restarts</th>
          </tr></thead><tbody>${podRows}</tbody></table>
          ${data.services.length ? `<h3 style="margin-top:1rem">Services</h3>
          <table class="table"><thead><tr><th>Service</th><th>Type</th><th>Cluster IP</th><th>Ports</th></tr></thead>
          <tbody>${data.services.map((s) => `<tr>
            <td class="mono">${esc(s.name)}</td><td>${esc(s.type)}</td>
            <td class="mono">${esc(s.cluster_ip)}</td>
            <td class="mono">${esc(s.ports.map((p) => p.port).join(", "))}</td>
          </tr>`).join("")}</tbody></table>` : ""}
          <div class="muted small" style="margin-top:.6rem">namespace <span class="mono">${esc(data.namespace)}</span></div>`;
      } catch (err) {
        box.innerHTML = `<div class="empty">${esc(err.message)}</div>`;
      }
    };
    $("#workloads-btn").onclick = loadWorkloads;
    loadWorkloads();

    /* ----------------------------------------------------------- CI panel */

    /* GitHub Actions for the repositories THIS project deploys — not the
       platform's own pipeline, which is what the operator console shows. */
    const loadCi = async () => {
      const box = $("#project-ci");
      if (!box) return;
      box.innerHTML = `<div class="empty">Loading…</div>`;
      try {
        const data = await api(`/projects/${id}/ci`);
        if (!data.repos.length) {
          box.innerHTML = `<div class="empty">No repositories yet — add a deployment to track its CI.</div>`;
          return;
        }
        box.innerHTML = data.repos.map((r) => {
          if (!r.reachable) {
            return `<div style="margin-bottom:1rem">
              <div class="mono">${esc(r.repo)}</div>
              <div class="empty">CI unavailable: ${esc(r.error || "unknown error")}</div></div>`;
          }
          if (!r.runs.length) {
            return `<div style="margin-bottom:1rem">
              <div class="mono">${esc(r.repo)}</div>
              <div class="empty">No workflow runs — this repository has no GitHub Actions workflows.</div></div>`;
          }
          const rows = r.runs.map((run) => `
            <tr>
              <td>${pill(run.conclusion || run.status || "unknown")}</td>
              <td>${esc(run.displayTitle || run.name || "")}</td>
              <td class="mono small">${esc(run.headBranch || "")}</td>
              <td class="muted small">${fmtDate(run.createdAt)}</td>
            </tr>`).join("");
          return `<div style="margin-bottom:1rem">
            <div class="mono">${esc(r.repo)} <span class="muted small">· ${esc(r.services.join(", "))}</span></div>
            <table class="table"><thead><tr>
              <th>Result</th><th>Run</th><th>Branch</th><th>When</th>
            </tr></thead><tbody>${rows}</tbody></table></div>`;
        }).join("");
      } catch (err) {
        box.innerHTML = `<div class="empty">${esc(err.message)}</div>`;
      }
    };
    $("#ci-btn").onclick = loadCi;
    loadCi();

    /* ------------------------------------------------------ secrets panel */

    /* Names only — values live in the secret store and no endpoint returns
       them, so this can show what a service carries without exposing it. */
    const loadSecrets = async () => {
      const box = $("#project-secrets");
      if (!box) return;
      box.innerHTML = `<div class="empty">Loading…</div>`;
      try {
        const data = await api(`/projects/${id}/secrets`);
        if (!data.deployments.length) {
          box.innerHTML = `<div class="empty">No deployments yet.</div>`;
          return;
        }
        box.innerHTML = `<table class="table"><thead><tr>
            <th>Service</th><th>Secrets</th><th>Environment</th>
          </tr></thead><tbody>${data.deployments.map((d) => `
            <tr>
              <td class="mono">${esc(d.service_name)}</td>
              <td>${d.secret_keys.length
                    ? d.secret_keys.map((k) => `<span class="pill">${esc(k)}</span>`).join(" ")
                    : `<span class="muted small">none</span>`}</td>
              <td>${d.env_keys.length
                    ? d.env_keys.map((k) => `<span class="pill">${esc(k)}</span>`).join(" ")
                    : `<span class="muted small">none</span>`}</td>
            </tr>`).join("")}</tbody></table>
          <div class="muted small" style="margin-top:.6rem">Names only — secret values are never returned by the API.</div>`;
      } catch (err) {
        box.innerHTML = `<div class="empty">${esc(err.message)}</div>`;
      }
    };
    $("#secrets-btn").onclick = loadSecrets;
    loadSecrets();

    /* ---------------------------------------------------- monitoring panel */

    const loadMetrics = async () => {
      const box = $("#project-metrics");
      if (!box) return;
      const minutes = $("#metrics-window").value;
      box.innerHTML = `<div class="empty">Loading metrics…</div>`;
      try {
        const data = await api(`/projects/${id}/metrics?since_minutes=${minutes}`);
        if (!data.backend_available) {
          box.innerHTML = `<div class="empty">Metrics backend unavailable.</div>`;
          return;
        }
        const anyData = data.panels.some((p) => p.series.length);
        if (!anyData) {
          box.innerHTML = `<div class="empty">No metrics yet for <span class="mono">${esc(data.namespace)}</span> — deploy something to see usage.</div>`;
          return;
        }
        box.innerHTML = `<div class="metric-grid">${data.panels.map(metricCard).join("")}</div>
          <div class="muted small" style="margin-top:.6rem">namespace <span class="mono">${esc(data.namespace)}</span> · ${data.window_minutes}m window</div>`;
      } catch (err) {
        box.innerHTML = `<div class="empty">${esc(err.message)}</div>`;
      }
    };
    $("#metrics-btn").onclick = loadMetrics;
    $("#metrics-window").onchange = loadMetrics;
    loadMetrics();

    $("#provision-btn").onclick = async () => {
      try {
        const { job_id } = await api(`/projects/${id}/provision`, { method: "POST" });
        toast("Provisioning started.");
        location.hash = `#/jobs/${job_id}`;
      } catch (err) { toast(err.message, true); }
    };

    // Absent in namespace mode, where there is no Terraform workspace.
    if ($("#plan-btn")) $("#plan-btn").onclick = async () => {
      toast("Running terraform plan…");
      try {
        const result = await api(`/projects/${id}/plan`);
        view().insertAdjacentHTML("beforeend",
          `<h2>Plan output</h2><div class="panel"><div class="log">${esc(result.output)}</div></div>`);
      } catch (err) { toast(err.message, true); }
    };

    $("#destroy-btn").onclick = async () => {
      // Destroying is irreversible, so require the project name to be typed.
      const typed = await modalPrompt(
        "Destroy environment",
        `This permanently destroys all infrastructure for "${project.name}". This cannot be undone. Type the project name to confirm:`,
        { placeholder: project.name },
      );
      if (typed === null || typed === "") return;
      if (typed !== project.name) return toast("Name did not match — nothing was destroyed.", true);
      try {
        const { job_id } = await api(`/projects/${id}/destroy`, {
          method: "POST", body: { confirm_name: typed },
        });
        toast("Destroy started.");
        location.hash = `#/jobs/${job_id}`;
      } catch (err) { toast(err.message, true); }
    };

    // The three tools do not take the same kind of target: Trivy scans a
    // built image, while Gitleaks and pip-audit scan a source repository.
    // Asking once for "image reference or https repository URL" and sending
    // that one string to all three guaranteed that two of them failed
    // whichever the user typed, so ask for what each tool actually needs.
    const SCAN_TARGETS = {
      trivy: { label: "Image reference to scan:", placeholder: "users-service:1.0.0" },
      repo: { label: "Repository URL to scan (https only):", placeholder: "https://github.com/org/service.git" },
    };
    const TOOL_KIND = { trivy: "trivy", gitleaks: "repo", pip_audit: "repo" };

    $("#scan-btn").onclick = async () => {
      const choice = $("#scan-tool").value;
      // tool -> target, so each request carries the target that tool accepts.
      const requests = [];

      if (choice === "all") {
        const image = await modalPrompt("New scan", SCAN_TARGETS.trivy.label, {
          placeholder: SCAN_TARGETS.trivy.placeholder,
        });
        if (!image) return;
        const repo = await modalPrompt("New scan", SCAN_TARGETS.repo.label, {
          placeholder: SCAN_TARGETS.repo.placeholder,
        });
        if (!repo) return;
        requests.push({ tool: "trivy", target: image });
        requests.push({ tool: "gitleaks", target: repo });
        requests.push({ tool: "pip_audit", target: repo });
      } else {
        const kind = SCAN_TARGETS[TOOL_KIND[choice]];
        const target = await modalPrompt("New scan", kind.label, { placeholder: kind.placeholder });
        if (!target) return;
        requests.push({ tool: choice, target });
      }

      try {
        for (const body of requests) {
          await api(`/projects/${id}/scans`, { method: "POST", body });
        }
        toast(requests.length > 1 ? `${requests.length} scans queued.` : "Scan queued.");
        renderProject(id);
      } catch (err) { toast(err.message, true); }
    };

    $("#deploy-btn").onclick = () => renderDeployForm(project);

    const loadLogs = async (search) => {
      const el = $("#project-logs");
      el.innerHTML = '<div class="empty">Loading logs…</div>';
      try {
        const body = await api(
          `/logs?project=${encodeURIComponent(project.name)}&limit=200` +
          (search ? `&search=${encodeURIComponent(search)}` : ""),
        );
        el.innerHTML = body.lines.length === 0
          ? '<div class="empty">No log lines yet.</div>'
          : `<div class="log">${body.lines.map((l) => {
              const d = new Date(Number(l.timestamp) / 1e6);
              return `<span class="muted mono" style="user-select:none">${esc(d.toLocaleTimeString())}</span> ${esc(l.line)}`;
            }).join("\n")}</div>`;
      } catch (err) {
        el.innerHTML = `<p class="error">${esc(err.message)}</p>`;
      }
    };
    $("#logs-btn").onclick = () => loadLogs($("#logs-search").value.trim());
    $("#logs-search").onkeydown = (ev) => {
      if (ev.key === "Enter") loadLogs(ev.target.value.trim());
    };

    const extendBtn = $("#extend-btn");
    if (extendBtn) {
      extendBtn.onclick = async () => {
        const hours = await modalPrompt(
          "Extend environment",
          "Extend this environment by how many hours?",
          { value: "24", type: "number", placeholder: "24" },
        );
        if (hours === null || hours === "") return;
        try {
          await api(`/projects/${id}/extend`, {
            method: "POST", body: { hours: Number(hours) },
          });
          toast("Environment extended.");
          renderProject(id);
        } catch (err) { toast(err.message, true); }
      };
    }

    view().querySelectorAll(".redeploy").forEach((btn) => {
      btn.onclick = async () => {
        try {
          await api(`/deployments/${btn.dataset.id}/redeploy`, { method: "POST" });
          toast("Redeploy queued.");
          renderProject(id);
        } catch (err) { toast(err.message, true); }
      };
    });

    autoRefresh(renderProject, id, [
      project.status,
      ...deployments.map((d) => d.status),
      ...scans.map((s) => s.status),
    ]);
  } catch (err) {
    showError(err, route);
  }
}

/** Parse a NAME=value block into an object.
 *
 * Splits on the first "=" only, so a value may contain them — a connection
 * string or a base64 blob otherwise loses everything after its first "=".
 */
function parseEnvLines(text) {
  const out = {};
  (text || "").split("\n").forEach((line) => {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) return;
    const at = trimmed.indexOf("=");
    if (at <= 0) return;
    out[trimmed.slice(0, at).trim()] = trimmed.slice(at + 1).trim();
  });
  return out;
}

function renderDeployForm(project) {
  const html = `
    <h2>Deploy an application</h2>
    <div class="panel">
      <form id="deploy-form">
        <div class="grid">
          <div class="field"><label for="d-name">Service name</label>
            <input id="d-name" pattern="[a-z0-9-]{1,60}" placeholder="users-service" required></div>
          <div class="field"><label for="d-port">Port</label>
            <input id="d-port" type="number" min="1" max="65535" value="8000" required></div>
        </div>
        <div class="grid">
          <div class="field"><label for="d-repo">Repository URL (https only)</label>
            <input id="d-repo" type="url" placeholder="https://github.com/org/repo" required></div>
          <div class="field"><label for="d-branch">Branch</label>
            <input id="d-branch" value="main" required></div>
          <div class="field"><label for="d-replicas">Replicas</label>
            <input id="d-replicas" type="number" min="1" max="10" value="2" required></div>
        </div>
        <div class="grid">
          <div class="field"><label for="d-health">Health check path</label>
            <input id="d-health" value="/livez" pattern="/[A-Za-z0-9\-._~/]*" required>
            <p class="muted small" style="margin:.3rem 0 0">The readiness and liveness probes call this path.</p></div>
        </div>
        <div class="field"><label for="d-env">Environment variables</label>
          <textarea id="d-env" rows="3" placeholder="LOG_LEVEL=debug&#10;FEATURE_X=1"></textarea>
          <p class="muted small" style="margin:.3rem 0 0">One NAME=value per line. Visible to anyone who can see this deployment.</p></div>
        <div class="field"><label for="d-secrets">Secrets</label>
          <textarea id="d-secrets" rows="3" placeholder="DATABASE_URL=postgres://..."></textarea>
          <p class="muted small" style="margin:.3rem 0 0">One NAME=value per line. Stored in the secret store and never shown again — not even here.</p></div>
        <div class="row"><button class="primary" type="submit">Build, scan and deploy</button></div>
        <p class="muted" style="margin-bottom:0">The image is scanned before it reaches the cluster; a CRITICAL or HIGH finding blocks the deployment.</p>
        <p class="error" id="deploy-error"></p>
      </form>
    </div>`;
  view().insertAdjacentHTML("beforeend", html);
  $("#deploy-form").scrollIntoView({ behavior: "smooth" });

  $("#deploy-form").onsubmit = async (e) => {
    e.preventDefault();
    $("#deploy-error").textContent = "";
    const repoInput = $("#d-repo");
    clearFieldError(repoInput);
    try {
      await api(`/projects/${project.id}/deployments`, {
        method: "POST",
        body: {
          service_name: $("#d-name").value.trim(),
          repo_url: repoInput.value.trim(),
          branch: $("#d-branch").value.trim(),
          port: Number($("#d-port").value),
          replicas: Number($("#d-replicas").value),
          health_path: $("#d-health").value.trim() || "/livez",
          env: parseEnvLines($("#d-env").value),
          secrets: parseEnvLines($("#d-secrets").value),
        },
      });
      toast("Deployment queued.");
      renderProject(project.id);
    } catch (err) {
      applyServerError($("#deploy-form"), err.message, { "d-repo": "url" });
      if (!repoInput.hasAttribute("aria-invalid")) $("#deploy-error").textContent = err.message;
    }
  };
}

/* ------------------------------------------------------------- security */

const SEVERITIES = ["critical", "high", "medium", "low", "unknown"];

function severityInline(summary) {
  return SEVERITIES
    .filter((s) => summary[s])
    .map((s) => `<span class="sev-tag ${s}">${summary[s]} ${s}</span>`)
    .join(" ") || `<span class="muted">clean</span>`;
}

/**
 * Inline SVG sparkline of critical+high findings over time.
 * Hand-drawn rather than pulling in a charting library for one small chart.
 */
function sparkline(trend) {
  if (!trend || trend.length < 2) return "";
  const values = trend.map((point) => (point.critical || 0) + (point.high || 0));
  const max = Math.max(...values, 1);
  const width = 320;
  const height = 48;
  const step = width / (values.length - 1);
  const points = values
    .map((value, index) => `${(index * step).toFixed(1)},${(height - (value / max) * (height - 6) - 3).toFixed(1)}`)
    .join(" ");

  return `
    <div class="panel">
      <div class="between" style="margin-bottom:.5rem">
        <span class="muted">Critical + high findings, last 30 days</span>
        <span class="muted">peak ${max}</span>
      </div>
      <svg viewBox="0 0 ${width} ${height}" width="100%" height="${height}"
           preserveAspectRatio="none" role="img"
           aria-label="Trend of critical and high findings over the last 30 days">
        <polyline points="${points}" fill="none" stroke="currentColor"
                  stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
      </svg>
      <div class="between muted" style="font-size:.8rem">
        <span>${esc(trend[0].date)}</span><span>${esc(trend[trend.length - 1].date)}</span>
      </div>
    </div>`;
}

async function renderSecurity(projectId) {
  setNav("security");
  loading();
  try {
    if (!projectId) {
      const projects = await api("/projects");
      view().innerHTML = `
        <h1>Security</h1>
        <p class="subtitle">Vulnerability and secret findings across your projects.</p>
        ${projects.length === 0
          ? `<div class="panel"><div class="empty">No projects yet.</div></div>`
          : `<div class="panel"><table><tbody>${projects.map((p) => `
              <tr><td><a href="#/security/${p.id}">${esc(p.name)}</a></td><td>${pill(p.status)}</td></tr>`
            ).join("")}</tbody></table></div>`}`;
      return;
    }

    const [project, summary, scans] = await Promise.all([
      api(`/projects/${projectId}`),
      api(`/projects/${projectId}/security/summary`),
      api(`/projects/${projectId}/scans`),
    ]);

    view().innerHTML = `
      <h1>Security — ${esc(project.name)}</h1>
      <p class="subtitle">Current findings from the most recent scan of each tool.</p>

      <div class="sev-grid">
        ${SEVERITIES.map((s) => `
          <div class="sev ${s}"><div class="n">${summary.current[s] ?? 0}</div><div class="k">${s}</div></div>`
        ).join("")}
      </div>

      ${sparkline(summary.trend)}

      <h2>Top issues</h2>
      <div class="panel table-wrap">
        ${summary.top_issues.length === 0
          ? `<div class="empty">No findings recorded.</div>`
          : `<table>
              <thead><tr><th>Severity</th><th>Identifier</th><th>Package</th><th>Fixed in</th><th>Count</th></tr></thead>
              <tbody>${summary.top_issues.map((i) => `
                <tr>
                  <td><span class="sev-tag ${esc(i.severity)}">${esc(i.severity)}</span></td>
                  <td class="mono">${esc(i.identifier || i.title || "—")}</td>
                  <td class="mono">${esc(i.package_name || "—")}</td>
                  <td class="mono">${esc(i.fixed_version || "no fix")}</td>
                  <td>${i.count}</td>
                </tr>`).join("")}
              </tbody></table>`}
      </div>

      <h2>Scan history</h2>
      <div class="panel table-wrap">
        ${scans.length === 0
          ? `<div class="empty">No scans yet.</div>`
          : `<table>
              <thead><tr><th>Tool</th><th>Target</th><th>Status</th><th>Findings</th><th>When</th><th></th></tr></thead>
              <tbody>${scans.map((s) => `
                <tr>
                  <td class="mono">${esc(s.tool)}</td>
                  <td class="mono">${esc(s.target)}</td>
                  <td>${pill(s.status)}</td>
                  <td>${s.summary ? severityInline(s.summary) : "—"}</td>
                  <td class="muted">${fmtDate(s.created_at)}</td>
                  <td><button class="small view-findings" data-id="${s.id}">Details</button></td>
                </tr>`).join("")}
              </tbody></table>`}
      </div>
      <div id="findings"></div>`;

    // The findings endpoint supports severity filtering and pagination; keep
    // the current selection when re-rendering so a filter survives paging.
    async function showFindings(scanId, severityFilter = "", page = 1) {
      const query = new URLSearchParams({ page: String(page), page_size: "50" });
      if (severityFilter) query.set("severity", severityFilter);
      const result = await api(`/scans/${scanId}/findings?${query}`);
      const pages = Math.max(1, Math.ceil(result.total / 50));

      $("#findings").innerHTML = `
        <div class="between">
          <h2>Findings (${result.total})</h2>
          <select id="sev-filter" style="width:auto">
            <option value="">All severities</option>
            ${SEVERITIES.map((s) =>
              `<option value="${s}"${s === severityFilter ? " selected" : ""}>${s}</option>`
            ).join("")}
          </select>
        </div>
        <div class="panel table-wrap">
          ${result.items.length === 0
            ? `<div class="empty">Nothing found${severityFilter ? ` at severity “${esc(severityFilter)}”` : " — this target is clean"}.</div>`
            : `<table>
                <thead><tr><th>Severity</th><th>Identifier</th><th>Package</th><th>Installed</th><th>Fixed in</th><th>Location</th></tr></thead>
                <tbody>${result.items.map((f) => `
                  <tr>
                    <td><span class="sev-tag ${esc(f.severity)}">${esc(f.severity)}</span></td>
                    <td class="mono">${esc(f.identifier || "—")}</td>
                    <td class="mono">${esc(f.package_name || "—")}</td>
                    <td class="mono">${esc(f.installed_version || "—")}</td>
                    <td class="mono">${esc(f.fixed_version || "no fix")}</td>
                    <td class="mono">${esc(f.file_path ? `${f.file_path}:${f.line_number ?? ""}` : "—")}</td>
                  </tr>`).join("")}
</tbody></table>`}
      </div>
        ${pages > 1 ? `<div class="row">
          <button class="small" id="prev-page" ${page <= 1 ? "disabled" : ""}>Previous</button>
          <span class="muted">Page ${page} of ${pages}</span>
          <button class="small" id="next-page" ${page >= pages ? "disabled" : ""}>Next</button>
        </div>` : ""}`;

      $("#sev-filter").onchange = (e) => showFindings(scanId, e.target.value, 1);
      const prev = $("#prev-page");
      const next = $("#next-page");
      if (prev) prev.onclick = () => showFindings(scanId, severityFilter, page - 1);
      if (next) next.onclick = () => showFindings(scanId, severityFilter, page + 1);
      $("#findings").scrollIntoView({ behavior: "smooth" });
    }

    view().querySelectorAll(".view-findings").forEach((btn) => {
      btn.onclick = async () => {
        try {
          await showFindings(btn.dataset.id);
        } catch (err) { toast(err.message, true); }
      };
    });
  } catch (err) {
    showError(err, route);
  }
}

/* ------------------------------------------------------------ catalogue */

async function renderCatalogue() {
  setNav("catalogue");
  loading();
  try {
    const [entries, teams] = await Promise.all([api("/catalogue"), api("/teams")]);
    const teamById = new Map(teams.map((t) => [t.id, t.name]));
    view().innerHTML = `
      <h1>Service catalogue</h1>
      <p class="subtitle">Every service running across your teams, with its current security posture.</p>
      <div class="panel">
        <div class="row" style="align-items:flex-end">
          <div class="field">
            <label for="cat-filter-status">Status</label>
            <select id="cat-filter-status">
              <option value="">All</option>
              <option value="live">live</option>
              <option value="building">building</option>
              <option value="scanning">scanning</option>
              <option value="failed">failed</option>
              <option value="undeployed">undeployed</option>
            </select>
          </div>
          <div class="field">
            <label for="cat-filter-sev">Findings</label>
            <select id="cat-filter-sev">
              <option value="">All</option>
              <option value="critical">Critical</option>
              <option value="high">High</option>
              <option value="clean">Clean</option>
            </select>
          </div>
          <div class="field">
            <label for="cat-filter-team">Team</label>
            <select id="cat-filter-team">
              <option value="">All teams</option>
              ${teams.map((t) => `<option value="${t.id}">${esc(t.name)}</option>`).join("")}
            </select>
          </div>
        </div>
      </div>
      <div class="panel table-wrap">
        <div id="cat-rows">${catalogueRows(entries, teamById)}</div>
      </div>`;

    const apply = () => {
      const status = $("#cat-filter-status").value;
      const sev = $("#cat-filter-sev").value;
      const team = $("#cat-filter-team").value;
      $("#cat-rows").innerHTML = catalogueRows(
        entries.filter((e) =>
          (!status || e.status === status) &&
          (!sev || (sev === "clean" ? !e.critical && !e.high : (sev === "critical" ? e.critical : e.high))) &&
          (!team || e.team_id === team)
        ),
        teamById,
      );
    };
    $("#cat-filter-status").onchange = apply;
    $("#cat-filter-sev").onchange = apply;
    $("#cat-filter-team").onchange = apply;
  } catch (err) {
    showError(err, route);
  }
}

function catalogueRows(entries, teamById) {
  if (entries.length === 0) {
    return `<div class="empty">Nothing deployed yet. Create an environment and deploy an app to see it here.</div>`;
  }
  return `<table>
    <thead><tr><th>Service</th><th>Project</th><th>Owner</th><th>Team</th><th>Status</th><th>Findings</th><th>Live URL</th><th>Logs</th><th>Updated</th></tr></thead>
    <tbody>${entries.map((e) => `
      <tr>
        <td>${esc(e.service_name)}</td>
        <td><a href="#/projects/${e.project_id}">${esc(e.project_name)}</a></td>
        <td class="muted">${esc(e.owner_email || "—")}</td>
        <td class="muted">${esc(teamById.get(e.team_id) || "")}</td>
        <td>${pill(e.status)}</td>
        <td>${e.critical || e.high
            ? `${e.critical ? `<span class="sev-tag critical">${e.critical} critical</span> ` : ""}${e.high ? `<span class="sev-tag high">${e.high} high</span>` : ""}`
            : `<span class="muted">clean</span>`}</td>
        <td>${e.live_url ? `<a href="${esc(e.live_url)}" target="_blank" rel="noopener">open</a>` : "—"}</td>
        <td>${e.logs_job_id ? `<a href="#/jobs/${e.logs_job_id}">logs</a>` : "—"}</td>
        <td class="muted">${fmtDate(e.updated_at)}</td>
      </tr>`).join("")}
    </tbody></table>`;
}

/* ---------------------------------------------------------------- teams */

async function renderTeams() {
  setNav("teams");
  loading();
  try {
    const teams = await api("/teams");
    const costs = await Promise.all(
      teams.map((team) => api(`/teams/${team.id}/costs`).catch(() => null))
    );
    // Only whether a credential exists — the API deliberately has no way to
    // read the token back, so there is nothing here to display or leak.
    const gitCreds = await Promise.all(
      teams.map((team) => api(`/teams/${team.id}/git-credential`).catch(() => null))
    );

    view().innerHTML = `
      <div class="between">
        <div>
          <h1>Teams</h1>
          <p class="subtitle">Projects belong to a team. Everyone in the team can see them.</p>
        </div>
        <button class="primary" id="new-team">New team</button>
      </div>
      ${teams.map((team, index) => {
        const cost = costs[index];
        const git = gitCreds[index];
        return `
        <div class="panel">
          <div class="between">
            <div>
              <h2 style="margin:0">${esc(team.name)} ${team.is_personal ? '<span class="pill draft">personal</span>' : ""}</h2>
              <p class="muted" style="margin:.25rem 0 0">${esc(team.description || "")}</p>
            </div>
            <div style="text-align:right">
              ${cost ? `<div><b>${cost.total.toFixed(2)} ${esc(cost.currency)}</b></div>
                        <div class="muted" style="font-size:.85rem">${cost.projects.length} project(s) to date</div>` : ""}
            </div>
          </div>
          <div class="row" style="margin-top:.75rem">
            <button class="small view-members" data-id="${team.id}">Members</button>
            <button class="small add-member" data-id="${team.id}">Add member</button>
          </div>
          <div id="members-${team.id}"></div>
          <div class="between" style="margin-top:1rem;padding-top:.75rem;border-top:1px solid var(--border)">
            <div>
              <b>Private repository access</b>
              <p class="muted small" style="margin:.25rem 0 0">
                ${git === null
                  ? "Status unavailable."
                  : git.configured
                    ? "A git credential is configured. The platform can clone this team's private repositories."
                      + (git.encrypted === false ? " Stored unencrypted — no Vault is configured on this instance." : "")
                    : "No credential. Only public repositories can be deployed."}
              </p>
            </div>
            <div class="row">
              <button class="small set-git" data-id="${team.id}">${git && git.configured ? "Replace token" : "Add token"}</button>
              ${git && git.configured ? `<button class="small danger clear-git" data-id="${team.id}">Remove</button>` : ""}
            </div>
          </div>
        </div>`;
      }).join("")}`;

    $("#new-team").onclick = async () => {
      const name = await modalPrompt("New team", "Team name:");
      if (!name) return;
      try {
        await api("/teams", { method: "POST", body: { name } });
        toast("Team created.");
        renderTeams();
      } catch (err) { toast(err.message, true); }
    };

    view().querySelectorAll(".view-members").forEach((btn) => {
      btn.onclick = async () => {
        try {
          const members = await api(`/teams/${btn.dataset.id}/members`);
          $(`#members-${btn.dataset.id}`).innerHTML = `
            <div class="table-wrap" style="margin-top:.75rem"><table>
              <thead><tr><th>Member</th><th>Role</th><th></th></tr></thead>
              <tbody>${members.map((m) => `
                <tr>
                  <td>${esc(m.email)}</td>
                  <td class="mono">${esc(m.role)}</td>
                  <td><button class="small danger remove-member" data-team="${btn.dataset.id}" data-user="${m.user_id}">Remove</button></td>
                </tr>`).join("")}
              </tbody></table></div>`;
          bindRemoveMember();
        } catch (err) { toast(err.message, true); }
      };
    });

    view().querySelectorAll(".add-member").forEach((btn) => {
      btn.onclick = async () => {
        const email = await modalPrompt("Add member", "Email address of the user to add:");
        if (!email) return;
        const role = await modalPrompt(
          "Add member",
          `Role for ${email} (viewer / developer / owner / admin):`,
          { value: "developer" },
        );
        if (!role) return;
        try {
          await api(`/teams/${btn.dataset.id}/members`, {
            method: "POST", body: { email, role },
          });
          toast("Member added.");
          renderTeams();
        } catch (err) { toast(err.message, true); }
      };
    });

    view().querySelectorAll(".set-git").forEach((btn) => {
      btn.onclick = async () => {
        const token = await modalPrompt(
          "Private repository access",
          "Paste a token the platform may use to clone this team's private repositories. "
          + "A GitHub App installation token is preferred — it expires within the hour and "
          + "covers only the repositories you selected. A fine-grained personal access token "
          + "also works; scope it to read-only access on the repositories you deploy. "
          + "The token can never be read back out of the platform."
          + (git && git.encrypted === false
              ? " Warning: this instance has no Vault configured, so it will be stored unencrypted."
              : " It is held in the platform's secret manager."),
          { type: "password", placeholder: "ghs_… or github_pat_…" },
        );
        if (!token) return;
        try {
          await api(`/teams/${btn.dataset.id}/git-credential`, {
            method: "PUT", body: { token },
          });
          toast("Credential stored.");
          renderTeams();
        } catch (err) { toast(err.message, true); }
      };
    });

    view().querySelectorAll(".clear-git").forEach((btn) => {
      btn.onclick = async () => {
        const ok = await modalConfirm(
          "Remove credential",
          "Deployments from this team's private repositories will start failing. Continue?",
          "Remove",
        );
        if (!ok) return;
        try {
          await api(`/teams/${btn.dataset.id}/git-credential`, { method: "DELETE" });
          toast("Credential removed.");
          renderTeams();
        } catch (err) { toast(err.message, true); }
      };
    });

    function bindRemoveMember() {
      view().querySelectorAll(".remove-member").forEach((btn) => {
        btn.onclick = async () => {
          const ok = await modalConfirm("Remove member", "Revoke this member's team access?");
          if (!ok) return;
          try {
            await api(`/teams/${btn.dataset.team}/members/${btn.dataset.user}`, { method: "DELETE" });
            toast("Member removed.");
            renderTeams();
          } catch (err) { toast(err.message, true); }
        };
      });
    }
  } catch (err) {
    showError(err, route);
  }
}

/* ----------------------------------------------------------------- jobs */

async function renderJobs() {
  setNav("jobs");
  loading();
  try {
    const projects = await api("/projects");
    view().innerHTML = `
      <h1>Jobs</h1>
      <p class="subtitle">Open a project to follow its provisioning and deployment jobs, or paste a job ID below.</p>
      <div class="panel">
        <form id="job-lookup" class="row">
          <input id="job-id" placeholder="Job ID" style="max-width:340px">
          <button class="primary" type="submit">Open job</button>
        </form>
      </div>
      ${projects.length
        ? `<div class="panel"><table><tbody>${projects.map((p) =>
            `<tr><td><a href="#/projects/${p.id}">${esc(p.name)}</a></td><td>${pill(p.status)}</td></tr>`
          ).join("")}</tbody></table></div>`
        : ""}`;
    $("#job-lookup").onsubmit = (e) => {
      e.preventDefault();
      const id = $("#job-id").value.trim();
      if (id) location.hash = `#/jobs/${id}`;
    };
  } catch (err) {
    showError(err, route);
  }
}

let activeStream;

/* ---------------------------------------------------------- metric cards */

function fmtMetric(value, unit) {
  if (value === null || value === undefined) return "—";
  if (unit === "bytes") {
    const units = ["B", "KiB", "MiB", "GiB", "TiB"];
    let v = value, i = 0;
    while (v >= 1024 && i < units.length - 1) { v /= 1024; i += 1; }
    return `${v.toFixed(v < 10 ? 2 : 0)} ${units[i]}`;
  }
  if (unit === "cores") return value.toFixed(value < 1 ? 3 : 2);
  return String(Math.round(value));
}

/** Inline sparkline — no chart library, just a scaled SVG polyline. */
function metricSpark(series) {
  if (series.length < 2) return "";
  const values = series.map((p) => p.v);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const w = 220, h = 40;
  const pts = series.map((p, i) => {
    const x = (i / (series.length - 1)) * w;
    const y = h - ((p.v - min) / span) * h;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true">
    <polyline points="${pts}" fill="none" stroke="currentColor" stroke-width="1.5"/></svg>`;
}

function metricCard(panel) {
  return `<div class="metric">
      <div class="metric-title">${esc(panel.title)}</div>
      <div class="metric-value">${esc(fmtMetric(panel.latest, panel.unit))}</div>
      ${metricSpark(panel.series)}
    </div>`;
}

/* ------------------------------------------------------ pipeline stages */

/**
 * Every long-running task logs its steps as "[n/N] label" (workers/tasks.py:
 * provision 2 or 4 steps, deploy 7, scan its own). Parsing those markers is
 * enough to show real progress without the worker having to publish a second,
 * separate progress channel that could drift from the log.
 */
function parseStages(log) {
  const stages = [];
  let total = 0;
  const re = /^\[(\d+)\/(\d+)\]\s*(.+)$/gm;
  let m;
  while ((m = re.exec(log || "")) !== null) {
    const index = Number(m[1]);
    total = Number(m[2]);
    if (!stages.some((s) => s.index === index)) {
      stages.push({ index, label: m[3].trim() });
    }
  }
  stages.sort((a, b) => a.index - b.index);
  return { stages, total };
}

/** Render the step list: everything before the newest marker is done. */
function stageTracker(log, jobStatus) {
  const { stages, total } = parseStages(log);
  if (!stages.length) return "";

  const current = stages[stages.length - 1].index;
  const failed = jobStatus === "failed";
  const finished = jobStatus === "succeeded";

  const rows = stages.map((s) => {
    let state = "done";
    if (s.index === current && !finished) state = failed ? "failed" : "running";
    const mark = state === "done" ? "✓" : state === "failed" ? "✗" : "●";
    return `<li class="stage ${state}"><span class="stage-mark">${mark}</span>
      <span class="stage-n">${s.index}/${total}</span>
      <span class="stage-label">${esc(s.label)}</span></li>`;
  }).join("");

  const doneCount = finished ? total : current - (failed ? 1 : 0);
  const pct = total ? Math.round((Math.max(doneCount, 0) / total) * 100) : 0;

  return `<div class="panel" id="stages">
      <div class="between" style="margin-bottom:.6rem">
        <b>Pipeline</b>
        <span class="muted">${finished ? total : current} of ${total} steps</span>
      </div>
      <div class="progress"><div class="progress-bar ${failed ? "failed" : ""}" style="width:${pct}%"></div></div>
      <ul class="stages">${rows}</ul>
    </div>`;
}

// Module-local currentGraph for live applyLogProgress updates.
let currentGraph = null;

async function renderJob(jobId) {
  setNav("jobs");
  loading();
  if (activeStream) { activeStream.close(); activeStream = null; }

  try {
    const job = await api(`/jobs/${jobId}`);
    view().innerHTML = `
      <div class="between">
        <div>
          <h1>${esc(job.type)} job ${pill(job.status)}</h1>
          <p class="subtitle mono">${esc(job.id)}</p>
        </div>
        <button class="danger" id="cancel-job">Cancel job</button>
      </div>
      ${job.error_message ? `<div class="panel"><p class="error">${esc(job.error_message)}</p></div>` : ""}
      <div class="panel" id="pg-job">Loading graph…</div>
      ${stageTracker(job.log, job.status)}
      <div class="panel">
        <div class="between" style="margin-bottom:.6rem">
          <span class="muted">Started ${fmtDate(job.started_at)}${job.finished_at ? ` · finished ${fmtDate(job.finished_at)}` : ""}</span>
          <span class="muted" id="stream-state"></span>
        </div>
        <div class="log" id="log">${esc(job.log || "Waiting for output…")}</div>
      </div>`;

    $("#cancel-job").onclick = async () => {
      try {
        await api(`/jobs/${jobId}/cancel`, { method: "POST" });
        toast("Cancellation requested.");
      } catch (err) { toast(err.message, true); }
    };

    // Fetch and render pipeline graph — Promise.all per spec
    async function renderGraph() {
      try {
        const [jobData, graph] = await Promise.all([
          api(`/jobs/${jobId}`),
          api(`/jobs/${jobId}/graph`),
        ]);
        currentGraph = graph;
        if (window.PipelineGraph) {
          const target = $("#pg-job");
          if (target) target.outerHTML = window.PipelineGraph.render(graph);
        }
      } catch (e) {
        // Fallback: stageTracker already rendered
      }
    }
    await renderGraph();

    // Only stream while the job can still produce output.
    if (job.status === "queued" || job.status === "running") {
      streamJobLog(jobId, job.status);
    } else {
      $("#stream-state").textContent = "finished";
    }
  } catch (err) {
    showError(err, route);
  }
}

function streamJobLog(jobId, jobStatus) {
  const logEl = $("#log");
  const stateEl = $("#stream-state");
  stateEl.textContent = "streaming…";

  // EventSource cannot send an Authorization header, so the log stream uses a
  // short-lived stream token minted server-side instead of the access token
  // (which would otherwise appear in proxy logs).
  fetch(`${API}/jobs/${jobId}/stream-token`, { method: "POST", headers: { Authorization: `Bearer ${token()}` } })
    .then((res) => {
      if (!res.ok) throw new Error(`stream-token ${res.status}`);
      return res.json();
    })
    .then((body) => {
      const stream = new EventSource(`${API}/jobs/${jobId}/logs?stream_token=${encodeURIComponent(body.message)}`);
      activeStream = stream;

      // The server emits named "log" events carrying {"delta": "..."} -- the
      // incremental tail of the job log since the last send.
      stream.addEventListener("log", (event) => {
        let delta = "";
        try { delta = JSON.parse(event.data).delta || ""; } catch { return; }
        if (!delta) return;
        if (logEl.textContent === "Waiting for output…") logEl.textContent = "";
        logEl.textContent += delta;

        logEl.scrollTop = logEl.scrollHeight;

        // Live graph rebuild from log markers -- no extra HTTP, ~1s liveness.
        // stageTracker stays as fallback if applyLogProgress unavailable.
        if (window.PipelineGraph && window.PipelineGraph.applyLogProgress && currentGraph) {
          const next = window.PipelineGraph.applyLogProgress(currentGraph, logEl.textContent, jobStatus);
          const target = document.getElementById("pg-job");
          if (target) target.outerHTML = window.PipelineGraph.render(next);
        }

        // Keep the step tracker in step with the log it is derived from.
        const stagesEl = $("#stages");
        const markup = stageTracker(logEl.textContent, jobStatus);
        if (markup) {
          if (stagesEl) stagesEl.outerHTML = markup;
          else logEl.closest(".panel").insertAdjacentHTML("beforebegin", markup);
        }
      });

      stream.addEventListener("done", () => {
        stateEl.textContent = "finished";
        stream.close();
        if (activeStream === stream) activeStream = null;
        // Final authoritative graph fetch on done
        renderJob(jobId);
      });

      stream.onerror = () => {
        stateEl.textContent = "stream disconnected";
        stream.close();
        if (activeStream === stream) activeStream = null;
      };
    })
    .catch((err) => {
      stateEl.textContent = `stream error: ${err.message}`;
    });
}

/* --------------------------------------------------------------- router */

const ROUTES = [
  [/^\/login$/, renderAuth],
  [/^\/projects$/, renderProjects],
  [/^\/projects\/new$/, renderNewProject],
  [/^\/projects\/([0-9a-f-]{36})$/, renderProject],
  [/^\/catalogue$/, renderCatalogue],
  [/^\/teams$/, renderTeams],
  [/^\/security$/, () => renderSecurity(null)],
  [/^\/security\/([0-9a-f-]{36})$/, renderSecurity],
  [/^\/jobs$/, renderJobs],
  [/^\/jobs\/([0-9a-f-]{36})$/, renderJob],
];

// Statuses that are still moving, so the view should refresh itself.
const IN_FLIGHT = new Set([
  "provisioning", "destroying", "queued", "building", "scanning", "deploying",
]);

let refreshTimer;

function scheduleRefresh(handler, arg) {
  clearTimeout(refreshTimer);
  refreshTimer = setTimeout(() => {
    // Only refresh if the user is still on the same view.
    if (!document.hidden) handler(arg);
  }, 5000);
}

/** Re-render while anything on screen is still in progress. */
function autoRefresh(handler, arg, statuses) {
  if (statuses.some((status) => IN_FLIGHT.has(status))) scheduleRefresh(handler, arg);
}

function route() {
  clearTimeout(refreshTimer);
  if (activeStream) { activeStream.close(); activeStream = null; }

  const path = location.hash.replace(/^#/, "") || "/projects";

  if (!token()) {
    setNav(null);
    showControlPlanePane();
    return renderAuth();
  }

  // The platform-ops half owns every #/platform/* route and renders into its
  // own sections; hand off and stop, so neither half writes over the other.
  const platform = path.match(/^\/platform(?:\/([\w-]+))?$/);
  if (platform) {
    // Typing the URL by hand must not strand a tenant on a console whose
    // every request will 404. Server-side gating is unchanged either way.
    if (!isPlatformAdmin()) {
      location.hash = "#/projects";
      return;
    }
    setNav(null);
    return window.PlatformConsole.mount(platform[1] || "topology");
  }

  showControlPlanePane();
  for (const [pattern, handler] of ROUTES) {
    const match = path.match(pattern);
    if (match) return handler(match[1]);
  }
  location.hash = "#/projects";
}

/** Fill the sidebar's account line from /auth/me.
 *
 * Called at boot and again after a form login: the sidebar is now permanent
 * chrome rather than a nav bar that reappeared on reload, so signing in has
 * to fill it there and then or it stays blank for the whole session.
 */
async function refreshWhoami({ clearOnFailure = false } = {}) {
  try {
    const me = await api("/auth/me");
    $("#whoami").textContent = me.email;
    $("#whoami").title = me.email;
  } catch {
    if (clearOnFailure) clearTokens();
  }
}

/** One click handler for the shared sidebar; both halves are hash routes. */
function bindSidebar() {
  $("#sidebar").addEventListener("click", (event) => {
    const item = event.target.closest(".nav-item");
    if (!item) return;
    location.hash = item.dataset.href || `#/platform/${item.dataset.view}`;
  });
}

async function boot() {
  $("#app").remove();
  $("#platform-root").hidden = false;
  bindSidebar();

  $("#logout").onclick = async () => {
    const refresh = localStorage.getItem(REFRESH_KEY);
    if (refresh) {
      // Best effort: revoke server-side, but log out locally regardless.
      await api("/auth/logout", { method: "POST", body: { refresh_token: refresh } }).catch(() => {});
    }
    clearTokens();
    $("#whoami").textContent = "";
    location.hash = "#/login";
    route();
  };

  if (token()) await refreshWhoami({ clearOnFailure: true });

  // SSO popup (docs/TODO.md Task 3.3): the IdP callback page postMessages
  // the tokens back; only accept messages from our own origin.
  window.addEventListener("message", (event) => {
    if (event.origin !== window.location.origin) return;
    if (!event.data || event.data.source !== "controlplane-oidc") return;
    setTokens(event.data.access_token, event.data.refresh_token);
    $("#whoami").textContent = event.data.email || $("#whoami").textContent;
    route();
  });

  window.addEventListener("hashchange", route);
  route();
}

boot();
