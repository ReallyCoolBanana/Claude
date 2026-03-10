package main

import (
	"encoding/json"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"strings"
	"time"
	"unicode"
)

// Posting represents a single occurrence of a word in a file
type Posting struct {
	File     string `json:"file"`
	Line     int    `json:"line"`
	Col      int    `json:"col"`
	Context  string `json:"context"`
}

// SearchResult represents a single search result
type SearchResult struct {
	File      string `json:"file"`
	Line      int    `json:"line"`
	Col       int    `json:"col"`
	Context   string `json:"context"`
	Score     int    `json:"score"`
}

// SearchOutput is the JSON output format
type SearchOutput struct {
	Query      string         `json:"query"`
	Mode       string         `json:"mode"`
	Results    []SearchResult `json:"results"`
	TotalHits  int            `json:"total_hits"`
	IndexTime  string         `json:"index_time"`
	QueryTime  string         `json:"query_time"`
	FilesRead  int            `json:"files_indexed"`
}

// InvertedIndex maps normalized words to their postings
type InvertedIndex struct {
	index    map[string][]Posting
	lines    map[string][]string // file -> lines for phrase matching
	files    int
}

func newIndex() *InvertedIndex {
	return &InvertedIndex{
		index: make(map[string][]Posting),
		lines: make(map[string][]string),
	}
}

func tokenize(s string) []string {
	var tokens []string
	var current []rune
	for _, r := range s {
		if unicode.IsLetter(r) || unicode.IsDigit(r) || r == '_' || r == '-' {
			current = append(current, unicode.ToLower(r))
		} else {
			if len(current) > 0 {
				tokens = append(tokens, string(current))
				current = current[:0]
			}
		}
	}
	if len(current) > 0 {
		tokens = append(tokens, string(current))
	}
	return tokens
}

func (idx *InvertedIndex) addFile(path string, rootDir string) error {
	data, err := os.ReadFile(path)
	if err != nil {
		return err
	}

	relPath, _ := filepath.Rel(rootDir, path)
	content := string(data)
	lines := strings.Split(content, "\n")
	idx.lines[relPath] = lines
	idx.files++

	for lineNum, line := range lines {
		tokens := tokenize(line)
		col := 0
		for _, token := range tokens {
			col = strings.Index(strings.ToLower(line[col:]), token)
			if col < 0 {
				col = 0
			}
			// Trim context to reasonable length
			ctx := line
			if len(ctx) > 200 {
				ctx = ctx[:200] + "..."
			}
			idx.index[token] = append(idx.index[token], Posting{
				File:    relPath,
				Line:    lineNum + 1,
				Col:     col + 1,
				Context: ctx,
			})
		}
	}
	return nil
}

func (idx *InvertedIndex) buildFromDir(rootDir string) error {
	return filepath.WalkDir(rootDir, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return nil
		}
		if d.IsDir() {
			name := d.Name()
			// Skip hidden dirs, node_modules, .git, vendor, binary output dirs
			if strings.HasPrefix(name, ".") || name == "node_modules" || name == "vendor" || name == "__pycache__" {
				return filepath.SkipDir
			}
			return nil
		}
		ext := strings.ToLower(filepath.Ext(path))
		if ext == ".md" || ext == ".json" || ext == ".py" || ext == ".go" || ext == ".yaml" || ext == ".yml" || ext == ".txt" {
			return idx.addFile(path, rootDir)
		}
		return nil
	})
}

// searchExact finds all postings for a single word
func (idx *InvertedIndex) searchExact(word string) []SearchResult {
	word = strings.ToLower(word)
	postings := idx.index[word]
	results := make([]SearchResult, 0, len(postings))
	for _, p := range postings {
		results = append(results, SearchResult{
			File:    p.File,
			Line:    p.Line,
			Col:     p.Col,
			Context: p.Context,
			Score:   1,
		})
	}
	return results
}

