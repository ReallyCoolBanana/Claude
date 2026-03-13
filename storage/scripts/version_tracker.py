#!/usr/bin/env python3
"""Version Tracker - Tracks latest versions of software packages.

Queries PyPI, npm, and GitHub for latest versions of key packages.
Compares changelogs between versions. Outputs structured JSON reports.

Usage:
    python version_tracker.py check <package> [--source pypi|npm|github]
    python version_tracker.py track <packages_file.json>
    python version_tracker.py compare <package> <old_version> <new_version>
    python version_tracker.py report [--packages PKG1,PKG2,...] [--output FILE]

Examples:
    python version_tracker.py check requests --source pypi
    python version_tracker.py check express --source npm
    python version_tracker.py check python/cpython --source github
    python version_tracker.py report --packages requests,flask,django
"""

import argparse
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


USER_AGENT = "VersionTracker/1.0 (compatible; doc-tools)"


@dataclass
class VersionInfo:
    """Version information for a package."""
    package: str
    source: str  # pypi, npm, github
    latest_version: str = ""
    previous_version: str = ""
    release_date: str = ""
    description: str = ""
    homepage: str = ""
    license: str = ""
    python_requires: str = ""
    changelog_url: str = ""
    changelog_snippet: str = ""
    all_versions: list = field(default_factory=list)
    error: str = ""
    checked_at: str = ""


@dataclass
class VersionReport:
    """Complete version tracking report."""
    packages: list = field(default_factory=list)
    generated_at: str = ""
    total_checked: int = 0
    errors: int = 0


def _http_get_json(url: str, timeout: int = 30) -> Optional[dict]:
    """Fetch JSON from a URL."""
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': USER_AGENT,
            'Accept': 'application/json',
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError,
            OSError, ValueError) as e:
        print(f"  Warning: HTTP error for {url}: {e}", file=sys.stderr)
        return None


def _http_get_text(url: str, timeout: int = 30) -> Optional[str]:
    """Fetch text from a URL."""
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': USER_AGENT,
            'Accept': 'text/plain, text/html',
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode('utf-8', errors='replace')
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
        print(f"  Warning: HTTP error for {url}: {e}", file=sys.stderr)
        return None


def check_pypi(package: str) -> VersionInfo:
    """Check PyPI for latest version of a Python package.

    Args:
        package: PyPI package name (e.g., 'requests', 'flask').

    Returns:
        VersionInfo with latest version details.
    """
    info = VersionInfo(package=package, source='pypi',
                       checked_at=datetime.now(timezone.utc).isoformat())

    data = _http_get_json(f'https://pypi.org/pypi/{package}/json')
    if not data:
        info.error = f"Failed to fetch PyPI data for {package}"
        return info

    pkg_info = data.get('info', {})
    info.latest_version = pkg_info.get('version', '')
    info.description = pkg_info.get('summary', '')
    info.homepage = (pkg_info.get('home_page', '') or
                     pkg_info.get('project_url', '') or
                     pkg_info.get('package_url', ''))
    info.license = pkg_info.get('license', '') or ''
    info.python_requires = pkg_info.get('requires_python', '') or ''

    # Get project URLs for changelog
    project_urls = pkg_info.get('project_urls') or {}
    for key in ('Changelog', 'Changes', 'History', 'Release Notes', 'What\'s New'):
        if key in project_urls:
            info.changelog_url = project_urls[key]
            break

    # Get all versions (sorted by upload date)
    releases = data.get('releases', {})
    versions_with_dates = []
    for ver, files in releases.items():
        if files:
            upload_time = files[0].get('upload_time', '')
            versions_with_dates.append((ver, upload_time))

    # Sort by upload time descending
    versions_with_dates.sort(key=lambda x: x[1], reverse=True)
    info.all_versions = [v[0] for v in versions_with_dates[:20]]

    # Get release date of latest
    latest_files = releases.get(info.latest_version, [])
    if latest_files:
        info.release_date = latest_files[0].get('upload_time', '')

    # Previous version (skip pre-releases)
    stable_versions = [v for v in info.all_versions
                       if not re.search(r'(a|b|rc|dev|alpha|beta|pre)', v, re.IGNORECASE)]
    if len(stable_versions) >= 2:
        info.previous_version = stable_versions[1]

    # Try to get changelog snippet
    if info.changelog_url:
        changelog_text = _http_get_text(info.changelog_url)
        if changelog_text:
            # Extract first section (likely latest version)
            info.changelog_snippet = _extract_changelog_section(
                changelog_text, info.latest_version
            )[:2000]

    return info


