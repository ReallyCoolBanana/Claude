#!/usr/bin/env python3
"""Best Practices Linter Config Generator

Takes best practice rules and generates linter configurations for
pylint, flake8, and ruff. Designed for use by DATA-GATHER-PRACTICES
teams to codify best practices into enforceable linter rules.

Usage:
    # Generate configs from a practices JSON file
    python practices_linter.py generate --input practices.json --linter ruff --output ruff.toml

    # Generate for all supported linters
    python practices_linter.py generate --input practices.json --linter all --output-dir ./configs/

    # List available practice-to-rule mappings
    python practices_linter.py mappings

    # Add a custom practice mapping
    python practices_linter.py add-mapping \
        --practice "no-bare-except" \
        --description "Never use bare except clauses" \
        --pylint "W0702" \
        --flake8 "E722" \
        --ruff "E722"

    # Validate a practices JSON file
    python practices_linter.py validate --input practices.json

    # Show what rules a practice maps to
    python practices_linter.py lookup --practice "no-bare-except"
"""

import argparse
import configparser
import json
import os
import sys
from datetime import datetime
from typing import Any

DEFAULT_MAPPINGS_PATH = os.path.join(os.path.dirname(__file__), "practice_mappings.json")

# Built-in mappings from common best practices to linter rules
BUILTIN_MAPPINGS: dict[str, dict] = {
    "no-bare-except": {
        "description": "Never use bare except clauses",
        "category": "error-handling",
        "pylint": {"codes": ["W0702"], "options": {}},
        "flake8": {"codes": ["E722"], "options": {}},
        "ruff": {"codes": ["E722"], "options": {}},
    },
    "no-wildcard-import": {
        "description": "Avoid wildcard imports (from x import *)",
        "category": "imports",
        "pylint": {"codes": ["W0401"], "options": {}},
        "flake8": {"codes": ["F403", "F405"], "options": {}},
        "ruff": {"codes": ["F403", "F405"], "options": {}},
    },
    "no-unused-imports": {
        "description": "Remove unused imports",
        "category": "imports",
        "pylint": {"codes": ["W0611"], "options": {}},
        "flake8": {"codes": ["F401"], "options": {}},
        "ruff": {"codes": ["F401"], "options": {}},
    },
    "no-unused-variables": {
        "description": "Remove unused variables",
        "category": "code-quality",
        "pylint": {"codes": ["W0612"], "options": {}},
        "flake8": {"codes": ["F841"], "options": {}},
        "ruff": {"codes": ["F841"], "options": {}},
    },
    "max-line-length": {
        "description": "Enforce maximum line length",
        "category": "formatting",
        "pylint": {"codes": ["C0301"], "options": {"max-line-length": 120}},
        "flake8": {"codes": ["E501"], "options": {"max-line-length": 120}},
        "ruff": {"codes": ["E501"], "options": {"line-length": 120}},
    },
    "require-docstrings": {
        "description": "Require docstrings for public functions/classes",
        "category": "documentation",
        "pylint": {"codes": ["C0114", "C0115", "C0116"], "options": {}},
        "flake8": {"codes": ["D100", "D101", "D102", "D103"], "options": {}, "plugins": ["flake8-docstrings"]},
        "ruff": {"codes": ["D100", "D101", "D102", "D103"], "options": {}},
    },
    "no-mutable-default-args": {
        "description": "Do not use mutable default arguments",
        "category": "bugs",
        "pylint": {"codes": ["W0102"], "options": {}},
        "flake8": {"codes": ["B006"], "options": {}, "plugins": ["flake8-bugbear"]},
        "ruff": {"codes": ["B006"], "options": {}},
    },
    "no-assert-in-production": {
        "description": "Avoid assert statements in production code",
        "category": "reliability",
        "pylint": {"codes": [], "options": {}},
        "flake8": {"codes": ["S101"], "options": {}, "plugins": ["flake8-bandit"]},
        "ruff": {"codes": ["S101"], "options": {}},
    },
    "use-f-strings": {
        "description": "Prefer f-strings over format() or % formatting",
        "category": "style",
        "pylint": {"codes": ["C0209"], "options": {}},
        "flake8": {"codes": [], "options": {}},
        "ruff": {"codes": ["UP031", "UP032"], "options": {}},
    },
    "no-print-statements": {
        "description": "Use logging instead of print statements",
        "category": "logging",
        "pylint": {"codes": [], "options": {}},
        "flake8": {"codes": ["T201"], "options": {}, "plugins": ["flake8-print"]},
        "ruff": {"codes": ["T201"], "options": {}},
    },
    "type-annotations": {
        "description": "Use type annotations for function signatures",
        "category": "typing",
        "pylint": {"codes": [], "options": {}},
        "flake8": {"codes": ["ANN001", "ANN201"], "options": {}, "plugins": ["flake8-annotations"]},
        "ruff": {"codes": ["ANN001", "ANN201"], "options": {}},
    },
    "no-global-statement": {
        "description": "Avoid global statement",
        "category": "code-quality",
        "pylint": {"codes": ["W0603"], "options": {}},
        "flake8": {"codes": ["W0603"], "options": {}},
        "ruff": {"codes": ["PLW0603"], "options": {}},
    },
    "naming-conventions": {
        "description": "Follow PEP 8 naming conventions",
        "category": "style",
        "pylint": {"codes": ["C0103"], "options": {}},
        "flake8": {"codes": ["N801", "N802", "N803", "N806"], "options": {}, "plugins": ["pep8-naming"]},
        "ruff": {"codes": ["N801", "N802", "N803", "N806"], "options": {}},
    },
    "no-complex-functions": {
        "description": "Keep function complexity low (max cyclomatic complexity)",
        "category": "complexity",
        "pylint": {"codes": ["R1260"], "options": {"max-complexity": 10}},
        "flake8": {"codes": ["C901"], "options": {"max-complexity": 10}},
        "ruff": {"codes": ["C901"], "options": {}},
    },
    "secure-coding": {
        "description": "Follow secure coding practices (no hardcoded passwords, SQL injection, etc.)",
        "category": "security",
        "pylint": {"codes": [], "options": {}},
        "flake8": {"codes": ["S105", "S106", "S107", "S608"], "options": {}, "plugins": ["flake8-bandit"]},
        "ruff": {"codes": ["S105", "S106", "S107", "S608"], "options": {}},
    },
}


