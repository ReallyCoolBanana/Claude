#!/usr/bin/env python3
"""API Documentation Parser - Parses OpenAPI/Swagger specs.

Extracts endpoint summaries, schemas, and parameters from OpenAPI 2.0 (Swagger)
and OpenAPI 3.x specifications. Generates structured reports of API capabilities.

Usage:
    python doc_parser.py parse <spec_file_or_url> [--output FILE] [--format json|markdown]
    python doc_parser.py summary <spec_file_or_url>
    python doc_parser.py endpoints <spec_file_or_url> [--method GET] [--tag users]

Examples:
    python doc_parser.py parse petstore.yaml
    python doc_parser.py parse https://petstore3.swagger.io/api/v3/openapi.json
    python doc_parser.py summary api-spec.json --format markdown
    python doc_parser.py endpoints api-spec.json --method POST
"""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional, Any


USER_AGENT = "DocParser/1.0 (compatible; api-tools)"


@dataclass
class APIParameter:
    """API endpoint parameter."""
    name: str
    location: str  # query, header, path, cookie, body
    required: bool = False
    param_type: str = ""
    description: str = ""
    default: Any = None
    enum: list = field(default_factory=list)


@dataclass
class APIResponse:
    """API endpoint response."""
    status_code: str
    description: str = ""
    content_type: str = ""
    schema_ref: str = ""
    schema_summary: str = ""


@dataclass
class APIEndpoint:
    """A single API endpoint."""
    path: str
    method: str
    summary: str = ""
    description: str = ""
    operation_id: str = ""
    tags: list = field(default_factory=list)
    parameters: list = field(default_factory=list)
    request_body: dict = field(default_factory=dict)
    responses: list = field(default_factory=list)
    security: list = field(default_factory=list)
    deprecated: bool = False


@dataclass
class APISchema:
    """An API schema/model definition."""
    name: str
    schema_type: str = ""
    description: str = ""
    properties: list = field(default_factory=list)
    required_fields: list = field(default_factory=list)
    enum_values: list = field(default_factory=list)


@dataclass
class APIInfo:
    """API information."""
    title: str = ""
    description: str = ""
    version: str = ""
    base_url: str = ""
    contact: dict = field(default_factory=dict)
    license: dict = field(default_factory=dict)
    terms_of_service: str = ""


@dataclass
class APIReport:
    """Complete API documentation report."""
    info: Optional[APIInfo] = None
    spec_version: str = ""  # openapi version (2.0, 3.0, 3.1)
    endpoints: list = field(default_factory=list)
    schemas: list = field(default_factory=list)
    security_schemes: list = field(default_factory=list)
    tags: list = field(default_factory=list)
    total_endpoints: int = 0
    methods_summary: dict = field(default_factory=dict)
    parsed_at: str = ""
    source: str = ""
    errors: list = field(default_factory=list)