def check_npm(package: str) -> VersionInfo:
    """Check npm registry for latest version of a Node.js package.

    Args:
        package: npm package name (e.g., 'express', 'react').

    Returns:
        VersionInfo with latest version details.
    """
    info = VersionInfo(package=package, source='npm',
                       checked_at=datetime.now(timezone.utc).isoformat())

    # URL-encode scoped packages
    encoded = urllib.parse.quote(package, safe='')
    data = _http_get_json(f'https://registry.npmjs.org/{encoded}')
    if not data:
        info.error = f"Failed to fetch npm data for {package}"
        return info

    dist_tags = data.get('dist-tags', {})
    info.latest_version = dist_tags.get('latest', '')

    # Get version-specific metadata
    versions = data.get('versions', {})
    latest_data = versions.get(info.latest_version, {})

    info.description = latest_data.get('description', '') or data.get('description', '')
    info.homepage = latest_data.get('homepage', '') or data.get('homepage', '')
    info.license = latest_data.get('license', '') or ''
    if isinstance(info.license, dict):
        info.license = info.license.get('type', '')

    # Release date
    time_data = data.get('time', {})
    info.release_date = time_data.get(info.latest_version, '')

    # All versions (most recent first)
    version_times = [(v, t) for v, t in time_data.items()
                     if v not in ('created', 'modified')]
    version_times.sort(key=lambda x: x[1], reverse=True)
    info.all_versions = [v[0] for v in version_times[:20]]

    # Previous stable version
    stable = [v for v in info.all_versions
              if not re.search(r'(alpha|beta|rc|dev|canary|next|pre)', v, re.IGNORECASE)]
    if len(stable) >= 2:
        info.previous_version = stable[1]

    # Repository URL for changelog
    repo = data.get('repository', {})
    if isinstance(repo, dict):
        repo_url = repo.get('url', '')
    else:
        repo_url = str(repo)
    repo_url = re.sub(r'^git\+', '', repo_url)
    repo_url = re.sub(r'\.git$', '', repo_url)
    repo_url = re.sub(r'^ssh://git@', 'https://', repo_url)
    if 'github.com' in repo_url:
        info.changelog_url = repo_url + '/blob/main/CHANGELOG.md'

    return info


def check_github(repo: str) -> VersionInfo:
    """Check GitHub for latest release of a repository.

    Args:
        repo: GitHub repo in 'owner/name' format (e.g., 'python/cpython').

    Returns:
        VersionInfo with latest release details.
    """
    info = VersionInfo(package=repo, source='github',
                       checked_at=datetime.now(timezone.utc).isoformat())

    # Get latest release
    data = _http_get_json(f'https://api.github.com/repos/{repo}/releases/latest')
    if not data:
        # Try tags instead
        tags = _http_get_json(f'https://api.github.com/repos/{repo}/tags')
        if tags and len(tags) > 0:
            info.latest_version = tags[0].get('name', '')
            if len(tags) > 1:
                info.previous_version = tags[1].get('name', '')
            info.all_versions = [t.get('name', '') for t in tags[:20]]
            return info
        info.error = f"Failed to fetch GitHub data for {repo}"
        return info

    info.latest_version = data.get('tag_name', '')
    info.release_date = data.get('published_at', '')
    info.description = data.get('name', '')
    info.homepage = data.get('html_url', '')
    info.changelog_snippet = (data.get('body', '') or '')[:2000]

    # Get recent releases
    releases = _http_get_json(f'https://api.github.com/repos/{repo}/releases?per_page=20')
    if releases:
        info.all_versions = [r.get('tag_name', '') for r in releases]
        stable = [v for v in info.all_versions
                  if not re.search(r'(alpha|beta|rc|dev|pre)', v, re.IGNORECASE)]
        if len(stable) >= 2:
            info.previous_version = stable[1]

    return info


