package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"time"
)

// CacheEntry stores metadata for a cached item
type CacheEntry struct {
	Key       string            `json:"key"`
	URL       string            `json:"url"`
	SHA256    string            `json:"sha256"`
	Size      int64             `json:"size"`
	TTL       int64             `json:"ttl_seconds"`
	CreatedAt int64             `json:"created_at"`
	ExpiresAt int64             `json:"expires_at"`
	Headers   map[string]string `json:"headers,omitempty"`
}

// CacheIndex is the main index structure
type CacheIndex struct {
	Version   string                `json:"version"`
	Updated   string                `json:"updated"`
	Entries   map[string]CacheEntry `json:"entries"`
}

// CacheStats for the stats command
type CacheStats struct {
	TotalEntries  int   `json:"total_entries"`
	TotalSize     int64 `json:"total_bytes"`
	ExpiredCount  int   `json:"expired_count"`
	ValidCount    int   `json:"valid_count"`
	OldestEntry   int64 `json:"oldest_entry_unix"`
	NewestEntry   int64 `json:"newest_entry_unix"`
}

// GCResult for garbage collection output
type GCResult struct {
	Removed    int   `json:"entries_removed"`
	BytesFreed int64 `json:"bytes_freed"`
	Remaining  int   `json:"entries_remaining"`
}

type Cache struct {
	dir       string
	indexPath string
	dataDir   string
	index     CacheIndex
}

func newCache(dir string) (*Cache, error) {
	dataDir := filepath.Join(dir, "data")
	os.MkdirAll(dataDir, 0755)

	c := &Cache{
		dir:       dir,
		indexPath: filepath.Join(dir, "cache_index.json"),
		dataDir:   dataDir,
	}

	// Load existing index or create new
	data, err := os.ReadFile(c.indexPath)
	if err != nil {
		c.index = CacheIndex{
			Version: "1.0",
			Entries: make(map[string]CacheEntry),
		}
	} else {
		if err := json.Unmarshal(data, &c.index); err != nil {
			c.index = CacheIndex{
				Version: "1.0",
				Entries: make(map[string]CacheEntry),
			}
		}
	}

	if c.index.Entries == nil {
		c.index.Entries = make(map[string]CacheEntry)
	}

	return c, nil
}

