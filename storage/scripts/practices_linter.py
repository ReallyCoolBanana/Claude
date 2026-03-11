#!/usr/bin/env python3
"""Best Practices Linter Config Generator

Takes best practice rules and generates linter configurations for
pylint, flake8, and ruff. Designed for use by DATA-GATHER-PRACTICES
teams to enforce discovered best practices.

Usage:
    # Generate ruff config from a practices JSON file
    python practices_linter.py generate --practices practices.json --linter ruff

    # Generate pylint config
    python practices_linter.py generate --practices practices.json --linter pylint

    # Generate flake8 config
    python practices_linter.py generate --practices practices.json --linter flake8

    # Generate all configs at once
    python practices_linter.py generate --practices practices.json --linter all --outdir ./configs

    # List available rule mappings
    python practices_linter.py list-rules --linter ruff

    # Validate a practices file
    python practices_linter.py validate --practices practices.json

    # Create a sample practices file
    python practices_linter.py sample --output sample_practices.json

Practices JSON format:
    [
        {
            "id": "BP-001",
            "name": "Use type hints",
            "category": "typing",
            "severity": "warning",
            "description": "All public functions should have type annotations",
            "rules": ["ANN001", "ANN201"],
            "linter_configs": {
                "ruff": {"select": ["ANN"]},
                "pylint": {"enable": ["missing-function-docstring"]},
                "flake8": {"select": ["ANN"]}
            }
        }
    ]
"""

import argparse
import configparser
import json
import os
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple


# Maps practice categories to linter rules
RULE_MAPPINGS: Dict[str, Dict[str, Any]] = {
    "naming": {
        "description": "Naming conventions and consistency",
        "ruff": {
            "select": ["N"],  # pep8-naming
            "rules": {
                "N801": "Class names should use CapWords convention",
                "N802": "Function name should be lowercase",
                "N803": "Argument name should be lowercase",
                "N806": "Variable in function should be lowercase",
                "N815": "Variable in class scope should not be mixedCase",
            }
        },
        "pylint": {
            "enable": ["invalid-name", "disallowed-name"],
            "options": {"good-names": "i,j,k,v,e,f,x,y,_"}
        },
        "flake8": {
            "select": ["N8"],
            "options": {}
        }
    },
    "typing": {
        "description": "Type annotations and type safety",
        "ruff": {
            "select": ["ANN", "TCH"],
            "rules": {
                "ANN001": "Missing type annotation for function argument",
                "ANN201": "Missing return type annotation for public function",
                "ANN202": "Missing return type annotation for private function",
                "TCH001": "Move application import into TYPE_CHECKING block",
            }
        },
        "pylint": {
            "enable": [],
            "options": {}
        },
        "flake8": {
            "select": ["ANN"],
            "options": {}
        }
    },
    "documentation": {
        "description": "Docstrings and documentation",
        "ruff": {
            "select": ["D"],
            "rules": {
                "D100": "Missing docstring in public module",
                "D101": "Missing docstring in public class",
                "D102": "Missing docstring in public method",
                "D103": "Missing docstring in public function",
                "D107": "Missing docstring in __init__",
                "D200": "No blank lines allowed after function docstring",
            }
        },
        "pylint": {
            "enable": ["missing-module-docstring", "missing-class-docstring",
                       "missing-function-docstring"],
            "options": {}
        },
        "flake8": {
            "select": ["D"],
            "options": {"docstring-convention": "google"}
        }
    },
    "complexity": {
        "description": "Code complexity limits",
        "ruff": {
            "select": ["C90", "PLR"],
            "rules": {
                "C901": "Function is too complex",
                "PLR0911": "Too many return statements",
                "PLR0912": "Too many branches",
                "PLR0913": "Too many arguments to function call",
            }
        },
        "pylint": {
            "enable": ["too-many-branches", "too-many-return-statements",
                       "too-many-arguments", "too-many-locals"],
            "options": {"max-args": "5", "max-locals": "15",
                        "max-branches": "12", "max-returns": "6"}
        },
        "flake8": {
            "select": ["C9"],
            "options": {"max-complexity": "10"}
        }
    },
    "security": {
        "description": "Security-related checks",
        "ruff": {
            "select": ["S"],
            "rules": {
                "S101": "Use of assert detected",
                "S102": "Use of exec detected",
                "S103": "Bad file permissions",
                "S104": "Binding to all interfaces",
                "S105": "Hardcoded password string",
                "S106": "Hardcoded password in function argument",
                "S107": "Hardcoded password default",
                "S108": "Insecure temp file/directory usage",
                "S110": "Try-except-pass detected",
                "S301": "Pickle usage detected",
                "S311": "Standard pseudo-random generators not suitable for security",
            }
        },
        "pylint": {
            "enable": ["exec-used", "eval-used"],
            "options": {}
        },
        "flake8": {
            "select": ["S"],
            "options": {}
        }
    },
    "imports": {
        "description": "Import organization and hygiene",
        "ruff": {
            "select": ["I", "F401", "F811"],
            "rules": {
                "I001": "Import block is un-sorted or un-formatted",
                "F401": "Unused import",
                "F811": "Redefinition of unused name",
            }
        },
        "pylint": {
            "enable": ["unused-import", "reimported", "wrong-import-order",
                       "ungrouped-imports"],
            "options": {}
        },
        "flake8": {
            "select": ["I", "F401"],
            "options": {}
        }
    },
    "error-handling": {
        "description": "Exception and error handling patterns",
        "ruff": {
            "select": ["TRY", "EM", "B"],
            "rules": {
                "TRY002": "Create your own exception",
                "TRY003": "Avoid specifying long messages outside the exception class",
                "TRY300": "Consider using else block",
                "EM101": "Exception must not use a string literal",
                "B904": "Within except clause, raise from err",
            }
        },
        "pylint": {
            "enable": ["bare-except", "broad-except", "try-except-raise",
                       "raising-bad-type"],
            "options": {}
        },
        "flake8": {
            "select": ["E7", "B"],
            "options": {}
        }
    },
    "testing": {
        "description": "Testing best practices",
        "ruff": {
            "select": ["PT"],
            "rules": {
                "PT001": "Use @pytest.fixture over @pytest.fixture()",
                "PT006": "Wrong type for pytest.mark.parametrize names",
                "PT018": "Assertion should be broken down into multiple parts",
                "PT023": "Use @pytest.mark.xyz over @pytest.mark.xyz()",
            }
        },
        "pylint": {
            "enable": [],
            "options": {}
        },
        "flake8": {
            "select": ["PT"],
            "options": {}
        }
    },
    "style": {
        "description": "General code style",
        "ruff": {
            "select": ["E", "W", "UP", "SIM"],
            "rules": {
                "E501": "Line too long",
                "W291": "Trailing whitespace",
                "UP": "pyupgrade - modern Python syntax",
                "SIM": "flake8-simplify - simplifiable constructs",
            }
        },
        "pylint": {
            "enable": ["line-too-long", "trailing-whitespace",
                       "unnecessary-pass", "consider-using-f-string"],
            "options": {"max-line-length": "120"}
        },
        "flake8": {
            "select": ["E", "W"],
            "options": {"max-line-length": "120"}
        }
    },
    "performance": {
        "description": "Performance and efficiency patterns",
        "ruff": {
            "select": ["PERF"],
            "rules": {
                "PERF101": "Do not cast an iterable to list before iterating",
                "PERF102": "Use dict.keys/values/items instead of list comprehension",
                "PERF401": "Use list comprehension instead of for-append loop",
                "PERF403": "Use dict comprehension instead of for-loop",
            }
        },
        "pylint": {
            "enable": ["consider-using-generator", "use-a-generator"],
            "options": {}
        },
        "flake8": {
            "select": [],
            "options": {}
        }
    }
}