def _extract_changelog_section(text: str, version: str) -> str:
    """Extract changelog section for a specific version."""
    # Strip HTML tags if present
    text = re.sub(r'<[^>]+>', '', text)

    # Try to find version header
    escaped = re.escape(version)
    patterns = [
        rf'(?:^|\n)(#+\s*(?:v?{escaped}|Version\s+{escaped}).*?)(?=\n#+\s|\Z)',
        rf'(?:^|\n)(v?{escaped}\s*[\(-].*?)(?=\n\S+\s*[\(-]|\Z)',
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
        if match:
            return match.group(1).strip()

    # Fallback: return first 500 chars
    return text[:500]


def compare_versions(package: str, old_ver: str, new_ver: str,
                     source: str = 'pypi') -> dict:
    """Compare two versions of a package.

    Args:
        package: Package name.
        old_ver: Older version string.
        new_ver: Newer version string.
        source: Package source (pypi, npm, github).

    Returns:
        Dict with comparison details.
    """
    result = {
        'package': package,
        'source': source,
        'old_version': old_ver,
        'new_version': new_ver,
        'compared_at': datetime.now(timezone.utc).isoformat(),
        'changes': [],
    }

    if source == 'pypi':
        old_data = _http_get_json(f'https://pypi.org/pypi/{package}/{old_ver}/json')
        new_data = _http_get_json(f'https://pypi.org/pypi/{package}/{new_ver}/json')

        if old_data and new_data:
            old_info = old_data.get('info', {})
            new_info = new_data.get('info', {})

            # Compare requires_python
            old_py = old_info.get('requires_python', '')
            new_py = new_info.get('requires_python', '')
            if old_py != new_py:
                result['changes'].append({
                    'field': 'requires_python',
                    'old': old_py,
                    'new': new_py,
                })

            # Compare dependencies
            old_deps = set(old_info.get('requires_dist') or [])
            new_deps = set(new_info.get('requires_dist') or [])
            added_deps = new_deps - old_deps
            removed_deps = old_deps - new_deps
            if added_deps:
                result['changes'].append({
                    'field': 'dependencies_added',
                    'values': sorted(added_deps),
                })
            if removed_deps:
                result['changes'].append({
                    'field': 'dependencies_removed',
                    'values': sorted(removed_deps),
                })

    elif source == 'github':
        old_data = _http_get_json(
            f'https://api.github.com/repos/{package}/releases/tags/{old_ver}')
        new_data = _http_get_json(
            f'https://api.github.com/repos/{package}/releases/tags/{new_ver}')

        if new_data:
            result['changes'].append({
                'field': 'release_notes',
                'value': (new_data.get('body', '') or '')[:2000],
            })

        # Get commits between tags
        compare = _http_get_json(
            f'https://api.github.com/repos/{package}/compare/{old_ver}...{new_ver}')
        if compare:
            result['total_commits'] = compare.get('total_commits', 0)
            result['files_changed'] = len(compare.get('files', []))
            commits = compare.get('commits', [])
            result['commit_messages'] = [
                c.get('commit', {}).get('message', '').split('\n')[0]
                for c in commits[:20]
            ]

    return result


def generate_report(packages: list, sources: Optional[dict] = None) -> VersionReport:
    """Generate a version tracking report for multiple packages.

    Args:
        packages: List of package names.
        sources: Optional dict mapping package -> source. Defaults to 'pypi'.

    Returns:
        VersionReport with all package info.
    """
    if sources is None:
        sources = {}

    report = VersionReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
    )

    for pkg in packages:
        source = sources.get(pkg, 'pypi')
        print(f"  Checking {pkg} ({source})...", file=sys.stderr)

        try:
            if source == 'pypi':
                info = check_pypi(pkg)
            elif source == 'npm':
                info = check_npm(pkg)
            elif source == 'github':
                info = check_github(pkg)
            else:
                info = VersionInfo(package=pkg, source=source,
                                   error=f"Unknown source: {source}")

            report.packages.append(info)
            if info.error:
                report.errors += 1
        except Exception as e:
            report.packages.append(VersionInfo(
                package=pkg, source=source, error=str(e),
                checked_at=datetime.now(timezone.utc).isoformat()
            ))
            report.errors += 1

        # Rate limiting
        time.sleep(0.5)

    report.total_checked = len(packages)
    return report


def report_to_json(report: VersionReport) -> str:
    """Serialize report to JSON."""
    data = {
        'generated_at': report.generated_at,
        'total_checked': report.total_checked,
        'errors': report.errors,
        'packages': [asdict(p) for p in report.packages],
    }
    return json.dumps(data, indent=2, ensure_ascii=False)


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description='Track software package versions.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest='command', help='Command to run')

    # check command
    check_p = subparsers.add_parser('check', help='Check a single package')
    check_p.add_argument('package', help='Package name')
    check_p.add_argument('--source', '-s', choices=['pypi', 'npm', 'github'],
                         default='pypi', help='Package source (default: pypi)')

    # compare command
    comp_p = subparsers.add_parser('compare', help='Compare two versions')
    comp_p.add_argument('package', help='Package name')
    comp_p.add_argument('old_version', help='Older version')
    comp_p.add_argument('new_version', help='Newer version')
    comp_p.add_argument('--source', '-s', choices=['pypi', 'npm', 'github'],
                         default='pypi')

    # track command
    track_p = subparsers.add_parser('track', help='Track packages from a JSON file')
    track_p.add_argument('packages_file', help='JSON file with package list')

    # report command
    report_p = subparsers.add_parser('report', help='Generate version report')
    report_p.add_argument('--packages', '-p', help='Comma-separated package names')
    report_p.add_argument('--output', '-o', help='Output file')

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == 'check':
        if args.source == 'pypi':
            info = check_pypi(args.package)
        elif args.source == 'npm':
            info = check_npm(args.package)
        elif args.source == 'github':
            info = check_github(args.package)
        print(json.dumps(asdict(info), indent=2))

    elif args.command == 'compare':
        result = compare_versions(args.package, args.old_version,
                                  args.new_version, args.source)
        print(json.dumps(result, indent=2))

    elif args.command == 'track':
        with open(args.packages_file) as f:
            config = json.load(f)
        packages = config.get('packages', [])
        sources = config.get('sources', {})
        report = generate_report(packages, sources)
        print(report_to_json(report))

    elif args.command == 'report':
        if not args.packages:
            print("Error: --packages required", file=sys.stderr)
            sys.exit(1)
        packages = [p.strip() for p in args.packages.split(',')]
        report = generate_report(packages)
        output = report_to_json(report)
        if args.output:
            with open(args.output, 'w') as f:
                f.write(output)
            print(f"Report written to {args.output}", file=sys.stderr)
        else:
            print(output)


if __name__ == '__main__':
    main()
