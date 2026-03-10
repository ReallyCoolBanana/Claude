/**
 * viz_kb_graph.js
 *
 * Renders a top-down layered DAG of Knowledge Base entry dependencies using D3 v7.
 * Usage:
 *   <script src="https://d3js.org/d3.v7.min.js"></script>
 *   <script src="viz_kb_graph.js"></script>
 *   <div id="kb-graph"></div>
 *   <script>renderKBGraph("kb-graph");</script>
 */

const KB_DATA = {
  entries: [
    { id: "KB-0001", title: "Storage System Stress Test & Improvement Results", team: "TEAM-0001", category: "optimization", builds_on: [], tags: ["storage-system", "stress-testing", "benchmarks"] },
    { id: "KB-0002", title: "Sonnet Research API — Multi-Agent Research System", team: "TEAM-0002", category: "integration", builds_on: ["KB-0001"], tags: ["sonnet-api", "multi-agent", "research-system"] },
    { id: "KB-0003", title: "Multi-Team Competitive Research: Quantitative Stock Analysis", team: "TEAM-0003", category: "market-research", builds_on: ["KB-0001", "KB-0002"], tags: ["quantitative-analysis", "ai-trading"] },
    { id: "KB-0004", title: "Data Gathering Methods: Iteration 1 Benchmarks", team: "TEAM-0004", category: "methodology", builds_on: ["KB-0003"], tags: ["data-gathering", "benchmarks"] },
    { id: "KB-0005", title: "Data Gathering Iteration 2: Parallel Hybrid Methods", team: "TEAM-0005", category: "methodology", builds_on: ["KB-0004"], tags: ["data-gathering", "openalex", "parallel-execution"] },
    { id: "KB-0006", title: "Archive Tools for Knowledge Base System", team: "TEAM-0006", category: "tool-usage", builds_on: ["KB-0001"], tags: ["archive-tools", "validation"] },
    { id: "KB-0007", title: "Research Team Configuration Testing", team: "TEAM-0007", category: "methodology", builds_on: ["KB-0004", "KB-0005", "KB-0006"], tags: ["agent-scaling", "team-sizing"] },
    { id: "KB-0008", title: "Data Storage Methods & AI-Optimized Storage", team: "TEAM-0008", category: "methodology", builds_on: ["KB-0001", "KB-0007"], tags: ["data-storage", "vector-databases", "RAG"] },
    { id: "KB-0009", title: "Multi-Team Operation 3: Archive Optimization & Go Tools", team: "TEAM-0009", category: "optimization", builds_on: ["KB-0006", "KB-0007", "KB-0008"], tags: ["go-tools", "high-performance"] },
    { id: "KB-0010", title: "Free Public APIs for AI/ML Self-Improvement", team: "TEAM-0012", category: "integration", builds_on: ["KB-0004", "KB-0005", "KB-0008"], tags: ["ai-ml-apis", "free-apis"] },
    { id: "KB-0012", title: "Free Public APIs for Developer Tools", team: "TEAM-0010", category: "integration", builds_on: ["KB-0004", "KB-0005"], tags: ["developer-tools", "public-apis"] },
    { id: "KB-0013", title: "Free Public APIs for Government/Open Data", team: "TEAM-0013", category: "integration", builds_on: ["KB-0004", "KB-0005"], tags: ["government-data", "open-data"] },
    { id: "KB-0014", title: "Operation 4: Public Data API Tool Development", team: "TEAM-0014", category: "integration", builds_on: ["KB-0004", "KB-0005", "KB-0009", "KB-0010"], tags: ["public-apis", "data-gathering"] }
  ]
};

