#!/usr/bin/env python3
"""Code Pattern Analyzer

Scans a Python codebase for design patterns and anti-patterns using AST parsing.
Reports findings with file locations and severity levels.

Usage:
    # Analyze a single file
    python pattern_analyzer.py scan --path myfile.py

    # Analyze a directory
    python pattern_analyzer.py scan --path ./src

    # Output as JSON
    python pattern_analyzer.py scan --path ./src --format json

    # Only show anti-patterns
    python pattern_analyzer.py scan --path ./src --anti-patterns-only

    # Only show specific categories
    python pattern_analyzer.py scan --path ./src --category creational

    # Show summary statistics
    python pattern_analyzer.py scan --path ./src --summary

    # List all detectable patterns
    python pattern_analyzer.py list-patterns
"""

import argparse
import ast
import json
import os
import sys
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Set, Tuple


@dataclass
class Finding:
    """A single pattern or anti-pattern finding."""
    pattern_name: str
    category: str  # creational, structural, behavioral, anti-pattern
    file_path: str
    line: int
    end_line: Optional[int]
    severity: str  # info, warning, error
    description: str
    code_context: str = ""
    suggestion: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        if d["end_line"] is None:
            del d["end_line"]
        if not d["code_context"]:
            del d["code_context"]
        if not d["suggestion"]:
            del d["suggestion"]
        return d


@dataclass
class AnalysisResult:
    """Complete analysis result for a codebase."""
    files_analyzed: int = 0
    files_with_errors: int = 0
    findings: List[Finding] = field(default_factory=list)
    parse_errors: List[Dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "files_analyzed": self.files_analyzed,
            "files_with_errors": self.files_with_errors,
            "total_findings": len(self.findings),
            "findings": [f.to_dict() for f in self.findings],
            "parse_errors": self.parse_errors,
            "summary": self.get_summary()
        }

    def get_summary(self) -> Dict[str, Any]:
        by_category: Dict[str, int] = {}
        by_pattern: Dict[str, int] = {}
        by_severity: Dict[str, int] = {}
        for f in self.findings:
            by_category[f.category] = by_category.get(f.category, 0) + 1
            by_pattern[f.pattern_name] = by_pattern.get(f.pattern_name, 0) + 1
            by_severity[f.severity] = by_severity.get(f.severity, 0) + 1
        return {
            "by_category": by_category,
            "by_pattern": by_pattern,
            "by_severity": by_severity
        }