def _load_mappings(path: str) -> dict[str, dict]:
    """Load custom mappings, falling back to builtins."""
    mappings = dict(BUILTIN_MAPPINGS)
    if os.path.exists(path):
        with open(path, "r") as f:
            custom = json.load(f)
        mappings.update(custom.get("mappings", {}))
    return mappings


def _save_mappings(mappings: dict[str, dict], path: str) -> None:
    """Save custom mappings to disk."""
    os.makedirs(os.path.dirname(path) if os.path.dirname(path) else ".", exist_ok=True)
    with open(path, "w") as f:
        json.dump({
            "version": "1.0",
            "last_updated": datetime.now().strftime("%Y-%m-%d"),
            "mappings": mappings,
        }, f, indent=2)


def _resolve_practices(practices_input: list[dict], mappings: dict) -> list[dict]:
    """Resolve practice definitions into linter rule sets.

    Each practice in the input can have:
    - name: matches a key in mappings
    - enabled: bool (default True)
    - options: override options for this practice
    """
    resolved = []
    for practice in practices_input:
        name = practice.get("name", "")
        if name not in mappings:
            resolved.append({
                "name": name,
                "status": "unmapped",
                "message": f"No linter mapping found for practice '{name}'",
            })
            continue
        if not practice.get("enabled", True):
            continue

        mapping = dict(mappings[name])
        # Apply option overrides
        overrides = practice.get("options", {})
        if overrides:
            for linter in ("pylint", "flake8", "ruff"):
                if linter in mapping and linter in overrides:
                    mapping[linter]["options"].update(overrides[linter])

        resolved.append({"name": name, "status": "mapped", "mapping": mapping})
    return resolved


