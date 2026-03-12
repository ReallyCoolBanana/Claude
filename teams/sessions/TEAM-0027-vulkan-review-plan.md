---
team_id: TEAM-0027
date: 2026-03-12
members: [coordinator]
objective: "Plan and launch a 12-agent review of the vulkan-orbiting-spheres program for errors and issues"
parent_team: null
builds_on_knowledge: [KB-0033, KB-0034]
---

# TEAM-0027: Vulkan Orbiting Spheres — 12-Agent Error & Issue Review

## Operation Codename: VULKAN-AUDIT

## Target Program

**vulkan-orbiting-spheres/** — A ~1,790-line Vulkan 3D application rendering two Phong-lit spheres with an orbit camera. C++/GLSL, CMake build, uses GLFW, GLM, vk-bootstrap, and VMA.

## Team Roster (12 Agents, 4 Teams, 1 Launch Wave)

All 12 agents launch simultaneously per SOP-011 (Anti-Sequential Rules). No dependencies between agents — each reads source code directly.

---

### TEAM A — Vulkan API Correctness (Think Tank, 3 Agents)

| Agent | ID | Focus Area | Primary Files |
|---|---|---|---|
| A1 | vk-init-audit | Instance, device, surface, queue creation | `vulkan_app.cpp:1-200`, `vulkan_app.h` |
| A2 | vk-sync-audit | Synchronization: fences, semaphores, barriers, frame-in-flight | `vulkan_app.cpp` (drawFrame, mainLoop, cleanup) |
| A3 | vk-swapchain-audit | Swapchain creation, recreation on resize, image acquisition, present | `vulkan_app.cpp` (swapchain-related methods) |

**What they look for:**
- Incorrect Vulkan API call ordering (spec violations)
- Missing error checking on VkResult returns
- Validation layer errors (wrong usage flags, invalid handles)
- Swapchain recreation edge cases (minimized window, lost device, suboptimal)
- Fence/semaphore misuse (signaling before wait, double-wait, deadlocks)
- Frame-in-flight synchronization bugs (using resources still in GPU use)
- Queue family index errors
- Missing `vkDeviceWaitIdle` before cleanup
- Image layout transition errors

**Output:** `teams/TEAM-0027/output/team-a-vulkan-api-correctness.json`

---

### TEAM B — Shader & Pipeline Analysis (Think Tank, 3 Agents)

| Agent | ID | Focus Area | Primary Files |
|---|---|---|---|
| B1 | shader-audit | GLSL vertex/fragment shader correctness | `shaders/shader.vert`, `shaders/shader.frag` |
| B2 | pipeline-audit | Graphics pipeline state configuration | `pipeline.cpp`, `pipeline.h` |
| B3 | descriptor-audit | Descriptor sets, UBO layout, push constants | `types.h`, `vulkan_app.cpp` (descriptor-related), `pipeline.cpp` |

**What they look for:**
- Shader input/output location mismatches between stages
- UBO std140/std430 layout alignment violations (padding bugs)
- Push constant size/offset errors (exceeding `maxPushConstantsSize`)
- Vertex attribute format/offset mismatches between C++ `Vertex` struct and shader `layout(location=N)`
- Missing or incorrect descriptor set bindings
- Shader precision issues (float vs double, normalization)
- Lighting math errors (incorrect Blinn-Phong, wrong normal transform)
- Render pass compatibility issues
- Dynamic state not being set before draw
- Depth test/write configuration errors
- Backface culling winding order vs vertex generation order mismatch

**Output:** `teams/TEAM-0027/output/team-b-shader-pipeline.json`

---

### TEAM C — Memory, Resources & Performance (Think Tank, 3 Agents)

| Agent | ID | Focus Area | Primary Files |
|---|---|---|---|
| C1 | memory-audit | VMA allocation, buffer creation, staging patterns | `vulkan_app.cpp` (buffer creation, VMA calls) |
| C2 | lifecycle-audit | Resource creation/destruction order, leak detection | `vulkan_app.cpp` (initVulkan, cleanup), `vulkan_app.h` |
| C3 | perf-audit | Performance anti-patterns, redundant operations | All `.cpp` files |

**What they look for:**
- VMA allocation flags misuse (wrong memory type for use case)
- Staging buffer not freed after transfer
- Missing pipeline barriers for buffer transfers
- Buffer/image creation without proper usage flags
- Destruction order violations (destroying device before resources)
- Leaked Vulkan objects (handles created but never destroyed)
- Missing cleanup on swapchain recreation (old image views, framebuffers)
- Unnecessary per-frame allocations
- Redundant state binds in command buffer recording
- Suboptimal present mode selection
- Uniform buffer update patterns (could use push constants instead, or vice versa)
- Command buffer allocation strategy (reset vs reallocate)

**Output:** `teams/TEAM-0027/output/team-c-memory-performance.json`

---

### TEAM D — Application Logic, Build & Cross-Platform (Mixed, 3 Agents)

| Agent | ID | Focus Area | Primary Files |
|---|---|---|---|
| D1 | geometry-audit | Sphere generation math, vertex/index correctness | `sphere.cpp`, `sphere.h`, `types.h` |
| D2 | input-audit | Camera, input handling, window management, main loop | `camera.cpp`, `camera.h`, `main.cpp`, `vulkan_app.cpp` (input callbacks) |
| D3 | build-audit | CMake configuration, shader compilation, dependencies, cross-platform | `CMakeLists.txt`, `PLAN.md`, `README.md`, `.gitignore` |

**What they look for:**
- UV sphere generation errors (incorrect normals, degenerate triangles at poles, seam artifacts)
- Index buffer winding order inconsistency with pipeline cull mode
- Off-by-one errors in stack/slice loops
- Camera gimbal lock or NaN from extreme pitch values
- Division by zero in perspective projection (zero-size window)
- Mouse callback race conditions (callback during frame render)
- Window resize not triggering swapchain recreation
- GLFW error handling gaps
- CMake version compatibility issues
- Missing shader recompilation on source change
- FetchContent version pinning (security/reproducibility)
- Hardcoded paths that break on Linux/macOS
- Missing Vulkan SDK version checks
- glslc compiler detection failures

**Output:** `teams/TEAM-0027/output/team-d-app-logic-build.json`

---

## Coordination Infrastructure

### Tools & Features Used

| Tool/Feature | How Used |
|---|---|
| **Agent tool** (subagent_type) | 12 parallel agent launches in a single message |
| **Isolation: worktree** | Each agent works in an isolated git worktree to prevent conflicts |
| **WebSearch** | Agents B1 and C1 search for known Vulkan best practices and common pitfalls |
| **Grep/Glob/Read** | All agents read source files directly — no waiting for other teams |
| **TodoWrite** | Coordinator tracks team completion and consolidation progress |
| **Think Tank convergence** | Post-operation: run `think_tank.py` to deduplicate and consolidate findings |
| **SOP-016 report format** | All output follows standard findings taxonomy with severity levels |
| **Knowledge Base** | Final consolidated findings written as KB entry (KB-0036+) |

### Output Format (Per Agent)

Each agent produces a JSON report following SOP-016 findings taxonomy:

```json
{
  "metadata": {
    "team": "TEAM-0027",
    "agent": "<agent-id>",
    "task": "vulkan-error-review",
    "date": "2026-03-12",
    "focus_area": "<description>",
    "files_reviewed": ["<paths>"],
    "completion_status": "complete"
  },
  "findings": [
    {
      "id": "<CATEGORY>-VK-<NNN>",
      "category": "bug|race_condition|resource_leak|missing_validation|performance|design|security|test_gap",
      "severity": "critical|high|medium|low",
      "title": "Short description",
      "file": "path/to/file.cpp",
      "line": 123,
      "description": "Detailed explanation of the issue",
      "evidence": "Code snippet or reasoning",
      "recommendation": "How to fix it",
      "vulkan_spec_reference": "Optional: Vulkan spec section"
    }
  ],
  "summary": {
    "total_findings": 0,
    "by_severity": {"critical": 0, "high": 0, "medium": 0, "low": 0},
    "top_concerns": ["<most important issues>"]
  }
}
```

### Consolidation Pipeline (Post-Operation)

1. **Collect** all 12 agent outputs from `teams/TEAM-0027/output/`
2. **Validate** JSON structure and required metadata
3. **Deduplicate** findings across agents (same file + line + category = duplicate)
4. **Cross-reference** findings (e.g., shader layout mismatch found by B1 + B3)
5. **Rank** by severity: critical > high > medium > low
6. **Assemble** master report: `teams/TEAM-0027/output/vulkan-audit-master-report.json`
7. **Create KB entry** with validated findings

### Agent Prompt Template

Each agent receives a self-contained prompt structured as:

```
You are Agent [ID] of TEAM-0027 (Operation VULKAN-AUDIT).

**YOUR MISSION:** [Specific focus area and what to look for]

**READ THESE FILES:**
- vulkan-orbiting-spheres/src/[relevant files]
- vulkan-orbiting-spheres/shaders/[if applicable]

**SEARCH THE WEB FOR:** [If applicable — known Vulkan pitfalls for this area]

**YOUR OUTPUT:** Write a JSON report to teams/TEAM-0027/output/[agent-id].json
following the findings format specified below.

[Full JSON schema]

**CONSTRAINTS:**
- Read ALL source code in your focus area — do not skim
- Every finding must include file path, line number, and evidence
- Use Vulkan spec references where applicable
- Severity must be justified (not inflated)
- Do NOT fix code — report only
```

### Monitoring Plan (SOP-012)

| Checkpoint | When | Action |
|---|---|---|
| CP-1 | 30-60s | Verify all 12 agents started and are reading files |
| CP-2 | 5 min | Confirm agents are producing findings (not stuck) |
| CP-3 | 50% done | Identify stragglers, consider reinforcement |
| CP-4 | First completion | Check output quality, begin consolidation prep |
| CP-5 | All done | Run full consolidation pipeline |

### Risk Mitigation

| Risk | Mitigation |
|---|---|
| Duplicate findings across agents | Overlapping scope is intentional at boundaries (e.g., B3 and A2 both look at descriptors from different angles). Deduplication in consolidation handles this. |
| Agent produces low-quality output | CP-2 quality check. Relaunch with refined prompt if needed. |
| Agent crashes | Relaunch with same prompt. Partial output is preserved in worktree. |
| Too many low-severity findings | Post-consolidation filter: present critical/high first, low as appendix. |

---

## Expected Deliverables

1. **12 individual agent reports** (JSON, in `teams/TEAM-0027/output/`)
2. **1 master consolidated report** (deduplicated, cross-referenced, ranked)
3. **1 knowledge base entry** (KB-0036: Vulkan Orbiting Spheres Audit Findings)
4. **1 team session log** (this file, updated with results)

## Launch Command

Single coordinator message with 12 parallel `Agent` tool calls, all with `isolation: "worktree"` for safety.
