#!/usr/bin/env python3
"""Documentation Fetcher - Extracts structured documentation from URLs.

Supports Python docs, ReadTheDocs, and generic HTML documentation sites.
Handles pagination and navigation. Outputs clean markdown with metadata.

Usage:
    python doc_fetcher.py <url> [--output FILE] [--format json|markdown] [--depth N] [--max-pages N]

Examples:
    python doc_fetcher.py https://docs.python.org/3/library/json.html
    python doc_fetcher.py https://flask.palletsprojects.com/en/3.0.x/ --depth 2 --max-pages 10
    python doc_fetcher.py https://example.com/docs --format json --output docs.json
"""

import argparse
import hashlib
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional


@dataclass
class DocPage:
    """Represents a single documentation page."""
    url: str
    title: str = ""
    content_markdown: str = ""
    headings: list = field(default_factory=list)
    code_blocks: list = field(default_factory=list)
    links: list = field(default_factory=list)
    nav_links: list = field(default_factory=list)
    fetched_at: str = ""
    content_hash: str = ""
    doc_type: str = "generic"  # python, readthedocs, generic


@dataclass
class DocResult:
    """Complete documentation fetch result."""
    source_url: str
    doc_type: str
    pages: list = field(default_factory=list)
    total_pages: int = 0
    fetch_time_seconds: float = 0.0
    metadata: dict = field(default_factory=dict)


class HTMLToMarkdown:
    """Converts HTML to clean Markdown."""

    # Tags to skip entirely
    SKIP_TAGS = {'script', 'style', 'nav', 'footer', 'header', 'aside',
                 'iframe', 'noscript', 'svg', 'form', 'button', 'input'}

    def __init__(self):
        self._result = []
        self._code_blocks = []
        self._headings = []
        self._links = []
        self._in_code = False
        self._in_pre = False
        self._list_depth = 0

    def convert(self, html_content: str) -> tuple:
        """Convert HTML to markdown. Returns (markdown, headings, code_blocks, links)."""
        self._result = []
        self._code_blocks = []
        self._headings = []
        self._links = []

        # Remove comments
        html_content = re.sub(r'<!--.*?-->', '', html_content, flags=re.DOTALL)

        # Remove skip tags and their content
        for tag in self.SKIP_TAGS:
            html_content = re.sub(
                rf'<{tag}[^>]*>.*?</{tag}>', '', html_content, flags=re.DOTALL | re.IGNORECASE
            )

        # Process the HTML
        self._process_html(html_content)

        markdown = '\n'.join(self._result)
        # Clean up excessive blank lines
        markdown = re.sub(r'\n{3,}', '\n\n', markdown)
        return markdown.strip(), self._headings, self._code_blocks, self._links

    def _process_html(self, content: str):
        """Process HTML content and convert to markdown."""
        # Simple tag-based processing
        pos = 0
        while pos < len(content):
            # Find next tag
            tag_start = content.find('<', pos)
            if tag_start == -1:
                # Rest is plain text
                text = self._clean_text(content[pos:])
                if text:
                    self._result.append(text)
                break

            # Text before tag
            if tag_start > pos:
                text = self._clean_text(content[pos:tag_start])
                if text and not self._in_pre:
                    self._result.append(text)
                elif text and self._in_pre:
                    self._result.append(content[pos:tag_start])

            # Find end of tag
            tag_end = content.find('>', tag_start)
            if tag_end == -1:
                break

            tag_full = content[tag_start:tag_end + 1]
            pos = tag_end + 1

            # Parse tag
            self._handle_tag(tag_full, content, pos)

    def _handle_tag(self, tag: str, content: str, pos: int):
        """Handle a single HTML tag."""
        # Extract tag name
        match = re.match(r'</?(\w+)', tag)
        if not match:
            return
        tag_name = match.group(1).lower()
        is_closing = tag.startswith('</')

        if tag_name in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
            level = int(tag_name[1])
            if not is_closing:
                # Extract heading text
                end_tag = f'</{tag_name}>'
                end_pos = content.find(end_tag, pos)
                if end_pos != -1:
                    heading_html = content[pos:end_pos]
                    heading_text = self._strip_tags(heading_html).strip()
                    prefix = '#' * level
                    self._result.append(f'\n{prefix} {heading_text}\n')
                    self._headings.append({'level': level, 'text': heading_text})

        elif tag_name == 'p':
            if is_closing:
                self._result.append('\n')
            else:
                self._result.append('\n')

        elif tag_name == 'br':
            self._result.append('\n')

        elif tag_name == 'pre':
            self._in_pre = not is_closing
            if not is_closing:
                self._result.append('\n```\n')
            else:
                self._result.append('\n```\n')

        elif tag_name == 'code':
            if not self._in_pre:
                self._result.append('`')

        elif tag_name in ('strong', 'b'):
            self._result.append('**')

        elif tag_name in ('em', 'i'):
            self._result.append('*')

        elif tag_name == 'a' and not is_closing:
            href_match = re.search(r'href=["\']([^"\']*)["\']', tag)
            if href_match:
                href = href_match.group(1)
                self._links.append(href)

        elif tag_name in ('ul', 'ol'):
            if not is_closing:
                self._list_depth += 1
            else:
                self._list_depth = max(0, self._list_depth - 1)
                self._result.append('\n')

        elif tag_name == 'li':
            if not is_closing:
                indent = '  ' * (self._list_depth - 1)
                self._result.append(f'\n{indent}- ')

        elif tag_name == 'hr':
            self._result.append('\n---\n')

        elif tag_name == 'blockquote':
            if not is_closing:
                self._result.append('\n> ')
            else:
                self._result.append('\n')

        elif tag_name == 'table':
            if not is_closing:
                self._result.append('\n')

        elif tag_name == 'tr':
            if is_closing:
                self._result.append('|\n')

        elif tag_name in ('td', 'th'):
            if not is_closing:
                self._result.append('| ')

    def _clean_text(self, text: str) -> str:
        """Clean HTML entities and whitespace from text."""
        text = html.unescape(text)
        text = re.sub(r'[ \t]+', ' ', text)
        return text.strip()

    def _strip_tags(self, html_str: str) -> str:
        """Remove all HTML tags from a string."""
        return html.unescape(re.sub(r'<[^>]+>', '', html_str))