class OpenAPIParser:
    """Parses OpenAPI/Swagger specifications."""

    def __init__(self):
        self._spec = {}
        self._errors = []

    def parse(self, source: str) -> APIReport:
        """Parse an OpenAPI spec from a file path or URL.

        Args:
            source: File path or URL to the OpenAPI spec.

        Returns:
            APIReport with parsed API documentation.
        """
        self._errors = []
        spec_text = self._load_source(source)
        if not spec_text:
            report = APIReport(parsed_at=datetime.now(timezone.utc).isoformat(),
                               source=source)
            report.errors = self._errors
            return report

        # Parse JSON or YAML
        self._spec = self._parse_spec_text(spec_text)
        if not self._spec:
            report = APIReport(parsed_at=datetime.now(timezone.utc).isoformat(),
                               source=source)
            report.errors = self._errors
            return report

        return self._build_report(source)

    def parse_dict(self, spec: dict, source: str = "<dict>") -> APIReport:
        """Parse an OpenAPI spec from a dictionary.

        Args:
            spec: The OpenAPI spec as a dictionary.
            source: Source label.

        Returns:
            APIReport with parsed API documentation.
        """
        self._errors = []
        self._spec = spec
        return self._build_report(source)

    def _load_source(self, source: str) -> Optional[str]:
        """Load spec from file or URL."""
        if source.startswith(('http://', 'https://')):
            try:
                req = urllib.request.Request(source, headers={
                    'User-Agent': USER_AGENT,
                    'Accept': 'application/json, application/yaml, text/yaml',
                })
                with urllib.request.urlopen(req, timeout=30) as resp:
                    return resp.read().decode('utf-8')
            except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
                self._errors.append(f"Failed to fetch {source}: {e}")
                return None
        else:
            try:
                with open(source, 'r', encoding='utf-8') as f:
                    return f.read()
            except (IOError, OSError) as e:
                self._errors.append(f"Failed to read {source}: {e}")
                return None

    def _parse_spec_text(self, text: str) -> Optional[dict]:
        """Parse spec text as JSON or YAML."""
        # Try JSON first
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Try YAML (basic parser, no PyYAML dependency required)
        try:
            return self._parse_basic_yaml(text)
        except Exception:
            pass

        # Try importing yaml if available
        try:
            import yaml
            return yaml.safe_load(text)
        except ImportError:
            self._errors.append(
                "Cannot parse YAML: install PyYAML or provide JSON format"
            )
        except Exception as e:
            self._errors.append(f"YAML parse error: {e}")

        return None

    def _parse_basic_yaml(self, text: str) -> dict:
        """Very basic YAML parser for simple OpenAPI specs.
        Handles common cases without requiring PyYAML.
        Falls back to error if too complex.
        """
        # This is intentionally limited - for complex YAML, use PyYAML
        result = {}
        lines = text.split('\n')

        if not any(line.strip().startswith('{') for line in lines[:3]):
            # Looks like YAML, try to convert simple cases
            # For now, raise to fall through to PyYAML
            raise ValueError("Complex YAML requires PyYAML")

        return result

    def _build_report(self, source: str) -> APIReport:
        """Build report from parsed spec."""
        report = APIReport(
            parsed_at=datetime.now(timezone.utc).isoformat(),
            source=source,
            errors=self._errors,
        )

        # Detect spec version
        if 'openapi' in self._spec:
            report.spec_version = str(self._spec['openapi'])
        elif 'swagger' in self._spec:
            report.spec_version = str(self._spec['swagger'])
        else:
            self._errors.append("Cannot detect spec version (no 'openapi' or 'swagger' field)")

        # Parse info
        report.info = self._parse_info()

        # Parse servers/base URL
        if report.spec_version.startswith('3'):
            servers = self._spec.get('servers', [])
            if servers:
                report.info.base_url = servers[0].get('url', '')
        elif report.spec_version.startswith('2'):
            host = self._spec.get('host', '')
            base_path = self._spec.get('basePath', '')
            schemes = self._spec.get('schemes', ['https'])
            if host:
                report.info.base_url = f"{schemes[0]}://{host}{base_path}"

        # Parse endpoints
        report.endpoints = self._parse_endpoints()
        report.total_endpoints = len(report.endpoints)

        # Methods summary
        methods = {}
        for ep in report.endpoints:
            m = ep.method.upper()
            methods[m] = methods.get(m, 0) + 1
        report.methods_summary = methods

        # Parse schemas
        report.schemas = self._parse_schemas()

        # Parse security schemes
        report.security_schemes = self._parse_security_schemes()

        # Parse tags
        report.tags = self._parse_tags()

        return report

    def _parse_info(self) -> APIInfo:
        """Parse API info section."""
        info = self._spec.get('info', {})
        return APIInfo(
            title=info.get('title', 'Untitled API'),
            description=info.get('description', ''),
            version=info.get('version', ''),
            contact=info.get('contact', {}),
            license=info.get('license', {}),
            terms_of_service=info.get('termsOfService', ''),
        )

    def _parse_endpoints(self) -> list:
        """Parse all API endpoints."""
        endpoints = []
        paths = self._spec.get('paths', {})

        for path, path_item in paths.items():
            if not isinstance(path_item, dict):
                continue

            # Path-level parameters
            path_params = path_item.get('parameters', [])

            for method in ('get', 'post', 'put', 'patch', 'delete',
                          'head', 'options', 'trace'):
                if method not in path_item:
                    continue

                operation = path_item[method]
                if not isinstance(operation, dict):
                    continue

                ep = APIEndpoint(
                    path=path,
                    method=method.upper(),
                    summary=operation.get('summary', ''),
                    description=operation.get('description', ''),
                    operation_id=operation.get('operationId', ''),
                    tags=operation.get('tags', []),
                    deprecated=operation.get('deprecated', False),
                )

                # Parameters (merge path-level and operation-level)
                all_params = path_params + operation.get('parameters', [])
                ep.parameters = [self._parse_parameter(p) for p in all_params]

                # Request body (OpenAPI 3.x)
                if 'requestBody' in operation:
                    ep.request_body = self._parse_request_body(operation['requestBody'])

                # Responses
                for status, resp_data in operation.get('responses', {}).items():
                    ep.responses.append(self._parse_response(str(status), resp_data))

                # Security
                ep.security = operation.get('security', [])

                endpoints.append(ep)

        return endpoints

    def _parse_parameter(self, param: dict) -> dict:
        """Parse a single parameter."""
        # Resolve $ref if present
        param = self._resolve_ref(param)

        schema = param.get('schema', {})
        return {
            'name': param.get('name', ''),
            'location': param.get('in', ''),
            'required': param.get('required', False),
            'type': schema.get('type', param.get('type', '')),
            'description': param.get('description', ''),
            'default': schema.get('default', param.get('default')),
            'enum': schema.get('enum', param.get('enum', [])),
        }

    def _parse_request_body(self, body: dict) -> dict:
        """Parse request body."""
        body = self._resolve_ref(body)
        result = {
            'required': body.get('required', False),
            'description': body.get('description', ''),
            'content_types': [],
        }

        for content_type, media in body.get('content', {}).items():
            schema = media.get('schema', {})
            schema = self._resolve_ref(schema)
            result['content_types'].append({
                'type': content_type,
                'schema': self._summarize_schema(schema),
            })

        return result

    def _parse_response(self, status: str, resp: dict) -> dict:
        """Parse a response definition."""
        resp = self._resolve_ref(resp)
        result = {
            'status_code': status,
            'description': resp.get('description', ''),
            'content_types': [],
        }

        # OpenAPI 3.x content
        for content_type, media in resp.get('content', {}).items():
            schema = media.get('schema', {})
            schema = self._resolve_ref(schema)
            result['content_types'].append({
                'type': content_type,
                'schema': self._summarize_schema(schema),
            })

        # Swagger 2.x schema
        if 'schema' in resp and 'content' not in resp:
            schema = self._resolve_ref(resp['schema'])
            result['content_types'].append({
                'type': 'application/json',
                'schema': self._summarize_schema(schema),
            })

        return result

    def _parse_schemas(self) -> list:
        """Parse schema/model definitions."""
        schemas = []

        # OpenAPI 3.x: components/schemas
        components = self._spec.get('components', {})
        schema_defs = components.get('schemas', {})

        # Swagger 2.x: definitions
        if not schema_defs:
            schema_defs = self._spec.get('definitions', {})

        for name, schema in schema_defs.items():
            if not isinstance(schema, dict):
                continue

            s = APISchema(
                name=name,
                schema_type=schema.get('type', ''),
                description=schema.get('description', ''),
                required_fields=schema.get('required', []),
                enum_values=schema.get('enum', []),
            )

            # Properties
            for prop_name, prop_def in schema.get('properties', {}).items():
                prop_def = self._resolve_ref(prop_def)
                s.properties.append({
                    'name': prop_name,
                    'type': prop_def.get('type', ''),
                    'format': prop_def.get('format', ''),
                    'description': prop_def.get('description', ''),
                    'required': prop_name in s.required_fields,
                })

            schemas.append(s)

        return schemas

    def _parse_security_schemes(self) -> list:
        """Parse security scheme definitions."""
        schemes = []

        # OpenAPI 3.x
        components = self._spec.get('components', {})
        sec_schemes = components.get('securitySchemes', {})

        # Swagger 2.x
        if not sec_schemes:
            sec_schemes = self._spec.get('securityDefinitions', {})

        for name, scheme in sec_schemes.items():
            if not isinstance(scheme, dict):
                continue
            schemes.append({
                'name': name,
                'type': scheme.get('type', ''),
                'scheme': scheme.get('scheme', ''),
                'description': scheme.get('description', ''),
                'in': scheme.get('in', ''),
                'bearer_format': scheme.get('bearerFormat', ''),
                'flows': list(scheme.get('flows', {}).keys()),
            })

        return schemes

    def _parse_tags(self) -> list:
        """Parse tag definitions."""
        tags = []
        for tag in self._spec.get('tags', []):
            tags.append({
                'name': tag.get('name', ''),
                'description': tag.get('description', ''),
            })
        return tags

    def _resolve_ref(self, obj: dict) -> dict:
        """Resolve a $ref reference."""
        if not isinstance(obj, dict) or '$ref' not in obj:
            return obj

        ref = obj['$ref']
        if not ref.startswith('#/'):
            return obj

        parts = ref[2:].split('/')
        current = self._spec
        for part in parts:
            part = part.replace('~1', '/').replace('~0', '~')
            if isinstance(current, dict):
                current = current.get(part, {})
            else:
                return obj

        return current if isinstance(current, dict) else obj

    def _summarize_schema(self, schema: dict, depth: int = 0) -> str:
        """Create a short summary of a schema."""
        if depth > 3:
            return "..."

        schema = self._resolve_ref(schema)

        if '$ref' in schema:
            ref = schema['$ref']
            return ref.split('/')[-1]

        schema_type = schema.get('type', '')

        if schema_type == 'array':
            items = schema.get('items', {})
            items_summary = self._summarize_schema(items, depth + 1)
            return f"array<{items_summary}>"

        if schema_type == 'object':
            props = schema.get('properties', {})
            if props:
                prop_names = list(props.keys())[:5]
                suffix = '...' if len(props) > 5 else ''
                return f"object{{{', '.join(prop_names)}{suffix}}}"
            return "object"

        if 'allOf' in schema:
            parts = [self._summarize_schema(s, depth + 1) for s in schema['allOf']]
            return ' & '.join(parts)

        if 'oneOf' in schema:
            parts = [self._summarize_schema(s, depth + 1) for s in schema['oneOf']]
            return ' | '.join(parts)

        if 'enum' in schema:
            vals = schema['enum'][:5]
            suffix = '...' if len(schema['enum']) > 5 else ''
            return f"enum({', '.join(str(v) for v in vals)}{suffix})"

        fmt = schema.get('format', '')
        if fmt:
            return f"{schema_type}({fmt})"

        return schema_type or 'any'


