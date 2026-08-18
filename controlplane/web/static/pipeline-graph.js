/**
 * Pipeline Graph — shared renderer for tenant deployments and platform CI.
 *
 * Exports: window.PipelineGraph = { layout, svg, render }
 * - layout(graph): pure function, returns { nodes, edges, width, height }
 * - svg(laidOut): returns SVG string
 * - render(graph): wraps svg() in a panel with accessibility list
 *
 * No deps, inline SVG strings (like existing sparklines).
 * Colour via CSS tokens: --ok, --fail, --warn, --accent, --muted.
 * running gets .pulse from platform/style.css:75-84.
 * Accessibility: <svg role="img" aria-label="..."> + visually-hidden <ul>.
 */

(function () {
  'use strict';

  // ── Config ────────────────────────────────────────────────────────
  const NODE_W = 180;
  const NODE_H = 56;
  const GAP_X = 72;
  const GAP_Y = 20;

  // Status → CSS class (matches existing tokens)
  const STATUS_CLASS = {
    queued: 'pipe-queued',
    running: 'pipe-running',
    succeeded: 'pipe-succeeded',
    failed: 'pipe-failed',
    cancelled: 'pipe-cancelled',
    skipped: 'pipe-skipped',
  };

  // ── Layout ────────────────────────────────────────────────────────

  /**
   * Kahn topological sort with longest-path layer assignment.
   * Returns { nodes: [{...node, layer, row, x, y, w, h}], edges: [{from, to, path}], width, height }
   */
  function layout(graph) {
    const nodesById = new Map(graph.nodes.map(n => [n.id, { ...n, layer: 0, row: 0, x: 0, y: 0, w: NODE_W, h: NODE_H }]));
    const inEdges = new Map();
    const outEdges = new Map();

    graph.edges.forEach(e => {
      if (!nodesById.has(e.from) || !nodesById.has(e.to)) return;
      (outEdges.get(e.from) || (outEdges.set(e.from, []), outEdges.get(e.from))).push(e.to);
      (inEdges.get(e.to) || (inEdges.set(e.to, []), inEdges.get(e.to))).push(e.from);
    });

    // 1. Layer assignment: longest path from roots (Kahn)
    const indegree = new Map();
    graph.nodes.forEach(n => { indegree.set(n.id, (inEdges.get(n.id) || []).length); });

    const queue = [];
    graph.nodes.forEach(n => { if (indegree.get(n.id) === 0) queue.push(n.id); });

    const topo = [];
    while (queue.length) {
      const u = queue.shift();
      topo.push(u);
      (outEdges.get(u) || []).forEach(v => {
        nodesById.get(v).layer = Math.max(nodesById.get(v).layer, nodesById.get(u).layer + 1);
        const d = indegree.get(v) - 1;
        indegree.set(v, d);
        if (d === 0) queue.push(v);
      });
    }

    // 2. Cycle guard: leftover nodes after Kahn → append in final layer
    const leftover = graph.nodes.filter(n => !topo.includes(n.id));
    if (leftover.length) {
      const maxLayer = Math.max(...graph.nodes.map(n => nodesById.get(n.id).layer));
      leftover.forEach((n, i) => {
        const node = nodesById.get(n.id);
        node.layer = maxLayer + 1;
        node.detail = (node.detail ? node.detail + '; ' : '') + 'dependency cycle';
        topo.push(n.id);
      });
    }

    // 3. Within-layer order: barycentre (mean predecessor row), tie-break by id
    const layers = new Map();
    topo.forEach(id => {
      const layer = nodesById.get(id).layer;
      (layers.get(layer) || (layers.set(layer, []), layers.get(layer))).push(id);
    });

    layers.forEach(nodeIds => {
      nodeIds.sort((a, b) => {
        const predsA = inEdges.get(a) || [];
        const predsB = inEdges.get(b) || [];
        const avgA = predsA.length ? predsA.reduce((s, p) => s + nodesById.get(p).row, 0) / predsA.length : 0;
        const avgB = predsB.length ? predsB.reduce((s, p) => s + nodesById.get(p).row, 0) / predsB.length : 0;
        if (avgA !== avgB) return avgA - avgB;
        return a.localeCompare(b);
      });
      nodeIds.forEach((id, i) => { nodesById.get(id).row = i; });
    });

    // 4. Coordinates
    let maxX = 0, maxY = 0;
    nodesById.forEach(n => {
      n.x = n.layer * (NODE_W + GAP_X);
      n.y = n.row * (NODE_H + GAP_Y);
      maxX = Math.max(maxX, n.x + NODE_W);
      maxY = Math.max(maxY, n.y + NODE_H);
    });

    // 5. Edge paths: orthogonal three-segment (right → mid-gap → left)
    const edges = graph.edges
      .filter(e => nodesById.has(e.from) && nodesById.has(e.to))
      .map(e => {
        const from = nodesById.get(e.from);
        const to = nodesById.get(e.to);
        const midX = from.x + NODE_W + (to.x - (from.x + NODE_W)) / 2;
        return {
          from: e.from,
          to: e.to,
          path: `M${from.x + NODE_W} ${from.y + NODE_H / 2} H${midX} V${to.y + NODE_H / 2} H${to.x}`,
        };
      });

    return {
      nodes: topo.map(id => nodesById.get(id)),
      edges,
      width: maxX + 24,
      height: maxY + 24,
    };
  }

  // ── SVG Rendering ────────────────────────────────────────────────

  function escapeHtml(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function formatDuration(s) {
    if (s === null || s === undefined) return '';
    if (s < 60) return `${s.toFixed(1)}s`;
    const m = Math.floor(s / 60);
    const sec = (s % 60).toFixed(1);
    return `${m}m ${sec}s`;
  }

  function nodeHtml(node, index) {
    const cls = STATUS_CLASS[node.status] || 'pipe-queued';
    const dur = formatDuration(node.duration_s);
    const title = escapeHtml(`${node.name} — ${node.status}${dur ? ' · ' + dur : ''}${node.detail ? ' · ' + node.detail : ''}`);
    const href = node.url ? `href="${escapeHtml(node.url)}"` : '';
    const runningPulse = node.status === 'running' ? ' pulse' : '';
    return `
      <g class="pipe-node ${cls}${runningPulse}" transform="translate(${node.x},${node.y})" data-id="${escapeHtml(node.id)}">
        <rect x="0" y="0" width="${NODE_W}" height="${NODE_H}" rx="6" ry="6" />
        ${href ? `<a ${href} target="_blank" rel="noopener"><text x="12" y="22" class="pipe-name">${escapeHtml(node.name)}</text></a>` : `<text x="12" y="22" class="pipe-name">${escapeHtml(node.name)}</text>`}
        <text x="12" y="42" class="pipe-meta">${escapeHtml(node.status)}${dur ? ' · ' + dur : ''}</text>
      </g>
    `;
  }

  function edgeHtml(edge) {
    return `<path d="${escapeHtml(edge.path)}" class="pipe-edge" />`;
  }

  function ariaLabel(graph) {
    const parts = [`Pipeline graph: ${escapeHtml(graph.title)}`];
    graph.nodes.forEach(n => parts.push(`${escapeHtml(n.name)} ${n.status}${n.duration_s ? ' ' + formatDuration(n.duration_s) : ''}`));
    return parts.join('; ');
  }

  function a11yList(laidOut) {
    const items = laidOut.nodes.map(n => `<li>${escapeHtml(n.name)} — ${n.status}${n.duration_s ? ' · ' + formatDuration(n.duration_s) : ''}${n.detail ? ' · ' + escapeHtml(n.detail) : ''}</li>`).join('');
    return `<ul class="pipe-a11y" aria-hidden="true">${items}</ul>`;
  }

  function svg(laidOut) {
    const viewBox = `0 0 ${laidOut.width} ${laidOut.height}`;
    const edgesSvg = laidOut.edges.map(edgeHtml).join('\n');
    const nodesSvg = laidOut.nodes.map(nodeHtml).join('\n');
    return `
      <svg class="pipe-svg" viewBox="${viewBox}" role="img" aria-label="${ariaLabel(laidOut)}" xmlns="http://www.w3.org/2000/svg">
        <style>
          .pipe-node .pipe-name { font: 600 13px system-ui, sans-serif; fill: var(--fg); }
          .pipe-node .pipe-meta { font: 12px system-ui, sans-serif; fill: var(--muted); }
          .pipe-node.pipe-queued rect { fill: var(--panel); stroke: var(--border); }
          .pipe-node.pipe-running rect { fill: var(--panel); stroke: var(--accent); animation: pipe-pulse 1.5s ease-in-out infinite; }
          .pipe-node.pipe-succeeded rect { fill: var(--ok-bg); stroke: var(--ok); }
          .pipe-node.pipe-failed rect { fill: var(--fail-bg); stroke: var(--fail); }
          .pipe-node.pipe-cancelled rect { fill: var(--warn-bg); stroke: var(--warn); }
          .pipe-node.pipe-skipped rect { fill: var(--panel-solid); stroke: var(--border); stroke-dasharray: 4 4; }
          .pipe-edge { fill: none; stroke: var(--border); stroke-width: 2; stroke-linecap: round; }
          .pipe-a11y { position: absolute; left: -9999px; }
          @keyframes pipe-pulse { 0%, 100% { stroke-opacity: 0.6; } 50% { stroke-opacity: 1; } }
        </style>
        <g class="pipe-edges">${edgesSvg}</g>
        <g class="pipe-nodes">${nodesSvg}</g>
      </svg>
      ${a11yList(laidOut)}
    `;
  }

  // ── Public API ────────────────────────────────────────────────────

  function render(graph) {
    const laidOut = layout(graph);
    const container = document.createElement('div');
    container.className = 'pipe-panel';
    container.innerHTML = svg(laidOut);
    return container;
  }

  // Expose
  window.PipelineGraph = { layout, svg, render };
})();