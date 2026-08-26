/**
 * Pipeline Graph — the one shared renderer for tenant deployments and
 * platform CI. Consumes the normalised pipeline-graph/1 contract and
 * nothing else; both frontend halves render its output via innerHTML.
 *
 * Exports exactly one global: window.PipelineGraph =
 *   { layout, render, bind, applyLogProgress, fromServicePipeline, STATUSES }
 *
 * The __pgHost shim makes the file require()-able under Node for the layout
 * tests (in Node, __pgHost = globalThis).
 *
 * Layout is layered (Sugiyama-lite): Kahn longest-path layers, dummy chain
 * nodes for long edges, two barycentre sweeps, orthogonal elbow edges.
 * Deterministic — no Math.random — the same input always draws the same
 * picture.
 *
 * Colour comes from CSS tokens via classes (.pg-node.<status>); shapes use
 * fill/stroke currentColor. No hex literals here.
 */

var __pgHost = (typeof window !== "undefined") ? window : globalThis;
__pgHost.PipelineGraph = (function () {
  "use strict";

  var STATUSES = ["pending", "running", "succeeded", "failed", "skipped", "cancelled"];

  var NODE_W = 190;
  var NODE_H = 44;
  var COL_GAP = 64;
  var ROW_GAP = 16;
  var PAD = 16;
  var DUMMY_H = 12;
  var MAX_LABEL = 24;

  var GLYPHS = {
    succeeded: "\u2713",   // ✓
    running: "\u25CF",     // ●
    failed: "\u2717",      // ✗
    skipped: "\u2298",     // ⊘
    cancelled: "\u2013",   // –
    pending: "\u25CB",     // ○
  };

  var STATUS_ORDER = ["pending", "running", "succeeded", "failed", "skipped", "cancelled"];

  function esc(s) {
    return String(s === null || s === undefined ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function truncate(s, max) {
    s = String(s === null || s === undefined ? "" : s);
    return s.length > max ? s.slice(0, max - 1) + "\u2026" : s;
  }

  function formatDuration(s) {
    if (s === null || s === undefined) return "";
    if (s < 60) return Math.round(s) + " s";
    var m = Math.floor(s / 60);
    var sec = Math.round(s % 60);
    return sec ? m + " m " + sec + " s" : m + " m";
  }

  // ── Layout ────────────────────────────────────────────────────────────

  /**
   * layout(graph, opts) → { nodes: [{id, x, y, w, h, layer, order, ...}],
   *                          edges: [{from, to, d}], width, height }
   * Pure, no DOM. Empty graph → {nodes: [], edges: [], width: 0, height: 0}
   * immediately, so nothing downstream produces NaN.
   */
  function layout(graph, opts) {
    opts = opts || {};
    var nodes = sanitise(graph);
    if (!nodes.length) return { nodes: [], edges: [], width: 0, height: 0 };

    var byId = new Map(nodes.map(function (n) { return [n.id, n]; }));

    // Kahn topological order + longest-path layer assignment.
    var indeg = new Map(), out = new Map();
    nodes.forEach(function (n) { indeg.set(n.id, 0); out.set(n.id, []); });
    nodes.forEach(function (n) {
      n.depends_on.forEach(function (d) {
        if (!byId.has(d)) return;
        indeg.set(n.id, indeg.get(n.id) + 1);
        out.get(d).push(n.id);
      });
    });
    var queue = nodes.filter(function (n) { return indeg.get(n.id) === 0; }).map(function (n) { return n.id; });
    var layer = new Map(nodes.map(function (n) { return [n.id, 0]; }));
    var topo = [];
    while (queue.length) {
      var u = queue.shift();
      topo.push(u);
      out.get(u).forEach(function (v) {
        layer.set(v, Math.max(layer.get(v), layer.get(u) + 1));
        indeg.set(v, indeg.get(v) - 1);
        if (indeg.get(v) === 0) queue.push(v);
      });
    }
    // Cycle guard: leftover nodes after Kahn sit in a final layer, flagged.
    var leftover = nodes.filter(function (n) { return topo.indexOf(n.id) === -1; });
    if (leftover.length) {
      var maxLayer = 0;
      topo.forEach(function (id) { maxLayer = Math.max(maxLayer, layer.get(id)); });
      leftover.forEach(function (n) {
        topo.push(n.id);
        layer.set(n.id, maxLayer + 1);
        n.detail = (n.detail ? n.detail + "; " : "") + "dependency cycle";
      });
    }

    // Dummy chain nodes for edges spanning more than one layer.
    if (opts.dummies !== false) insertDummies(nodes, byId, layer);

    // In/out edges after dummy insertion.
    var inEdges = new Map(), outEdges = new Map();
    nodes.forEach(function (n) { inEdges.set(n.id, []); outEdges.set(n.id, []); });
    nodes.forEach(function (n) {
      n.depends_on.forEach(function (d) {
        if (!byId.has(d)) return;
        inEdges.get(n.id).push(d);
        outEdges.get(d).push(n.id);
      });
    });

    // Within-layer ordering: two barycentre sweeps, stable.
    // Iterates every node (not just topo) so dummy chain nodes inserted
    // above get a layer group too — otherwise they're missing from `rank`
    // below and their x/y resolve to NaN, poisoning the graph's width/height.
    var groups = new Map();
    nodes.forEach(function (n) {
      var L = layer.get(n.id);
      if (!groups.has(L)) groups.set(L, []);
      groups.get(L).push(n.id);
    });
    var rank = new Map();
    groups.forEach(function (ids) { ids.forEach(function (id, i) { rank.set(id, i); }); });

    function bary(id, dir) {
      var deps = dir === "pred" ? inEdges.get(id) : outEdges.get(id);
      if (!deps.length) return -1;
      var s = 0, n = 0;
      deps.forEach(function (d) { var r = rank.get(d); if (r !== undefined) { s += r; n += 1; } });
      return n ? s / n : -1;
    }
    function sweep(dir) {
      var order = dir === "pred" ? Array.from(groups.keys()).sort(function (a, b) { return a - b; })
                                  : Array.from(groups.keys()).sort(function (a, b) { return b - a; });
      order.forEach(function (L) {
        var ids = groups.get(L).slice();
        ids.sort(function (a, b) {
          var ka = bary(a, dir), kb = bary(b, dir);
          if (ka !== kb) return ka < kb ? -1 : 1;
          return 0; // stable: ties keep the previous order
        });
        ids.forEach(function (id, i) { rank.set(id, i); });
        groups.set(L, ids);
      });
    }
    sweep("pred");
    sweep("succ");

    // Coordinates + per-column vertical centering.
    var colHeight = new Map();
    var maxCol = 0;
    groups.forEach(function (ids, L) {
      var h = ids.length * NODE_H + Math.max(0, ids.length - 1) * ROW_GAP;
      colHeight.set(L, h);
      maxCol = Math.max(maxCol, h);
    });
    nodes.forEach(function (n) {
      n.layer = layer.get(n.id);
      n.order = rank.get(n.id);
      n.w = n.dummy ? 0 : NODE_W;
      n.h = n.dummy ? DUMMY_H : NODE_H;
      n.x = PAD + n.layer * (NODE_W + COL_GAP);
      n.y = PAD + n.order * (NODE_H + ROW_GAP);
    });
    groups.forEach(function (ids, L) {
      var off = (maxCol - colHeight.get(L)) / 2;
      if (off > 0) ids.forEach(function (id) { byId.get(id).y += off; });
    });

    // Edge paths — orthogonal elbows, shared gutter reads as a bus.
    var edges = [];
    nodes.forEach(function (n) {
      n.depends_on.forEach(function (d) {
        if (!byId.has(d)) return;
        edges.push({ from: d, to: n.id, d: edgePath(byId.get(d), n) });
      });
    });

    var maxX = 0, maxY = 0;
    nodes.forEach(function (n) {
      maxX = Math.max(maxX, n.x + n.w);
      maxY = Math.max(maxY, n.y + n.h);
    });
    return {
      nodes: topo.map(function (id) { return byId.get(id); }),
      edges: edges,
      width: maxX + PAD,
      height: maxY + PAD,
    };
  }

  function sanitise(graph) {
    var seen = new Set();
    var all = graph.nodes || [];
    var nodes = [];
    all.forEach(function (n) {
      if (!n || !n.id || seen.has(n.id)) return;
      seen.add(n.id);
      nodes.push({
        id: n.id,
        label: n.label !== undefined && n.label !== null ? String(n.label) : n.id,
        status: STATUSES.indexOf(n.status) !== -1 ? n.status : "pending",
        depends_on: (n.depends_on || []).filter(function (d) { return all.some(function (m) { return m && m.id === d; }); }),
        started_at: n.started_at !== undefined ? n.started_at : null,
        finished_at: n.finished_at !== undefined ? n.finished_at : null,
        duration_s: typeof n.duration_s === "number" && n.duration_s >= 0 ? n.duration_s : null,
        detail: n.detail || "",
        url: n.url || null,
        fanout: Array.isArray(n.fanout) ? n.fanout : [],
      });
    });
    return nodes;
  }

  function insertDummies(nodes, byId, layer) {
    var dn = 0;
    nodes.forEach(function (n) {
      var newDeps = [];
      n.depends_on.forEach(function (d) {
        var dl = layer.get(d);
        if (dl === undefined || layer.get(n.id) <= dl + 1) { newDeps.push(d); return; }
        var prev = d;
        for (var L = dl + 1; L < layer.get(n.id); L++) {
          var id = "__d" + dn;
          dn += 1;
          nodes.push({ id: id, label: "", status: "pending", depends_on: [prev],
                       started_at: null, finished_at: null, duration_s: null,
                       detail: "", url: null, fanout: [], dummy: true, layer: L });
          byId.set(id, nodes[nodes.length - 1]);
          layer.set(id, L);
          prev = id;
        }
        newDeps.push(prev);
      });
      n.depends_on = newDeps;
    });
  }

  function edgePath(a, b) {
    var x1 = a.x + a.w, y1 = a.y + a.h / 2;
    var x2 = b.x, y2 = b.y + b.h / 2;
    if (Math.abs(y2 - y1) < 1) return "M " + x1 + " " + y1 + " H " + x2;
    var xm = x1 + COL_GAP / 2;
    var R = Math.min(8, Math.abs(y2 - y1) / 2);
    var s = y2 - y1 > 0 ? 1 : -1;
    return "M " + x1 + " " + y1 +
      " H " + (xm - R) +
      " Q " + xm + " " + y1 + " " + xm + " " + (y1 + s * R) +
      " V " + (y2 - s * R) +
      " Q " + xm + " " + y2 + " " + (xm + R) + " " + y2 +
      " H " + x2;
  }

  // ── Rendering ─────────────────────────────────────────────────────────

  /**
   * render(graph, opts) → HTML string. Both halves build with innerHTML.
   * <svg role="img" aria-labelledby> with <title>/<desc> plus a
   * visually-hidden <ul class="pg-sr"> — the accessible content is the
   * list; the SVG is labelled decoration.
   */
  function render(graph, opts) {
    opts = opts || {};
    var laid = layout(graph, opts);
    if (!laid.nodes.length) {
      return '<div class="pg-wrap"><div class="pg-empty">No pipeline steps yet.</div></div>';
    }
    var uid = opts.uid || ("pg" + Math.random().toString(36).slice(2, 8));
    var titleId = "pg-t-" + uid, descId = "pg-d-" + uid;
    var title = graph.title ? esc(graph.title) : "Pipeline";
    var desc = esc(summary(laid));

    var edgesSvg = laid.edges.map(function (e) {
      return '<path class="pg-edge" d="' + e.d + '" />';
    }).join("\n");

    var labelOf = {};
    laid.nodes.forEach(function (n) { labelOf[n.id] = n.label; });
    var nodesSvg = laid.nodes.map(function (n) {
      return nodeHtml(n, labelOf);
    }).join("\n");

    var sr = laid.nodes.filter(function (n) { return !n.dummy; }).map(function (n) {
      var bits = [n.label, n.status];
      if (n.duration_s !== null && n.duration_s !== undefined) bits.push("in " + formatDuration(n.duration_s));
      var deps = (n.depends_on || []).filter(function (d) { return labelOf[d] !== undefined; })
        .map(function (d) { return labelOf[d]; });
      if (deps.length) bits.push("after " + deps.join(", "));
      return "<li>" + esc(bits.join(" — ")) + "</li>";
    }).join("");

    return (
      '<div class="pg-wrap">' +
        (graph.degraded
          ? '<div class="pg-degraded">Live data unavailable: ' + esc(graph.degraded_reason || "source unreachable") + "</div>"
          : "") +
        (graph.detail ? '<div class="pg-detail">' + esc(graph.detail) + "</div>" : "") +
        // Natural pixel size, not width="100%": the layout is as wide as the
        // pipeline is long (2508px for a ten-stage deploy), so scaling it to
        // the card squashed every node to an 18px-tall sliver of unreadable
        // text. .pg-wrap already scrolls horizontally — this is what gives it
        // something to scroll.
        '<svg class="pg-svg" viewBox="0 0 ' + laid.width + " " + laid.height + '"' +
          ' width="' + laid.width + '" height="' + laid.height + '"' +
          ' preserveAspectRatio="xMinYMin meet"' +
          ' role="img" aria-labelledby="' + titleId + " " + descId + '" xmlns="http://www.w3.org/2000/svg">' +
          "<title id=\"" + titleId + '">' + title + "</title>" +
          "<desc id=\"" + descId + '">' + desc + "</desc>" +
          '<g class="pg-edges">' + edgesSvg + "</g>" +
          '<g class="pg-nodes">' + nodesSvg + "</g>" +
        "</svg>" +
        '<ul class="pg-sr">' + sr + "</ul>" +
      "</div>"
    );
  }

  function summary(laid) {
    var counts = {};
    STATUS_ORDER.forEach(function (s) { counts[s] = 0; });
    var n = 0;
    laid.nodes.forEach(function (x) {
      if (x.dummy) return;
      n += 1;
      counts[x.status] = (counts[x.status] || 0) + 1;
    });
    return n + " job" + (n === 1 ? "" : "s") + "; " + STATUS_ORDER.map(function (s) {
      return counts[s] + " " + s;
    }).join(", ") + ".";
  }

  function nodeHtml(n, labelOf) {
    if (n.dummy) {
      return '<g class="pg-dummy" data-id="' + esc(n.id) + '" transform="translate(' + n.x + "," + n.y + ')">' +
        '<path d="M 0 0 L 0 ' + n.h + '" class="pg-edge" /></g>';
    }
    var cls = "pg-node " + n.status + (n.status === "running" ? " running" : "");
    var glyph = GLYPHS[n.status] || "?";
    var label = truncate(n.label, MAX_LABEL);
    var metaBits = [n.status];
    if (n.duration_s !== null && n.duration_s !== undefined) metaBits.push(formatDuration(n.duration_s));
    if (n.detail) metaBits.push(truncate(n.detail, 40));
    var a11y = n.label + " — " + n.status +
      (n.duration_s !== null && n.duration_s !== undefined ? " — " + formatDuration(n.duration_s) : "");
    var href = n.url ? 'href="' + esc(n.url) + '" target="_blank" rel="noopener"' : "";
    var linkOpen = href ? "<a " + href + ">" : "";
    var linkClose = href ? "</a>" : "";
    var stack = "";
    var chip = "";
    if (n.fanout && n.fanout.length) {
      stack = '<rect class="pg-stack" x="-4" y="-4" width="' + NODE_W + '" height="' + NODE_H + '" rx="6" />' +
              '<rect class="pg-stack" x="-2" y="-2" width="' + NODE_W + '" height="' + NODE_H + '" rx="6" />';
      chip = '<g class="pg-chip" transform="translate(' + (NODE_W - 26) + ",6)\"><rect width=\"22\" height=\"14\" rx=\"3\" /><text x=\"11\" y=\"11\" text-anchor=\"middle\">×" + n.fanout.length + "</text></g>";
    }
    return (
      '<g class="' + cls + '" transform="translate(' + n.x + "," + n.y + ')"' +
        ' data-id="' + esc(n.id) + '" tabindex="0" role="button"' +
        ' aria-label="' + esc(a11y) + '">' +
        stack +
        "<rect x=\"0\" y=\"0\" width=\"" + NODE_W + "\" height=\"" + NODE_H + '" rx="6" />' +
        '<circle class="pg-dot" cx="14" cy="18" r="4" />' +
        '<text class="pg-glyph" x="14" y="22" text-anchor="middle">' + glyph + "</text>" +
        linkOpen +
          '<text class="pg-name" x="28" y="20">' + esc(label) + "</text>" +
          '<text class="pg-meta" x="28" y="36">' + esc(metaBits.join(" · ")) + "</text>" +
        linkClose +
        "<title>" + esc(n.label + " — " + a11y) + "</title>" +
        chip +
      "</g>"
    );
  }

  // ── Binding (browser only) ────────────────────────────────────────────

  /**
   * bind(rootEl, graph, opts) — addEventListener delegation, never inline
   * attributes (CSP: script-src 'self'). Node click → opts.onNode(node);
   * clicking a matrix node with fanout toggles expansion by re-running the
   * same layout() on a derived graph (cap 12 + a "+N more" pseudo-node).
   * Keyboard: Enter/Space on a focused node. Listeners attach once per
   * element; a mutated current-graph ref (state.current) keeps them live
   * across re-renders.
   */
  function bind(rootEl, graph, opts) {
    opts = opts || {};
    var state = { expanded: {}, current: graph, onNode: opts.onNode,
                  opts: opts, rootEl: rootEl };
    if (rootEl.__pgBound) return;
    rootEl.__pgBound = true;
    rootEl.addEventListener("click", function (ev) {
      var g = ev.target.closest ? ev.target.closest("[data-id]") : null;
      if (!g || !rootEl.contains(g)) return;
      activate(state, g.getAttribute("data-id"), ev);
    });
    rootEl.addEventListener("keydown", function (ev) {
      if (ev.key !== "Enter" && ev.key !== " ") return;
      var g = ev.target.closest ? ev.target.closest("[data-id]") : null;
      if (!g || !rootEl.contains(g)) return;
      ev.preventDefault();
      activate(state, g.getAttribute("data-id"), ev);
    });
  }

  function activate(state, id, ev) {
    var node = null;
    (state.current.nodes || []).forEach(function (n) { if (n.id === id) node = n; });
    if (!node) return;
    if (node.fanout && node.fanout.length) {
      state.expanded[id] = !state.expanded[id];
      state.current = derive(state.current, state.expanded);
      state.rootEl.innerHTML = render(state.current, state.opts);
      return;
    }
    if (state.onNode) state.onNode(node, ev);
  }

  /**
   * derive(graph, expanded) — pure: replace expanded matrix nodes with N
   * sibling legs (cap 12, plus a "+N more" pseudo-node), each inheriting
   * the parent's depends_on and feeding the parent's dependents.
   */
  function derive(graph, expanded) {
    var replace = {};  // parent id -> sibling ids
    var outNodes = [];
    graph.nodes.forEach(function (n) {
      var fan = (n.fanout || []).filter(function (f) { return f && f.id; });
      if (expanded[n.id] && fan.length) {
        var legs = fan.slice(0, 12);
        var sibs = legs.map(function (f) {
          return { id: f.id, label: f.label || f.id, status: f.status || n.status,
                   duration_s: typeof f.duration_s === "number" ? f.duration_s : null,
                   detail: n.detail, url: f.url || null, fanout: [],
                   depends_on: n.depends_on.slice() };
        });
        var extra = fan.length - legs.length;
        if (extra > 0) {
          sibs.push({ id: n.id + ":more", label: "+" + extra + " more",
                      status: n.status, detail: "", url: null, fanout: [],
                      depends_on: n.depends_on.slice() });
        }
        replace[n.id] = sibs.map(function (s) { return s.id; });
        sibs.forEach(function (s) { outNodes.push(s); });
        return;
      }
      var deps = [];
      (n.depends_on || []).forEach(function (d) {
        if (replace[d]) deps = deps.concat(replace[d]);
        else deps.push(d);
      });
      outNodes.push({ id: n.id, label: n.label, status: n.status,
                      started_at: n.started_at, finished_at: n.finished_at,
                      duration_s: n.duration_s, detail: n.detail, url: n.url,
                      fanout: n.fanout, depends_on: deps });
    });
    return { version: graph.version, source: graph.source, title: graph.title,
             subtitle: graph.subtitle, status: graph.status, url: graph.url,
             degraded: graph.degraded, degraded_reason: graph.degraded_reason,
             detail: graph.detail, generated_at: graph.generated_at,
             nodes: outNodes };
  }

  // ── Live progress from the job log (tenant half) ──────────────────────

  var LOG_STEP_RE = /^\[(\d+)\/(\d+)\]\s*(.+)$/gm;

  /**
   * applyLogProgress(graph, logText, jobStatus) — pure. Recomputes the
   * statuses of a linear (tenant) graph from the [n/N] markers in the log.
   * Never regresses a terminal node (succeeded/failed/cancelled/skipped)
   * back to pending — the log may be truncated, the graph rows are not.
   */
  function applyLogProgress(graph, logText, jobStatus) {
    if (!graph || !graph.nodes || graph.nodes.length === 0) return graph;
    if (graph.source === "ci") return graph;

    var markers = {};
    var m, current = 0;
    LOG_STEP_RE.lastIndex = 0;
    while ((m = LOG_STEP_RE.exec(logText || "")) !== null) {
      var n = Number(m[1]);
      markers[n] = Number(m[2]);
      current = Math.max(current, n);
    }

    var terminal = jobStatus === "succeeded" || jobStatus === "failed" || jobStatus === "cancelled";
    var nodes = graph.nodes.map(function (node, i) {
      // Never regress a terminal node back to pending — the log may be
      // truncated, the graph rows are not.
      if (node.status === "succeeded" || node.status === "failed" ||
          node.status === "cancelled" || node.status === "skipped") return node;
      var index = i + 1;
      var desired = node.status;
      if (index < current) desired = "succeeded";
      else if (index === current) {
        if (jobStatus === "succeeded") desired = "succeeded";
        else if (jobStatus === "cancelled") desired = "cancelled";
        else if (jobStatus === "failed") desired = "failed";
        else desired = "running";
      } else if (terminal) desired = "skipped";
      else desired = "pending";
      return { ...node, status: desired };
    });
    return { ...graph, nodes: nodes };
  }

  // ── service_pipeline adapter (phase 8) ────────────────────────────────

  /**
   * fromServicePipeline(r) — pure. r is the service_pipeline result
   * ({stages: [{stage, state, detail}], blocking: stageName}).
   * ok→succeeded, pending→pending, failed/blocked→failed; every stage after
   * r.blocking → skipped — the function short-circuits, so those stages
   * were never evaluated and must not show as pending.
   */
  function fromServicePipeline(r) {
    var stages = (r && r.stages) || [];
    var blockingIndex = -1;
    if (r && r.blocking) {
      stages.some(function (s, i) {
        if (s.stage === r.blocking) { blockingIndex = i; return true; }
        return false;
      });
    }
    var stateMap = { ok: "succeeded", pending: "pending", failed: "failed", blocked: "failed" };
    var nodes = stages.map(function (s, i) {
      var status = stateMap[s.state] || "pending";
      if (i > blockingIndex) status = "skipped";
      return {
        id: String(s.stage).replace(/[^A-Za-z0-9._:@-]+/g, "-"),
        label: s.stage,
        status: status,
        detail: s.detail || "",
        depends_on: i > 0 ? [nodesSlug(i - 1)] : [],
        started_at: null, finished_at: null, duration_s: null, url: null, fanout: [],
      };
    });
    var graphStatus = "succeeded";
    if (nodes.some(function (n) { return n.status === "failed"; })) graphStatus = "failed";
    else if (nodes.some(function (n) { return n.status === "pending"; })) graphStatus = "pending";
    else if (nodes.some(function (n) { return n.status === "running"; })) graphStatus = "running";
    return {
      version: "pipeline-graph/1",
      source: "service",
      title: r ? r.service : "",
      subtitle: "service pipeline",
      status: graphStatus,
      url: null,
      degraded: false,
      degraded_reason: "",
      detail: r && r.blocking_detail ? "blocked at " + r.blocking + ": " + r.blocking_detail : "",
      generated_at: null,
      nodes: nodes,
    };
    function nodesSlug(i) {
      var s = stages[i];
      return String(s.stage).replace(/[^A-Za-z0-9._:@-]+/g, "-");
    }
  }

  return { layout: layout, render: render, bind: bind,
           applyLogProgress: applyLogProgress,
           fromServicePipeline: fromServicePipeline,
           STATUSES: STATUSES };
})();
