package main

import (
	"encoding/json"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"strings"
	"time"
)

// ValidationIssue represents a single validation problem
type ValidationIssue struct {
	Severity string `json:"severity"` // error, warning
	Category string `json:"category"`
	File     string `json:"file"`
	Message  string `json:"message"`
}

// ValidationReport is the final output
type ValidationReport struct {
	Pass       bool              `json:"pass"`
	Timestamp  string            `json:"timestamp"`
	Duration   string            `json:"duration"`
	Summary    map[string]int    `json:"summary"`
	Issues     []ValidationIssue `json:"issues"`
	Stats      RepoStats         `json:"stats"`
}

type RepoStats struct {
	TotalFiles    int `json:"total_files"`
	IndexFiles    int `json:"index_files"`
	KBEntries     int `json:"kb_entries"`
	TeamSessions  int `json:"team_sessions"`
	Scripts       int `json:"scripts"`
	CrossRefs     int `json:"cross_references"`
}

// Generic index.json structure for scripts
type ScriptsIndex struct {
	Version     string `json:"version"`
	LastUpdated string `json:"last_updated"`
	Scripts     []struct {
		ID           string   `json:"id"`
		Name         string   `json:"name"`
		Filename     string   `json:"filename"`
		Language     string   `json:"language"`
		Category     string   `json:"category"`
		Description  string   `json:"description"`
		Dependencies []string `json:"dependencies"`
		AddedByTeam  string   `json:"added_by_team"`
	} `json:"scripts"`
	Categories    map[string][]string `json:"categories"`
	LanguageIndex map[string][]string `json:"language_index"`
}

// KB index structure
type KBIndex struct {
	Version     string `json:"version"`
	LastUpdated string `json:"last_updated"`
	Entries     []struct {
		ID       string   `json:"id"`
		Title    string   `json:"title"`
		Category string   `json:"category"`
		Tags     []string `json:"tags"`
		BuildsOn []string `json:"builds_on"`
		Filename string   `json:"filename"` // may be empty; convention is ID + ".md"
	} `json:"entries"`
}

// Teams index structure
type TeamsIndex struct {
	Version  string `json:"version"`
	Sessions []struct {
		ID       string `json:"id"`
		Filename string `json:"filename"`
		Team     string `json:"team"`
	} `json:"sessions"`
}

type Validator struct {
	root   string
	issues []ValidationIssue
	stats  RepoStats
	allFiles map[string]bool
}

func newValidator(root string) *Validator {
	return &Validator{
		root:     root,
		allFiles: make(map[string]bool),
	}
}

func (v *Validator) addIssue(severity, category, file, msg string) {
	v.issues = append(v.issues, ValidationIssue{
		Severity: severity,
		Category: category,
		File:     file,
		Message:  msg,
	})
}

func (v *Validator) fileExists(rel string) bool {
	abs := filepath.Join(v.root, rel)
	_, err := os.Stat(abs)
	return err == nil
}

// Scan all files in the repo
func (v *Validator) scanFiles() {
	filepath.WalkDir(v.root, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return nil
		}
		if d.IsDir() {
			name := d.Name()
			if strings.HasPrefix(name, ".") || name == "node_modules" || name == "__pycache__" {
				return filepath.SkipDir
			}
			return nil
		}
		rel, _ := filepath.Rel(v.root, path)
		v.allFiles[rel] = true
		v.stats.TotalFiles++
		return nil
	})
}

// Check that required index.json files exist and parse
func (v *Validator) validateIndexFiles() {
	requiredIndices := []string{
		"knowledge-base/index.json",
		"storage/scripts/index.json",
		"teams/sessions/index.json",
	}

	for _, idx := range requiredIndices {
		abs := filepath.Join(v.root, idx)
		data, err := os.ReadFile(abs)
		if err != nil {
			v.addIssue("error", "index_missing", idx, "Required index file not found")
			continue
		}
		v.stats.IndexFiles++

		var parsed interface{}
		if err := json.Unmarshal(data, &parsed); err != nil {
			v.addIssue("error", "index_parse", idx, fmt.Sprintf("Invalid JSON: %v", err))
		}
	}

	// Check optional indices
	optionalIndices := []string{
		"market-research/picks/index.json",
		"storage/api-tools/index.json",
		"storage/sources/index.json",
	}
	for _, idx := range optionalIndices {
		abs := filepath.Join(v.root, idx)
		data, err := os.ReadFile(abs)
		if err != nil {
			continue // optional
		}
		v.stats.IndexFiles++
		var parsed interface{}
		if err := json.Unmarshal(data, &parsed); err != nil {
			v.addIssue("error", "index_parse", idx, fmt.Sprintf("Invalid JSON: %v", err))
		}
	}
}

