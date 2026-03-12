# Sorted Archive: Best Practices (SORT-ARCHIVE-3)

**Generated:** 2026-03-11
**Team:** SORT-ARCHIVE-3
**Source:** DATA-GATHER-PRACTICES pipeline
**Status:** Schema ready, awaiting findings ingestion

---

## Sorting Schema

Practices are classified along three dimensions:

| Dimension | Values |
|-----------|--------|
| **Category** | Design Patterns & Architecture, Testing & QA, Security, Performance Engineering, CI/CD & DevOps, Code Review & Collaboration, Documentation Standards |
| **Priority** | Critical, High, Medium, Low |
| **Difficulty** | Easy, Medium, Hard |

---

## Category 1: Design Patterns & Architecture

### 1.1 — Dependency Injection over Hard-Coded Dependencies
- **Priority:** High
- **Difficulty:** Medium
- **Rationale:** Reduces coupling, improves testability, and makes components replaceable without modifying consuming code. Central to SOLID principles (Dependency Inversion).
- **Implementation Steps:**
  1. Identify classes that instantiate their own collaborators internally.
  2. Refactor constructors to accept interfaces/abstractions instead.
  3. Wire dependencies at composition root (entry point or DI container).
  4. Add integration tests to verify wiring.
- **Enforcement Tools:** Language-specific DI frameworks (e.g., Python `inject`, Java Spring, .NET DI container). Linters that detect `new` inside business logic.
- **Sources:** Martin Fowler's Inversion of Control Containers (martinfowler.com), Clean Architecture (Robert C. Martin)

### 1.2 — Favor Composition Over Inheritance
- **Priority:** High
- **Difficulty:** Medium
- **Rationale:** Deep inheritance hierarchies are brittle and hard to reason about. Composition via interfaces/mixins keeps behavior modular.
- **Implementation Steps:**
  1. Audit inheritance trees deeper than 2 levels.
  2. Extract shared behavior into composable units (strategies, decorators, mixins).
  3. Replace `is-a` relationships with `has-a` where behavior is the concern.
- **Enforcement Tools:** Static analysis rules limiting inheritance depth. Code review checklists.
- **Sources:** Design Patterns (GoF), Effective Java (Bloch)

### 1.3 — Separate Business Logic from Infrastructure
- **Priority:** Critical
- **Difficulty:** Hard
- **Rationale:** Business rules change at a different rate than I/O, databases, or frameworks. Separating them enables testing without infrastructure and framework migration without rewriting logic.
- **Implementation Steps:**
  1. Define domain layer with pure functions/classes, no I/O imports.
  2. Create ports (interfaces) for infrastructure needs.
  3. Implement adapters for databases, APIs, file systems.
  4. Wire via composition root.
- **Enforcement Tools:** Architecture tests (ArchUnit, import-linter), hexagonal/clean architecture templates.
- **Sources:** Hexagonal Architecture (Alistair Cockburn), Clean Architecture (Robert C. Martin)

---

## Category 2: Testing & Quality Assurance

### 2.1 — Test Pyramid: Unit > Integration > E2E
- **Priority:** Critical
- **Difficulty:** Medium
- **Rationale:** Over-reliance on E2E tests leads to slow, flaky CI. A healthy ratio (70/20/10) keeps feedback fast and failures localized.
- **Implementation Steps:**
  1. Measure current test distribution across layers.
  2. Identify E2E tests that can be replaced by focused integration or unit tests.
  3. Establish team ratio targets and track in CI dashboards.
  4. Introduce contract tests to replace some integration tests.
- **Enforcement Tools:** Coverage tools with layer tagging, CI metrics dashboards, test categorization plugins.
- **Sources:** Google Testing Blog, "Testing Strategies in a Microservice Architecture" (martinfowler.com)

### 2.2 — Mutation Testing to Validate Test Suite Quality
- **Priority:** Medium
- **Difficulty:** Medium
- **Rationale:** Code coverage alone does not prove tests catch bugs. Mutation testing injects faults and checks if tests detect them, revealing weak assertions.
- **Implementation Steps:**
  1. Select a mutation testing tool (mutmut for Python, Stryker for JS/TS, pitest for Java).
  2. Run against critical modules first.
  3. Set mutation score thresholds (aim for >80% on core logic).
  4. Add to CI as a quality gate (nightly, not per-commit to manage runtime).
