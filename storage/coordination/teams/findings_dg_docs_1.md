# DATA-GATHER-DOCS-1: Official Documentation Findings

**Team:** DATA-GATHER-DOCS-1
**Date:** 2026-03-11
**Focus:** Official manuals and documentation for Python, key frameworks, and API standards

---

## 1. Python Official Documentation

### Python 3.14 (Current Stable - Released Oct 7, 2025)
**Latest patch:** 3.14.3
**Source:** https://docs.python.org/3/whatsnew/3.14.html

**Major Features:**
- **Free-threaded Python officially supported (PEP 779)** - The free-threaded build (no GIL) is now officially supported and will not be removed without deprecation. Decision on making it default deferred to future based on ecosystem readiness.
- **Deferred Annotation Evaluation (PEP 649)** - Annotations on functions, classes, and modules are no longer evaluated eagerly. Stored in special-purpose annotate functions, evaluated only when necessary. This effectively makes `from __future__ import annotations` behavior the default.
- **Template String Literals / t-strings (PEP 750)** - New t-string syntax for custom string processing, using familiar f-string syntax.
- **Multiple Interpreters in stdlib (PEP 734)** - New `concurrent.interpreters` module exposes running multiple Python interpreters in the same process.
- **Zstandard Compression (PEP 784)** - New `compression.zstd` module for Zstandard compression.
- **Simplified except Syntax (PEP 758)** - `except` and `except*` may now omit brackets.
- **Zero-overhead External Debugger Interface (PEP 768)** - For CPython external debugging.
- **Disallow control flow exiting finally (PEP 765)** - Return/break/continue that exit a finally block are now disallowed.
- **Emscripten support (PEP 776)** - Tier 3 supported platform.
- **UUID v6-v8 support** - Plus up to 40% faster generation for v3-v5.
- **Experimental JIT compiler** in official macOS/Windows binaries.
- **Official Android binary releases** now available.
- **Sigstore replaces PGP** for release verification.

### Python 3.13 (Released Oct 7, 2024)
**Latest patch:** 3.13.12
**Source:** https://docs.python.org/3.13/whatsnew/3.13.html

**Major Features:**
- **New Interactive Interpreter** - Based on PyPy's, with multi-line editing, color support, colorized exception tracebacks.
- **Free-Threaded Mode (PEP 703)** - Experimental free-threaded build disabling the GIL.
- **JIT Compiler (PEP 744)** - Preliminary experimental JIT using copy-and-patch technique.
- **locals() Semantics (PEP 667)** - Defined semantics for mutating the returned mapping.
- **Improved Error Messages** - Color tracebacks by default.
- **Type System Improvements:**
  - PEP 696: TypeVar/ParamSpec/TypeVarTuple defaults
  - PEP 742: `typing.TypeIs` for type narrowing (alternative to TypeGuard)
  - PEP 705: `ReadOnly` for TypedDict items
  - PEP 702: `warnings.deprecated()` decorator
- Removed deprecated modules per PEP 594.
- New `dbm.sqlite3` backend (default for new files).
- WASI is Tier 2; Android is Tier 3.

### Python 3.12 (Security-fixes only)
**Latest patch:** 3.12.13
**Source:** https://docs.python.org/3/whatsnew/3.12.html

**Key Features (for reference):**
- PEP 701: More flexible f-string parsing
- PEP 695: New type parameter syntax (`class Foo[T]: ...`)
- PEP 698: `@override` decorator
- PEP 684: Isolated subinterpreters with separate GILs
- PEP 669: New debugging/profiling API
- `distutils` removed from stdlib
- ~5% overall performance improvement

### Python Typing Module - Comprehensive Summary

| PEP | Feature | Version |
|-----|---------|---------|
| PEP 695 | New type parameter syntax (`class C[T]: ...`, `type Alias = ...`) | 3.12 |
| PEP 698 | `@override` decorator | 3.12 |
| PEP 696 | TypeVar/ParamSpec/TypeVarTuple defaults | 3.13 |
| PEP 742 | `TypeIs` for type narrowing | 3.13 |
| PEP 702 | `warnings.deprecated()` decorator | 3.13 |
| PEP 705 | `ReadOnly` for TypedDict items | 3.13 |
| PEP 649 | Deferred evaluation of annotations | 3.14 |