def generate_pylint_config(resolved: list[dict]) -> str:
    """Generate a .pylintrc configuration."""
    enable_codes = []
    options: dict[str, Any] = {}

    for item in resolved:
        if item.get("status") != "mapped":
            continue
        mapping = item["mapping"]
        pylint = mapping.get("pylint", {})
        enable_codes.extend(pylint.get("codes", []))
        options.update(pylint.get("options", {}))

    lines = [
        "# Generated by practices_linter.py",
        f"# Date: {datetime.now().strftime('%Y-%m-%d')}",
        "",
        "[MAIN]",
        "",
        "[MESSAGES CONTROL]",
    ]
    if enable_codes:
        lines.append(f"enable={','.join(sorted(set(enable_codes)))}")
    lines.append("")
    lines.append("[FORMAT]")
    if "max-line-length" in options:
        lines.append(f"max-line-length={options['max-line-length']}")
    lines.append("")
    lines.append("[DESIGN]")
    if "max-complexity" in options:
        lines.append(f"max-complexity={options['max-complexity']}")
    lines.append("")
    return "\n".join(lines)


def generate_flake8_config(resolved: list[dict]) -> str:
    """Generate a .flake8 configuration."""
    select_codes = []
    options: dict[str, Any] = {}
    plugins = set()

    for item in resolved:
        if item.get("status") != "mapped":
            continue
        mapping = item["mapping"]
        flake8 = mapping.get("flake8", {})
        select_codes.extend(flake8.get("codes", []))
        options.update(flake8.get("options", {}))
        plugins.update(flake8.get("plugins", []))

    lines = [
        "# Generated by practices_linter.py",
        f"# Date: {datetime.now().strftime('%Y-%m-%d')}",
        "",
        "[flake8]",
    ]
    if select_codes:
        lines.append(f"select = {','.join(sorted(set(select_codes)))}")
    if "max-line-length" in options:
        lines.append(f"max-line-length = {options['max-line-length']}")
    if "max-complexity" in options:
        lines.append(f"max-complexity = {options['max-complexity']}")
    if plugins:
        lines.append(f"# Required plugins: {', '.join(sorted(plugins))}")
    lines.append("")
    return "\n".join(lines)


def generate_ruff_config(resolved: list[dict]) -> str:
    """Generate a ruff.toml configuration."""
    select_codes = []
    options: dict[str, Any] = {}

    for item in resolved:
        if item.get("status") != "mapped":
            continue
        mapping = item["mapping"]
        ruff = mapping.get("ruff", {})
        select_codes.extend(ruff.get("codes", []))
        options.update(ruff.get("options", {}))

    lines = [
        "# Generated by practices_linter.py",
        f"# Date: {datetime.now().strftime('%Y-%m-%d')}",
        "",
    ]
    if "line-length" in options:
        lines.append(f"line-length = {options['line-length']}")
    lines.append("")
    lines.append("[lint]")
    if select_codes:
        codes_str = ", ".join(f'"{c}"' for c in sorted(set(select_codes)))
        lines.append(f"select = [{codes_str}]")
    lines.append("")
    return "\n".join(lines)


def generate_config(practices_input: list[dict], linter: str, mappings: dict) -> str:
    """Generate linter configuration from practices.

    Args:
        practices_input: List of practice dicts with 'name' and optional 'enabled', 'options'
        linter: One of 'pylint', 'flake8', 'ruff'
        mappings: Practice-to-rule mappings
    Returns:
        Config file content as string
    """
    resolved = _resolve_practices(practices_input, mappings)
    generators = {
        "pylint": generate_pylint_config,
        "flake8": generate_flake8_config,
        "ruff": generate_ruff_config,
    }
    if linter not in generators:
        raise ValueError(f"Unsupported linter: {linter}. Supported: {list(generators.keys())}")
    return generators[linter](resolved)


def validate_practices(practices_input: list[dict], mappings: dict) -> dict:
    """Validate a practices definition and return a report."""
    report = {"valid": True, "total": len(practices_input), "mapped": 0, "unmapped": [], "disabled": 0}
    for practice in practices_input:
        name = practice.get("name", "")
        if not practice.get("enabled", True):
            report["disabled"] += 1
            continue
        if name in mappings:
            report["mapped"] += 1
        else:
            report["unmapped"].append(name)
    if report["unmapped"]:
        report["valid"] = False
    return report