- **Enforcement Tools:** mutmut, Stryker, pitest, CI scheduled jobs.
- **Sources:** mutation-testing.org, Stryker documentation

### 2.3 — Property-Based Testing for Edge Case Discovery
- **Priority:** Medium
- **Difficulty:** Medium
- **Rationale:** Example-based tests only cover cases the developer imagines. Property-based testing generates thousands of inputs, finding edge cases humans miss (off-by-one, empty inputs, unicode, overflow).
- **Implementation Steps:**
  1. Identify pure functions with well-defined input/output contracts.
  2. Write property invariants (e.g., "sorted output length equals input length").
  3. Use Hypothesis (Python), fast-check (JS), QuickCheck (Haskell/Scala).
  4. Shrink failing cases for minimal reproduction.
- **Enforcement Tools:** Hypothesis, fast-check, QuickCheck, jqwik (Java).
- **Sources:** Hypothesis documentation, "Choosing properties for property-based testing" (fsharpforfunandprofit.com)

---

## Category 3: Security Best Practices

### 3.1 — Secrets Management: Never Commit Secrets
- **Priority:** Critical
- **Difficulty:** Easy
- **Rationale:** Leaked credentials in git history are the #1 cause of cloud breaches. Prevention is far cheaper than rotation.
- **Implementation Steps:**
  1. Add `.env`, `*.pem`, `*credentials*` to `.gitignore`.
  2. Install pre-commit hooks that scan for secrets (detect-secrets, gitleaks).
  3. Use vault-based secret management (HashiCorp Vault, AWS Secrets Manager, 1Password CLI).
  4. Rotate any secrets that were ever committed, even in old history.
- **Enforcement Tools:** gitleaks, detect-secrets (Yelp), truffleHog, GitHub secret scanning.
- **Sources:** OWASP Secrets Management Cheat Sheet, GitHub secret scanning docs

### 3.2 — Automated Dependency Vulnerability Scanning
- **Priority:** Critical
- **Difficulty:** Easy
- **Rationale:** Most codebases have >80% third-party code by volume. Known CVEs in dependencies are trivially exploitable if unpatched.
- **Implementation Steps:**
  1. Enable Dependabot / Renovate for auto-PRs on vulnerable deps.
  2. Add `npm audit` / `pip-audit` / `cargo audit` to CI pipeline.
  3. Set policy: critical CVEs block merge, high CVEs have 7-day SLA.
  4. Review and pin transitive dependencies.
- **Enforcement Tools:** Dependabot, Renovate, Snyk, npm audit, pip-audit, cargo audit, OSSF Scorecard.
- **Sources:** OWASP Dependency Check, Snyk vulnerability database, OSSF Scorecard

### 3.3 — Input Validation and Output Encoding
- **Priority:** Critical
- **Difficulty:** Medium
- **Rationale:** Injection attacks (SQL, XSS, command) remain in the OWASP Top 10. Systematic input validation at boundaries and output encoding at rendering eliminates entire vulnerability classes.
- **Implementation Steps:**
  1. Define validation schemas at every API boundary (Pydantic, Zod, JSON Schema).
  2. Use parameterized queries exclusively (never string-concatenated SQL).
  3. Apply context-aware output encoding (HTML, URL, JS) at template level.
  4. Add fuzzing to CI for parser code.
- **Enforcement Tools:** Pydantic, Zod, SQLAlchemy (ORM), DOMPurify, semgrep rules for injection patterns.
- **Sources:** OWASP Top 10, OWASP Input Validation Cheat Sheet

---

## Category 4: Performance Engineering

### 4.1 — Performance Budgets in CI
- **Priority:** High
- **Difficulty:** Medium
- **Rationale:** Performance degrades incrementally. Without budgets, no single commit is "the slow one" but the product becomes slow over months.
- **Implementation Steps:**
  1. Establish baselines: bundle size, critical-path latency, memory usage.
  2. Set budgets as CI checks (e.g., bundle < 200KB gzipped, p95 < 200ms).
  3. Fail PR if budget exceeded; require justification for exceptions.
  4. Track trends over time in dashboards.
- **Enforcement Tools:** Lighthouse CI, bundlesize, k6/Locust for API perf, custom CI scripts.
- **Sources:** web.dev performance budgets, Lighthouse CI documentation