def report_to_json(report: APIReport) -> str:
    """Serialize report to JSON."""
    data = {
        'info': asdict(report.info) if report.info else {},
        'spec_version': report.spec_version,
        'source': report.source,
        'parsed_at': report.parsed_at,
        'total_endpoints': report.total_endpoints,
        'methods_summary': report.methods_summary,
        'tags': report.tags,
        'security_schemes': report.security_schemes,
        'endpoints': [asdict(ep) for ep in report.endpoints],
        'schemas': [asdict(s) for s in report.schemas],
        'errors': report.errors,
    }
    return json.dumps(data, indent=2, ensure_ascii=False, default=str)


def report_to_markdown(report: APIReport) -> str:
    """Serialize report to markdown."""
    parts = []

    # Header
    title = report.info.title if report.info else 'API Report'
    parts.append(f"# {title}")
    parts.append("")

    if report.info:
        if report.info.description:
            parts.append(report.info.description)
            parts.append("")
        parts.append(f"- **Version**: {report.info.version}")
        parts.append(f"- **Base URL**: {report.info.base_url}")
        parts.append(f"- **Spec Version**: {report.spec_version}")
    parts.append(f"- **Total Endpoints**: {report.total_endpoints}")
    parts.append(f"- **Methods**: {', '.join(f'{m}: {c}' for m, c in sorted(report.methods_summary.items()))}")
    parts.append("")

    # Security
    if report.security_schemes:
        parts.append("## Security Schemes")
        parts.append("")
        for scheme in report.security_schemes:
            parts.append(f"- **{scheme['name']}**: {scheme['type']}"
                        f" ({scheme.get('scheme', '')}) - {scheme.get('description', '')}")
        parts.append("")

    # Tags
    if report.tags:
        parts.append("## Tags")
        parts.append("")
        for tag in report.tags:
            parts.append(f"- **{tag['name']}**: {tag.get('description', '')}")
        parts.append("")

    # Endpoints by tag
    parts.append("## Endpoints")
    parts.append("")

    # Group by tag
    tagged = {}
    untagged = []
    for ep in report.endpoints:
        if ep.tags:
            for tag in ep.tags:
                tagged.setdefault(tag, []).append(ep)
        else:
            untagged.append(ep)

    for tag_name in sorted(tagged.keys()):
        parts.append(f"### {tag_name}")
        parts.append("")
        for ep in tagged[tag_name]:
            deprecated = " (DEPRECATED)" if ep.deprecated else ""
            parts.append(f"#### `{ep.method} {ep.path}`{deprecated}")
            if ep.summary:
                parts.append(f"**{ep.summary}**")
            if ep.description:
                parts.append(f"\n{ep.description}")
            if ep.parameters:
                parts.append("\n**Parameters:**")
                for p in ep.parameters:
                    req = " (required)" if p.get('required') else ""
                    parts.append(f"- `{p['name']}` ({p.get('location', '')}, {p.get('type', '')}){req}: {p.get('description', '')}")
            if ep.responses:
                parts.append("\n**Responses:**")
                for r in ep.responses:
                    parts.append(f"- `{r['status_code']}`: {r.get('description', '')}")
            parts.append("")

    if untagged:
        parts.append("### Other")
        parts.append("")
        for ep in untagged:
            deprecated = " (DEPRECATED)" if ep.deprecated else ""
            parts.append(f"#### `{ep.method} {ep.path}`{deprecated}")
            if ep.summary:
                parts.append(f"**{ep.summary}**")
            parts.append("")

    # Schemas
    if report.schemas:
        parts.append("## Schemas")
        parts.append("")
        for schema in report.schemas:
            parts.append(f"### {schema.name}")
            if schema.description:
                parts.append(f"{schema.description}")
            parts.append(f"Type: `{schema.schema_type}`")
            if schema.properties:
                parts.append("\n| Property | Type | Required | Description |")
                parts.append("|----------|------|----------|-------------|")
                for prop in schema.properties:
                    req = "Yes" if prop.get('required') else "No"
                    ptype = prop.get('type', '')
                    if prop.get('format'):
                        ptype += f" ({prop['format']})"
                    parts.append(f"| {prop['name']} | {ptype} | {req} | {prop.get('description', '')} |")
            parts.append("")

    # Errors
    if report.errors:
        parts.append("## Parsing Errors")
        parts.append("")
        for err in report.errors:
            parts.append(f"- {err}")
        parts.append("")

    return '\n'.join(parts)


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description='Parse OpenAPI/Swagger specifications.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest='command', help='Command to run')

    # parse command
    parse_p = subparsers.add_parser('parse', help='Parse a full spec')
    parse_p.add_argument('source', help='File path or URL to OpenAPI spec')
    parse_p.add_argument('--output', '-o', help='Output file')
    parse_p.add_argument('--format', '-f', choices=['json', 'markdown'],
                         default='json', help='Output format (default: json)')

    # summary command
    summary_p = subparsers.add_parser('summary', help='Show API summary')
    summary_p.add_argument('source', help='File path or URL to OpenAPI spec')
    summary_p.add_argument('--format', '-f', choices=['json', 'markdown'],
                           default='markdown')

    # endpoints command
    endpoints_p = subparsers.add_parser('endpoints', help='List endpoints')
    endpoints_p.add_argument('source', help='File path or URL to OpenAPI spec')
    endpoints_p.add_argument('--method', '-m', help='Filter by HTTP method')
    endpoints_p.add_argument('--tag', '-t', help='Filter by tag')

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    api_parser = OpenAPIParser()

    if args.command == 'parse':
        report = api_parser.parse(args.source)
        if args.format == 'markdown':
            output = report_to_markdown(report)
        else:
            output = report_to_json(report)

        if args.output:
            with open(args.output, 'w', encoding='utf-8') as f:
                f.write(output)
            print(f"Output written to {args.output}", file=sys.stderr)
        else:
            print(output)

    elif args.command == 'summary':
        report = api_parser.parse(args.source)
        if args.format == 'markdown':
            parts = [
                f"# {report.info.title}" if report.info else "# API",
                f"Version: {report.info.version}" if report.info else "",
                f"Base URL: {report.info.base_url}" if report.info else "",
                f"Spec: OpenAPI {report.spec_version}",
                f"Total endpoints: {report.total_endpoints}",
                f"Methods: {report.methods_summary}",
                f"Tags: {', '.join(t['name'] for t in report.tags)}",
                f"Schemas: {len(report.schemas)}",
                f"Security: {', '.join(s['name'] for s in report.security_schemes)}",
            ]
            print('\n'.join(parts))
        else:
            summary = {
                'title': report.info.title if report.info else '',
                'version': report.info.version if report.info else '',
                'base_url': report.info.base_url if report.info else '',
                'spec_version': report.spec_version,
                'total_endpoints': report.total_endpoints,
                'methods_summary': report.methods_summary,
                'tags': [t['name'] for t in report.tags],
                'schemas_count': len(report.schemas),
                'security_schemes': [s['name'] for s in report.security_schemes],
            }
            print(json.dumps(summary, indent=2))

    elif args.command == 'endpoints':
        report = api_parser.parse(args.source)
        endpoints = report.endpoints

        if hasattr(args, 'method') and args.method:
            endpoints = [e for e in endpoints if e.method == args.method.upper()]

        if hasattr(args, 'tag') and args.tag:
            endpoints = [e for e in endpoints if args.tag in e.tags]

        for ep in endpoints:
            deprecated = " [DEPRECATED]" if ep.deprecated else ""
            tags = f" [{', '.join(ep.tags)}]" if ep.tags else ""
            print(f"{ep.method:8s} {ep.path:40s} {ep.summary}{tags}{deprecated}")


if __name__ == '__main__':
    main()