**Note:** `AnyStr` deprecated in 3.13; will be removed from `typing.__all__` in 3.16 and fully removed in 3.18.

**Official typing docs:** https://docs.python.org/3/library/typing.html
**Typing guides:** https://typing.python.org/en/latest/guides/modernizing.html

---

## 2. FastAPI Official Documentation

### Current Version: ~0.135.x (March 2026)
**Source:** https://fastapi.tiangolo.com/release-notes/
**PyPI:** https://pypi.org/project/fastapi/
**GitHub:** https://github.com/fastapi/fastapi/releases

**Key Recent Changes:**
- **Server-Sent Events (SSE) support** added natively
- **Streaming JSON Lines and binary data** with `yield` support
- **Starlette 1.0.0+ support** (Starlette upgraded from >=0.40.0 to >=0.46.0, then to 1.0.0rc1)
- **Strict Content-Type checking** for JSON requests (default on, disable with `strict_content_type=False`)
- **FastAPI Agents Skill** added
- **Pydantic v1 support fully dropped** - minimum Pydantic >=2.7.0
- **Rust-powered JSON serialization** via Pydantic v2 - 2x+ performance for JSON responses
- **Python 3.8 support dropped** - internal syntax upgraded to Python 3.9+
- **`fastapi-slim` dropped** - use `fastapi[standard]` or `fastapi` instead
- **Deprecation warnings for `pydantic.v1`** usage
- **Custom `FastAPIDeprecationWarning`** introduced
- Standard deps now include `pydantic-settings >=2.0.0` and `pydantic-extra-types >=2.0.0`

**Recommended Setup (2026):** Python 3.12 or 3.13 + FastAPI + Pydantic v2 + uvloop

---

## 3. Django Official Documentation

### Current Version: Django 6.0.3 (Released Dec 3, 2025)
**Source:** https://docs.djangoproject.com/en/6.0/releases/6.0/
**Download:** https://www.djangoproject.com/download/

**Django 6.0 Headline Features:**
1. **Template Partials** - Named fragments within template files for modular, reusable template code
2. **Background Tasks Framework** - Built-in task framework (DEP 0014) for background processing
3. **Content Security Policy (CSP) Support** - Native CSP support after 13 months of iteration
4. **Python 3.12 minimum** required

**Django 6.0 Requirements:**
- Python 3.12+ minimum
- MariaDB 10.6+
- asgiref >=3.9.1

**Key Breaking Changes:**
- `as_sql()` second return element must now be a tuple (not list)
- JSON serializer now writes newline at end of output
- `Field.pre_save()` may be called more than once (must be idempotent)
- Default URL scheme changed from "http" to "https" for `forms.URLField`

**New Deprecations:**
- `ADMINS`/`MANAGERS` as (name, address) tuples deprecated; use email strings
- `URLIZE_ASSUME_HTTPS` transitional setting for HTTP->HTTPS default change in Django 7.0

### Django 5.2 LTS
**Source:** https://docs.djangoproject.com/en/6.0/releases/5.2/
- Long-term support until April 2028
- Supports Python 3.10-3.14
- Shell management command auto-imports models
- MySQL defaults to utf8mb4

### Django 4.2 LTS
- End of life: April 2026

---

## 4. Flask Official Documentation

### Current Version: Flask 3.1.3 (Feb 19, 2026)
**Source:** https://flask.palletsprojects.com/en/stable/changes/
**PyPI:** https://pypi.org/project/Flask/
**GitHub:** https://github.com/pallets/flask