class PatternDetector:
    """Detects design patterns and anti-patterns via AST analysis."""

    def __init__(self):
        self.findings: List[Finding] = []
        self.current_file = ""

    def _add(self, pattern_name: str, category: str, node: ast.AST,
             severity: str, description: str, suggestion: str = "",
             end_line: Optional[int] = None):
        self.findings.append(Finding(
            pattern_name=pattern_name,
            category=category,
            file_path=self.current_file,
            line=getattr(node, 'lineno', 0),
            end_line=end_line or getattr(node, 'end_lineno', None),
            severity=severity,
            description=description,
            suggestion=suggestion
        ))

    def analyze_file(self, filepath: str) -> List[Finding]:
        """Analyze a single Python file."""
        self.findings = []
        self.current_file = filepath

        try:
            with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
                source = f.read()
        except (IOError, OSError) as e:
            return []

        try:
            tree = ast.parse(source, filename=filepath)
        except SyntaxError:
            return []

        self._detect_singleton(tree)
        self._detect_factory(tree)
        self._detect_builder(tree)
        self._detect_observer(tree)
        self._detect_decorator_pattern(tree)
        self._detect_strategy(tree)
        self._detect_template_method(tree)
        self._detect_iterator(tree)
        self._detect_context_manager(tree)

        # Anti-patterns
        self._detect_god_class(tree)
        self._detect_long_method(tree)
        self._detect_too_many_params(tree)
        self._detect_deep_nesting(tree)
        self._detect_bare_except(tree)
        self._detect_mutable_default(tree)
        self._detect_global_state(tree)
        self._detect_star_import(tree)
        self._detect_nested_classes_abuse(tree)
        self._detect_return_in_init(tree)

        return self.findings

    # ---- Design Pattern Detectors ----

    def _detect_singleton(self, tree: ast.Module):
        """Detect Singleton pattern implementations."""
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            has_instance_attr = False
            has_new_method = False
            for item in node.body:
                # Class-level _instance attribute
                if isinstance(item, ast.Assign):
                    for target in item.targets:
                        if isinstance(target, ast.Name) and target.id in ("_instance", "__instance", "instance"):
                            has_instance_attr = True
                # __new__ override
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if item.name == "__new__":
                        has_new_method = True
            if has_instance_attr and has_new_method:
                self._add("singleton", "creational", node, "info",
                          f"Singleton pattern detected in class '{node.name}'")
            elif has_instance_attr:
                # Check for classmethod-based singleton
                for item in node.body:
                    if isinstance(item, ast.FunctionDef) and item.decorator_list:
                        for dec in item.decorator_list:
                            if isinstance(dec, ast.Name) and dec.id == "classmethod":
                                if "instance" in item.name.lower() or "get" in item.name.lower():
                                    self._add("singleton", "creational", node, "info",
                                              f"Singleton pattern (classmethod variant) in class '{node.name}'")

    def _detect_factory(self, tree: ast.Module):
        """Detect Factory Method / Abstract Factory patterns."""
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                name_lower = node.name.lower()
                # Factory function naming patterns
                if any(name_lower.startswith(p) for p in ("create_", "make_", "build_", "new_")):
                    # Check if it returns an object instantiation
                    for child in ast.walk(node):
                        if isinstance(child, ast.Return) and child.value:
                            if isinstance(child.value, ast.Call):
                                self._add("factory_method", "creational", node, "info",
                                          f"Factory method pattern: '{node.name}'")
                                break
                # factory_method or create naming inside a class
                if name_lower in ("factory_method", "create_instance"):
                    self._add("factory_method", "creational", node, "info",
                              f"Factory method pattern: '{node.name}'")

            if isinstance(node, ast.ClassDef):
                name_lower = node.name.lower()
                if "factory" in name_lower:
                    self._add("factory_method", "creational", node, "info",
                              f"Factory class detected: '{node.name}'")

    def _detect_builder(self, tree: ast.Module):
        """Detect Builder pattern (method chaining returning self)."""
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            chain_methods = 0
            build_method = False
            for item in node.body:
                if isinstance(item, ast.FunctionDef):
                    if item.name == "build":
                        build_method = True
                    # Check if method returns self
                    for child in ast.walk(item):
                        if isinstance(child, ast.Return) and child.value:
                            if isinstance(child.value, ast.Name) and child.value.id == "self":
                                chain_methods += 1
                                break
            if chain_methods >= 2 and build_method:
                self._add("builder", "creational", node, "info",
                          f"Builder pattern detected in class '{node.name}' "
                          f"({chain_methods} chainable methods + build())")

    def _detect_observer(self, tree: ast.Module):
        """Detect Observer/Pub-Sub pattern."""
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            methods = {item.name for item in node.body if isinstance(item, ast.FunctionDef)}
            observer_methods = {"subscribe", "unsubscribe", "notify"} | \
                               {"attach", "detach", "notify"} | \
                               {"add_observer", "remove_observer", "notify_observers"} | \
                               {"on", "off", "emit"} | \
                               {"register", "unregister", "notify"}
            matches = methods & observer_methods
            if len(matches) >= 2:
                self._add("observer", "behavioral", node, "info",
                          f"Observer/event pattern in class '{node.name}' "
                          f"(methods: {', '.join(sorted(matches))})")

    def _detect_decorator_pattern(self, tree: ast.Module):
        """Detect Decorator design pattern (wrapper classes)."""
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            # Look for __init__ that takes a wrapped/component object
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == "__init__":
                    args = [a.arg for a in item.args.args if a.arg != "self"]
                    wrapper_args = {"wrapped", "component", "decorated", "inner", "delegate"}
                    if wrapper_args & set(args):
                        # Check if it delegates methods via __getattr__
                        for sub in node.body:
                            if isinstance(sub, ast.FunctionDef) and sub.name == "__getattr__":
                                self._add("decorator_pattern", "structural", node, "info",
                                          f"Decorator/Wrapper pattern in class '{node.name}'")
                                break

    def _detect_strategy(self, tree: ast.Module):
        """Detect Strategy pattern (interchangeable algorithms)."""
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            name_lower = node.name.lower()
            if "strategy" in name_lower:
                self._add("strategy", "behavioral", node, "info",
                          f"Strategy pattern class: '{node.name}'")
                continue
            # Abstract base with single method that subclasses override
            methods = [item for item in node.body if isinstance(item, ast.FunctionDef)]
            for m in methods:
                for child in ast.walk(m):
                    if isinstance(child, ast.Raise):
                        if isinstance(child.exc, ast.Call):
                            if isinstance(child.exc.func, ast.Name):
                                if child.exc.func.id == "NotImplementedError":
                                    self._add("strategy", "behavioral", node, "info",
                                              f"Possible Strategy/Template base class '{node.name}' "
                                              f"(raises NotImplementedError in '{m.name}')")
                                    break

    def _detect_template_method(self, tree: ast.Module):
        """Detect Template Method pattern."""
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            abstract_methods = set()
            concrete_methods = set()
            for item in node.body:
                if not isinstance(item, ast.FunctionDef):
                    continue
                raises_not_impl = False
                calls_self = set()
                for child in ast.walk(item):
                    if isinstance(child, ast.Raise) and isinstance(child.exc, ast.Call):
                        if isinstance(child.exc.func, ast.Name) and child.exc.func.id == "NotImplementedError":
                            raises_not_impl = True
                    if isinstance(child, ast.Call):
                        if isinstance(child.func, ast.Attribute):
                            if isinstance(child.func.value, ast.Name) and child.func.value.id == "self":
                                calls_self.add(child.func.attr)
                if raises_not_impl:
                    abstract_methods.add(item.name)
                elif calls_self & abstract_methods and len(calls_self) >= 2:
                    concrete_methods.add(item.name)

            if abstract_methods and concrete_methods:
                self._add("template_method", "behavioral", node, "info",
                          f"Template Method pattern in '{node.name}': "
                          f"template={', '.join(concrete_methods)}, "
                          f"steps={', '.join(abstract_methods)}")

    def _detect_iterator(self, tree: ast.Module):
        """Detect Iterator pattern (__iter__ + __next__)."""
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            methods = {item.name for item in node.body if isinstance(item, ast.FunctionDef)}
            if "__iter__" in methods and "__next__" in methods:
                self._add("iterator", "behavioral", node, "info",
                          f"Iterator pattern in class '{node.name}'")

    def _detect_context_manager(self, tree: ast.Module):
        """Detect Context Manager pattern."""
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            methods = {item.name for item in node.body if isinstance(item, ast.FunctionDef)}
            if "__enter__" in methods and "__exit__" in methods:
                self._add("context_manager", "structural", node, "info",
                          f"Context Manager pattern in class '{node.name}'")
            # Also check for async context manager
            async_methods = {item.name for item in node.body if isinstance(item, ast.AsyncFunctionDef)}
            if "__aenter__" in async_methods and "__aexit__" in async_methods:
                self._add("async_context_manager", "structural", node, "info",
                          f"Async Context Manager pattern in class '{node.name}'")

    # ---- Anti-Pattern Detectors ----

    def _detect_god_class(self, tree: ast.Module):
        """Detect God Class anti-pattern (too many methods/attributes)."""
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            methods = [item for item in node.body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))]
            # Count unique self.xxx assignments across all methods
            attrs: Set[str] = set()
            for m in methods:
                for child in ast.walk(m):
                    if isinstance(child, ast.Assign):
                        for target in child.targets:
                            if isinstance(target, ast.Attribute):
                                if isinstance(target.value, ast.Name) and target.value.id == "self":
                                    attrs.add(target.attr)

            if len(methods) > 20:
                self._add("god_class", "anti-pattern", node, "warning",
                          f"God Class: '{node.name}' has {len(methods)} methods (threshold: 20)",
                          suggestion="Consider splitting into smaller, focused classes (SRP)")
            if len(attrs) > 15:
                self._add("god_class", "anti-pattern", node, "warning",
                          f"God Class: '{node.name}' has {len(attrs)} instance attributes (threshold: 15)",
                          suggestion="Consider grouping related attributes into separate classes")

    def _detect_long_method(self, tree: ast.Module):
        """Detect Long Method anti-pattern."""
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            start = node.lineno
            end = getattr(node, 'end_lineno', start)
            length = end - start + 1
            if length > 50:
                self._add("long_method", "anti-pattern", node, "warning",
                          f"Long method: '{node.name}' is {length} lines (threshold: 50)",
                          suggestion="Consider extracting helper methods",
                          end_line=end)

    def _detect_too_many_params(self, tree: ast.Module):
        """Detect functions with too many parameters."""
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            params = node.args
            count = len(params.args) + len(params.kwonlyargs)
            # Subtract 'self' or 'cls'
            if params.args and params.args[0].arg in ("self", "cls"):
                count -= 1
            if count > 6:
                self._add("too_many_parameters", "anti-pattern", node, "warning",
                          f"Too many parameters: '{node.name}' has {count} params (threshold: 6)",
                          suggestion="Consider using a config object, dataclass, or kwargs")

    def _detect_deep_nesting(self, tree: ast.Module):
        """Detect deeply nested code blocks."""
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            max_depth = self._calc_nesting_depth(node, 0)
            if max_depth > 4:
                self._add("deep_nesting", "anti-pattern", node, "warning",
                          f"Deep nesting in '{node.name}': depth {max_depth} (threshold: 4)",
                          suggestion="Consider early returns, guard clauses, or extracting functions")

    def _calc_nesting_depth(self, node: ast.AST, current: int) -> int:
        """Recursively calculate max nesting depth."""
        max_depth = current
        nesting_nodes = (ast.If, ast.For, ast.While, ast.With, ast.Try,
                         ast.AsyncFor, ast.AsyncWith)
        for child in ast.iter_child_nodes(node):
            if isinstance(child, nesting_nodes):
                depth = self._calc_nesting_depth(child, current + 1)
                max_depth = max(max_depth, depth)
            else:
                depth = self._calc_nesting_depth(child, current)
                max_depth = max(max_depth, depth)
        return max_depth

    def _detect_bare_except(self, tree: ast.Module):
        """Detect bare except clauses."""
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                if node.type is None:
                    self._add("bare_except", "anti-pattern", node, "error",
                              "Bare except clause catches all exceptions including SystemExit and KeyboardInterrupt",
                              suggestion="Use 'except Exception:' or a more specific exception type")

    def _detect_mutable_default(self, tree: ast.Module):
        """Detect mutable default arguments."""
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for default in node.args.defaults + node.args.kw_defaults:
                if default is None:
                    continue
                if isinstance(default, (ast.List, ast.Dict, ast.Set)):
                    self._add("mutable_default", "anti-pattern", node, "error",
                              f"Mutable default argument in '{node.name}'",
                              suggestion="Use None as default and create the mutable object inside the function")

    def _detect_global_state(self, tree: ast.Module):
        """Detect excessive use of global statements."""
        for node in ast.walk(tree):
            if isinstance(node, ast.Global):
                self._add("global_state", "anti-pattern", node, "warning",
                          f"Global statement used for: {', '.join(node.names)}",
                          suggestion="Consider passing values as parameters or using a class")

    def _detect_star_import(self, tree: ast.Module):
        """Detect wildcard imports."""
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.names and any(alias.name == "*" for alias in node.names):
                    module = node.module or "unknown"
                    self._add("star_import", "anti-pattern", node, "warning",
                              f"Wildcard import: 'from {module} import *'",
                              suggestion="Import specific names to avoid namespace pollution")

    def _detect_nested_classes_abuse(self, tree: ast.Module):
        """Detect excessive nested class definitions."""
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            nested = [item for item in node.body if isinstance(item, ast.ClassDef)]
            if len(nested) > 3:
                self._add("nested_classes_abuse", "anti-pattern", node, "warning",
                          f"Class '{node.name}' has {len(nested)} nested classes",
                          suggestion="Consider moving nested classes to module level")

    def _detect_return_in_init(self, tree: ast.Module):
        """Detect return with value in __init__."""
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "__init__":
                for child in ast.walk(node):
                    if isinstance(child, ast.Return) and child.value is not None:
                        self._add("return_in_init", "anti-pattern", child, "error",
                                  "__init__ returns a value (should return None)",
                                  suggestion="Remove the return value from __init__")


