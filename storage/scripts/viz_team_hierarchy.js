/**
 * viz_team_hierarchy.js
 *
 * Renders an interactive D3.js v7 force-directed graph showing team hierarchy,
 * agent membership, and knowledge dependencies.
 *
 * Usage:
 *   <script src="https://d3js.org/d3.v7.min.js"></script>
 *   <script src="viz_team_hierarchy.js"></script>
 *   <div id="graph"></div>
 *   <script>renderTeamHierarchy("graph", TEAM_DATA);</script>
 */

const TEAM_DATA = {
  teams: [
    { id: "TEAM-0001", objective: "Stress test storage system", phase: 1,
      members: ["coordinator", "team-lead-1", "team-lead-2", "team-lead-3", "team-lead-4", "sub-agent-alpha-x4", "sub-agent-beta-x4", "sub-agent-gamma-x4"],
      builds_on: [], kb_output: "KB-0001" },
    { id: "TEAM-0002", objective: "Build Sonnet Research API", phase: 2,
      members: ["coordinator", "team-a-lead", "team-a-sub-1", "team-a-sub-2", "team-b-lead", "team-b-sub-1", "team-b-sub-2"],
      builds_on: ["KB-0001"], kb_output: "KB-0002" },
    { id: "TEAM-0003", objective: "Competitive research: Quantitative Stock Analysis with AI", phase: 2,
      members: ["coordinator", "team1-lead", "team1-agents-x8", "team2-lead", "team2-agents-x4", "team3-lead", "team3-agents-x3", "team4-lead", "team4-agents-x3", "team5-lead", "team5-agents-x4"],
      builds_on: ["KB-0001", "KB-0002"], kb_output: "KB-0003" },
    { id: "TEAM-0004", objective: "Continuous improvement loop for data gathering", phase: 3,
      members: ["coordinator", "team1a-lead", "team1a-agents-x4", "team1b-lead", "team1b-agents-x4", "team2-lead", "team2-agents-x4", "team3-lead", "team3-agents-x4"],
      builds_on: ["KB-0002", "KB-0003"], kb_output: "KB-0004" },
    { id: "TEAM-0005", objective: "Iteration 2 data gathering improvements", phase: 3,
      members: ["software-engineer"],
      builds_on: ["KB-0004"], kb_output: "KB-0005", parent_team: "TEAM-0004" },
    { id: "TEAM-0006", objective: "Build archive validation tools", phase: 3,
      members: ["agent-a1", "agent-a2", "agent-a3"],
      builds_on: ["KB-0001"], kb_output: "KB-0006" },
    { id: "TEAM-0007", objective: "Test research team configurations", phase: 3,
      members: ["coordinator", "research-config-1", "research-config-2", "research-config-3", "research-config-4", "coding-team-a", "coding-team-b", "archive-team"],
      builds_on: ["KB-0004", "KB-0005", "KB-0006"], kb_output: "KB-0007" },
    { id: "TEAM-0008", objective: "Research data storage methods", phase: 3,
      members: ["coordinator", "research-lead-R1", "research-lead-R2", "research-lead-R3", "subagent-R1a", "subagent-R1b", "subagent-R2a", "subagent-R2b", "subagent-R3a", "subagent-R3b"],
      builds_on: ["KB-0001", "KB-0007"], kb_output: "KB-0008" },
    { id: "TEAM-0009", objective: "Multi-team operation: Go tools + archive optimization", phase: 3,
      members: ["coordinator", "research-lead", "prog-team-a", "prog-team-b", "prog-team-c", "archive-team-1", "archive-team-2", "archive-team-3"],
      builds_on: ["KB-0006", "KB-0007", "KB-0008"], kb_output: "KB-0009" },
    { id: "TEAM-0010", objective: "Research free APIs: developer tools", phase: 4,
      members: ["research-team-5"],
      builds_on: ["KB-0004", "KB-0005"], kb_output: "KB-0012" },
    { id: "TEAM-0012", objective: "Research free APIs: AI/ML self-improvement", phase: 4,
      members: ["research-agent-6"],
      builds_on: ["KB-0004", "KB-0005", "KB-0008"], kb_output: "KB-0010" },
    { id: "TEAM-0013", objective: "Research free APIs: government/open data", phase: 4,
      members: ["research-team-4"],
      builds_on: ["KB-0004", "KB-0005"], kb_output: "KB-0013" },
    { id: "TEAM-0014", objective: "Operation 4: Build public API data-gathering tools", phase: 4,
      members: ["coordinator", "research-team-1", "research-team-2", "research-team-3", "research-team-4", "research-team-5", "research-team-6", "sw-team-2", "sw-team-3"],
      builds_on: ["KB-0004", "KB-0005", "KB-0009", "KB-0010"], kb_output: "KB-0014" }
  ]
};