class DocFetcher:
    """Fetches and structures documentation from URLs."""

    USER_AGENT = (
        "Mozilla/5.0 (compatible; DocFetcher/1.0; "
        "+https://github.com/doc-fetcher)"
    )

    # Rate limiting
    MIN_REQUEST_INTERVAL = 1.0  # seconds between requests

    def __init__(self, depth: int = 1, max_pages: int = 20, timeout: int = 30):
        """Initialize the fetcher.

        Args:
            depth: How many levels of links to follow (1 = just the page).
            max_pages: Maximum number of pages to fetch.
            timeout: HTTP request timeout in seconds.
        """
        self.depth = depth
        self.max_pages = max_pages
        self.timeout = timeout
        self._visited = set()
        self._last_request_time = 0
        self._converter = HTMLToMarkdown()

    def fetch(self, url: str) -> DocResult:
        """Fetch documentation from a URL.

        Args:
            url: The documentation URL to fetch.

        Returns:
            DocResult with all fetched pages and metadata.
        """
        start_time = time.time()
        result = DocResult(
            source_url=url,
            doc_type=self._detect_doc_type(url),
        )

        self._crawl(url, result, current_depth=0)

        result.total_pages = len(result.pages)
        result.fetch_time_seconds = round(time.time() - start_time, 2)
        result.metadata = {
            'fetched_at': datetime.now(timezone.utc).isoformat(),
            'max_depth': self.depth,
            'max_pages': self.max_pages,
        }

        return result

    def _detect_doc_type(self, url: str) -> str:
        """Detect the type of documentation site."""
        parsed = urllib.parse.urlparse(url)
        host = parsed.hostname or ''

        if 'docs.python.org' in host:
            return 'python'
        if 'readthedocs' in host or '.rtfd.' in host:
            return 'readthedocs'
        if 'palletsprojects.com' in host:
            return 'readthedocs'  # Flask, Jinja2, etc.
        if 'djangoproject.com' in host:
            return 'readthedocs'
        if 'sphinx' in host:
            return 'readthedocs'
        return 'generic'

    def _rate_limit(self):
        """Enforce minimum interval between requests."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self.MIN_REQUEST_INTERVAL:
            time.sleep(self.MIN_REQUEST_INTERVAL - elapsed)
        self._last_request_time = time.time()

    def _fetch_url(self, url: str) -> Optional[str]:
        """Fetch a URL and return the HTML content."""
        if url in self._visited:
            return None
        if len(self._visited) >= self.max_pages:
            return None

        self._visited.add(url)
        self._rate_limit()

        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': self.USER_AGENT,
                'Accept': 'text/html,application/xhtml+xml',
            })
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                content_type = resp.headers.get('Content-Type', '')
                if 'text/html' not in content_type and 'xhtml' not in content_type:
                    return None
                charset = 'utf-8'
                ct_match = re.search(r'charset=([^\s;]+)', content_type)
                if ct_match:
                    charset = ct_match.group(1)
                return resp.read().decode(charset, errors='replace')
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as e:
            print(f"  Warning: Failed to fetch {url}: {e}", file=sys.stderr)
            return None

    def _extract_title(self, html_content: str) -> str:
        """Extract the page title from HTML."""
        match = re.search(r'<title[^>]*>(.*?)</title>', html_content, re.DOTALL | re.IGNORECASE)
        if match:
            return html.unescape(re.sub(r'<[^>]+>', '', match.group(1))).strip()
        # Fallback to first h1
        match = re.search(r'<h1[^>]*>(.*?)</h1>', html_content, re.DOTALL | re.IGNORECASE)
        if match:
            return html.unescape(re.sub(r'<[^>]+>', '', match.group(1))).strip()
        return "Untitled"

    def _extract_main_content(self, html_content: str, doc_type: str) -> str:
        """Extract the main content area based on doc type."""
        # Try site-specific selectors first
        if doc_type == 'python':
            # Python docs use <div class="body" role="main">
            match = re.search(
                r'<div\s+class="body"\s+role="main">(.*?)</div>\s*(?=<div\s+class="sphinxsidebar|<footer)',
                html_content, re.DOTALL | re.IGNORECASE
            )
            if match:
                return match.group(1)

        if doc_type == 'readthedocs':
            # ReadTheDocs uses <div role="main"> or <div class="rst-content">
            for pattern in [
                r'<div\s+[^>]*role="main"[^>]*>(.*?)</div>\s*(?=<footer|</div>\s*<footer)',
                r'<div\s+class="rst-content"[^>]*>(.*?)</div>\s*(?=<footer)',
                r'<div\s+class="document"[^>]*>(.*?)</div>\s*(?=<div\s+class="sphinxsidebar)',
            ]:
                match = re.search(pattern, html_content, re.DOTALL | re.IGNORECASE)
                if match:
                    return match.group(1)

        # Generic: try common content containers
        for pattern in [
            r'<main[^>]*>(.*?)</main>',
            r'<article[^>]*>(.*?)</article>',
            r'<div\s+[^>]*role="main"[^>]*>(.*?)</div>',
            r'<div\s+[^>]*class="[^"]*content[^"]*"[^>]*>(.*?)</div>',
            r'<div\s+[^>]*class="[^"]*documentation[^"]*"[^>]*>(.*?)</div>',
            r'<div\s+[^>]*id="content"[^>]*>(.*?)</div>',
        ]:
            match = re.search(pattern, html_content, re.DOTALL | re.IGNORECASE)
            if match:
                return match.group(1)

        # Fallback: use body
        match = re.search(r'<body[^>]*>(.*?)</body>', html_content, re.DOTALL | re.IGNORECASE)
        if match:
            return match.group(1)

        return html_content

    def _extract_code_blocks(self, markdown: str) -> list:
        """Extract code blocks from markdown content."""
        blocks = []
        for match in re.finditer(r'```(\w*)\n(.*?)```', markdown, re.DOTALL):
            blocks.append({
                'language': match.group(1) or 'text',
                'code': match.group(2).strip(),
            })
        return blocks

    def _extract_nav_links(self, html_content: str, base_url: str, doc_type: str) -> list:
        """Extract navigation/pagination links."""
        nav_links = []
        parsed_base = urllib.parse.urlparse(base_url)
        base_domain = parsed_base.hostname

        # Look for next/prev links
        nav_patterns = [
            r'<link\s+rel="next"\s+[^>]*href="([^"]*)"',
            r'<a\s+[^>]*class="[^"]*next[^"]*"[^>]*href="([^"]*)"',
            r'<a\s+[^>]*rel="next"[^>]*href="([^"]*)"',
        ]

        for pattern in nav_patterns:
            for match in re.finditer(pattern, html_content, re.IGNORECASE):
                href = match.group(1)
                full_url = urllib.parse.urljoin(base_url, href)
                parsed = urllib.parse.urlparse(full_url)
                if parsed.hostname == base_domain:
                    nav_links.append(full_url)

        # For deeper crawls, extract same-domain documentation links
        if self.depth > 1:
            for match in re.finditer(r'href="([^"]*)"', html_content):
                href = match.group(1)
                if href.startswith('#') or href.startswith('mailto:'):
                    continue
                full_url = urllib.parse.urljoin(base_url, href)
                parsed = urllib.parse.urlparse(full_url)
                # Stay on same domain, same path prefix
                if (parsed.hostname == base_domain and
                        self._is_doc_link(full_url, base_url, doc_type)):
                    nav_links.append(full_url)

        return list(set(nav_links))

    def _is_doc_link(self, url: str, base_url: str, doc_type: str) -> bool:
        """Check if a URL is likely a documentation page."""
        parsed = urllib.parse.urlparse(url)
        path = parsed.path.lower()

        # Skip non-doc resources
        skip_extensions = {'.png', '.jpg', '.jpeg', '.gif', '.svg', '.css',
                          '.js', '.ico', '.pdf', '.zip', '.tar', '.gz'}
        for ext in skip_extensions:
            if path.endswith(ext):
                return False

        # For Python docs, stay within the same library section
        if doc_type == 'python':
            base_path = urllib.parse.urlparse(base_url).path
            base_prefix = '/'.join(base_path.split('/')[:3])  # e.g., /3/library
            return path.startswith(base_prefix)

        # For ReadTheDocs, stay within same version
        if doc_type == 'readthedocs':
            base_path = urllib.parse.urlparse(base_url).path
            base_parts = base_path.split('/')
            if len(base_parts) >= 3:
                base_prefix = '/'.join(base_parts[:3])
                return path.startswith(base_prefix)

        # Generic: stay in same directory tree
        base_path = urllib.parse.urlparse(base_url).path
        base_dir = '/'.join(base_path.split('/')[:-1])
        return path.startswith(base_dir)

    def _crawl(self, url: str, result: DocResult, current_depth: int):
        """Recursively crawl documentation pages."""
        if current_depth > self.depth:
            return
        if len(result.pages) >= self.max_pages:
            return

        html_content = self._fetch_url(url)
        if not html_content:
            return

        print(f"  Fetched: {url}", file=sys.stderr)

        # Extract main content
        main_html = self._extract_main_content(html_content, result.doc_type)

        # Convert to markdown
        markdown, headings, code_blocks_from_html, links = self._converter.convert(main_html)

        # Extract code blocks from the markdown
        code_blocks = self._extract_code_blocks(markdown)

        # Build page
        page = DocPage(
            url=url,
            title=self._extract_title(html_content),
            content_markdown=markdown,
            headings=headings,
            code_blocks=code_blocks if code_blocks else code_blocks_from_html,
            links=links,
            fetched_at=datetime.now(timezone.utc).isoformat(),
            content_hash=hashlib.sha256(markdown.encode()).hexdigest()[:16],
            doc_type=result.doc_type,
        )

        # Extract nav links for crawling
        nav_links = self._extract_nav_links(html_content, url, result.doc_type)
        page.nav_links = nav_links

        result.pages.append(page)

        # Follow links if depth allows
        if current_depth < self.depth:
            for link in nav_links:
                if len(result.pages) >= self.max_pages:
                    break
                self._crawl(link, result, current_depth + 1)

    def to_json(self, result: DocResult) -> str:
        """Serialize result to JSON."""
        data = {
            'source_url': result.source_url,
            'doc_type': result.doc_type,
            'total_pages': result.total_pages,
            'fetch_time_seconds': result.fetch_time_seconds,
            'metadata': result.metadata,
            'pages': [asdict(p) for p in result.pages],
        }
        return json.dumps(data, indent=2, ensure_ascii=False)

    def to_markdown(self, result: DocResult) -> str:
        """Serialize result to a single markdown document."""
        parts = [
            f"# Documentation: {result.source_url}",
            f"",
            f"- **Type**: {result.doc_type}",
            f"- **Pages fetched**: {result.total_pages}",
            f"- **Fetch time**: {result.fetch_time_seconds}s",
            f"- **Fetched at**: {result.metadata.get('fetched_at', 'N/A')}",
            f"",
            "---",
            "",
        ]

        for i, page in enumerate(result.pages):
            parts.append(f"## Page {i + 1}: {page.title}")
            parts.append(f"**URL**: {page.url}")
            parts.append("")
            parts.append(page.content_markdown)
            parts.append("")
            parts.append("---")
            parts.append("")

        return '\n'.join(parts)


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description='Fetch and structure documentation from URLs.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument('url', help='Documentation URL to fetch')
    parser.add_argument('--output', '-o', help='Output file (default: stdout)')
    parser.add_argument('--format', '-f', choices=['json', 'markdown'], default='markdown',
                        help='Output format (default: markdown)')
    parser.add_argument('--depth', '-d', type=int, default=1,
                        help='Link follow depth (default: 1, just the page)')
    parser.add_argument('--max-pages', '-m', type=int, default=20,
                        help='Maximum pages to fetch (default: 20)')
    parser.add_argument('--timeout', '-t', type=int, default=30,
                        help='HTTP timeout in seconds (default: 30)')

    args = parser.parse_args()

    fetcher = DocFetcher(
        depth=args.depth,
        max_pages=args.max_pages,
        timeout=args.timeout,
    )

    print(f"Fetching documentation from: {args.url}", file=sys.stderr)
    result = fetcher.fetch(args.url)
    print(f"Done: {result.total_pages} pages in {result.fetch_time_seconds}s", file=sys.stderr)

    if args.format == 'json':
        output = fetcher.to_json(result)
    else:
        output = fetcher.to_markdown(result)

    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            f.write(output)
        print(f"Output written to: {args.output}", file=sys.stderr)
    else:
        print(output)


if __name__ == '__main__':
    main()
