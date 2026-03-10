/**
 * Script-to-Source Data Flow Visualization
 *
 * Renders a left-to-right flow diagram using D3.js v7 showing how scripts
 * connect to data sources, with dependency chains.
 *
 * Usage:
 *   <script src="https://d3js.org/d3.v7.min.js"></script>
 *   <script src="viz_data_flow.js"></script>
 *   <script>renderDataFlow("#container", FLOW_DATA);</script>
 */

const FLOW_DATA = {
  scripts: [
    { id: "SCR-0001", name: "validate-storage", category: "utility", uses_sources: [], depends_on: [], external: [] },
    { id: "SCR-0002", name: "test-edge-cases", category: "utility", uses_sources: [], depends_on: [], external: [] },
    { id: "SCR-0003", name: "repair-storage", category: "utility", uses_sources: [], depends_on: [], external: [] },
    { id: "SCR-0004", name: "grade-research", category: "analysis", uses_sources: [], depends_on: [], external: [] },
    { id: "SCR-0005", name: "merge-research", category: "analysis", uses_sources: [], depends_on: [], external: [] },
    { id: "SCR-0006", name: "method_websearch", category: "data-collection", uses_sources: [], depends_on: [], external: ["DuckDuckGo"] },
    { id: "SCR-0007", name: "method_wikipedia", category: "data-collection", uses_sources: ["SRC-0035"], depends_on: [], external: [] },
    { id: "SCR-0008", name: "method_arxiv", category: "data-collection", uses_sources: [], depends_on: [], external: ["arXiv"] },
    { id: "SCR-0009", name: "method_hybrid", category: "data-collection", uses_sources: [], depends_on: ["SCR-0006", "SCR-0007", "SCR-0008"], external: [] },
    { id: "SCR-0010", name: "method_iterative", category: "data-collection", uses_sources: [], depends_on: [], external: ["DuckDuckGo"] },
    { id: "SCR-0011", name: "benchmark_runner", category: "analysis", uses_sources: [], depends_on: ["SCR-0006", "SCR-0007", "SCR-0008", "SCR-0009", "SCR-0010"], external: [] },
    { id: "SCR-0012", name: "kb_validator", category: "utility", uses_sources: [], depends_on: [], external: [] },
    { id: "SCR-0013", name: "index_rebuilder", category: "utility", uses_sources: [], depends_on: [], external: [] },
    { id: "SCR-0014", name: "cross_ref_checker", category: "utility", uses_sources: [], depends_on: [], external: [] },
    { id: "SCR-0015", name: "fast_search (Go)", category: "utility", uses_sources: [], depends_on: [], external: [] },
    { id: "SCR-0016", name: "fast_validate (Go)", category: "utility", uses_sources: [], depends_on: [], external: [] },
    { id: "SCR-0017", name: "fast_cache (Go)", category: "utility", uses_sources: [], depends_on: [], external: [] },
    { id: "SCR-0018", name: "kb_search", category: "utility", uses_sources: [], depends_on: [], external: [] },
    { id: "SCR-0019", name: "handoff_tracker", category: "utility", uses_sources: [], depends_on: [], external: [] },
    { id: "SCR-0020", name: "staleness_detector", category: "utility", uses_sources: [], depends_on: ["SCR-0012", "SCR-0014", "SCR-0019"], external: [] },
    { id: "SCR-0021", name: "ddg_utils", category: "data-collection", uses_sources: [], depends_on: [], external: ["DuckDuckGo"] },
    { id: "SCR-0022", name: "request_cache", category: "data-collection", uses_sources: [], depends_on: [], external: [] },
    { id: "SCR-0023", name: "method_openalex", category: "data-collection", uses_sources: ["SRC-0012"], depends_on: ["SCR-0022"], external: [] },
    { id: "SCR-0024", name: "method_hackernews", category: "data-collection", uses_sources: ["SRC-0011"], depends_on: ["SCR-0022"], external: [] },
    { id: "SCR-0025", name: "method_guardian", category: "data-collection", uses_sources: ["SRC-0010"], depends_on: ["SCR-0021", "SCR-0022"], external: ["DuckDuckGo"] },
    { id: "SCR-0026", name: "method_semantic_scholar", category: "data-collection", uses_sources: ["SRC-0013"], depends_on: ["SCR-0022"], external: [] },
    { id: "SCR-0027", name: "method_wikidata", category: "data-collection", uses_sources: ["SRC-0014"], depends_on: ["SCR-0022"], external: [] },
    { id: "SCR-0028", name: "method_github", category: "data-collection", uses_sources: ["SRC-0025"], depends_on: ["SCR-0022"], external: [] }
  ],
  sources: [
    { id: "SRC-0001", name: "Alpha Vantage", type: "financial" },
    { id: "SRC-0002", name: "Yahoo Finance", type: "financial" },
    { id: "SRC-0003", name: "FRED", type: "financial" },
    { id: "SRC-0004", name: "CoinGecko", type: "financial" },
    { id: "SRC-0005", name: "Twelve Data", type: "financial" },
    { id: "SRC-0006", name: "Exchange Rates", type: "financial" },
    { id: "SRC-0007", name: "Finnhub", type: "financial" },
    { id: "SRC-0008", name: "NewsAPI", type: "news" },
    { id: "SRC-0009", name: "GNews", type: "news" },
    { id: "SRC-0010", name: "The Guardian", type: "news" },
    { id: "SRC-0011", name: "Hacker News", type: "news" },
    { id: "SRC-0012", name: "OpenAlex", type: "scientific" },
    { id: "SRC-0013", name: "Semantic Scholar", type: "scientific" },
    { id: "SRC-0014", name: "Wikidata", type: "scientific" },
    { id: "SRC-0015", name: "CORE", type: "scientific" },
    { id: "SRC-0016", name: "CrossRef", type: "scientific" },
    { id: "SRC-0017", name: "DBpedia", type: "scientific" },
    { id: "SRC-0018", name: "Papers With Code", type: "scientific" },
    { id: "SRC-0019", name: "US Census", type: "government" },
    { id: "SRC-0020", name: "Data.gov", type: "government" },
    { id: "SRC-0021", name: "Open-Meteo", type: "geospatial" },
    { id: "SRC-0022", name: "OpenWeatherMap", type: "geospatial" },
    { id: "SRC-0023", name: "BLS", type: "government" },
    { id: "SRC-0024", name: "World Bank", type: "government" },
    { id: "SRC-0025", name: "GitHub API", type: "general" },
    { id: "SRC-0026", name: "PyPI", type: "general" },
    { id: "SRC-0027", name: "npm Registry", type: "general" },
    { id: "SRC-0028", name: "Reddit", type: "social-media" },
    { id: "SRC-0029", name: "Stack Exchange", type: "general" },
    { id: "SRC-0030", name: "Nominatim", type: "geospatial" },
    { id: "SRC-0031", name: "Hugging Face", type: "scientific" },
    { id: "SRC-0032", name: "Free Dictionary", type: "general" },
    { id: "SRC-0033", name: "Datamuse", type: "general" },
    { id: "SRC-0034", name: "ConceptNet", type: "scientific" },
    { id: "SRC-0035", name: "Wikipedia REST", type: "general" }
  ],
  external_services: ["DuckDuckGo", "arXiv"]
};