def load_practices(path: str) -> List[Dict[str, Any]]:
    """Load practices from a JSON file."""
    with open(path, 'r') as f:
        data = json.load(f)
    if isinstance(data, dict) and "practices" in data:
        data = data["practices"]
    return data


def validate_practices(practices: List[Dict[str, Any]]) -> List[str]:
    """Validate practice entries. Returns list of issues."""
    issues = []
    seen_ids = set()
    for i, p in enumerate(practices):
        if "id" not in p:
            issues.append(f"Practice {i}: missing 'id'")
        elif p["id"] in seen_ids:
            issues.append(f"Practice {i}: duplicate id '{p['id']}'")
        else:
            seen_ids.add(p["id"])
        if "name" not in p:
            issues.append(f"Practice {i}: missing 'name'")
        if "category" not in p:
            issues.append(f"Practice {i}: missing 'category'")
        if "severity" in p and p["severity"] not in ("error", "warning", "info", "convention"):
            issues.append(f"Practice {i}: invalid severity '{p['severity']}'")
    return issues


def _collect_ruff_config(practices: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Build ruff configuration from practices."""
    select = set()
    ignore = set()
    per_file_ignores: Dict[str, List[str]] = {}
    options: Dict[str, Any] = {}

    for p in practices:
        cat = p.get("category", "")
        severity = p.get("severity", "warning")

        # Use custom linter_configs if provided
        if "linter_configs" in p and "ruff" in p["linter_configs"]:
            rc = p["linter_configs"]["ruff"]
            select.update(rc.get("select", []))
            ignore.update(rc.get("ignore", []))
            for k, v in rc.get("per-file-ignores", {}).items():
                per_file_ignores.setdefault(k, []).extend(v)
            options.update(rc.get("options", {}))
            continue

        # Map category to rules
        if cat in RULE_MAPPINGS:
            mapping = RULE_MAPPINGS[cat]["ruff"]
            sel = mapping.get("select", [])
            if isinstance(sel, list):
                select.update(sel)
            elif isinstance(sel, str):
                select.add(sel)

    config = {
        "line-length": int(options.get("line-length", 120)),
        "target-version": options.get("target-version", "py39"),
        "lint": {
            "select": sorted(select),
        }
    }
    if ignore:
        config["lint"]["ignore"] = sorted(ignore)
    if per_file_ignores:
        config["lint"]["per-file-ignores"] = per_file_ignores
    return config


def generate_ruff_toml(practices: List[Dict[str, Any]]) -> str:
    """Generate ruff.toml configuration."""
    config = _collect_ruff_config(practices)
    lines = [
        "# Auto-generated ruff configuration from best practices",
        f"# Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        f'line-length = {config["line-length"]}',
        f'target-version = "{config["target-version"]}"',
        "",
        "[lint]",
        f'select = {json.dumps(config["lint"]["select"])}',
    ]
    if "ignore" in config["lint"]:
        lines.append(f'ignore = {json.dumps(config["lint"]["ignore"])}')
    if "per-file-ignores" in config["lint"]:
        lines.append("")
        lines.append("[lint.per-file-ignores]")
        for pattern, rules in config["lint"]["per-file-ignores"].items():
            lines.append(f'"{pattern}" = {json.dumps(rules)}')

    return "\n".join(lines) + "\n"


def _collect_pylint_config(practices: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Build pylint configuration from practices."""
    enable = set()
    disable = set()
    options: Dict[str, str] = {}

    for p in practices:
        cat = p.get("category", "")
        if "linter_configs" in p and "pylint" in p["linter_configs"]:
            pc = p["linter_configs"]["pylint"]
            enable.update(pc.get("enable", []))
            disable.update(pc.get("disable", []))
            options.update(pc.get("options", {}))
            continue
        if cat in RULE_MAPPINGS:
            mapping = RULE_MAPPINGS[cat]["pylint"]
            enable.update(mapping.get("enable", []))
            options.update(mapping.get("options", {}))

    return {"enable": sorted(enable), "disable": sorted(disable), "options": options}


def generate_pylintrc(practices: List[Dict[str, Any]]) -> str:
    """Generate .pylintrc configuration."""
    config = _collect_pylint_config(practices)
    cp = configparser.ConfigParser()

    cp["MAIN"] = {"jobs": "0", "suggestion-mode": "yes"}
    cp["MESSAGES CONTROL"] = {}
    if config["enable"]:
        cp["MESSAGES CONTROL"]["enable"] = ",\n    ".join(config["enable"])
    if config["disable"]:
        cp["MESSAGES CONTROL"]["disable"] = ",\n    ".join(config["disable"])

    cp["FORMAT"] = {
        "max-line-length": config["options"].get("max-line-length", "120"),
        "max-module-lines": "1000",
    }
    cp["DESIGN"] = {
        "max-args": config["options"].get("max-args", "5"),
        "max-locals": config["options"].get("max-locals", "15"),
        "max-branches": config["options"].get("max-branches", "12"),
        "max-returns": config["options"].get("max-returns", "6"),
        "max-statements": config["options"].get("max-statements", "50"),
    }

    import io
    buf = io.StringIO()
    buf.write(f"# Auto-generated pylint configuration from best practices\n")
    buf.write(f"# Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n")
    cp.write(buf)
    return buf.getvalue()


def _collect_flake8_config(practices: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Build flake8 configuration from practices."""
    select = set()
    ignore = set()
    options: Dict[str, str] = {}

    for p in practices:
        cat = p.get("category", "")
        if "linter_configs" in p and "flake8" in p["linter_configs"]:
            fc = p["linter_configs"]["flake8"]
            select.update(fc.get("select", []))
            ignore.update(fc.get("ignore", []))
            options.update(fc.get("options", {}))
            continue
        if cat in RULE_MAPPINGS:
            mapping = RULE_MAPPINGS[cat]["flake8"]
            sel = mapping.get("select", [])
            if isinstance(sel, list):
                select.update(sel)
            options.update(mapping.get("options", {}))

    return {"select": sorted(select), "ignore": sorted(ignore), "options": options}


def generate_flake8(practices: List[Dict[str, Any]]) -> str:
    """Generate .flake8 configuration."""
    config = _collect_flake8_config(practices)
    cp = configparser.ConfigParser()
    section = {
        "max-line-length": config["options"].get("max-line-length", "120"),
        "max-complexity": config["options"].get("max-complexity", "10"),
    }
    if config["select"]:
        section["select"] = ",".join(config["select"])
    if config["ignore"]:
        section["ignore"] = ",".join(config["ignore"])
    if "docstring-convention" in config["options"]:
        section["docstring-convention"] = config["options"]["docstring-convention"]

    cp["flake8"] = section

    import io
    buf = io.StringIO()
    buf.write(f"# Auto-generated flake8 configuration from best practices\n")
    buf.write(f"# Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n")
    cp.write(buf)
    return buf.getvalue()


GENERATORS = {
    "ruff": ("ruff.toml", generate_ruff_toml),
    "pylint": (".pylintrc", generate_pylintrc),
    "flake8": (".flake8", generate_flake8),
}


def generate_sample_practices() -> List[Dict[str, Any]]:
    """Generate a sample practices file."""
    return [
        {
            "id": "BP-001",
            "name": "Use type hints for all public functions",
            "category": "typing",
            "severity": "warning",
            "description": "All public functions and methods should have type annotations for parameters and return types."
        },
        {
            "id": "BP-002",
            "name": "Write docstrings for public APIs",
            "category": "documentation",
            "severity": "warning",
            "description": "Every public module, class, and function should have a docstring."
        },
        {
            "id": "BP-003",
            "name": "Keep functions simple",
            "category": "complexity",
            "severity": "error",
            "description": "Functions should have low cyclomatic complexity (max 10)."
        },
        {
            "id": "BP-004",
            "name": "No hardcoded secrets",
            "category": "security",
            "severity": "error",
            "description": "Never hardcode passwords, API keys, or other secrets in source code."
        },
        {
            "id": "BP-005",
            "name": "Organize imports",
            "category": "imports",
            "severity": "convention",
            "description": "Imports should be sorted and grouped: stdlib, third-party, local."
        },
        {
            "id": "BP-006",
            "name": "Proper exception handling",
            "category": "error-handling",
            "severity": "warning",
            "description": "Avoid bare except clauses. Use specific exception types and chain exceptions."
        },
        {
            "id": "BP-007",
            "name": "Follow PEP 8 naming",
            "category": "naming",
            "severity": "convention",
            "description": "Use snake_case for functions/variables, CamelCase for classes."
        },
        {
            "id": "BP-008",
            "name": "Use list comprehensions",
            "category": "performance",
            "severity": "info",
            "description": "Prefer list/dict comprehensions over for-append loops."
        }
    ]


def main():
    parser = argparse.ArgumentParser(
        description="Best Practices Linter Config Generator",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    subparsers = parser.add_subparsers(dest="command")

    # generate
    gen_p = subparsers.add_parser("generate", help="Generate linter config from practices")
    gen_p.add_argument("--practices", required=True, help="Path to practices JSON file")
    gen_p.add_argument("--linter", required=True,
                       choices=["ruff", "pylint", "flake8", "all"],
                       help="Target linter")
    gen_p.add_argument("--outdir", default=".", help="Output directory")

    # list-rules
    list_p = subparsers.add_parser("list-rules", help="List available rule mappings")
    list_p.add_argument("--linter", choices=["ruff", "pylint", "flake8"],
                        help="Show rules for specific linter")
    list_p.add_argument("--category", help="Show rules for specific category")

    # validate
    val_p = subparsers.add_parser("validate", help="Validate a practices file")
    val_p.add_argument("--practices", required=True, help="Path to practices JSON")

    # sample
    sample_p = subparsers.add_parser("sample", help="Generate sample practices file")
    sample_p.add_argument("--output", default="-", help="Output file (- for stdout)")

    args = parser.parse_args()

    if args.command == "generate":
        practices = load_practices(args.practices)
        issues = validate_practices(practices)
        if issues:
            print("Validation warnings:", file=sys.stderr)
            for issue in issues:
                print(f"  - {issue}", file=sys.stderr)

        os.makedirs(args.outdir, exist_ok=True)
        linters = list(GENERATORS.keys()) if args.linter == "all" else [args.linter]
        for linter in linters:
            filename, generator = GENERATORS[linter]
            content = generator(practices)
            outpath = os.path.join(args.outdir, filename)
            with open(outpath, 'w') as f:
                f.write(content)
            print(f"Generated {outpath}")

    elif args.command == "list-rules":
        for cat, mapping in sorted(RULE_MAPPINGS.items()):
            if args.category and cat != args.category:
                continue
            print(f"\n=== {cat} === ({mapping['description']})")
            linters_to_show = [args.linter] if args.linter else ["ruff", "pylint", "flake8"]
            for linter in linters_to_show:
                if linter in mapping:
                    m = mapping[linter]
                    print(f"  [{linter}]")
                    if "rules" in m:
                        for rule_id, desc in m["rules"].items():
                            print(f"    {rule_id}: {desc}")
                    elif "enable" in m:
                        for rule in m["enable"]:
                            print(f"    {rule}")
                    sel = m.get("select", [])
                    if sel and "rules" not in m:
                        print(f"    select: {', '.join(sel) if isinstance(sel, list) else sel}")

    elif args.command == "validate":
        practices = load_practices(args.practices)
        issues = validate_practices(practices)
        if issues:
            print(f"Found {len(issues)} issues:")
            for issue in issues:
                print(f"  - {issue}")
            sys.exit(1)
        else:
            print(f"Valid: {len(practices)} practices, no issues found.")

    elif args.command == "sample":
        sample = generate_sample_practices()
        output = json.dumps(sample, indent=2)
        if args.output == "-":
            print(output)
        else:
            with open(args.output, 'w') as f:
                f.write(output)
            print(f"Sample practices written to {args.output}")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
