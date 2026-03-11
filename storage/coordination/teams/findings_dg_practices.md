# Best Practices for Code - Data Gathering Findings

**Team:** DATA-GATHER-PRACTICES
**Date:** 2026-03-11
**Status:** Complete

---

## 1. Design Patterns & SOLID Principles

### 1.1 SOLID Principles (Still Foundational in 2025-2026)

**Why it matters:** SOLID principles reduce coupling, improve testability, and make codebases maintainable as they scale. A 2026 academic paper confirms these remain the primary defense against complexity in modern systems.

**The Five Principles:**

| Principle | Summary | Implementation Guidance |
|-----------|---------|------------------------|
| **Single Responsibility (SRP)** | One class/module = one reason to change | Apply to functions, modules, microservices, and even teams. If a module has multiple reasons to change, split it. |
| **Open-Closed (OCP)** | Open for extension, closed for modification | Use strategy pattern, plugins, or composition. New features should not require editing existing code. |
| **Liskov Substitution (LSP)** | Subtypes must be substitutable for base types | Use contract tests. If overriding a method changes behavior in unexpected ways, the hierarchy is wrong. |
| **Interface Segregation (ISP)** | Don't force clients to depend on unused methods | Prefer many small interfaces over one large one. In TypeScript, use `Pick<T, K>` for narrow types. |
| **Dependency Inversion (DIP)** | Depend on abstractions, not concretes | Use dependency injection. Define interfaces at the consumer level, not the provider level. |

**Beyond OOP:** SOLID now applies to microservices (SRP = one service per bounded context), cloud-native architecture (DIP = depend on service contracts, not implementations), and hexagonal/clean architecture.

**Tools & Automation:**
- SonarQube: Detects SOLID violations via coupling/cohesion metrics
- ArchUnit (Java) / ts-arch (TypeScript): Architectural fitness functions that enforce dependency rules in tests
- NDepend / JDepend: Dependency analysis

**Sources:**
- https://www.digitalocean.com/community/conceptual-articles/s-o-l-i-d-the-first-five-principles-of-object-oriented-design
- https://realpython.com/solid-principles-python/
- https://revolt.digital/blog/best-practices-for-software-development-in-2025-efficiency-quality-and-security/

### 1.2 Modern Design Pattern Guidance

**Why it matters:** Patterns are proven solutions, but misapplication creates unnecessary complexity.

**How to implement:**
1. Understand the problem first - never apply a pattern without a clear problem
2. Choose the simplest pattern that solves the problem
3. Avoid unnecessary abstraction layers
4. Follow YAGNI - don't add patterns speculatively
5. Prefer composition over inheritance

**Key 2025-2026 patterns:**
- **Event-driven architecture** for microservices communication
- **API-first design** for modern services
- **Hexagonal/Ports-and-adapters** for testable, framework-independent core logic
- **CQRS** (Command Query Responsibility Segregation) for read/write scaling

**Anti-patterns to avoid:**
- God objects / God services
- Premature abstraction
- Golden hammer (using one pattern for everything)
- Cargo cult programming (copying patterns without understanding)

---

## 2. Testing Best Practices

### 2.1 Property-Based Testing

**Why it matters:** A 2025 OOPSLA study found that a property-based test is 52x more likely to catch a mutation than a unit test.