### 4.2 — Database Query Optimization and Monitoring
- **Priority:** High
- **Difficulty:** Medium
- **Rationale:** Database queries are the most common backend bottleneck. N+1 queries, missing indexes, and full table scans are easily preventable.
- **Implementation Steps:**
  1. Enable slow query logging in all environments.
  2. Add query count assertions in tests (detect N+1).
  3. Review EXPLAIN plans for queries touching >10K rows.
  4. Establish index review as part of schema migration PR process.
- **Enforcement Tools:** django-debug-toolbar, pganalyze, pt-query-digest, nplusone (Rails), SQLAlchemy query logging.
- **Sources:** Use The Index Luke (use-the-index-luke.com), Percona toolkit docs

---

## Category 5: CI/CD & DevOps

### 5.1 — Trunk-Based Development with Short-Lived Branches
- **Priority:** High
- **Difficulty:** Medium
- **Rationale:** Long-lived branches create merge pain and delay integration feedback. Trunk-based development with feature flags enables continuous integration without long branches.
- **Implementation Steps:**
  1. Set branch age limit policy (e.g., max 2 days).
  2. Implement feature flags for incomplete features (LaunchDarkly, Unleash, or simple config).
  3. Require branches to be rebased on main before merge.
  4. Monitor branch age in CI dashboards.
- **Enforcement Tools:** Branch protection rules, feature flag services, CI branch age warnings.
- **Sources:** trunkbaseddevelopment.com, Accelerate (Forsgren, Humble, Kim)

### 5.2 — Reproducible Builds via Lockfiles and Pinned Dependencies
- **Priority:** High
- **Difficulty:** Easy
- **Rationale:** "Works on my machine" and non-deterministic CI are caused by floating dependency versions. Lockfiles ensure byte-identical installs.
- **Implementation Steps:**
  1. Commit lockfiles (package-lock.json, poetry.lock, Cargo.lock, go.sum).
  2. Use `--frozen-lockfile` / `--locked` in CI install commands.
  3. Pin base Docker images to digests, not tags.
  4. Audit and update lockfiles on a regular cadence (weekly Renovate runs).
- **Enforcement Tools:** npm ci, pip install --require-hashes, cargo install --locked, Docker image digests.
- **Sources:** Reproducible Builds project (reproducible-builds.org), 12-Factor App (Config)

### 5.3 — Infrastructure as Code (IaC) with Drift Detection
- **Priority:** High
- **Difficulty:** Hard
- **Rationale:** Manual infrastructure changes create undocumented state, making incidents harder to debug and recovery unreliable. IaC ensures infrastructure is versioned, reviewable, and reproducible.
- **Implementation Steps:**
  1. Define all infrastructure in Terraform, Pulumi, or CDK.
  2. Run `terraform plan` in CI on every infra PR.
  3. Schedule drift detection (compare actual state vs. declared state) weekly.
  4. Alert on drift; auto-remediate for stateless resources.
- **Enforcement Tools:** Terraform, Pulumi, AWS CDK, Spacelift, env0, driftctl.
- **Sources:** Terraform best practices (HashiCorp), Pulumi docs

---

## Category 6: Code Review & Collaboration

### 6.1 — Small, Focused Pull Requests (<400 lines)
- **Priority:** High
- **Difficulty:** Easy
- **Rationale:** Large PRs get rubber-stamped. Studies show review quality drops sharply above 400 lines. Small PRs get faster, more thorough reviews.
- **Implementation Steps:**
  1. Set team guidelines for max PR size (recommend 200-400 lines changed).
  2. Break features into stacked PRs or incremental slices.
  3. Use draft PRs for early feedback on approach.
  4. Track PR size metrics in team retrospectives.
- **Enforcement Tools:** GitHub/GitLab PR size labels, Danger.js for automated size warnings, graphite.dev for stacked PRs.
- **Sources:** Google Engineering Practices (google.github.io), "Modern Code Review" (Bacchelli & Bird, ICSE 2013)

### 6.2 — Automated Code Review via Linters and Formatters
- **Priority:** High
- **Difficulty:** Easy
- **Rationale:** Human reviewers should focus on logic, design, and correctness--not style. Auto-formatting and linting eliminate style debates and catch trivial bugs before review.
- **Implementation Steps:**
  1. Adopt team-wide formatter (Prettier, Black, gofmt, rustfmt) with zero config overrides.
  2. Run linters in CI (ESLint, Ruff, clippy, golangci-lint).
  3. Add pre-commit hooks for format + lint.
  4. Enforce via CI: fail if files are not formatted.
