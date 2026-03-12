# SORT-ARCHIVE-1: Documentation Sorting Schema & Archive

**Team:** SORT-ARCHIVE-1
**Date:** 2026-03-11
**Status:** Active - Awaiting gatherer findings

---

## 1. Sorting Schema

### Category Definitions

All incoming documentation findings are classified into exactly one primary category and may have secondary tags.

#### CAT-1: Language Documentation
- **Scope:** Official language references, standard library docs, language specifications
- **Languages covered:** Python, JavaScript/TypeScript, Go, Rust, Java, C/C++, Ruby, etc.
- **Includes:** PEPs, language RFCs, type system docs, package management (pip, npm, cargo)
- **Sub-tags:** `lang-python`, `lang-js`, `lang-go`, `lang-rust`, `lang-java`, `lang-other`
- **Quality signals:** Official docs preferred; community docs marked with `community-source`

#### CAT-2: Framework Documentation
- **Scope:** Web frameworks, application frameworks, libraries with significant API surface
- **Examples:** FastAPI, Django, Flask, React, Next.js, Express, Spring Boot, Rails
- **Includes:** API references, getting-started guides, migration guides, best practices
- **Sub-tags:** `fw-web-backend`, `fw-web-frontend`, `fw-data`, `fw-ml`, `fw-testing`
- **Quality signals:** Version-pinned docs preferred; note version in metadata

#### CAT-3: Systems Documentation
- **Scope:** Operating systems, containers, networking, kernel, low-level systems
- **Examples:** Linux man pages, Docker/Podman, Kubernetes, systemd, networking (TCP/IP, DNS)
- **Includes:** System administration, shell scripting, process management, file systems
- **Sub-tags:** `sys-linux`, `sys-containers`, `sys-networking`, `sys-security`, `sys-shell`
- **Quality signals:** Distro-specific docs should note distro and version

#### CAT-4: Cloud/Infrastructure Documentation
- **Scope:** Cloud providers, IaC tools, CI/CD, observability, deployment
- **Examples:** AWS, GCP, Azure, Terraform, Ansible, GitHub Actions, Prometheus, Grafana
- **Includes:** Service-specific docs, architecture patterns, pricing/limits references
- **Sub-tags:** `cloud-aws`, `cloud-gcp`, `cloud-azure`, `infra-iac`, `infra-cicd`, `infra-observability`
- **Quality signals:** Note date of retrieval; cloud docs change frequently

#### CAT-5: Database Documentation
- **Scope:** Relational DBs, NoSQL, search engines, caching, message queues
- **Examples:** PostgreSQL, MySQL, SQLite, MongoDB, Redis, Elasticsearch, RabbitMQ, Kafka
- **Includes:** Query references, configuration, optimization guides, data modeling
- **Sub-tags:** `db-relational`, `db-nosql`, `db-search`, `db-cache`, `db-queue`
- **Quality signals:** Version-critical; always note DB version

---

## 2. Sorting Methodology

### Step-by-step Process

1. **Intake:** Read raw finding from gatherer team (file or bus message)
2. **Classify:** Assign primary category (CAT-1 through CAT-5) based on subject matter
3. **Tag:** Apply sub-tags and cross-cutting tags (e.g., `security`, `performance`, `beginner`)
4. **Assess quality:** Rate confidence (high/medium/low) based on source authority
5. **Deduplicate:** Check against existing KB entries to avoid redundancy
6. **Structure:** Convert to knowledge-base entry format (YAML frontmatter + Markdown body)
7. **Cross-reference:** Set `builds_on` field linking to related KB entries
8. **Archive:** Write structured entry to knowledge-base/entries/

### Cross-cutting Tags (applied in addition to category sub-tags)
- `security` - Security-relevant documentation
- `performance` - Performance tuning and optimization
- `beginner` - Introductory/getting-started material
- `advanced` - Advanced/expert-level material
- `api-reference` - Pure API reference material
- `tutorial` - Step-by-step tutorial content
- `migration` - Version migration or technology migration guides
- `best-practices` - Established best practices and patterns

### Deduplication Rules
- Same topic + same version = duplicate (skip)
- Same topic + newer version = update (supersedes old entry)
- Same topic + different depth = keep both (tag beginner vs advanced)
- Overlapping topics = keep both, cross-reference via `builds_on`

---

## 3. Input Source Status

| Source | Channel | Status | Findings Count |
|--------|---------|--------|---------------|
| DATA-GATHER-DOCS-1 | `dg-docs-1-to-sorters` | Channel created, no findings yet | 0 |
| DATA-GATHER-DOCS-2 | `dg-docs-2-to-sorters` | Channel created, no findings yet | 0 |
| findings_dg_docs_1.md | File | Not yet created | 0 |
| findings_dg_docs_2.md | File | Not yet created | 0 |

**Note:** DATA-GATHER-DOCS-1 is focused on Python/APIs/frameworks documentation. DATA-GATHER-DOCS-2 is focused on systems/cloud/infrastructure documentation.

---

## 4. Processed Findings

*No findings processed yet. Schema is ready for immediate processing when gatherer data arrives.*

---

## 5. Knowledge Base Entry Drafts

*Queue empty. Drafts will be added here as findings are sorted and structured.*

### Draft Template (ready for use)

```markdown
---
id: KB-XXXX
date: 2026-03-11
team: SORT-ARCHIVE-1
role: documentation-archiver
category: [methodology|tool-usage|integration]
tags: [documentation, {category-sub-tag}, {cross-cutting-tags}]
status: validated
confidence: high|medium|low
builds_on: []
---
# {Title}: {Subject} Documentation Reference

## Context
Documentation gathered by DATA-GATHER-DOCS-{1|2} on {date}.
Source: {url or reference}

## Method
Sorted using SORT-ARCHIVE-1 schema v1.0, category {CAT-N}.

## Result
{Structured summary of the documentation content}

## Lessons Learned
{Key takeaways relevant to AI agent teams}

## Recommendations
{How future teams should use this documentation}
```

---

## 6. Mapping to Existing Knowledge Base

The current KB (28 entries, KB-0001 through KB-0028) contains no dedicated documentation reference entries. New documentation entries would extend the KB in a new direction. Recommended `builds_on` links for each category:

- **CAT-1 (Language):** Could build on KB-0004/KB-0005 (data gathering methods used Python)
- **CAT-2 (Framework):** Could build on KB-0014 (API tool development)
- **CAT-3 (Systems):** Could build on KB-0016 (SQLite WAL), KB-0009 (multi-team ops)
- **CAT-4 (Cloud/Infra):** Could build on KB-0019 (architecture analysis)
- **CAT-5 (Database):** Could build on KB-0016 (SQLite WAL), KB-0008 (data storage methods)