**How to implement:**
1. Identify invariants that should hold for all valid inputs (e.g., sorting output is always ordered)
2. Write generators for your domain types
3. Leverage shrinking - tools auto-minimize failing cases
4. Complement (don't replace) example-based tests

**Tools:**
- **Hypothesis** (Python) - the gold standard
- **fast-check** (JavaScript/TypeScript)
- **QuickCheck** (Haskell, with ports to many languages)
- **PropEr** (Erlang)
- **jqwik** (Java)

**Source:** https://cseweb.ucsd.edu/~mcoblenz/assets/pdf/OOPSLA_2025_PBT.pdf

### 2.2 Mutation Testing

**Why it matters:** Measures whether tests actually detect bugs, not just whether they cover lines. Code coverage is a necessary but insufficient metric.

**How to implement:**
1. **Risk-based thresholds:** Payment processing = 95%+ mutation score; logging utilities = 70%
2. **Incremental execution:** Only mutate modified files and immediate dependencies in PRs
3. **Temporal scheduling:** Full analysis nightly/weekly; incremental on PRs
4. **Track trends:** Upward trends matter more than absolute scores

**Tools:**
- **Stryker** (JavaScript/TypeScript, C#) - most popular for JS ecosystem
- **PIT / pitest** (Java) - mature and fast
- **mutmut** (Python)
- **cargo-mutants** (Rust)

**Source:** https://mastersoftwaretesting.com/testing-fundamentals/types-of-testing/mutation-testing

### 2.3 TDD/BDD Latest Practices (2025-2026)

**Why it matters:** Teams adopting TDD reduced defect density by 40-90%. IBM and Microsoft report up to 90% fewer defects in pre-release code.

**TDD Best Practices:**
- Red-Green-Refactor cycle remains central
- Write descriptive test names: `should_calculate_total_with_tax`
- Use Arrange-Act-Assert structure
- AI accelerates TDD: LLMs draft ~70% of happy-path tests; humans focus on edge cases and intent
- TDD naturally produces modular, loosely coupled code

**BDD Best Practices:**
- Use for customer-facing applications and when working with non-technical stakeholders
- Layer approaches: core logic under TDD, high-level flows under BDD, release acceptance under ATDD
- Cucumber remains the standard BDD tool for plain-language scenarios

**Emerging:**
- Continuous mutation testing surfaces weak assertions early
- DORA metrics in IDE (lead time, change failure rate visible as you type)

**Sources:**
- https://monday.com/blog/rnd/test-driven-development-tdd/
- https://katalon.com/resources-center/blog/tdd-vs-bdd

### 2.4 Test Architecture

**Why it matters:** Poor test architecture leads to slow, brittle, unmaintainable test suites.

**Best practices:**
- **Testing pyramid:** Many unit tests, fewer integration tests, fewest E2E tests
- **Testing trophy (Kent C. Dodds):** More integration tests, fewer mocks
- **Flakiness management:** Explicit waits, robust selectors, retries, careful test data management
- **Test isolation:** Each test should be independent; no shared mutable state
- **Contract testing:** Use Pact or similar for microservice boundaries

---

## 3. Code Review Practices

### 3.1 Google's Code Review Guidelines

**Why it matters:** Google's guidelines are the industry gold standard. Their system enables code review feedback in 1-5 hours with small, focused changes.

**Core philosophy:** Seek continuous improvement, not perfection. "There is no such thing as perfect code -- there is only better code."

**What reviewers should focus on:**
1. **Design** - Is the code well-designed?
2. **Functionality** - Does it behave as intended? Is it good for users?
3. **Complexity** - Could it be simpler? Would another developer understand it?
4. **Tests** - Correct and well-designed automated tests?
5. **Naming** - Clear and descriptive?
6. **Comments** - Necessary and up to date?
7. **Style** - Follows project conventions?

**Comment severity labels (adopt these):**
- **Nit:** Minor thing, not blocking
- **Optional/Consider:** Good idea, not required
- **FYI:** For future consideration, no action needed now

**Reviewer etiquette:**
- Comment on the code, never the developer
- Reinforce what's done well, not just improvements
- Encourage simplification over explaining complexity
- Don't interrupt focused work for reviews; wait for natural break points

**Change size:** ~100 lines is reasonable; 1000 lines is too large.

**Tools & Automation:**
- GitHub pull request templates with checklists
- CODEOWNERS files for automatic reviewer assignment
- Danger.js / Peril for automated PR checks
- Auto-labeling (size, area) with GitHub Actions

**Sources:**
- https://google.github.io/eng-practices/review/
- https://google.github.io/eng-practices/review/reviewer/standard.html
- https://www.michaelagreiler.com/code-reviews-at-google/
- https://solmaz.io/google-eng-practices-github

---

## 4. Security Best Practices

### 4.1 OWASP Top 10: 2025

**Why it matters:** The OWASP Top 10 is the globally recognized standard for web application security risks. The 2025 edition reflects a major shift toward supply chain and ecosystem security.

**The 2025 Categories:**

| Rank | Category | Key Change from 2021 |
|------|----------|---------------------|
| A01 | Broken Access Control | More focus on microservices, zero-trust |
| A02 | Security Misconfiguration | Moved higher; IaC/container misconfigs |
| A03 | Software Supply Chain Failures | **NEW** - dependencies, CI/CD, build systems |
| A04 | Cryptographic Failures | TLS 1.3, AEAD, post-quantum readiness |
| A05 | Injection | Expanded to GraphQL, APIs, AI models |
| A06 | Insecure Design | Shift-left security emphasis |
| A07 | Authentication Failures | Sessions, tokens, identity federation |
| A08 | Software/Data Integrity Failures | Tamper-proof deployment |
| A09 | Logging and Alerting Failures | Real-time alerts, automation |
| A10 | Mishandling Exceptional Conditions | **NEW** - error handling, fallbacks |

**Sources:**
- https://owasp.org/Top10/2025/en/
- https://www.aikido.dev/blog/owasp-top-10-2025-changes-for-developers

### 4.2 Supply Chain Security & SBOMs

**Why it matters:** OWASP 2025 ranks Software Supply Chain Failures at #3. The EU Cyber Resilience Act mandates SBOMs with penalties up to 15M EUR.

**How to implement:**
1. **Generate SBOMs at build time** using SPDX or CycloneDX formats
2. **Track all dependencies** including transitive ones; prune unused libraries
3. **Pin versions and use lockfiles** to prevent dependency drift
4. **Integrate SCA into CI/CD** - scan every commit for known CVEs
5. **Use VEX documents** to prioritize vulnerability risk
6. **Only obtain components from trusted sources** over secure links
7. **Sign artifacts** - ensure build provenance

**Tools:**
- **Syft** - SBOM generation
- **Grype** - vulnerability scanning against SBOMs
- **OWASP Dependency-Check** - identifies known CVEs in dependencies
- **Snyk** - SCA + container scanning
- **Dependabot / Renovate** - automated dependency updates
- **Sigstore / cosign** - artifact signing

**Sources:**
- https://owasp.org/Top10/2025/A03_2025-Software_Supply_Chain_Failures/
- https://anchore.com/blog/software-supply-chain-security-in-2025-sboms-take-center-stage/
- https://www.oligo.security/academy/ultimate-guide-to-software-supply-chain-security-in-2025

### 4.3 Secure Coding Practices

**How to implement:**
1. Integrate security into SDLC from design phase (shift-left)
2. Implement RBAC/ABAC with least privilege
3. Use SAST, DAST, IAST in CI/CD pipelines
4. Review all AI-generated code for vulnerabilities (OWASP mandate)
5. Design for failure - fail safely and predictably
6. Pair OWASP Top 10 with ASVS for verifiable security
7. Align with PCI-DSS 4.0, NIST 800-53/SSDF, SOC 2, ISO 27001

**Source:** https://owasp.org/www-project-secure-coding-practices-quick-reference-guide/

---

## 5. Performance Engineering

### 5.1 Profiling & Benchmarking

**Why it matters:** Without measurement, optimization is guesswork. You might make things slower without realizing it.

**The Performance Loop:**
1. Profile to find bottlenecks
2. Optimize the identified bottleneck
3. Benchmark to validate improvement
4. Profile again to confirm no regressions
5. Repeat

**Profiling best practices:**
- Start early - embed performance considerations from the design phase
- Continuous testing and monitoring, not just pre-release
- Profile in production-like environments
- Use flame graphs to visualize hot paths

**Benchmarking best practices:**
- Establish benchmarks early in the lifecycle
- Use consistent, controlled environments
- Run multiple iterations and report statistical measures (median, p95, p99)
- Types: Application benchmarks (micro), Synthetic (controlled), Real-world (production patterns)

**2025 Engineering Performance Metrics (DORA):**
- Cycle Time: < 7 days (high performers)
- PR Review Time: < 24 hours
- Deployment Frequency: at least weekly
- MTTR: within 1 hour

**Tools:**
- **pprof** (Go), **py-spy** (Python), **node --prof** / **clinic.js** (Node.js)
- **Benchmark.js** / **tinybench** (JS), **BenchmarkDotNet** (C#), **JMH** (Java)
- **k6** / **Locust** (load testing)
- **Datadog** / **Grafana** / **New Relic** (continuous monitoring)

**Sources:**
- https://particular.net/videos/performance-loop-a-practical-guide-to-profiling-and-benchmarking
- https://pflb.us/blog/performance-engineering/

---

## 6. CI/CD Best Practices

### 6.1 GitHub Actions Patterns

**Why it matters:** GitHub Actions is used by 62% of developers (2025 survey). It's the default CI/CD for GitHub-hosted projects.

**Workflow best practices:**
1. **Descriptive file names:** `build-and-test.yml`, `deploy-prod.yml`
2. **Dependency caching:** Use `actions/cache` or built-in cache options
3. **Parallel jobs:** Run independent jobs concurrently
4. **Path filters:** Skip workflows when irrelevant files change
5. **Matrix strategies:** Test across multiple versions/platforms in parallel
6. **Concurrency groups:** `cancel-in-progress: true` to cancel redundant runs
7. **Never hardcode secrets:** Use GitHub Secrets and environment variables

**Security in CI/CD:**
- Pin action versions to full SHA, not tags (prevents supply chain attacks)
- Use `permissions` to restrict GITHUB_TOKEN scope
- Run security scans (SAST, SCA) in every PR
- Sign artifacts and generate provenance

**Source:**
- https://github.com/github/awesome-copilot/blob/main/instructions/github-actions-ci-cd-best-practices.instructions.md
- https://calmops.com/devops/cicd-pipelines-2026/

### 6.2 Deployment Strategies

**How to implement:**
- **Blue-Green:** Two identical environments; switch traffic after validation
- **Canary:** Roll out to small percentage (5-10%), monitor, gradually increase
- **Progressive delivery:** Feature flags + canary + automated verification
- **GitOps:** Git as single source of truth; ArgoCD/Flux sync state

**Source:** https://www.kellton.com/kellton-tech-blog/continuous-integration-deployment-best-practices-2025

### 6.3 Feature Flags

**Why it matters:** 95% of DevOps teams using feature flags slashed release cycle times by 40%. 82% avoided production incidents.

**Best practices:**
1. **Keep flags short-lived** - remove after full rollout
2. **Set expiration dates** - flag with no change for weeks should trigger review
3. **Test both ON and OFF states** in automated tests
4. **Use a management tool** (LaunchDarkly, Unleash, Flagsmith, ConfigCat)
5. **Automate stale flag detection** to prevent tech debt
6. **Categorize flags:** Release, Operational, Experiment, Permission
7. **Progressive rollout:** Start at 5-10%, monitor, increase gradually

**Sources:**
- https://launchdarkly.com/blog/what-are-feature-flags/
- https://www.kodekx.com/blog/feature-flags-best-practices-2025

---

## 7. Code Quality

### 7.1 Static Analysis Tools

**Why it matters:** ~70% of organizations have discovered vulnerabilities in AI-generated code (2026). Static analysis catches issues before they reach production.

**Top tools by category:**

| Category | Tools |
|----------|-------|
| **Multi-language SAST** | SonarQube, Semgrep, Snyk Code, Qodana |
| **JavaScript/TypeScript** | ESLint, Biome (successor to Rome), oxlint |
| **Python** | Ruff (extremely fast), pylint, mypy, pyright |
| **Go** | golangci-lint, staticcheck |
| **Rust** | clippy (built-in) |
| **Security-focused** | Semgrep, Aikido, Veracode, Fortify |

**Best practices:**
1. Integrate into IDE (pre-commit feedback) AND CI/CD (enforcement)
2. Set quality gates with thresholds for bugs, vulnerabilities, code smells
3. Use AI-driven analysis that scans at pre-commit and merge stages
4. Customize rules to match team standards
5. Treat warnings as errors in CI (zero-tolerance policy)

**Sources:**
- https://thectoclub.com/tools/best-code-analysis-tools/
- https://www.codeant.ai/blogs/static-code-analysis-tools

### 7.2 TypeScript Type Checking

**Why it matters:** Applications with strict mode enabled experience ~40% fewer type-related bugs reaching production.

**How to implement:**
1. **Enable `strict: true`** from day one on new projects
2. **Avoid `any`** - use `unknown` instead for truly unknown types
3. **Enable `exactOptionalPropertyTypes`** for stricter optional handling
4. **Incremental migration** for existing projects: 2-3 files per week, start with utility files
5. **Complement with linting:** `noExplicitAny` rule in ESLint/Biome

**Key strict flags:**
- `strictNullChecks` - distinct null/undefined types
- `noImplicitAny` - no implicit any fallback
- `strictPropertyInitialization` - class properties must be initialized
- `strictFunctionTypes` - contravariant parameter checking
- `strictBindCallApply` - correct arguments for call/bind/apply

**Performance:** Zero bundle size impact. ~2-5% slower type checking only.

**Sources:**
- https://medium.com/@nikhithsomasani/best-practices-for-using-typescript-in-2025-a-guide-for-experienced-developers-4fca1cfdf052
- https://www.typescriptlang.org/tsconfig/strict.html

### 7.3 Linting Configuration

**Best practices:**
1. Use a single, fast tool where possible (Biome replaces ESLint + Prettier for JS/TS)
2. Enforce formatting via pre-commit hooks (husky + lint-staged or lefthook)
3. Auto-fix on save in IDE; fail on CI if unfixed
4. Shared configs across team (publish as npm package for consistency)
5. Regularly update rules as ecosystem evolves

---

## Summary: Top 10 Most Impactful Practices

1. **Enable TypeScript strict mode** from day one (40% fewer type bugs)
2. **Adopt property-based testing** alongside unit tests (52x better mutation detection)
3. **Generate SBOMs and scan dependencies** in CI/CD (OWASP #3 risk)
4. **Use feature flags for trunk-based development** (40% faster release cycles)
5. **Follow Google's code review guidelines** (small CLs, severity labels, continuous improvement)
6. **Set quality gates with static analysis** (SonarQube/Semgrep in CI)
7. **Profile before optimizing, benchmark after** (the performance loop)
8. **Pin GitHub Action versions to SHA** (supply chain security)
9. **Apply SOLID principles beyond OOP** (to services, modules, teams)
10. **Practice TDD with AI assistance** (LLMs draft happy-path tests; humans own intent and edge cases)

---

*Gathered by DATA-GATHER-PRACTICES team, 2026-03-11*