// Validate KB entries and cross-references
func (v *Validator) validateKB() {
	abs := filepath.Join(v.root, "knowledge-base/index.json")
	data, err := os.ReadFile(abs)
	if err != nil {
		return
	}

	var kbIndex KBIndex
	if err := json.Unmarshal(data, &kbIndex); err != nil {
		return
	}

	knownIDs := make(map[string]bool)
	indexedFiles := make(map[string]bool)
	for _, entry := range kbIndex.Entries {
		knownIDs[entry.ID] = true
		v.stats.KBEntries++

		// Determine filename: use explicit filename or convention ID.md
		filename := entry.Filename
		if filename == "" {
			filename = entry.ID + ".md"
		}
		indexedFiles[filename] = true

		// Check file exists
		entryPath := filepath.Join("knowledge-base/entries", filename)
		if !v.fileExists(entryPath) {
			v.addIssue("error", "file_missing", entryPath, fmt.Sprintf("KB entry %s references missing file", entry.ID))
		}
	}

	// Validate builds_on references
	for _, entry := range kbIndex.Entries {
		for _, ref := range entry.BuildsOn {
			v.stats.CrossRefs++
			if !knownIDs[ref] {
				v.addIssue("error", "broken_ref", "knowledge-base/index.json",
					fmt.Sprintf("Entry %s builds_on %s which does not exist", entry.ID, ref))
			}
		}
	}

	// Check for orphan KB files
	entriesDir := filepath.Join(v.root, "knowledge-base/entries")
	if entries, err := os.ReadDir(entriesDir); err == nil {
		for _, e := range entries {
			name := e.Name()
			if !e.IsDir() && !indexedFiles[name] && name != ".gitkeep" {
				v.addIssue("warning", "orphan_file", filepath.Join("knowledge-base/entries", name),
					"File exists but not referenced in index.json")
			}
		}
	}
}

// Validate scripts and dependencies
func (v *Validator) validateScripts() {
	abs := filepath.Join(v.root, "storage/scripts/index.json")
	data, err := os.ReadFile(abs)
	if err != nil {
		return
	}

	var scriptsIdx ScriptsIndex
	if err := json.Unmarshal(data, &scriptsIdx); err != nil {
		return
	}

	knownIDs := make(map[string]bool)
	for _, s := range scriptsIdx.Scripts {
		knownIDs[s.ID] = true
		v.stats.Scripts++

		// Check file exists
		scriptPath := filepath.Join("storage/scripts", s.Filename)
		if !v.fileExists(scriptPath) {
			v.addIssue("error", "file_missing", scriptPath, fmt.Sprintf("Script %s (%s) references missing file", s.ID, s.Name))
		}
	}

	// Validate dependencies
	for _, s := range scriptsIdx.Scripts {
		for _, dep := range s.Dependencies {
			v.stats.CrossRefs++
			if !knownIDs[dep] {
				v.addIssue("error", "broken_dep", "storage/scripts/index.json",
					fmt.Sprintf("Script %s depends on %s which does not exist", s.ID, dep))
			}
		}
	}

	// Check category consistency
	for cat, ids := range scriptsIdx.Categories {
		for _, id := range ids {
			if !knownIDs[id] {
				v.addIssue("warning", "category_ref", "storage/scripts/index.json",
					fmt.Sprintf("Category '%s' references unknown script %s", cat, id))
			}
		}
	}

	// Cycle detection in script dependencies
	v.detectCycles(scriptsIdx)
}

func (v *Validator) detectCycles(idx ScriptsIndex) {
	// Build adjacency list
	deps := make(map[string][]string)
	for _, s := range idx.Scripts {
		deps[s.ID] = s.Dependencies
	}

	// DFS cycle detection
	white := 0 // unvisited
	gray := 1  // in progress
	black := 2 // done
	colors := make(map[string]int)

	var dfs func(node string, path []string) bool
	dfs = func(node string, path []string) bool {
		colors[node] = gray
		path = append(path, node)
		for _, dep := range deps[node] {
			if colors[dep] == gray {
				cycle := append(path, dep)
				v.addIssue("error", "cycle", "storage/scripts/index.json",
					fmt.Sprintf("Dependency cycle detected: %s", strings.Join(cycle, " -> ")))
				return true
			}
			if colors[dep] == white {
				if dfs(dep, path) {
					return true
				}
			}
		}
		colors[node] = black
		_ = white // suppress unused
		return false
	}

	for id := range deps {
		if colors[id] == white {
			dfs(id, nil)
		}
	}
}