# Pattern descriptions for listing
PATTERN_CATALOG = {
    "Design Patterns": {
        "creational": {
            "singleton": "Single instance class with __new__ or classmethod",
            "factory_method": "Functions/classes named create_*/make_*/Factory*",
            "builder": "Classes with chainable methods (return self) and build()",
        },
        "structural": {
            "decorator_pattern": "Wrapper classes with __getattr__ delegation",
            "context_manager": "Classes implementing __enter__/__exit__",
            "async_context_manager": "Classes implementing __aenter__/__aexit__",
        },
        "behavioral": {
            "observer": "Classes with subscribe/notify or attach/detach methods",
            "strategy": "Base classes with NotImplementedError (interchangeable algorithms)",
            "template_method": "Base class with abstract steps called by concrete method",
            "iterator": "Classes implementing __iter__/__next__",
        }
    },
    "Anti-Patterns": {
        "anti-pattern": {
            "god_class": "Classes with >20 methods or >15 attributes",
            "long_method": "Functions/methods longer than 50 lines",
            "too_many_parameters": "Functions with >6 parameters",
            "deep_nesting": "Code nested more than 4 levels deep",
            "bare_except": "Bare except: clauses",
            "mutable_default": "Mutable default arguments (list, dict, set)",
            "global_state": "Use of global statement",
            "star_import": "Wildcard imports (from x import *)",
            "nested_classes_abuse": "More than 3 nested class definitions",
            "return_in_init": "__init__ returning a value",
        }
    }
}


