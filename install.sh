#!/usr/bin/env bash
# ============================================================
#  Claude Agent Network — One-Click Installer
#  Checks and installs all dependencies automatically
# ============================================================

set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

PASS="${GREEN}✓${NC}"
FAIL="${RED}✗${NC}"
WARN="${YELLOW}!${NC}"
INFO="${BLUE}→${NC}"

ERRORS=0
INSTALLED=0
SKIPPED=0

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ─── Header ───────────────────────────────────────────────
echo ""
echo -e "${CYAN}${BOLD}╔══════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}${BOLD}║     Claude Agent Network — Installer             ║${NC}"
echo -e "${CYAN}${BOLD}║     310 Nodes · 4,809 Edges · 58+ Agents         ║${NC}"
echo -e "${CYAN}${BOLD}╚══════════════════════════════════════════════════╝${NC}"
echo ""

# ─── Helper functions ─────────────────────────────────────
check() { echo -e "  $PASS $1"; }
fail()  { echo -e "  $FAIL $1"; ERRORS=$((ERRORS + 1)); }
warn()  { echo -e "  $WARN $1"; }
info()  { echo -e "  $INFO $1"; }
installed() { echo -e "  $PASS ${GREEN}Installed:${NC} $1"; INSTALLED=$((INSTALLED + 1)); }
skipped()   { echo -e "  $PASS ${YELLOW}Already installed:${NC} $1"; SKIPPED=$((SKIPPED + 1)); }

command_exists() { command -v "$1" &>/dev/null; }

# ─── 1. System Requirements ──────────────────────────────
echo -e "${BOLD}[1/6] Checking system requirements...${NC}"

# OS
OS="$(uname -s)"
case "$OS" in
    Linux*)  check "OS: Linux" ;;
    Darwin*) check "OS: macOS" ;;
    MINGW*|CYGWIN*|MSYS*) check "OS: Windows (Git Bash)" ;;
    *)       warn "OS: $OS (untested, may work)" ;;
esac

# Python
if command_exists python3; then
    PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
    PY_MAJOR=$(echo "$PY_VER" | cut -d. -f1)
    PY_MINOR=$(echo "$PY_VER" | cut -d. -f2)
    if [ "$PY_MAJOR" -ge 3 ] && [ "$PY_MINOR" -ge 10 ]; then
        check "Python $PY_VER (>= 3.10 required)"
    else
        fail "Python $PY_VER found but >= 3.10 required"
    fi
else
    fail "Python 3 not found. Install Python 3.10+ from https://python.org"
fi

# Git
if command_exists git; then
    GIT_VER=$(git --version | awk '{print $3}')
    check "Git $GIT_VER"
else
    fail "Git not found. Install from https://git-scm.com"
fi

# Node.js (optional, for Claude Code CLI)
if command_exists node; then
    NODE_VER=$(node --version)
    check "Node.js $NODE_VER (for Claude Code CLI)"
else
    warn "Node.js not found (optional — needed for Claude Code CLI)"
fi

# pip
if command_exists pip3; then
    check "pip3 available"
    PIP_CMD="pip3"
elif python3 -m pip --version &>/dev/null; then
    check "pip available (via python3 -m pip)"
    PIP_CMD="python3 -m pip"
else
    fail "pip not found. Install with: python3 -m ensurepip"
    PIP_CMD=""
fi

echo ""

# ─── 2. Python Dependencies ──────────────────────────────
echo -e "${BOLD}[2/6] Checking Python dependencies...${NC}"

install_pip_package() {
    local pkg="$1"
    local import_name="${2:-$1}"
    local min_ver="${3:-}"

    if python3 -c "import $import_name" 2>/dev/null; then
        skipped "$pkg"
    else
        if [ -n "$PIP_CMD" ]; then
            info "Installing $pkg..."
            if [ -n "$min_ver" ]; then
                $PIP_CMD install "$pkg>=$min_ver" --quiet 2>/dev/null && installed "$pkg" || fail "Failed to install $pkg"
            else
                $PIP_CMD install "$pkg" --quiet 2>/dev/null && installed "$pkg" || fail "Failed to install $pkg"
            fi
        else
            fail "Cannot install $pkg — pip not available"
        fi
    fi
}

install_pip_package "pyyaml" "yaml" "6.0"
install_pip_package "mcp" "mcp" "1.0.0"
install_pip_package "anthropic" "anthropic"

echo ""

# ─── 3. Directory Structure ──────────────────────────────
echo -e "${BOLD}[3/6] Checking project directories...${NC}"

ensure_dir() {
    if [ -d "$1" ]; then
        check "$(basename "$1")/"
    else
        mkdir -p "$1"
        installed "Created $1"
    fi
}

ensure_dir "$PROJECT_DIR/storage/data"
ensure_dir "$PROJECT_DIR/storage/coordination/bus"
ensure_dir "$PROJECT_DIR/storage/coordination/db"
ensure_dir "$PROJECT_DIR/storage/coordination/logs"
ensure_dir "$PROJECT_DIR/storage/pointer-routes"
ensure_dir "$PROJECT_DIR/knowledge-base/entries"
ensure_dir "$PROJECT_DIR/teams/sessions"

echo ""