func (c *Cache) save() error {
	c.index.Updated = time.Now().Format(time.RFC3339)
	data, err := json.MarshalIndent(c.index, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(c.indexPath, data, 0644)
}

func hashKey(url string) string {
	h := sha256.Sum256([]byte(url))
	return hex.EncodeToString(h[:])
}

func (c *Cache) dataPath(key string) string {
	return filepath.Join(c.dataDir, key)
}

func (c *Cache) get(url string) ([]byte, *CacheEntry, error) {
	key := hashKey(url)
	entry, ok := c.index.Entries[key]
	if !ok {
		return nil, nil, fmt.Errorf("cache miss")
	}

	// Check TTL
	now := time.Now().Unix()
	if entry.ExpiresAt > 0 && now > entry.ExpiresAt {
		return nil, nil, fmt.Errorf("cache expired")
	}

	data, err := os.ReadFile(c.dataPath(key))
	if err != nil {
		// Data file missing, clean up index
		delete(c.index.Entries, key)
		c.save()
		return nil, nil, fmt.Errorf("cache data missing: %v", err)
	}

	return data, &entry, nil
}

func (c *Cache) set(url string, value []byte, ttlSeconds int64, headers map[string]string) error {
	key := hashKey(url)
	now := time.Now().Unix()

	var expiresAt int64
	if ttlSeconds > 0 {
		expiresAt = now + ttlSeconds
	}

	// Write data file
	if err := os.WriteFile(c.dataPath(key), value, 0644); err != nil {
		return err
	}

	// Update index
	c.index.Entries[key] = CacheEntry{
		Key:       key,
		URL:       url,
		SHA256:    hex.EncodeToString(sha256.New().Sum(value)),
		Size:      int64(len(value)),
		TTL:       ttlSeconds,
		CreatedAt: now,
		ExpiresAt: expiresAt,
		Headers:   headers,
	}

	return c.save()
}

func (c *Cache) delete(url string) error {
	key := hashKey(url)
	if _, ok := c.index.Entries[key]; !ok {
		return fmt.Errorf("key not found")
	}

	os.Remove(c.dataPath(key))
	delete(c.index.Entries, key)
	return c.save()
}

func (c *Cache) stats() CacheStats {
	now := time.Now().Unix()
	s := CacheStats{}

	for _, entry := range c.index.Entries {
		s.TotalEntries++
		s.TotalSize += entry.Size

		if entry.ExpiresAt > 0 && now > entry.ExpiresAt {
			s.ExpiredCount++
		} else {
			s.ValidCount++
		}

		if s.OldestEntry == 0 || entry.CreatedAt < s.OldestEntry {
			s.OldestEntry = entry.CreatedAt
		}
		if entry.CreatedAt > s.NewestEntry {
			s.NewestEntry = entry.CreatedAt
		}
	}

	return s
}

func (c *Cache) gc() GCResult {
	now := time.Now().Unix()
	result := GCResult{}

	// Collect expired keys
	var toRemove []string
	for key, entry := range c.index.Entries {
		if entry.ExpiresAt > 0 && now > entry.ExpiresAt {
			toRemove = append(toRemove, key)
			result.BytesFreed += entry.Size
		}
	}

	sort.Strings(toRemove) // deterministic order
	for _, key := range toRemove {
		os.Remove(c.dataPath(key))
		delete(c.index.Entries, key)
	}

	result.Removed = len(toRemove)
	result.Remaining = len(c.index.Entries)

	c.save()
	return result
}

func printUsage() {
	fmt.Fprintf(os.Stderr, `fast_cache - High-speed file-based key-value cache for API responses

Usage:
  fast_cache [--dir <cache_dir>] <command> [args...]

Commands:
  get <url>                         Get cached value for URL
  set <url> <value> [--ttl <secs>]  Cache a value with optional TTL
  set <url> --file <path> [--ttl <secs>]  Cache file contents
  delete <url>                      Remove cached entry
  stats                             Show cache statistics
  gc                                Remove expired entries
  keys                              List all cached URLs

Options:
  --dir <path>   Cache directory (default: ./cache_data)
  --ttl <secs>   Time-to-live in seconds (default: 3600)
  --header <k=v> Add metadata header (repeatable)
  --help         Show this help

Examples:
  fast_cache set "https://api.example.com/data" '{"result": 42}' --ttl 3600
  fast_cache get "https://api.example.com/data"
  fast_cache gc
  fast_cache stats
`)
}

func main() {
	args := os.Args[1:]

	cacheDir := ""
	var remaining []string
	ttl := int64(3600)
	headers := make(map[string]string)
	inputFile := ""

	for i := 0; i < len(args); i++ {
		switch args[i] {
		case "--dir":
			i++
			if i < len(args) {
				cacheDir = args[i]
			}
		case "--ttl":
			i++
			if i < len(args) {
				fmt.Sscanf(args[i], "%d", &ttl)
			}
		case "--header":
			i++
			if i < len(args) {
				parts := splitFirst(args[i], "=")
				if len(parts) == 2 {
					headers[parts[0]] = parts[1]
				}
			}
		case "--file":
			i++
			if i < len(args) {
				inputFile = args[i]
			}
		case "--help", "-h":
			printUsage()
			os.Exit(0)
		default:
			remaining = append(remaining, args[i])
		}
	}

	if len(remaining) == 0 {
		printUsage()
		os.Exit(1)
	}

	if cacheDir == "" {
		cacheDir = filepath.Join(".", "cache_data")
	}

	cache, err := newCache(cacheDir)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Error initializing cache: %v\n", err)
		os.Exit(1)
	}

	command := remaining[0]
	cmdArgs := remaining[1:]

	switch command {
	case "get":
		if len(cmdArgs) < 1 {
			fmt.Fprintf(os.Stderr, "Usage: fast_cache get <url>\n")
			os.Exit(1)
		}
		data, entry, err := cache.get(cmdArgs[0])
		if err != nil {
			result := map[string]interface{}{
				"hit":   false,
				"error": err.Error(),
				"url":   cmdArgs[0],
				"key":   hashKey(cmdArgs[0]),
			}
			enc := json.NewEncoder(os.Stdout)
			enc.SetIndent("", "  ")
			enc.Encode(result)
			os.Exit(1)
		}
		result := map[string]interface{}{
			"hit":        true,
			"url":        entry.URL,
			"key":        entry.Key,
			"size":       entry.Size,
			"created_at": entry.CreatedAt,
			"expires_at": entry.ExpiresAt,
			"headers":    entry.Headers,
			"data":       string(data),
		}
		enc := json.NewEncoder(os.Stdout)
		enc.SetIndent("", "  ")
		enc.Encode(result)

	case "set":
		if len(cmdArgs) < 1 {
			fmt.Fprintf(os.Stderr, "Usage: fast_cache set <url> <value> [--ttl <secs>]\n")
			os.Exit(1)
		}
		url := cmdArgs[0]
		var value []byte
		if inputFile != "" {
			value, err = os.ReadFile(inputFile)
			if err != nil {
				fmt.Fprintf(os.Stderr, "Error reading file: %v\n", err)
				os.Exit(1)
			}
		} else if len(cmdArgs) >= 2 {
			value = []byte(cmdArgs[1])
		} else {
			fmt.Fprintf(os.Stderr, "Provide a value or --file <path>\n")
			os.Exit(1)
		}

		if err := cache.set(url, value, ttl, headers); err != nil {
			fmt.Fprintf(os.Stderr, "Error: %v\n", err)
			os.Exit(1)
		}
		result := map[string]interface{}{
			"stored": true,
			"url":    url,
			"key":    hashKey(url),
			"size":   len(value),
			"ttl":    ttl,
		}
		enc := json.NewEncoder(os.Stdout)
		enc.SetIndent("", "  ")
		enc.Encode(result)

	case "delete":
		if len(cmdArgs) < 1 {
			fmt.Fprintf(os.Stderr, "Usage: fast_cache delete <url>\n")
			os.Exit(1)
		}
		if err := cache.delete(cmdArgs[0]); err != nil {
			fmt.Fprintf(os.Stderr, "Error: %v\n", err)
			os.Exit(1)
		}
		fmt.Println(`{"deleted": true}`)

	case "stats":
		s := cache.stats()
		enc := json.NewEncoder(os.Stdout)
		enc.SetIndent("", "  ")
		enc.Encode(s)

	case "gc":
		result := cache.gc()
		enc := json.NewEncoder(os.Stdout)
		enc.SetIndent("", "  ")
		enc.Encode(result)

	case "keys":
		var keys []map[string]interface{}
		for _, entry := range cache.index.Entries {
			keys = append(keys, map[string]interface{}{
				"url":        entry.URL,
				"key":        entry.Key,
				"size":       entry.Size,
				"expires_at": entry.ExpiresAt,
			})
		}
		if keys == nil {
			keys = []map[string]interface{}{}
		}
		enc := json.NewEncoder(os.Stdout)
		enc.SetIndent("", "  ")
		enc.Encode(keys)

	default:
		fmt.Fprintf(os.Stderr, "Unknown command: %s\n", command)
		printUsage()
		os.Exit(1)
	}
}

func splitFirst(s, sep string) []string {
	idx := -1
	for i := 0; i < len(s); i++ {
		if string(s[i]) == sep {
			idx = i
			break
		}
	}
	if idx < 0 {
		return []string{s}
	}
	return []string{s[:idx], s[idx+1:]}
}