// searchAND finds files containing all words, returns postings from first word
func (idx *InvertedIndex) searchAND(words []string) []SearchResult {
	if len(words) == 0 {
		return nil
	}

	// Find files containing all words
	fileSets := make([]map[string]bool, len(words))
	for i, w := range words {
		w = strings.ToLower(w)
		fileSets[i] = make(map[string]bool)
		for _, p := range idx.index[w] {
			fileSets[i][p.File] = true
		}
	}

	// Intersect
	common := make(map[string]bool)
	for f := range fileSets[0] {
		common[f] = true
	}
	for i := 1; i < len(fileSets); i++ {
		for f := range common {
			if !fileSets[i][f] {
				delete(common, f)
			}
		}
	}

	// Score: files with more matches rank higher
	fileScores := make(map[string]int)
	for _, w := range words {
		w = strings.ToLower(w)
		for _, p := range idx.index[w] {
			if common[p.File] {
				fileScores[p.File]++
			}
		}
	}

	// Collect results from matching files for the first word
	var results []SearchResult
	firstWord := strings.ToLower(words[0])
	for _, p := range idx.index[firstWord] {
		if common[p.File] {
			results = append(results, SearchResult{
				File:    p.File,
				Line:    p.Line,
				Col:     p.Col,
				Context: p.Context,
				Score:   fileScores[p.File],
			})
		}
	}
	return results
}

// searchPhrase finds exact phrase matches
func (idx *InvertedIndex) searchPhrase(phrase string) []SearchResult {
	phraseLower := strings.ToLower(phrase)
	var results []SearchResult

	for file, lines := range idx.lines {
		for lineNum, line := range lines {
			lineLower := strings.ToLower(line)
			col := strings.Index(lineLower, phraseLower)
			if col >= 0 {
				ctx := line
				if len(ctx) > 200 {
					ctx = ctx[:200] + "..."
				}
				results = append(results, SearchResult{
					File:    file,
					Line:    lineNum + 1,
					Col:     col + 1,
					Context: ctx,
					Score:   10, // Phrase matches score highest
				})
			}
		}
	}
	return results
}

func printUsage() {
	fmt.Fprintf(os.Stderr, `fast_search - High-speed full-text search for the knowledge base

Usage:
  fast_search [options] <query>

Options:
  --root <dir>     Repository root directory (default: auto-detect)
  --mode <mode>    Search mode: exact, and, phrase (default: auto)
  --limit <n>      Max results to return (default: 50)
  --help           Show this help

Search Modes:
  exact    Single word exact match
  and      All words must appear in the same file
  phrase   Exact phrase match (use quotes)

Examples:
  fast_search "knowledge base"     # phrase match (quoted)
  fast_search --mode and api cache # AND search
  fast_search --mode exact market  # exact word match
`)
}

func findRepoRoot() string {
	// Walk up from executable or cwd to find CLAUDE.md
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

func main() {
	args := os.Args[1:]

	root := ""
	mode := "auto"
	limit := 50
	var queryParts []string

	for i := 0; i < len(args); i++ {
		switch args[i] {
		case "--root":
			i++
			if i < len(args) {
				root = args[i]
			}
		case "--mode":
			i++
			if i < len(args) {
				mode = args[i]
			}
		case "--limit":
			i++
			if i < len(args) {
				fmt.Sscanf(args[i], "%d", &limit)
			}
		case "--help", "-h":
			printUsage()
			os.Exit(0)
		default:
			queryParts = append(queryParts, args[i])
		}
	}

	if len(queryParts) == 0 {
		printUsage()
		os.Exit(1)
	}

	if root == "" {
		root = findRepoRoot()
	}

	query := strings.Join(queryParts, " ")

	// Auto-detect mode
	if mode == "auto" {
		words := tokenize(query)
		if len(words) > 1 {
			// If the original query looks like a phrase, use phrase mode
			mode = "phrase"
		} else {
			mode = "exact"
		}
	}

	// Build index
	indexStart := time.Now()
	idx := newIndex()
	if err := idx.buildFromDir(root); err != nil {
		fmt.Fprintf(os.Stderr, "Error building index: %v\n", err)
		os.Exit(1)
	}
	indexTime := time.Since(indexStart)

	// Execute query
	queryStart := time.Now()
	var results []SearchResult

	switch mode {
	case "exact":
		results = idx.searchExact(query)
	case "and":
		words := tokenize(query)
		results = idx.searchAND(words)
	case "phrase":
		results = idx.searchPhrase(query)
	default:
		fmt.Fprintf(os.Stderr, "Unknown mode: %s\n", mode)
		os.Exit(1)
	}
	queryTime := time.Since(queryStart)

	// Limit results
	totalHits := len(results)
	if len(results) > limit {
		results = results[:limit]
	}

	// Output JSON
	output := SearchOutput{
		Query:     query,
		Mode:      mode,
		Results:   results,
		TotalHits: totalHits,
		IndexTime: indexTime.String(),
		QueryTime: queryTime.String(),
		FilesRead: idx.files,
	}

	enc := json.NewEncoder(os.Stdout)
	enc.SetIndent("", "  ")
	enc.Encode(output)
}