**Recent Changes:**
- **3.1.3 (Feb 2026):** Security fix - session marked as accessed for key-only operations (`in`, `len`)
- **3.1.2 (Aug 2025):** `stream_with_context` fixed for async views; test client `follow_redirects` session fix
- **3.1.1 (May 2025):** Fix signing key selection with `SECRET_KEY_FALLBACKS`; `flask --help` loads app first
- **3.1.0 (Nov 2024):** Dropped Python 3.8; minimum Werkzeug >=3.1, ItsDangerous >=2.2, Blinker >=1.9; per-request `max_content_length`; `MAX_FORM_MEMORY_SIZE` and `MAX_FORM_PARTS` config

---

## 5. API Documentation Standards

### OpenAPI Specification

#### OpenAPI 3.2.0 (Latest - Sep 19, 2025)
**Source:** https://spec.openapis.org/oas/v3.2.0.html
**GitHub:** https://github.com/OAI/OpenAPI-Specification

**Key additions in 3.2.0:**
- **Tag metadata standardized** with `summary`, `parent`, and `kind`
- **Streaming payloads first-class support** via `itemSchema`, `prefixEncoding` for SSE, JSON Lines, multipart feeds
- **OAuth 2.0 device authorization** in security schemes
- **Metadata URLs, deprecated flag** for security schemes
- **URI references for shared schemes**
- Particularly useful for SSE feeds or MCP connectors

#### OpenAPI 3.1.0
**Source:** https://spec.openapis.org/oas/v3.1.0.html

**Key features:**
- Full JSON Schema Draft 2020-12 alignment
- Native webhooks support
- Improved `$ref` behavior aligned with JSON Schema
- `exclusiveMaximum`/`exclusiveMinimum` must be numeric
- `contentEncoding`/`contentMediaType` replace `format` for file payloads

### GraphQL Specification

#### September 2025 Edition (Latest)
**Source:** https://spec.graphql.org/September2025/
**GitHub:** https://github.com/graphql/graphql-spec

First update since October 2021. 100+ commits, contributions from dozens of community members.

**Key new features:**
1. **OneOf Input Objects** - Mutually exclusive input shapes for tidier schemas
2. **Schema Coordinates** - Stable identifiers for fields/types across diffs; enables reliable codegen, linting, PR comments
3. **Descriptions on Documents** - Document queries and operations (helpful for AI tools)
4. **Expanded Deprecation Support** - Broader deprecation across schema elements
5. **Full Unicode Support** - Grammar supports entire Unicode range

---

## 6. Tool Requests for Programming Teams

Based on documentation analysis, the following tools/utilities would be valuable:

1. **Python 3.14 t-string template processor** - Build a utility that leverages PEP 750 t-strings for safe HTML/SQL templating
2. **FastAPI SSE helper library** - Standardized SSE endpoint patterns using the new native SSE support
3. **OpenAPI 3.2.0 streaming schema generator** - Tool to generate streaming endpoint schemas using `itemSchema`/`prefixEncoding`
4. **Django 6.0 background task patterns** - Reference implementation for the new Tasks framework
5. **GraphQL Schema Coordinates linter** - Tool leveraging the new Schema Coordinates spec for automated schema review
6. **Pydantic v2 migration checker** - Script to detect lingering pydantic.v1 usage in codebases
7. **Python typing modernizer** - Tool to upgrade typing syntax from older patterns to 3.12+ style (PEP 695)

---

## Version Summary Table

| Technology | Current Stable | Latest Patch | Python Min |
|-----------|---------------|-------------|-----------|
| Python | 3.14 | 3.14.3 | - |
| Python LTS | 3.13 | 3.13.12 | - |
| FastAPI | 0.135.x | ~0.135.1 | 3.9 |
| Django | 6.0 | 6.0.3 | 3.12 |
| Django LTS | 5.2 | 5.2.12 | 3.10 |
| Flask | 3.1 | 3.1.3 | 3.9 |
| OpenAPI | 3.2.0 | 3.2.0 | - |
| GraphQL | Sep 2025 | Sep 2025 | - |
| Pydantic | 2.x | >=2.7.0 | 3.8 |