def main():
    parser = argparse.ArgumentParser(description="Best Practices Linter Config Generator")
    parser.add_argument("--mappings-file", default=DEFAULT_MAPPINGS_PATH, help="Path to custom mappings file")
    sub = parser.add_subparsers(dest="command")

    # generate
    p_gen = sub.add_parser("generate", help="Generate linter config from practices")
    p_gen.add_argument("--input", required=True, help="Practices JSON file")
    p_gen.add_argument("--linter", choices=["pylint", "flake8", "ruff", "all"], required=True)
    p_gen.add_argument("--output", default=None, help="Output file (stdout if omitted)")
    p_gen.add_argument("--output-dir", default=None, help="Output directory (for --linter all)")

    # mappings
    sub.add_parser("mappings", help="List available practice-to-rule mappings")

    # add-mapping
    p_add = sub.add_parser("add-mapping", help="Add a custom practice mapping")
    p_add.add_argument("--practice", required=True)
    p_add.add_argument("--description", default="")
    p_add.add_argument("--category", default="custom")
    p_add.add_argument("--pylint", default="", help="Comma-separated pylint codes")
    p_add.add_argument("--flake8", default="", help="Comma-separated flake8 codes")
    p_add.add_argument("--ruff", default="", help="Comma-separated ruff codes")

    # validate
    p_val = sub.add_parser("validate", help="Validate a practices JSON file")
    p_val.add_argument("--input", required=True)

    # lookup
    p_look = sub.add_parser("lookup", help="Look up rules for a practice")
    p_look.add_argument("--practice", required=True)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    mappings = _load_mappings(args.mappings_file)

    if args.command == "generate":
        with open(args.input, "r") as f:
            practices_data = json.load(f)
        if isinstance(practices_data, dict) and "practices" in practices_data:
            practices_data = practices_data["practices"]

        if args.linter == "all":
            out_dir = args.output_dir or "."
            os.makedirs(out_dir, exist_ok=True)
            filenames = {"pylint": ".pylintrc", "flake8": ".flake8", "ruff": "ruff.toml"}
            for linter, fname in filenames.items():
                config = generate_config(practices_data, linter, mappings)
                path = os.path.join(out_dir, fname)
                with open(path, "w") as f:
                    f.write(config)
                print(f"Generated {path}")
        else:
            config = generate_config(practices_data, args.linter, mappings)
            if args.output:
                with open(args.output, "w") as f:
                    f.write(config)
                print(f"Generated {args.output}")
            else:
                print(config)

    elif args.command == "mappings":
        for name, mapping in sorted(mappings.items()):
            desc = mapping.get("description", "")
            cat = mapping.get("category", "")
            pylint_codes = mapping.get("pylint", {}).get("codes", [])
            flake8_codes = mapping.get("flake8", {}).get("codes", [])
            ruff_codes = mapping.get("ruff", {}).get("codes", [])
            print(f"{name}:")
            print(f"  Description: {desc}")
            print(f"  Category:    {cat}")
            print(f"  Pylint:      {', '.join(pylint_codes) if pylint_codes else '(none)'}")
            print(f"  Flake8:      {', '.join(flake8_codes) if flake8_codes else '(none)'}")
            print(f"  Ruff:        {', '.join(ruff_codes) if ruff_codes else '(none)'}")
            print()

    elif args.command == "add-mapping":
        new_mapping = {
            "description": args.description,
            "category": args.category,
            "pylint": {"codes": [c.strip() for c in args.pylint.split(",") if c.strip()], "options": {}},
            "flake8": {"codes": [c.strip() for c in args.flake8.split(",") if c.strip()], "options": {}},
            "ruff": {"codes": [c.strip() for c in args.ruff.split(",") if c.strip()], "options": {}},
        }
        mappings[args.practice] = new_mapping
        _save_mappings(mappings, args.mappings_file)
        print(f"Added mapping for '{args.practice}'")
        print(json.dumps(new_mapping, indent=2))

    elif args.command == "validate":
        with open(args.input, "r") as f:
            practices_data = json.load(f)
        if isinstance(practices_data, dict) and "practices" in practices_data:
            practices_data = practices_data["practices"]
        report = validate_practices(practices_data, mappings)
        print(json.dumps(report, indent=2))

    elif args.command == "lookup":
        if args.practice in mappings:
            print(json.dumps(mappings[args.practice], indent=2))
        else:
            print(f"No mapping found for '{args.practice}'")
            sys.exit(1)


if __name__ == "__main__":
    main()