function renderDataFlow(containerId, data) {
  // --- Configuration ---
  const margin = { top: 40, right: 40, bottom: 40, left: 40 };
  const nodeWidth = 160;
  const nodeHeight = 36;
  const nodeSpacing = 8;
  const columnGap = 280;

  const sourceTypeColors = {
    financial: "#2d9d4e",
    news: "#3b82f6",
    scientific: "#8b5cf6",
    government: "#dc2626",
    geospatial: "#92400e",
    general: "#6b7280",
    "social-media": "#f97316"
  };

  const scriptCategoryColors = {
    "data-collection": "#0d9488",
    utility: "#64748b",
    analysis: "#d97706"
  };

  // --- Classify scripts into columns ---
  const dataCollectionScripts = data.scripts.filter(s => s.category === "data-collection");
  const otherScripts = data.scripts.filter(s => s.category !== "data-collection");

  // Determine which sources are orphans (no script uses them directly)
  const usedSourceIds = new Set();
  data.scripts.forEach(s => s.uses_sources.forEach(id => usedSourceIds.add(id)));
  const sources = data.sources.map(s => ({ ...s, orphan: !usedSourceIds.has(s.id) }));

  // --- Compute layout dimensions ---
  const col0Count = otherScripts.length;
  const col1Count = dataCollectionScripts.length + data.external_services.length;
  const col2Count = sources.length;
  const maxRows = Math.max(col0Count, col1Count, col2Count);
  const contentHeight = maxRows * (nodeHeight + nodeSpacing);
  const width = margin.left + 3 * nodeWidth + 2 * columnGap + margin.right;
  const height = contentHeight + margin.top + margin.bottom;

  // Column x positions (left to right: utility/analysis, data-collection, sources)
  const colX = [
    margin.left,
    margin.left + nodeWidth + columnGap,
    margin.left + 2 * (nodeWidth + columnGap)
  ];

  // --- Build node positions ---
  const nodes = [];
  const nodeMap = {};

  function yPos(index, totalInColumn) {
    const colHeight = totalInColumn * (nodeHeight + nodeSpacing) - nodeSpacing;
    const offsetY = margin.top + (contentHeight - colHeight) / 2;
    return offsetY + index * (nodeHeight + nodeSpacing);
  }

  // Column 0: utility/analysis scripts
  otherScripts.forEach((s, i) => {
    const node = {
      ...s,
      x: colX[0],
      y: yPos(i, col0Count),
      w: nodeWidth,
      h: nodeHeight,
      color: scriptCategoryColors[s.category] || "#64748b",
      nodeType: "script",
      column: 0
    };
    nodes.push(node);
    nodeMap[s.id] = node;
  });

  // Column 1: data-collection scripts + external services
  const col1Items = [];
  dataCollectionScripts.forEach(s => col1Items.push({ ...s, isExternal: false }));
  data.external_services.forEach(name => {
    col1Items.push({
      id: `EXT-${name}`,
      name,
      category: "external",
      uses_sources: [],
      depends_on: [],
      external: [],
      isExternal: true
    });
  });

  col1Items.forEach((s, i) => {
    const node = {
      ...s,
      x: colX[1],
      y: yPos(i, col1Items.length),
      w: nodeWidth,
      h: nodeHeight,
      color: s.isExternal ? "#94a3b8" : (scriptCategoryColors[s.category] || "#0d9488"),
      nodeType: s.isExternal ? "external" : "script",
      column: 1
    };
    nodes.push(node);
    nodeMap[s.id] = node;
  });

  // Column 2: sources
  sources.forEach((s, i) => {
    const node = {
      ...s,
      x: colX[2],
      y: yPos(i, col2Count),
      w: nodeWidth,
      h: nodeHeight,
      color: sourceTypeColors[s.type] || "#6b7280",
      nodeType: "source",
      column: 2
    };
    nodes.push(node);
    nodeMap[s.id] = node;
  });

  // --- Build edges ---
  const edges = [];

  data.scripts.forEach(script => {
    const src = nodeMap[script.id];
    if (!src) return;

    // Direct script-to-source links (solid)
    script.uses_sources.forEach(targetId => {
      const tgt = nodeMap[targetId];
      if (tgt) {
        edges.push({ source: src, target: tgt, type: "direct" });
      }
    });

    // Script-to-script dependencies (dashed)
    script.depends_on.forEach(depId => {
      const tgt = nodeMap[depId];
      if (tgt) {
        edges.push({ source: src, target: tgt, type: "dependency" });
      }
    });

    // External service usage (dotted)
    script.external.forEach(extName => {
      const tgt = nodeMap[`EXT-${extName}`];
      if (tgt) {
        edges.push({ source: src, target: tgt, type: "indirect" });
      }
    });
  });

  // --- Build adjacency for highlight/click ---
  const adjacency = {};
  nodes.forEach(n => { adjacency[n.id] = new Set(); });

  edges.forEach(e => {
    adjacency[e.source.id].add(e.target.id);
    adjacency[e.target.id].add(e.source.id);
  });

  // Transitive consumers: for click on a source, find all scripts that
  // consume it directly or transitively through dependency chains.
  function findTransitiveConsumers(sourceId) {
    const consumers = new Set();
    // Direct consumers
    data.scripts.forEach(s => {
      if (s.uses_sources.includes(sourceId)) consumers.add(s.id);
    });
    // Walk dependency chains upward
    let changed = true;
    while (changed) {
      changed = false;
      data.scripts.forEach(s => {
        if (!consumers.has(s.id)) {
          const depsOverlap = s.depends_on.some(d => consumers.has(d));
          if (depsOverlap) {
            consumers.add(s.id);
            changed = true;
          }
        }
      });
    }
    return consumers;
  }

  // --- Clear container and create SVG ---
  const container = d3.select(containerId);
  container.selectAll("*").remove();
  container.style("position", "relative");

  const svg = container.append("svg")
    .attr("width", "100%")
    .attr("height", "100%")
    .attr("viewBox", `0 0 ${width} ${height}`)
    .style("font-family", "'Inter', 'Segoe UI', system-ui, -apple-system, sans-serif")
    .style("background", "#0f172a");

  // Defs for arrow markers and cloud clip path
  const defs = svg.append("defs");

  // Arrow markers for each edge type
  ["direct", "dependency", "indirect"].forEach(type => {
    const colors = { direct: "#94a3b8", dependency: "#94a3b8", indirect: "#94a3b8" };
    defs.append("marker")
      .attr("id", `arrow-${type}`)
      .attr("viewBox", "0 0 10 6")
      .attr("refX", 10)
      .attr("refY", 3)
      .attr("markerWidth", 8)
      .attr("markerHeight", 6)
      .attr("orient", "auto")
      .append("path")
      .attr("d", "M0,0 L10,3 L0,6 Z")
      .attr("fill", colors[type]);
  });

  // Highlighted arrow markers
  ["direct", "dependency", "indirect"].forEach(type => {
    defs.append("marker")
      .attr("id", `arrow-${type}-hl`)
      .attr("viewBox", "0 0 10 6")
      .attr("refX", 10)
      .attr("refY", 3)
      .attr("markerWidth", 8)
      .attr("markerHeight", 6)
      .attr("orient", "auto")
      .append("path")
      .attr("d", "M0,0 L10,3 L0,6 Z")
      .attr("fill", "#fbbf24");
  });

  // Zoom group
  const g = svg.append("g");

  const zoom = d3.zoom()
    .scaleExtent([0.2, 3])
    .on("zoom", (event) => {
      g.attr("transform", event.transform);
    });

  svg.call(zoom);

  // --- Column headers ---
  const headers = [
    { text: "Utility / Analysis Scripts", x: colX[0] + nodeWidth / 2 },
    { text: "Data Collection Scripts", x: colX[1] + nodeWidth / 2 },
    { text: "Data Sources", x: colX[2] + nodeWidth / 2 }
  ];

  g.selectAll(".col-header")
    .data(headers)
    .join("text")
    .attr("class", "col-header")
    .attr("x", d => d.x)
    .attr("y", margin.top - 16)
    .attr("text-anchor", "middle")
    .attr("fill", "#cbd5e1")
    .attr("font-size", "13px")
    .attr("font-weight", 600)
    .attr("letter-spacing", "0.5px")
    .text(d => d.text);

  // --- Draw edges ---
  function edgePath(e) {
    const sx = e.source.x + e.source.w;
    const sy = e.source.y + e.source.h / 2;
    const tx = e.target.x;
    const ty = e.target.y + e.target.h / 2;

    // If source is to the right of target, flip
    let x1 = sx, y1 = sy, x2 = tx, y2 = ty;
    if (e.source.x > e.target.x) {
      x1 = e.source.x;
      x2 = e.target.x + e.target.w;
    } else if (e.source.column === e.target.column) {
      // Same column: curve out to the left
      x1 = e.source.x;
      x2 = e.target.x;
      const midX = x1 - 40;
      return `M${x1},${y1} C${midX},${y1} ${midX},${y2} ${x2},${y2}`;
    }

    const midX = (x1 + x2) / 2;
    return `M${x1},${y1} C${midX},${y1} ${midX},${y2} ${x2},${y2}`;
  }

  function dashArray(type) {
    if (type === "dependency") return "6,4";
    if (type === "indirect") return "2,4";
    return "none";
  }

  const edgeGroup = g.append("g").attr("class", "edges");

  const edgePaths = edgeGroup.selectAll(".edge")
    .data(edges)
    .join("path")
    .attr("class", d => `edge edge-${d.type}`)
    .attr("d", edgePath)
    .attr("fill", "none")
    .attr("stroke", "#475569")
    .attr("stroke-width", 1.5)
    .attr("stroke-dasharray", d => dashArray(d.type))
    .attr("marker-end", d => `url(#arrow-${d.type})`)
    .attr("opacity", 0.6);

  // --- Draw nodes ---
  const nodeGroup = g.append("g").attr("class", "nodes");

  const nodeGs = nodeGroup.selectAll(".node")
    .data(nodes)
    .join("g")
    .attr("class", d => `node node-${d.nodeType}`)
    .attr("transform", d => `translate(${d.x},${d.y})`)
    .style("cursor", "pointer");

  // Node rectangles
  nodeGs.each(function (d) {
    const el = d3.select(this);

    if (d.nodeType === "external") {
      // Cloud-like shape: rounded rect with dashed border
      el.append("rect")
        .attr("width", d.w)
        .attr("height", d.h)
        .attr("rx", d.h / 2)
        .attr("ry", d.h / 2)
        .attr("fill", "#1e293b")
        .attr("stroke", d.color)
        .attr("stroke-width", 1.5)
        .attr("stroke-dasharray", "4,3")
        .attr("opacity", 1);
    } else {
      el.append("rect")
        .attr("width", d.w)
        .attr("height", d.h)
        .attr("rx", 6)
        .attr("ry", 6)
        .attr("fill", "#1e293b")
        .attr("stroke", d.color)
        .attr("stroke-width", 1.5)
        .attr("opacity", d.orphan ? 0.35 : 1);
    }

    // Label
    el.append("text")
      .attr("x", d.w / 2)
      .attr("y", d.h / 2)
      .attr("text-anchor", "middle")
      .attr("dominant-baseline", "central")
      .attr("fill", d.orphan ? "#64748b" : "#e2e8f0")
      .attr("font-size", "11px")
      .attr("font-weight", 500)
      .text(d.name);
  });

  // --- Tooltip ---
  const tooltip = container.append("div")
    .style("position", "absolute")
    .style("pointer-events", "none")
    .style("background", "#1e293b")
    .style("border", "1px solid #334155")
    .style("border-radius", "6px")
    .style("padding", "8px 12px")
    .style("font-size", "12px")
    .style("color", "#e2e8f0")
    .style("font-family", "'Inter', sans-serif")
    .style("box-shadow", "0 4px 12px rgba(0,0,0,0.4)")
    .style("display", "none")
    .style("z-index", "10");

  // --- Interaction state ---
  let clickedSourceId = null;

  // Hover behavior
  nodeGs.on("mouseenter", function (event, d) {
    if (clickedSourceId) return; // Don't override click highlight

    const connected = adjacency[d.id];

    // Dim everything
    nodeGs.attr("opacity", n => (n.id === d.id || connected.has(n.id)) ? 1 : 0.15);
    edgePaths
      .attr("opacity", e =>
        (e.source.id === d.id || e.target.id === d.id) ? 1 : 0.05)
      .attr("stroke", e =>
        (e.source.id === d.id || e.target.id === d.id) ? "#fbbf24" : "#475569")
      .attr("stroke-width", e =>
        (e.source.id === d.id || e.target.id === d.id) ? 2.5 : 1.5)
      .attr("marker-end", e =>
        (e.source.id === d.id || e.target.id === d.id)
          ? `url(#arrow-${e.type}-hl)` : `url(#arrow-${e.type})`);

    // Tooltip
    const typeLabel = d.nodeType === "source" ? d.type
      : d.nodeType === "external" ? "external service"
      : d.category;
    tooltip
      .html(`<strong>${d.name}</strong><br/><span style="color:#94a3b8">${d.id} &middot; ${typeLabel}</span>`)
      .style("display", "block")
      .style("left", (event.offsetX + 14) + "px")
      .style("top", (event.offsetY - 10) + "px");
  });

  nodeGs.on("mousemove", function (event) {
    tooltip
      .style("left", (event.offsetX + 14) + "px")
      .style("top", (event.offsetY - 10) + "px");
  });

  nodeGs.on("mouseleave", function () {
    if (clickedSourceId) return;
    resetHighlight();
    tooltip.style("display", "none");
  });

  // Click on source: show all transitive consumers
  nodeGs.on("click", function (event, d) {
    event.stopPropagation();

    if (d.nodeType !== "source") {
      // Click on non-source clears selection
      clickedSourceId = null;
      resetHighlight();
      return;
    }

    if (clickedSourceId === d.id) {
      clickedSourceId = null;
      resetHighlight();
      return;
    }

    clickedSourceId = d.id;
    const consumers = findTransitiveConsumers(d.id);
    consumers.add(d.id);

    // Build set of relevant edges
    const relevantEdgeSet = new Set();
    edges.forEach((e, i) => {
      if (consumers.has(e.source.id) && consumers.has(e.target.id)) {
        relevantEdgeSet.add(i);
      }
      // Also include direct link to this source
      if (e.target.id === d.id && consumers.has(e.source.id)) {
        relevantEdgeSet.add(i);
      }
    });

    nodeGs.attr("opacity", n => consumers.has(n.id) ? 1 : 0.1);
    edgePaths
      .attr("opacity", (e, i) => relevantEdgeSet.has(i) ? 1 : 0.03)
      .attr("stroke", (e, i) => relevantEdgeSet.has(i) ? "#fbbf24" : "#475569")
      .attr("stroke-width", (e, i) => relevantEdgeSet.has(i) ? 2.5 : 1.5)
      .attr("marker-end", (e, i) =>
        relevantEdgeSet.has(i)
          ? `url(#arrow-${e.type}-hl)` : `url(#arrow-${e.type})`);

    tooltip
      .html(`<strong>${d.name}</strong><br/><span style="color:#94a3b8">${consumers.size - 1} consuming script(s)</span>`)
      .style("display", "block")
      .style("left", (event.offsetX + 14) + "px")
      .style("top", (event.offsetY - 10) + "px");
  });

  // Click on background clears
  svg.on("click", () => {
    clickedSourceId = null;
    resetHighlight();
    tooltip.style("display", "none");
  });

  function resetHighlight() {
    nodeGs.attr("opacity", d => d.orphan ? 0.35 : 1);
    edgePaths
      .attr("opacity", 0.6)
      .attr("stroke", "#475569")
      .attr("stroke-width", 1.5)
      .attr("marker-end", d => `url(#arrow-${d.type})`);
  }

  // --- Legend ---
  const legendData = {
    edgeTypes: [
      { label: "Direct source link", dash: "none", type: "direct" },
      { label: "Script dependency", dash: "6,4", type: "dependency" },
      { label: "Indirect / external", dash: "2,4", type: "indirect" }
    ],
    sourceColors: [
      { label: "Financial", color: sourceTypeColors.financial },
      { label: "News", color: sourceTypeColors.news },
      { label: "Scientific", color: sourceTypeColors.scientific },
      { label: "Government", color: sourceTypeColors.government },
      { label: "Geospatial", color: sourceTypeColors.geospatial },
      { label: "General", color: sourceTypeColors.general },
      { label: "Social Media", color: sourceTypeColors["social-media"] }
    ],
    scriptColors: [
      { label: "Data Collection", color: scriptCategoryColors["data-collection"] },
      { label: "Utility", color: scriptCategoryColors.utility },
      { label: "Analysis", color: scriptCategoryColors.analysis }
    ]
  };

  const legend = g.append("g")
    .attr("transform", `translate(${margin.left}, ${height - 30})`);

  const legendBg = legend.append("rect")
    .attr("x", -10)
    .attr("y", -14)
    .attr("rx", 6)
    .attr("fill", "#1e293b")
    .attr("stroke", "#334155")
    .attr("stroke-width", 1);

  let lx = 0;
  const ly = 0;
  const itemGap = 18;

  // Edge type legend
  legend.append("text")
    .attr("x", lx).attr("y", ly)
    .attr("fill", "#94a3b8").attr("font-size", "10px").attr("font-weight", 700)
    .text("EDGES:");
  lx += 48;

  legendData.edgeTypes.forEach(item => {
    legend.append("line")
      .attr("x1", lx).attr("y1", ly - 3)
      .attr("x2", lx + 24).attr("y2", ly - 3)
      .attr("stroke", "#94a3b8").attr("stroke-width", 1.5)
      .attr("stroke-dasharray", item.dash === "none" ? null : item.dash);
    lx += 28;
    legend.append("text")
      .attr("x", lx).attr("y", ly)
      .attr("fill", "#cbd5e1").attr("font-size", "10px")
      .text(item.label);
    lx += item.label.length * 6 + itemGap;
  });

  lx += 10;

  // Source colors
  legend.append("text")
    .attr("x", lx).attr("y", ly)
    .attr("fill", "#94a3b8").attr("font-size", "10px").attr("font-weight", 700)
    .text("SOURCES:");
  lx += 62;

  legendData.sourceColors.forEach(item => {
    legend.append("rect")
      .attr("x", lx).attr("y", ly - 9)
      .attr("width", 10).attr("height", 10).attr("rx", 2)
      .attr("fill", item.color);
    lx += 14;
    legend.append("text")
      .attr("x", lx).attr("y", ly)
      .attr("fill", "#cbd5e1").attr("font-size", "10px")
      .text(item.label);
    lx += item.label.length * 5.6 + 10;
  });

  lx += 10;

  // Script colors
  legend.append("text")
    .attr("x", lx).attr("y", ly)
    .attr("fill", "#94a3b8").attr("font-size", "10px").attr("font-weight", 700)
    .text("SCRIPTS:");
  lx += 58;

  legendData.scriptColors.forEach(item => {
    legend.append("rect")
      .attr("x", lx).attr("y", ly - 9)
      .attr("width", 10).attr("height", 10).attr("rx", 2)
      .attr("fill", item.color);
    lx += 14;
    legend.append("text")
      .attr("x", lx).attr("y", ly)
      .attr("fill", "#cbd5e1").attr("font-size", "10px")
      .text(item.label);
    lx += item.label.length * 5.6 + 10;
  });

  // Size legend background to fit
  legendBg
    .attr("width", lx + 10)
    .attr("height", 28);
}