// Validate team sessions
func (v *Validator) validateTeams() {
	abs := filepath.Join(v.root, "teams/sessions/index.json")
	data, err := os.ReadFile(abs)
	if err != nil {
		return
	}

	var teamsIdx TeamsIndex
	if err := json.Unmarshal(data, &teamsIdx); err != nil {
		return
	}

	for _, s := range teamsIdx.Sessions {
		v.stats.TeamSessions++
		sessionPath := filepath.Join("teams/sessions", s.Filename)
		if s.Filename != "" && !v.fileExists(sessionPath) {
			v.addIssue("error", "file_missing", sessionPath,
				fmt.Sprintf("Team session %s references missing file", s.ID))
		}
	}

	// Check for orphan session files
	sessionsDir := filepath.Join(v.root, "teams/sessions")
	if entries, err := os.ReadDir(sessionsDir); err == nil {
		indexed := make(map[string]bool)
		indexed["index.json"] = true
		for _, s := range teamsIdx.Sessions {
			indexed[s.Filename] = true
		}
		for _, e := range entries {
			name := e.Name()
			if !e.IsDir() && !indexed[name] && name != ".gitkeep" {
				v.addIssue("warning", "orphan_file", filepath.Join("teams/sessions", name),
					"Session file not referenced in index.json")
			}
		}
	}
}

// Detect orphan files in storage/scripts that aren't in the index
func (v *Validator) detectOrphanScripts() {
	abs := filepath.Join(v.root, "storage/scripts/index.json")
	data, err := os.ReadFile(abs)
	if err != nil {
		return
	}

	var scriptsIdx ScriptsIndex
	if err := json.Unmarshal(data, &scriptsIdx); err != nil {
		return
	}

	indexed := make(map[string]bool)
	indexed["index.json"] = true
	indexed["README.md"] = true
	for _, s := range scriptsIdx.Scripts {
		indexed[s.Filename] = true
		// Also index directories for multi-file scripts
		parts := strings.Split(s.Filename, "/")
		if len(parts) > 1 {
			indexed[parts[0]] = true
		}
	}

	scriptsDir := filepath.Join(v.root, "storage/scripts")
	entries, err := os.ReadDir(scriptsDir)
	if err != nil {
		return
	}
	for _, e := range entries {
		name := e.Name()
		if !indexed[name] && name != ".gitkeep" {
			v.addIssue("warning", "orphan_file", filepath.Join("storage/scripts", name),
				"File/directory not referenced in scripts index.json")
		}
	}
}

func findRepoRoot() string {
	dir, _ := os.Getwd()
	for {
		if _, err := os.Stat(filepath.Join(dir, "CLAUDE.md")); err == nil {
			return dir
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			break
		}
		dir = parent
	}
	return "/home/user/Claude"
}

func printUsage() {
	fmt.Fprintf(os.Stderr, `fast_validate - High-speed repository structure validator

Usage:
  fast_validate [options]

Options:
  --root <dir>   Repository root directory (default: auto-detect)
  --verbose      Show all checks, not just failures
  --help         Show this help

Validates:
  - All index.json files exist and parse
  - Cross-references (KB builds_on, script dependencies)
  - Referenced files exist on disk
  - Orphan file detection
  - Dependency cycle detection
`)
}

func main() {
	args := os.Args[1:]
	root := ""
	verbose := false

	for i := 0; i < len(args); i++ {
		switch args[i] {
		case "--root":
			i++
			if i < len(args) {
				root = args[i]
			}
		case "--verbose", "-v":
			verbose = true
		case "--help", "-h":
			printUsage()
			os.Exit(0)
		}
	}

	if root == "" {
		root = findRepoRoot()
	}

	_ = verbose

	start := time.Now()
	v := newValidator(root)

	// Run all validations
	v.scanFiles()
	v.validateIndexFiles()
	v.validateKB()
	v.validateScripts()
	v.validateTeams()
	v.detectOrphanScripts()

	duration := time.Since(start)

	// Build summary
	summary := make(map[string]int)
	for _, issue := range v.issues {
		summary[issue.Severity]++
	}

	pass := summary["error"] == 0
	report := ValidationReport{
		Pass:      pass,
		Timestamp: time.Now().Format(time.RFC3339),
		Duration:  duration.String(),
		Summary:   summary,
		Issues:    v.issues,
		Stats:     v.stats,
	}

	if report.Issues == nil {
		report.Issues = []ValidationIssue{}
	}

	enc := json.NewEncoder(os.Stdout)
	enc.SetIndent("", "  ")
	enc.Encode(report)

	if !pass {
		os.Exit(1)
	}
}