function renderKBGraph(containerId, data) {
  const entries = (data || KB_DATA).entries;

  // --- Category colors ---
  const CATEGORY_COLORS = {
    "optimization": "#3b82f6",
    "integration": "#14b8a6",
    "market-research": "#22c55e",
    "methodology": "#f97316",
    "tool-usage": "#a855f7"
  };

  // --- Build lookup maps ---
  const entryMap = new Map(entries.map(e => [e.id, e]));

  // Compute dependents (who builds on this entry)
  const dependentsMap = new Map(entries.map(e => [e.id, []]));
  entries.forEach(e => {
    e.builds_on.forEach(parentId => {
      if (dependentsMap.has(parentId)) {
        dependentsMap.get(parentId).push(e.id);
      }
    });
  });

  // --- Layer assignment (topological) ---
  const layerMap = new Map();

  function assignLayer(id) {
    if (layerMap.has(id)) return layerMap.get(id);
    const entry = entryMap.get(id);
    if (!entry || entry.builds_on.length === 0) {
      layerMap.set(id, 0);
      return 0;
    }
    const parentLayers = entry.builds_on
      .filter(pid => entryMap.has(pid))
      .map(pid => assignLayer(pid));
    const layer = (parentLayers.length > 0 ? Math.max(...parentLayers) : 0) + 1;
    layerMap.set(id, layer);
    return layer;
  }

  entries.forEach(e => assignLayer(e.id));

  // Group entries by layer
  const maxLayer = Math.max(...layerMap.values());
  const layers = [];
  for (let i = 0; i <= maxLayer; i++) layers.push([]);
  entries.forEach(e => layers[layerMap.get(e.id)].push(e));

  // --- Layout dimensions ---
  const NODE_BASE_W = 180;
  const NODE_BASE_H = 80;
  const NODE_PAD_W = 40;
  const NODE_PAD_H = 100;
  const LAYER_GAP = NODE_BASE_H + NODE_PAD_H;

  // Node sizing based on dependent count
  function nodeScale(id) {
    const depCount = dependentsMap.get(id).length;
    return 1 + depCount * 0.12;
  }

  // Assign positions
  const nodePositions = new Map();
  layers.forEach((layer, li) => {
    const totalWidth = layer.reduce((sum, e) => {
      const s = nodeScale(e.id);
      return sum + NODE_BASE_W * s + NODE_PAD_W;
    }, -NODE_PAD_W);
    let x = -totalWidth / 2;
    layer.forEach(e => {
      const s = nodeScale(e.id);
      const w = NODE_BASE_W * s;
      const h = NODE_BASE_H * s;
      nodePositions.set(e.id, {
        x: x + w / 2,
        y: li * LAYER_GAP,
        w, h, scale: s
      });
      x += w + NODE_PAD_W;
    });
  });

  // --- Compute ancestry chains ---
  function getAncestors(id, visited = new Set()) {
    if (visited.has(id)) return visited;
    visited.add(id);
    const entry = entryMap.get(id);
    if (entry) entry.builds_on.forEach(pid => getAncestors(pid, visited));
    return visited;
  }

  function getDescendants(id, visited = new Set()) {
    if (visited.has(id)) return visited;
    visited.add(id);
    dependentsMap.get(id).forEach(cid => getDescendants(cid, visited));
    return visited;
  }

  // --- Build edges ---
  const edges = [];
  entries.forEach(e => {
    e.builds_on.forEach(parentId => {
      if (entryMap.has(parentId)) {
        edges.push({ source: parentId, target: e.id });
      }
    });
  });

  // --- Truncate helper ---
  function truncate(str, max) {
    return str.length > max ? str.slice(0, max - 1) + "\u2026" : str;
  }

  // --- Render ---
  const container = d3.select("#" + containerId);
  container.selectAll("*").remove();

  // Inject styles
  container.style("position", "relative").style("overflow", "hidden");

  const containerRect = container.node().getBoundingClientRect();
  const width = containerRect.width || 1100;
  const height = containerRect.height || 700;

  const svg = container.append("svg")
    .attr("width", width)
    .attr("height", height)
    .style("background", "#0f172a")
    .style("font-family", "'Segoe UI', system-ui, -apple-system, sans-serif");

  // Arrowhead marker
  svg.append("defs").append("marker")
    .attr("id", "kb-arrow")
    .attr("viewBox", "0 0 10 6")
    .attr("refX", 10)
    .attr("refY", 3)
    .attr("markerWidth", 8)
    .attr("markerHeight", 6)
    .attr("orient", "auto")
    .append("path")
    .attr("d", "M0,0 L10,3 L0,6 Z")
    .attr("fill", "#64748b");

  // Highlighted arrowhead
  svg.select("defs").append("marker")
    .attr("id", "kb-arrow-hl")
    .attr("viewBox", "0 0 10 6")
    .attr("refX", 10)
    .attr("refY", 3)
    .attr("markerWidth", 8)
    .attr("markerHeight", 6)
    .attr("orient", "auto")
    .append("path")
    .attr("d", "M0,0 L10,3 L0,6 Z")
    .attr("fill", "#facc15");

  // Zoom group
  const g = svg.append("g");

  const zoom = d3.zoom()
    .scaleExtent([0.2, 3])
    .on("zoom", (event) => g.attr("transform", event.transform));

  svg.call(zoom);

  // Center the graph initially
  const allPos = Array.from(nodePositions.values());
  const minX = Math.min(...allPos.map(p => p.x - p.w / 2));
  const maxX = Math.max(...allPos.map(p => p.x + p.w / 2));
  const minY = Math.min(...allPos.map(p => p.y - p.h / 2));
  const maxY = Math.max(...allPos.map(p => p.y + p.h / 2));
  const graphW = maxX - minX;
  const graphH = maxY - minY;
  const graphCX = (minX + maxX) / 2;
  const graphCY = (minY + maxY) / 2;
  const initScale = Math.min(width / (graphW + 120), height / (graphH + 120), 1);
  const initTx = width / 2 - graphCX * initScale;
  const initTy = 60 - minY * initScale + (height - (graphH * initScale + 120)) / 2;

  svg.call(zoom.transform, d3.zoomIdentity.translate(initTx, initTy).scale(initScale));

  // --- Draw edges ---
  const edgeGroup = g.append("g").attr("class", "edges");

  function edgePath(edge) {
    const s = nodePositions.get(edge.source);
    const t = nodePositions.get(edge.target);
    const sx = s.x, sy = s.y + s.h / 2;
    const tx = t.x, ty = t.y - t.h / 2;
    const midY = (sy + ty) / 2;
    return `M${sx},${sy} C${sx},${midY} ${tx},${midY} ${tx},${ty}`;
  }

  const edgeEls = edgeGroup.selectAll("path")
    .data(edges)
    .enter().append("path")
    .attr("d", edgePath)
    .attr("fill", "none")
    .attr("stroke", "#475569")
    .attr("stroke-width", 1.5)
    .attr("marker-end", "url(#kb-arrow)")
    .attr("opacity", 0.6);

  // --- Draw nodes ---
  const nodeGroup = g.append("g").attr("class", "nodes");

  const nodes = nodeGroup.selectAll("g.node")
    .data(entries, d => d.id)
    .enter().append("g")
    .attr("class", "node")
    .attr("transform", d => {
      const p = nodePositions.get(d.id);
      return `translate(${p.x},${p.y})`;
    })
    .style("cursor", "pointer");

  // Node rectangles
  nodes.append("rect")
    .attr("x", d => -nodePositions.get(d.id).w / 2)
    .attr("y", d => -nodePositions.get(d.id).h / 2)
    .attr("width", d => nodePositions.get(d.id).w)
    .attr("height", d => nodePositions.get(d.id).h)
    .attr("rx", 10)
    .attr("ry", 10)
    .attr("fill", d => {
      const color = CATEGORY_COLORS[d.category] || "#6b7280";
      return color + "22";
    })
    .attr("stroke", d => CATEGORY_COLORS[d.category] || "#6b7280")
    .attr("stroke-width", 2);

  // ID label
  nodes.append("text")
    .attr("y", d => -nodePositions.get(d.id).h / 2 + 18)
    .attr("text-anchor", "middle")
    .attr("fill", d => CATEGORY_COLORS[d.category] || "#6b7280")
    .attr("font-size", d => 11 * nodePositions.get(d.id).scale)
    .attr("font-weight", 700)
    .text(d => d.id);

  // Title label
  nodes.append("text")
    .attr("y", d => -nodePositions.get(d.id).h / 2 + 36)
    .attr("text-anchor", "middle")
    .attr("fill", "#e2e8f0")
    .attr("font-size", d => 9.5 * nodePositions.get(d.id).scale)
    .text(d => {
      const maxChars = Math.floor(18 * nodePositions.get(d.id).scale);
      return truncate(d.title, maxChars);
    });

  // Team label
  nodes.append("text")
    .attr("y", d => -nodePositions.get(d.id).h / 2 + 52)
    .attr("text-anchor", "middle")
    .attr("fill", "#94a3b8")
    .attr("font-size", d => 8.5 * nodePositions.get(d.id).scale)
    .text(d => d.team);

  // Category label
  nodes.append("text")
    .attr("y", d => -nodePositions.get(d.id).h / 2 + 66)
    .attr("text-anchor", "middle")
    .attr("fill", d => CATEGORY_COLORS[d.category] || "#6b7280")
    .attr("font-size", d => 8 * nodePositions.get(d.id).scale)
    .attr("font-style", "italic")
    .text(d => d.category);

  // --- Tooltip ---
  const tooltip = container.append("div")
    .style("position", "absolute")
    .style("pointer-events", "none")
    .style("background", "#1e293b")
    .style("border", "1px solid #475569")
    .style("border-radius", "8px")
    .style("padding", "12px 16px")
    .style("color", "#e2e8f0")
    .style("font-family", "'Segoe UI', system-ui, sans-serif")
    .style("font-size", "13px")
    .style("line-height", "1.5")
    .style("max-width", "340px")
    .style("box-shadow", "0 8px 24px rgba(0,0,0,0.4)")
    .style("display", "none")
    .style("z-index", "10");

  nodes.on("mouseenter", function (event, d) {
    const deps = dependentsMap.get(d.id);
    const depLabels = deps.length > 0
      ? deps.map(id => `${id} (${truncate(entryMap.get(id).title, 30)})`).join("<br>  ")
      : "None";

    tooltip
      .style("display", "block")
      .html(
        `<strong style="color:${CATEGORY_COLORS[d.category]}">${d.id}</strong><br>` +
        `<strong>${d.title}</strong><br>` +
        `<span style="color:#94a3b8">Team:</span> ${d.team}<br>` +
        `<span style="color:#94a3b8">Tags:</span> ${d.tags.join(", ")}<br>` +
        `<span style="color:#94a3b8">Depended on by:</span><br>  ${depLabels}`
      );
  })
  .on("mousemove", function (event) {
    const rect = container.node().getBoundingClientRect();
    let left = event.clientX - rect.left + 16;
    let top = event.clientY - rect.top + 16;
    // Keep tooltip within container bounds
    const ttNode = tooltip.node();
    if (left + ttNode.offsetWidth > rect.width) left = event.clientX - rect.left - ttNode.offsetWidth - 8;
    if (top + ttNode.offsetHeight > rect.height) top = event.clientY - rect.top - ttNode.offsetHeight - 8;
    tooltip.style("left", left + "px").style("top", top + "px");
  })
  .on("mouseleave", function () {
    tooltip.style("display", "none");
  });

  // --- Click to highlight dependency chain ---
  let selectedId = null;

  nodes.on("click", function (event, d) {
    event.stopPropagation();

    if (selectedId === d.id) {
      // Deselect
      selectedId = null;
      resetHighlight();
      return;
    }

    selectedId = d.id;

    const ancestors = getAncestors(d.id);
    const descendants = getDescendants(d.id);
    const chain = new Set([...ancestors, ...descendants]);

    // Dim non-chain nodes
    nodes.select("rect")
      .attr("opacity", n => chain.has(n.id) ? 1 : 0.15);
    nodes.selectAll("text")
      .attr("opacity", n => chain.has(n.id) ? 1 : 0.15);

    // Highlight chain edges
    edgeEls
      .attr("opacity", e => chain.has(e.source) && chain.has(e.target) ? 1 : 0.06)
      .attr("stroke", e => chain.has(e.source) && chain.has(e.target) ? "#facc15" : "#475569")
      .attr("stroke-width", e => chain.has(e.source) && chain.has(e.target) ? 2.5 : 1.5)
      .attr("marker-end", e =>
        chain.has(e.source) && chain.has(e.target) ? "url(#kb-arrow-hl)" : "url(#kb-arrow)"
      );

    // Highlight selected node border
    nodes.select("rect")
      .attr("stroke-width", n => n.id === d.id ? 3.5 : 2)
      .attr("stroke", n => {
        if (n.id === d.id) return "#facc15";
        return CATEGORY_COLORS[n.category] || "#6b7280";
      });
  });

  function resetHighlight() {
    nodes.select("rect")
      .attr("opacity", 1)
      .attr("stroke-width", 2)
      .attr("stroke", d => CATEGORY_COLORS[d.category] || "#6b7280");
    nodes.selectAll("text").attr("opacity", 1);
    edgeEls
      .attr("opacity", 0.6)
      .attr("stroke", "#475569")
      .attr("stroke-width", 1.5)
      .attr("marker-end", "url(#kb-arrow)");
  }

  svg.on("click", () => {
    selectedId = null;
    resetHighlight();
  });

  // --- Legend ---
  const legendData = [
    { label: "optimization", color: CATEGORY_COLORS["optimization"] },
    { label: "integration", color: CATEGORY_COLORS["integration"] },
    { label: "market-research", color: CATEGORY_COLORS["market-research"] },
    { label: "methodology", color: CATEGORY_COLORS["methodology"] },
    { label: "tool-usage", color: CATEGORY_COLORS["tool-usage"] }
  ];

  const legend = svg.append("g")
    .attr("transform", `translate(16, ${height - legendData.length * 22 - 16})`);

  legend.append("rect")
    .attr("x", -8)
    .attr("y", -12)
    .attr("width", 160)
    .attr("height", legendData.length * 22 + 16)
    .attr("rx", 6)
    .attr("fill", "#1e293bee")
    .attr("stroke", "#334155")
    .attr("stroke-width", 1);

  const legendItems = legend.selectAll("g.legend-item")
    .data(legendData)
    .enter().append("g")
    .attr("class", "legend-item")
    .attr("transform", (d, i) => `translate(0, ${i * 22})`);

  legendItems.append("rect")
    .attr("width", 14)
    .attr("height", 14)
    .attr("rx", 3)
    .attr("fill", d => d.color + "44")
    .attr("stroke", d => d.color)
    .attr("stroke-width", 1.5);

  legendItems.append("text")
    .attr("x", 20)
    .attr("y", 11)
    .attr("fill", "#cbd5e1")
    .attr("font-size", 12)
    .text(d => d.label);
}