def collect_python_files(path: str) -> List[str]:
    """Collect all Python files from a path."""
    if os.path.isfile(path):
        return [path] if path.endswith('.py') else []
    files = []
    for root, dirs, filenames in os.walk(path):
        # Skip common non-project directories
        dirs[:] = [d for d in dirs if d not in {
            '__pycache__', '.git', '.venv', 'venv', 'node_modules',
            '.tox', '.eggs', '*.egg-info', '.mypy_cache'
        }]
        for fn in filenames:
            if fn.endswith('.py'):
                files.append(os.path.join(root, fn))
    return sorted(files)


def format_text(result: AnalysisResult, summary_only: bool = False) -> str:
    """Format results as human-readable text."""
    lines = [f"Code Pattern Analysis Report",
             f"Files analyzed: {result.files_analyzed}",
             f"Total findings: {len(result.findings)}",
             ""]

    if summary_only:
        s = result.get_summary()
        lines.append("By Category:")
        for cat, count in sorted(s["by_category"].items()):
            lines.append(f"  {cat}: {count}")
        lines.append("\nBy Pattern:")
        for pat, count in sorted(s["by_pattern"].items(), key=lambda x: -x[1]):
            lines.append(f"  {pat}: {count}")
        lines.append("\nBy Severity:")
        for sev, count in sorted(s["by_severity"].items()):
            lines.append(f"  {sev}: {count}")
        return "\n".join(lines)

    # Group by file
    by_file: Dict[str, List[Finding]] = {}
    for f in result.findings:
        by_file.setdefault(f.file_path, []).append(f)

    for filepath in sorted(by_file.keys()):
        lines.append(f"--- {filepath} ---")
        for f in sorted(by_file[filepath], key=lambda x: x.line):
            icon = {"info": "[i]", "warning": "[W]", "error": "[E]"}.get(f.severity, "[?]")
            lines.append(f"  {icon} L{f.line}: {f.pattern_name} ({f.category})")
            lines.append(f"      {f.description}")
            if f.suggestion:
                lines.append(f"      => {f.suggestion}")
        lines.append("")

    if result.parse_errors:
        lines.append("Parse Errors:")
        for err in result.parse_errors:
            lines.append(f"  {err['file']}: {err['error']}")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Code Pattern Analyzer - detect design patterns and anti-patterns",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    subparsers = parser.add_subparsers(dest="command")

    # scan
    scan_p = subparsers.add_parser("scan", help="Scan code for patterns")
    scan_p.add_argument("--path", required=True, help="File or directory to scan")
    scan_p.add_argument("--format", choices=["text", "json"], default="text",
                        help="Output format")
    scan_p.add_argument("--anti-patterns-only", action="store_true",
                        help="Only show anti-patterns")
    scan_p.add_argument("--patterns-only", action="store_true",
                        help="Only show design patterns (no anti-patterns)")
    scan_p.add_argument("--category", help="Filter by category")
    scan_p.add_argument("--summary", action="store_true",
                        help="Show summary statistics only")
    scan_p.add_argument("--min-severity", choices=["info", "warning", "error"],
                        default="info", help="Minimum severity to report")
    scan_p.add_argument("--output", help="Output file (stdout if omitted)")

    # list-patterns
    subparsers.add_parser("list-patterns", help="List all detectable patterns")

    args = parser.parse_args()

    if args.command == "scan":
        files = collect_python_files(args.path)
        if not files:
            print(f"No Python files found in {args.path}", file=sys.stderr)
            sys.exit(1)

        detector = PatternDetector()
        result = AnalysisResult()

        for filepath in files:
            result.files_analyzed += 1
            try:
                findings = detector.analyze_file(filepath)
                result.findings.extend(findings)
            except Exception as e:
                result.files_with_errors += 1
                result.parse_errors.append({"file": filepath, "error": str(e)})

        # Filter
        severity_order = {"info": 0, "warning": 1, "error": 2}
        min_sev = severity_order.get(args.min_severity, 0)
        result.findings = [f for f in result.findings
                           if severity_order.get(f.severity, 0) >= min_sev]

        if args.anti_patterns_only:
            result.findings = [f for f in result.findings if f.category == "anti-pattern"]
        if args.patterns_only:
            result.findings = [f for f in result.findings if f.category != "anti-pattern"]
        if args.category:
            result.findings = [f for f in result.findings
                               if f.category == args.category]

        if args.format == "json":
            output = json.dumps(result.to_dict(), indent=2)
        else:
            output = format_text(result, summary_only=args.summary)

        if args.output:
            with open(args.output, 'w') as f:
                f.write(output)
            print(f"Report written to {args.output}")
        else:
            print(output)

    elif args.command == "list-patterns":
        for group_name, categories in PATTERN_CATALOG.items():
            print(f"\n{'='*60}")
            print(f" {group_name}")
            print(f"{'='*60}")
            for cat, patterns in categories.items():
                print(f"\n  [{cat}]")
                for name, desc in patterns.items():
                    print(f"    {name}: {desc}")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