function renderTeamHierarchy(containerId, data) {
  const container = document.getElementById(containerId);
  if (!container) {
    console.error(`Container #${containerId} not found`);
    return;
  }

  // --- Configuration ---
  const PHASE_COLORS = {
    1: "#4a90d9",  // blue - Infrastructure
    2: "#50b86c",  // green - API/Research
    3: "#e8923e",  // orange - Optimization
    4: "#9b59b6",  // purple - Data Integration
  };

  const PHASE_LABELS = {
    1: "Phase 1: Infrastructure",
    2: "Phase 2: API/Research",
    3: "Phase 3: Optimization",
    4: "Phase 4: Data Integration",
  };

  const TEAM_RADIUS = 28;
  const LEAD_RADIUS = 12;
  const SUB_RADIUS = 8;
  const COORDINATOR_RADIUS = 14;

  // --- Parse members into structured node data ---
  function classifyMember(name) {
    const lower = name.toLowerCase();
    if (lower === "coordinator") return "coordinator";
    if (lower.includes("lead")) return "lead";
    return "sub-agent";
  }

  function parseMultiplier(name) {
    const match = name.match(/-x(\d+)$/);
    return match ? parseInt(match[1], 10) : 1;
  }

  // --- Build graph data ---
  function buildGraph(teamData) {
    const nodes = [];
    const links = [];
    const expandedTeams = new Set();
    const kbToTeam = new Map();

    // Map kb_output to team id for knowledge edges
    for (const team of teamData.teams) {
      kbToTeam.set(team.kb_output, team.id);
    }

    // Create team nodes
    for (const team of teamData.teams) {
      nodes.push({
        id: team.id,
        type: "team",
        label: team.id,
        objective: team.objective,
        phase: team.phase,
        color: PHASE_COLORS[team.phase],
        radius: TEAM_RADIUS,
        memberCount: team.members.length,
        members: team.members,
        parentTeam: team.parent_team || null,
        kbOutput: team.kb_output,
      });

      // Create member nodes (hidden initially)
      for (const member of team.members) {
        const role = classifyMember(member);
        const count = parseMultiplier(member);
        const memberId = `${team.id}::${member}`;

        nodes.push({
          id: memberId,
          type: "member",
          role,
          label: member,
          count,
          teamId: team.id,
          phase: team.phase,
          color: PHASE_COLORS[team.phase],
          radius: role === "coordinator" ? COORDINATOR_RADIUS
                : role === "lead" ? LEAD_RADIUS
                : SUB_RADIUS,
          visible: false,
        });

        links.push({
          source: team.id,
          target: memberId,
          type: "membership",
          visible: false,
        });
      }

      // Knowledge dependency edges
      for (const kb of team.builds_on) {
        const sourceTeam = kbToTeam.get(kb);
        if (sourceTeam) {
          links.push({
            source: sourceTeam,
            target: team.id,
            type: "knowledge",
            label: kb,
            visible: true,
          });
        }
      }

      // Parent-child edge
      if (team.parent_team) {
        links.push({
          source: team.parent_team,
          target: team.id,
          type: "parent-child",
          visible: true,
        });
      }
    }

    return { nodes, links, expandedTeams };
  }

  const graph = buildGraph(data);

  // --- Dimensions ---
  const width = container.clientWidth || 960;
  const height = container.clientHeight || 700;

  // --- Clear container and create SVG ---
  container.innerHTML = "";
  container.style.position = "relative";

  const svg = d3.select(container)
    .append("svg")
    .attr("width", "100%")
    .attr("height", height)
    .attr("viewBox", [0, 0, width, height])
    .style("background", "#0d1117")
    .style("border-radius", "8px");

  // Defs for arrowheads and filters
  const defs = svg.append("defs");

  // Arrow marker for knowledge edges
  defs.append("marker")
    .attr("id", "arrow-knowledge")
    .attr("viewBox", "0 -5 10 10")
    .attr("refX", 35)
    .attr("refY", 0)
    .attr("markerWidth", 6)
    .attr("markerHeight", 6)
    .attr("orient", "auto")
    .append("path")
    .attr("d", "M0,-5L10,0L0,5")
    .attr("fill", "#555");

  // Arrow marker for parent-child edges
  defs.append("marker")
    .attr("id", "arrow-parent")
    .attr("viewBox", "0 -5 10 10")
    .attr("refX", 35)
    .attr("refY", 0)
    .attr("markerWidth", 8)
    .attr("markerHeight", 8)
    .attr("orient", "auto")
    .append("path")
    .attr("d", "M0,-5L10,0L0,5")
    .attr("fill", "#e8923e");

  // Drop shadow filter
  const filter = defs.append("filter")
    .attr("id", "drop-shadow")
    .attr("x", "-50%").attr("y", "-50%")
    .attr("width", "200%").attr("height", "200%");
  filter.append("feDropShadow")
    .attr("dx", 0).attr("dy", 2)
    .attr("stdDeviation", 3)
    .attr("flood-color", "#000")
    .attr("flood-opacity", 0.4);

  // Zoom group
  const g = svg.append("g");

  const zoom = d3.zoom()
    .scaleExtent([0.2, 4])
    .on("zoom", (event) => g.attr("transform", event.transform));

  svg.call(zoom);

  // --- Tooltip ---
  const tooltip = d3.select(container)
    .append("div")
    .style("position", "absolute")
    .style("pointer-events", "none")
    .style("background", "rgba(13, 17, 23, 0.95)")
    .style("border", "1px solid #30363d")
    .style("border-radius", "6px")
    .style("padding", "10px 14px")
    .style("font-family", "'Segoe UI', system-ui, sans-serif")
    .style("font-size", "13px")
    .style("color", "#c9d1d9")
    .style("max-width", "320px")
    .style("box-shadow", "0 4px 12px rgba(0,0,0,0.4)")
    .style("opacity", 0)
    .style("z-index", 1000);

  // --- Get visible nodes and links ---
  function getVisibleNodes() {
    return graph.nodes.filter(n => n.type === "team" || n.visible);
  }

  function getVisibleLinks() {
    return graph.links.filter(l => l.visible);
  }

  // --- Simulation ---
  let simulation;

  function initSimulation(nodes, links) {
    if (simulation) simulation.stop();

    simulation = d3.forceSimulation(nodes)
      .force("link", d3.forceLink(links).id(d => d.id).distance(d => {
        if (d.type === "membership") return 60;
        if (d.type === "parent-child") return 100;
        return 180;
      }).strength(d => {
        if (d.type === "membership") return 0.8;
        if (d.type === "parent-child") return 0.5;
        return 0.15;
      }))
      .force("charge", d3.forceManyBody().strength(d =>
        d.type === "team" ? -400 : -80
      ))
      .force("center", d3.forceCenter(width / 2, height / 2).strength(0.05))
      .force("collision", d3.forceCollide().radius(d => d.radius + 8))
      .force("x", d3.forceX(width / 2).strength(0.03))
      .force("y", d3.forceY(height / 2).strength(0.03))
      .alphaDecay(0.02)
      .on("tick", ticked);

    return simulation;
  }

  // --- Rendering ---
  const linkGroup = g.append("g").attr("class", "links");
  const nodeGroup = g.append("g").attr("class", "nodes");
  const labelGroup = g.append("g").attr("class", "labels");

  function render() {
    const visibleNodes = getVisibleNodes();
    const visibleLinks = getVisibleLinks();

    // --- Links ---
    const linkSel = linkGroup.selectAll("line")
      .data(visibleLinks, d => `${d.source.id || d.source}-${d.target.id || d.target}`);

    linkSel.exit().transition().duration(300).attr("opacity", 0).remove();

    const linkEnter = linkSel.enter()
      .append("line")
      .attr("opacity", 0);

    const linkMerge = linkEnter.merge(linkSel)
      .attr("stroke", d => {
        if (d.type === "parent-child") return PHASE_COLORS[3];
        if (d.type === "knowledge") return "#30363d";
        return d3.color(PHASE_COLORS[
          (graph.nodes.find(n => n.id === (d.source.id || d.source)) || {}).phase || 1
        ]).copy({ opacity: 0.4 });
      })
      .attr("stroke-width", d => {
        if (d.type === "parent-child") return 2.5;
        if (d.type === "knowledge") return 1.5;
        return 1;
      })
      .attr("stroke-dasharray", d => {
        if (d.type === "knowledge") return "6,4";
        if (d.type === "parent-child") return "8,4";
        return "none";
      })
      .attr("marker-end", d => {
        if (d.type === "knowledge") return "url(#arrow-knowledge)";
        if (d.type === "parent-child") return "url(#arrow-parent)";
        return null;
      })
      .transition().duration(400).attr("opacity", 1);

    // --- Nodes ---
    const nodeSel = nodeGroup.selectAll(".node-group")
      .data(visibleNodes, d => d.id);

    nodeSel.exit().transition().duration(300).attr("opacity", 0).remove();

    const nodeEnter = nodeSel.enter()
      .append("g")
      .attr("class", "node-group")
      .attr("opacity", 0)
      .style("cursor", d => d.type === "team" ? "pointer" : "default")
      .call(d3.drag()
        .on("start", dragStarted)
        .on("drag", dragged)
        .on("end", dragEnded)
      );

    // Team nodes: circles
    nodeEnter.filter(d => d.type === "team")
      .append("circle")
      .attr("r", d => d.radius)
      .attr("fill", d => d.color)
      .attr("stroke", d => d3.color(d.color).brighter(0.8))
      .attr("stroke-width", 2.5)
      .attr("filter", "url(#drop-shadow)");

    // Member nodes
    nodeEnter.filter(d => d.type === "member" && d.role === "coordinator")
      .append("polygon")
      .attr("points", d => {
        const r = d.radius;
        return `0,${-r} ${r},0 0,${r} ${-r},0`;
      })
      .attr("fill", "#f0c040")
      .attr("stroke", "#d4a017")
      .attr("stroke-width", 1.5);

    nodeEnter.filter(d => d.type === "member" && d.role === "lead")
      .append("circle")
      .attr("r", d => d.radius)
      .attr("fill", d => d3.color(d.color).brighter(0.6))
      .attr("stroke", d => d3.color(d.color).brighter(1.0))
      .attr("stroke-width", 1.5);

    nodeEnter.filter(d => d.type === "member" && d.role === "sub-agent")
      .append("circle")
      .attr("r", d => d.radius)
      .attr("fill", d => d3.color(d.color).brighter(1.2))
      .attr("stroke", d => d3.color(d.color).brighter(1.6))
      .attr("stroke-width", 1);

    // Count badge for "xN" members
    const badgeNodes = nodeEnter.filter(d => d.type === "member" && d.count > 1);
    badgeNodes.append("circle")
      .attr("cx", d => d.radius * 0.7)
      .attr("cy", d => -d.radius * 0.7)
      .attr("r", 8)
      .attr("fill", "#e74c3c")
      .attr("stroke", "#0d1117")
      .attr("stroke-width", 1.5);
    badgeNodes.append("text")
      .attr("x", d => d.radius * 0.7)
      .attr("y", d => -d.radius * 0.7)
      .attr("text-anchor", "middle")
      .attr("dominant-baseline", "central")
      .attr("fill", "#fff")
      .attr("font-size", "9px")
      .attr("font-weight", "bold")
      .text(d => `x${d.count}`);

    // Member count badge on team nodes
    nodeEnter.filter(d => d.type === "team")
      .append("text")
      .attr("text-anchor", "middle")
      .attr("dominant-baseline", "central")
      .attr("fill", "#fff")
      .attr("font-size", "11px")
      .attr("font-weight", "bold")
      .attr("class", "team-count")
      .text(d => d.memberCount);

    // Hover and click
    nodeEnter
      .on("mouseover", (event, d) => showTooltip(event, d))
      .on("mousemove", (event) => moveTooltip(event))
      .on("mouseout", () => hideTooltip())
      .on("click", (event, d) => {
        if (d.type === "team") toggleTeam(d.id);
      });

    nodeEnter.transition().duration(400).attr("opacity", 1);

    // --- Labels for team nodes ---
    const labelSel = labelGroup.selectAll(".team-label")
      .data(visibleNodes.filter(n => n.type === "team"), d => d.id);

    labelSel.exit().remove();

    const labelEnter = labelSel.enter()
      .append("text")
      .attr("class", "team-label")
      .attr("text-anchor", "middle")
      .attr("dy", d => d.radius + 16)
      .attr("fill", "#8b949e")
      .attr("font-size", "11px")
      .attr("font-family", "'Segoe UI', system-ui, sans-serif")
      .attr("pointer-events", "none")
      .text(d => d.label);

    // Member labels (only for expanded)
    const memberLabelSel = labelGroup.selectAll(".member-label")
      .data(visibleNodes.filter(n => n.type === "member"), d => d.id);

    memberLabelSel.exit().transition().duration(300).attr("opacity", 0).remove();

    memberLabelSel.enter()
      .append("text")
      .attr("class", "member-label")
      .attr("text-anchor", "start")
      .attr("dx", d => d.radius + 4)
      .attr("dy", 3)
      .attr("fill", "#6e7681")
      .attr("font-size", "9px")
      .attr("font-family", "'Segoe UI', system-ui, sans-serif")
      .attr("pointer-events", "none")
      .attr("opacity", 0)
      .text(d => d.label)
      .transition().duration(400).attr("opacity", 1);

    // Restart simulation
    initSimulation(visibleNodes, visibleLinks);
  }

  function ticked() {
    linkGroup.selectAll("line")
      .attr("x1", d => d.source.x)
      .attr("y1", d => d.source.y)
      .attr("x2", d => d.target.x)
      .attr("y2", d => d.target.y);

    nodeGroup.selectAll(".node-group")
      .attr("transform", d => `translate(${d.x},${d.y})`);

    labelGroup.selectAll(".team-label")
      .attr("x", d => d.x)
      .attr("y", d => d.y);

    labelGroup.selectAll(".member-label")
      .attr("x", d => d.x)
      .attr("y", d => d.y);
  }

  // --- Toggle expand/collapse ---
  function toggleTeam(teamId) {
    const isExpanded = graph.expandedTeams.has(teamId);

    if (isExpanded) {
      graph.expandedTeams.delete(teamId);
      graph.nodes.forEach(n => {
        if (n.teamId === teamId) n.visible = false;
      });
      graph.links.forEach(l => {
        if (l.type === "membership") {
          const targetId = l.target.id || l.target;
          if (targetId.startsWith(teamId + "::")) l.visible = false;
        }
      });
    } else {
      graph.expandedTeams.add(teamId);
      // Position members near their team node
      const teamNode = graph.nodes.find(n => n.id === teamId);
      graph.nodes.forEach(n => {
        if (n.teamId === teamId) {
          n.visible = true;
          n.x = teamNode.x + (Math.random() - 0.5) * 60;
          n.y = teamNode.y + (Math.random() - 0.5) * 60;
        }
      });
      graph.links.forEach(l => {
        if (l.type === "membership") {
          const targetId = l.target.id || l.target;
          if (targetId.startsWith(teamId + "::")) l.visible = true;
        }
      });
    }

    render();
  }

  // --- Drag ---
  function dragStarted(event, d) {
    if (!event.active) simulation.alphaTarget(0.3).restart();
    d.fx = d.x;
    d.fy = d.y;
  }

  function dragged(event, d) {
    d.fx = event.x;
    d.fy = event.y;
  }

  function dragEnded(event, d) {
    if (!event.active) simulation.alphaTarget(0);
    d.fx = null;
    d.fy = null;
  }

  // --- Tooltip ---
  function showTooltip(event, d) {
    let html = "";
    if (d.type === "team") {
      const expanded = graph.expandedTeams.has(d.id);
      html = `
        <div style="font-weight:bold;color:${d.color};margin-bottom:4px;">${d.id}</div>
        <div style="margin-bottom:6px;">${d.objective}</div>
        <div style="color:#8b949e;font-size:11px;">${PHASE_LABELS[d.phase]}</div>
        <div style="color:#8b949e;font-size:11px;margin-top:2px;">Output: ${d.kbOutput}</div>
        <div style="color:#8b949e;font-size:11px;margin-top:2px;">Members: ${d.memberCount}</div>
        <div style="color:#58a6ff;font-size:11px;margin-top:6px;">Click to ${expanded ? "collapse" : "expand"} members</div>
      `;
    } else {
      const roleLabel = d.role === "coordinator" ? "Coordinator"
                      : d.role === "lead" ? "Team Lead"
                      : "Sub-agent";
      const countInfo = d.count > 1 ? ` (x${d.count} instances)` : "";
      html = `
        <div style="font-weight:bold;color:${d.role === 'coordinator' ? '#f0c040' : d.color};">${d.label}</div>
        <div style="color:#8b949e;font-size:11px;margin-top:2px;">${roleLabel}${countInfo}</div>
        <div style="color:#8b949e;font-size:11px;">Team: ${d.teamId}</div>
      `;
    }
    tooltip.html(html).style("opacity", 1);
    moveTooltip(event);
  }

  function moveTooltip(event) {
    const rect = container.getBoundingClientRect();
    const x = event.clientX - rect.left + 16;
    const y = event.clientY - rect.top - 10;
    tooltip.style("left", x + "px").style("top", y + "px");
  }

  function hideTooltip() {
    tooltip.style("opacity", 0);
  }

  // --- Legend ---
  function renderLegend() {
    const legend = svg.append("g")
      .attr("transform", "translate(16, 16)");

    const bg = legend.append("rect")
      .attr("rx", 6).attr("ry", 6)
      .attr("fill", "rgba(13, 17, 23, 0.85)")
      .attr("stroke", "#30363d")
      .attr("stroke-width", 1);

    let yOff = 16;
    const items = [];

    // Title
    legend.append("text")
      .attr("x", 12).attr("y", yOff)
      .attr("fill", "#c9d1d9")
      .attr("font-size", "12px")
      .attr("font-weight", "bold")
      .attr("font-family", "'Segoe UI', system-ui, sans-serif")
      .text("Legend");
    yOff += 22;

    // Phase colors
    for (const [phase, label] of Object.entries(PHASE_LABELS)) {
      legend.append("circle")
        .attr("cx", 20).attr("cy", yOff)
        .attr("r", 7)
        .attr("fill", PHASE_COLORS[phase]);
      legend.append("text")
        .attr("x", 34).attr("y", yOff + 4)
        .attr("fill", "#8b949e")
        .attr("font-size", "11px")
        .attr("font-family", "'Segoe UI', system-ui, sans-serif")
        .text(label);
      yOff += 20;
    }

    yOff += 6;

    // Coordinator (diamond)
    legend.append("polygon")
      .attr("points", `14,${yOff - 6} 20,${yOff} 14,${yOff + 6} 8,${yOff}`)
      .attr("fill", "#f0c040")
      .attr("stroke", "#d4a017")
      .attr("stroke-width", 1);
    legend.append("text")
      .attr("x", 34).attr("y", yOff + 4)
      .attr("fill", "#8b949e")
      .attr("font-size", "11px")
      .attr("font-family", "'Segoe UI', system-ui, sans-serif")
      .text("Coordinator");
    yOff += 20;

    // Lead
    legend.append("circle")
      .attr("cx", 14).attr("cy", yOff)
      .attr("r", 6)
      .attr("fill", "#7cc48e")
      .attr("stroke", "#a0dbb0")
      .attr("stroke-width", 1);
    legend.append("text")
      .attr("x", 34).attr("y", yOff + 4)
      .attr("fill", "#8b949e")
      .attr("font-size", "11px")
      .attr("font-family", "'Segoe UI', system-ui, sans-serif")
      .text("Team Lead");
    yOff += 20;

    // Sub-agent
    legend.append("circle")
      .attr("cx", 14).attr("cy", yOff)
      .attr("r", 4)
      .attr("fill", "#a8d8b8")
      .attr("stroke", "#c8f0d0")
      .attr("stroke-width", 1);
    legend.append("text")
      .attr("x", 34).attr("y", yOff + 4)
      .attr("fill", "#8b949e")
      .attr("font-size", "11px")
      .attr("font-family", "'Segoe UI', system-ui, sans-serif")
      .text("Sub-agent");
    yOff += 24;

    // Edge types
    // Knowledge dependency
    legend.append("line")
      .attr("x1", 6).attr("y1", yOff)
      .attr("x2", 26).attr("y2", yOff)
      .attr("stroke", "#30363d")
      .attr("stroke-width", 1.5)
      .attr("stroke-dasharray", "6,4");
    legend.append("text")
      .attr("x", 34).attr("y", yOff + 4)
      .attr("fill", "#8b949e")
      .attr("font-size", "11px")
      .attr("font-family", "'Segoe UI', system-ui, sans-serif")
      .text("Knowledge dependency");
    yOff += 20;

    // Parent-child
    legend.append("line")
      .attr("x1", 6).attr("y1", yOff)
      .attr("x2", 26).attr("y2", yOff)
      .attr("stroke", PHASE_COLORS[3])
      .attr("stroke-width", 2.5)
      .attr("stroke-dasharray", "8,4");
    legend.append("text")
      .attr("x", 34).attr("y", yOff + 4)
      .attr("fill", "#8b949e")
      .attr("font-size", "11px")
      .attr("font-family", "'Segoe UI', system-ui, sans-serif")
      .text("Parent-child team");
    yOff += 20;

    // Count badge
    legend.append("circle")
      .attr("cx", 14).attr("cy", yOff)
      .attr("r", 8)
      .attr("fill", "#e74c3c")
      .attr("stroke", "#0d1117")
      .attr("stroke-width", 1.5);
    legend.append("text")
      .attr("x", 14).attr("y", yOff)
      .attr("text-anchor", "middle")
      .attr("dominant-baseline", "central")
      .attr("fill", "#fff")
      .attr("font-size", "9px")
      .attr("font-weight", "bold")
      .text("xN");
    legend.append("text")
      .attr("x", 34).attr("y", yOff + 4)
      .attr("fill", "#8b949e")
      .attr("font-size", "11px")
      .attr("font-family", "'Segoe UI', system-ui, sans-serif")
      .text("Agent count badge");
    yOff += 16;

    // Size background
    bg.attr("width", 190).attr("height", yOff + 8);
  }

  // --- Initial render ---
  renderLegend();
  render();

  // Center the view initially
  svg.call(zoom.transform, d3.zoomIdentity.translate(0, 0).scale(0.9));
}