# ─── 4. Data Files ───────────────────────────────────────
echo -e "${BOLD}[4/6] Checking core data files...${NC}"

check_file() {
    local filepath="$1"
    local label="$2"
    if [ -f "$filepath" ]; then
        local size
        size=$(wc -c < "$filepath" | tr -d ' ')
        check "$label ($(numfmt --to=iec "$size" 2>/dev/null || echo "${size}B"))"
    else
        warn "$label — missing (will be created on first run)"
    fi
}

check_file "$PROJECT_DIR/storage/pointer-network.json" "Pointer Network"
check_file "$PROJECT_DIR/storage/pointer-routes/route_log.jsonl" "Route Log"
check_file "$PROJECT_DIR/storage/pointer-routes/route_analysis.json" "Route Analysis"
check_file "$PROJECT_DIR/storage/pointer-routes/route_patterns.json" "Route Patterns"
check_file "$PROJECT_DIR/storage/coordination/route_tracker.py" "Route Tracker"
check_file "$PROJECT_DIR/knowledge-base/index.json" "Knowledge Base Index"
check_file "$PROJECT_DIR/storage/scripts/index.json" "Scripts Index"
check_file "$PROJECT_DIR/storage/sources/index.json" "Sources Index"
check_file "$PROJECT_DIR/CLAUDE.md" "CLAUDE.md (project config)"

echo ""

# ─── 5. Hooks & Permissions ──────────────────────────────
echo -e "${BOLD}[5/6] Setting up hooks and permissions...${NC}"

# Make hooks executable
if [ -d "$PROJECT_DIR/.claude/hooks" ]; then
    chmod +x "$PROJECT_DIR/.claude/hooks/"*.sh 2>/dev/null && check "Hook scripts made executable" || warn "No hook scripts found"
else
    warn ".claude/hooks/ directory not found"
fi

# Make Python scripts executable
SCRIPT_COUNT=0
for f in "$PROJECT_DIR/storage/pointer-routes/"*.py "$PROJECT_DIR/storage/coordination/"*.py; do
    if [ -f "$f" ]; then
        chmod +x "$f"
        SCRIPT_COUNT=$((SCRIPT_COUNT + 1))
    fi
done
check "$SCRIPT_COUNT Python scripts made executable"

echo ""

# ─── 6. Validation ───────────────────────────────────────
echo -e "${BOLD}[6/6] Running validation checks...${NC}"

# Test Python imports
if python3 -c "import json, sqlite3, collections, pathlib, heapq, subprocess, datetime, re, gc" 2>/dev/null; then
    check "Python stdlib imports OK"
else
    fail "Some stdlib imports failed"
fi

# Test pointer network loads
if python3 -c "
import json, sys
try:
    with open('$PROJECT_DIR/storage/pointer-network.json') as f:
        data = json.load(f)
    nodes = len(data.get('nodes', []))
    stats = data.get('stats', {})
    edges = stats.get('total_edges', 0)
    print(f'{nodes} nodes, {edges} edges')
except Exception as e:
    print(f'Error: {e}', file=sys.stderr)
    sys.exit(1)
" 2>/dev/null; then
    NETWORK_INFO=$(python3 -c "
import json
with open('$PROJECT_DIR/storage/pointer-network.json') as f:
    data = json.load(f)
print(f\"{len(data.get('nodes', []))} nodes, {data.get('stats', {}).get('total_edges', 0)} edges\")
" 2>/dev/null)
    check "Pointer network loads OK ($NETWORK_INFO)"
else
    warn "Pointer network failed to load (may need to be generated)"
fi

# Test route tracker
if python3 -c "
import sys
sys.path.insert(0, '$PROJECT_DIR/storage/coordination')
import route_tracker
" 2>/dev/null; then
    check "Route tracker imports OK"
else
    warn "Route tracker import failed (may need dependencies)"
fi

# Test route log
if [ -f "$PROJECT_DIR/storage/pointer-routes/route_log.jsonl" ]; then
    ROUTE_COUNT=$(wc -l < "$PROJECT_DIR/storage/pointer-routes/route_log.jsonl" | tr -d ' ')
    check "Route log: $ROUTE_COUNT entries"
else
    warn "Route log not found"
fi

echo ""

# ─── Summary ─────────────────────────────────────────────
echo -e "${CYAN}${BOLD}══════════════════════════════════════════════════${NC}"
if [ "$ERRORS" -eq 0 ]; then
    echo -e "${GREEN}${BOLD}  Installation complete!${NC}"
else
    echo -e "${YELLOW}${BOLD}  Installation complete with $ERRORS issue(s)${NC}"
fi
echo ""
echo -e "  ${GREEN}$INSTALLED${NC} packages installed"
echo -e "  ${YELLOW}$SKIPPED${NC} already present"
if [ "$ERRORS" -gt 0 ]; then
    echo -e "  ${RED}$ERRORS${NC} errors (see above)"
fi
echo ""
echo -e "${BOLD}  Quick start:${NC}"
echo -e "    cd $PROJECT_DIR"
echo -e "    python3 storage/coordination/route_tracker.py analyze"
echo -e "    open storage/pointer-routes/network_visualization.html"
echo ""
echo -e "${CYAN}${BOLD}══════════════════════════════════════════════════${NC}"
echo ""