- **Enforcement Tools:** pre-commit framework, Prettier, Black, Ruff, ESLint, clippy, MegaLinter.
- **Sources:** Google Style Guides, pre-commit.com

---

## Category 7: Documentation Standards

### 7.1 — Architecture Decision Records (ADRs)
- **Priority:** High
- **Difficulty:** Easy
- **Rationale:** Decisions made without written rationale get revisited repeatedly. ADRs record the context, options considered, and why a choice was made, preventing circular debates.
- **Implementation Steps:**
  1. Create `docs/decisions/` directory with ADR template (title, status, context, decision, consequences).
  2. Number sequentially (ADR-001, ADR-002, ...).
  3. Require ADR for any architectural or technology choice.
  4. Reference ADR numbers in PRs and code comments.
- **Enforcement Tools:** adr-tools CLI, PR template checkbox requiring ADR link for architectural changes.
- **Sources:** "Documenting Architecture Decisions" (Michael Nygard), adr.github.io

### 7.2 — Runbooks for Every Production Service
- **Priority:** High
- **Difficulty:** Medium
- **Rationale:** During incidents, responders need step-by-step instructions, not tribal knowledge. Runbooks reduce MTTR and enable any on-call engineer to respond effectively.
- **Implementation Steps:**
  1. Create runbook template: service overview, common failure modes, diagnostic steps, remediation steps, escalation path.
  2. Store alongside service code or in ops wiki.
  3. Link runbooks from alerting rules (PagerDuty, OpsGenie alert body includes runbook URL).
  4. Review and update runbooks after every incident retrospective.
- **Enforcement Tools:** Runbook templates in repo, CI check that every service has a runbook, alert rule linting.
- **Sources:** Google SRE Book (Chapter 14: Managing Incidents), PagerDuty incident response docs

---

## Summary Matrix

| # | Practice | Category | Priority | Difficulty |
|---|----------|----------|----------|------------|
| 1.1 | Dependency Injection | Design Patterns | High | Medium |
| 1.2 | Composition over Inheritance | Design Patterns | High | Medium |
| 1.3 | Separate Business from Infrastructure | Design Patterns | Critical | Hard |
| 2.1 | Test Pyramid | Testing & QA | Critical | Medium |
| 2.2 | Mutation Testing | Testing & QA | Medium | Medium |
| 2.3 | Property-Based Testing | Testing & QA | Medium | Medium |
| 3.1 | Secrets Management | Security | Critical | Easy |
| 3.2 | Dependency Vulnerability Scanning | Security | Critical | Easy |
| 3.3 | Input Validation & Output Encoding | Security | Critical | Medium |
| 4.1 | Performance Budgets in CI | Performance | High | Medium |
| 4.2 | Database Query Optimization | Performance | High | Medium |
| 5.1 | Trunk-Based Development | CI/CD & DevOps | High | Medium |
| 5.2 | Reproducible Builds | CI/CD & DevOps | High | Easy |
| 5.3 | Infrastructure as Code | CI/CD & DevOps | High | Hard |
| 6.1 | Small Pull Requests | Code Review | High | Easy |
| 6.2 | Automated Linting & Formatting | Code Review | High | Easy |
| 7.1 | Architecture Decision Records | Documentation | High | Easy |
| 7.2 | Production Runbooks | Documentation | High | Medium |

---

## Priority Distribution
- **Critical:** 5 practices (3.1, 3.2, 3.3, 2.1, 1.3)
- **High:** 11 practices
- **Medium:** 2 practices (2.2, 2.3)
- **Low:** 0 practices

## Quick Wins (High Priority + Easy Difficulty)
1. Secrets Management (3.1)
2. Dependency Vulnerability Scanning (3.2)
3. Reproducible Builds (5.2)
4. Small Pull Requests (6.1)
5. Automated Linting & Formatting (6.2)
6. Architecture Decision Records (7.1)

---

*Archive generated by SORT-ARCHIVE-3. Will be updated as DATA-GATHER-PRACTICES delivers additional findings.*
