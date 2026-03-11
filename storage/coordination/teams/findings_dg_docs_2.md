# DATA-GATHER-DOCS-2: Official Manuals & Documentation Findings
**Gathered:** 2026-03-11
**Agent:** DATA-GATHER-DOCS-2
**Focus:** Linux systems, Cloud providers, Infrastructure tools, Database systems

---

## 1. LINUX SYSTEMS DOCUMENTATION

### 1.1 Linux Kernel
- **Latest stable:** 6.19.6 (released 2026-03-04)
- **Mainline:** 7.0-rc2 (released 2026-03-01) - Linux 7.0 confirmed by Linus Torvalds for mid-April 2026
- **LTS kernels:** 6.18.16, 6.12.76, 6.6.129, 6.1.166, 5.15.202, 5.10.252
- **Official docs:** https://kernel.org, https://docs.kernel.org
- **Source:** https://kernel.org/releases.html

### 1.2 systemd
- **Latest stable:** v259.3 (2026-03-04), fixes GHSA-6pwp-j5vg-5j6m privilege escalation
- **Latest major:** v259 (2025-12-17)
- **Upcoming:** v260 RC in Debian repos
- **Official docs:** https://systemd.io, https://github.com/systemd/systemd
- **Key changes in v259:**
  - cgroup v1 support REMOVED entirely - cgroup v2 unified hierarchy only
  - Kernel baseline bumped to v5.4 minimum, v5.7 recommended
  - TPM 1.2 support dropped (TPM 2.0 only)
  - Journal default storage changed to 'persistent'
  - nftables only (iptables/libiptc removed from systemd-networkd/nspawn)
  - SysV init scripts formally deprecated, removal planned for v260
  - Experimental musl libc support
  - New systemd-report tool for collecting metrics via Varlink
  - Varlink expansion: Reload/Reexecute calls, feature parity with D-Bus
- **v260 planned requirements:** Linux kernel 5.10+, glibc 2.34+, OpenSSL 3.0+, Python 3.9+

### 1.3 cgroups v2
- **Official doc:** https://docs.kernel.org/admin-guide/cgroup-v2.html
- **Source RST:** https://github.com/torvalds/linux/blob/master/Documentation/admin-guide/cgroup-v2.rst
- **Man page:** https://man7.org/linux/man-pages/man7/cgroups.7.html
- **Status:** Now the ONLY supported hierarchy. systemd v259 removed cgroup v1 support. Kubernetes 1.35 deprecates cgroup v1 (kubelet refuses to start by default on v1 nodes).
- **Key architecture:**
  - Single unified hierarchy (unlike v1's multiple hierarchies)
  - Controllers: CPU, Memory, IO, PID, Device, RDMA, Misc
  - Every process belongs to exactly one cgroup
  - Merged in Linux kernel 4.5 (2016), now fully mature

### 1.4 eBPF
- **Official docs:** https://docs.ebpf.io, https://ebpf.io
- **Kernel docs:** https://docs.kernel.org/bpf/
- **Foundation:** https://ebpf.foundation
- **Key developments (2025-2026):**
  - eBPF Foundation awarded $100K in research grants (U of Michigan, UC Riverside)
  - Test suite expansion in kernel CI for better regression detection
  - libbpf library updates: BPF token permissions, arena maps support
  - Tooling improvements across C, Rust, and higher-level languages
  - Growing adoption in networking, observability, and security domains
  - New eBPF Meetup Program launched

---

## 2. CLOUD PROVIDER DOCUMENTATION

### 2.1 AWS (Amazon Web Services)
- **Docs:** https://docs.aws.amazon.com
- **Key updates (Jan-Mar 2026):**
  - **Lambda:** Durable functions development accelerated with Kiro power for multi-step AI workflows
  - **ECS:** Enhanced Cloud Map integration for service discovery, automatic DNS records and health checks
  - **EKS:** Hybrid Nodes support, native OIDC provider, new access entry types (EC2, HYBRID_LINUX, HYPERPOD_LINUX), native GitOps support (ArgoCD/Flux)
  - **ECR:** Cross-repository layer sharing via blob mounting
  - **CDK 2026:** Mixins as core language construct, 150+ PRs spanning EKS/Bedrock/ECS
  - **MCP Servers:** New Model Context Protocol servers for ECS, EKS, and Serverless (open source, AWS Labs GitHub)
  - **IAM:** Simplified in-console role creation panel for EC2, Lambda, EKS, ECS, Glue, CloudFormation
  - **GPU:** NVIDIA Blackwell G7e instances GA for AI inference
- **Source:** https://aws.amazon.com/blogs/aws/, https://docs.aws.amazon.com/eks/latest/userguide/

### 2.2 Google Cloud Platform (GCP)
- **Docs:** https://docs.cloud.google.com
- **Cloud Run (release notes updated 2026-03-07):**
  - Direct VPC egress now GA (no Serverless VPC Access connector needed)
  - Task timeout up to 168 hours (7 days) for jobs
  - Go 1.23 runtime (Preview), Node.js 22 runtime (GA)
  - In-memory volume type now GA
  - GPU support (Preview) in europe-west4
  - Firestore and Vertex AI integrations (Preview)
  - ITAR compliance scope
- **GKE:** Remains industry standard for managed Kubernetes; Knative serving now separate experience from Cloud Run
- **Cloud Run Functions (formerly Cloud Functions):** Go 1.23 (Preview), Node.js 22 (GA)
- **Source:** https://docs.cloud.google.com/run/docs/release-notes

### 2.3 Microsoft Azure
- **Docs:** https://learn.microsoft.com/en-us/azure/
- **Container Apps (ACA):**
  - Serverless GPU support now GA (NVIDIA A100, T4 with scale-to-zero, per-second billing)
  - 48% YoY adoption growth
  - Built on Kubernetes with Dapr, KEDA, Envoy
  - Linux containers only
  - No direct Kubernetes API access
- **AKS:** Full Kubernetes API access, Linux + Windows containers, deep customization
- **Azure Functions:** Supports Flex Consumption, App Service, Premium, and Container Apps plans
- **Decision guide:** Container Apps for serverless microservices, AKS for full K8s control, Functions for ephemeral event-driven code
- **Source:** https://learn.microsoft.com/en-us/azure/container-apps/

---

## 3. INFRASTRUCTURE TOOLS

### 3.1 Docker
- **Latest Engine:** v29.3.0 (2026-03-05)
- **Official docs:** https://docs.docker.com/engine/release-notes/29/
- **Key changes in v29:**
  - containerd image store is now default for fresh installs
  - Experimental nftables firewall backend support
  - BuildKit updated to v0.28.0
  - Minimum API version lowered to v1.40 (Docker 19.03)
  - Go module deprecated: `github.com/docker/docker` -> `github.com/moby/moby/client`
  - CLI plugin error-hooks support
- **Docker Desktop updates:**
  - Compose v5 with new Go SDK for programmatic control
  - Docker Sandboxes: secure microVM-based environments for coding agents
  - Docker Model Runner: Anthropic-compatible API, vLLM Metal support
  - MCP Toolkit with profiles and custom catalogs
  - Security: CVE-2026-2664 (grpcfuse OOB read), CVE-2026-28400 (Model Runner flag injection)
  - Linux kernel 6.12.67 in Desktop
- **Source:** https://docs.docker.com/desktop/release-notes/

### 3.2 Kubernetes
- **Latest stable:** v1.35.2 (2026-02-26)
- **Upcoming:** v1.36.0 scheduled for 2026-04-22
- **Official docs:** https://kubernetes.io/releases/
- **Key changes in 1.35:**
  - cgroup v1 deprecated: kubelet refuses to start on cgroup v1 nodes by default
  - containerd 1.x end of support: must switch to containerd 2.0+ before next K8s version
  - Ingress NGINX being retired (March 2026) - migrate to Gateway API
- **Gateway API v1.4.0** (GA, 2025-10-06):
  - Successor to Ingress API
  - Role-oriented design (Infrastructure Provider, Cluster Operator, App Developer)
  - Core resources: GatewayClass, Gateway, HTTPRoute
  - Production maturity with Istio, NGINX, Traefik, cloud LB implementations
  - Docs: https://gateway-api.sigs.k8s.io/
- **Support:** 1.35 (EOL Feb 2027), 1.34 (EOL Oct 2026), 1.33 (EOL Jun 2026)
- **Source:** https://kubernetes.io/releases/, https://github.com/kubernetes/kubernetes/releases

### 3.3 Terraform / OpenTofu
- **Terraform latest stable:** v1.14.6 (BUSL-1.1 license)
- **Terraform pre-release:** v1.15.0-alpha20260304
- **OpenTofu latest:** v1.11.5 (2026-02-12, MPL 2.0 open source)
- **Official docs:** https://developer.hashicorp.com/terraform, https://opentofu.org/
- **Terraform key features:**
  - Variables and locals in module source/version attributes
  - Experimental deferred actions for count/for_each with unknown values
  - Terraform Enterprise Replicated support ends April 1, 2026
- **OpenTofu key features:**
  - CNCF Sandbox project (accepted April 2025)
  - Client-side state encryption (PBKDF2, AWS KMS, GCP KMS, OpenBao)
  - Parallel provider fetching in `tofu init`
  - Compatible with Terraform v1.5.x; migrate via OpenTofu v1.6.x
- **Source:** https://github.com/hashicorp/terraform/releases, https://github.com/opentofu/opentofu/releases

---

## 4. DATABASE SYSTEMS

### 4.1 PostgreSQL
- **Latest:** 18.3 (2026-02-26, out-of-cycle regression fix release)
- **Current major:** PostgreSQL 18 (released 2025-09-25)
- **Official docs:** https://www.postgresql.org/docs/18/, https://www.postgresql.org/docs/release/
- **Supported versions:** 18.3, 17.9, 16.13, 15.17, 14.22 (5-year support per major version)
- **PostgreSQL 18 major features:**
  - **Async I/O subsystem:** Up to 3x performance improvement for sequential scans, bitmap heap scans, vacuums
  - **Skip scan for B-tree indexes:** Multicolumn B-tree usable in more cases
  - **UUIDv7:** `uuidv7()` function for timestamp-ordered UUIDs
  - **Virtual generated columns:** Compute on read (now default for generated columns)
  - **OAuth 2.0 authentication:** Integration with modern identity providers via pg_hba.conf
  - **OLD/NEW in RETURNING:** Available for INSERT, UPDATE, DELETE, MERGE
  - **Temporal constraints:** PRIMARY KEY, UNIQUE, FOREIGN KEY over ranges
  - **Wire protocol v3.2:** First update since 2003
  - **Upgrade improvements:** Statistics preserved during major-version upgrades
  - **Security:** Data checksums enabled by default, MD5 deprecated for SCRAM-SHA-256, TLS 1.3 cipher control
- **Recent security fixes (18.2):** Heap buffer overflow in pgcrypto, multibyte character length validation bug
- **Source:** https://www.postgresql.org/about/news/postgresql-183-179-1613-1517-and-1422-released-3246/

### 4.2 SQLite
- **Latest:** 3.52.0 (2026-03-06)
- **Official docs:** https://sqlite.org, https://sqlite.org/changes.html
- **Key changes in 3.52.0:**
  - 15-year-old database corruption bug fixed
  - CLI significantly enhanced (Query Result Formatter library)
  - Rounding now to 17 significant digits (was 15)
  - ALTER TABLE: can now add/remove NOT NULL and CHECK constraints
  - New functions: `json_array_insert()`, `jsonb_array_insert()`
  - TEMP triggers can now modify/query tables in main schema
  - VACUUM INTO enhanced with URI reserve parameter
  - `SQLITE_PREPARE_FROM_DDL` option for virtual tables
  - Windows RT support discontinued
- **Support pledge:** Through year 2050
- **Release cadence goal:** Once every six months
- **Source:** https://www.sqlite.org/releaselog/3_52_0.html

### 4.3 Redis
- **Latest:** Redis 8.6.1 (February 2026)
- **Official docs:** https://redis.io/docs/latest/
- **License:** Source-available (not open source)
- **Key info:**
  - Redis Cloud: 8.4 available in select regions, auto-upgrade path to 8.6
  - Major version yearly, minor version at 6 months
  - Latest stable fully supported; two additional versions get maintenance only
- **Source:** https://github.com/redis/redis/releases

### 4.4 Valkey (Redis Fork)
- **Latest:** 9.0.3 (2026-02-24)
- **Also available:** 8.1.6, 7.2.12
- **Official docs:** https://valkey.io, https://valkey.io/topics/releases/
- **License:** BSD (open source, Linux Foundation project)
- **Key features:**
  - 1B+ RPS clusters, 40% higher throughput vs baseline
  - Hash field expiration (fine-grained TTLs)
  - 3-year maintenance support, 5-year extended security support
- **2026 roadmap:** Semantic caching, hybrid search (full-text + vector similarity), improved durability, compression/SSD integration
- **Recent CVEs:** CVE-2026-21863 (DoS via cluster bus), CVE-2026-27623 (empty request handling)
- **Source:** https://github.com/valkey-io/valkey/releases

---

## 5. KEY ARCHITECTURAL TRENDS (2026)

1. **cgroup v2 is now mandatory** - systemd v259 removed v1, Kubernetes 1.35 deprecates v1, containerd 2.0 required
2. **nftables replaces iptables** - systemd and Docker both moving to nftables
3. **Gateway API replaces Ingress** - Kubernetes retiring Ingress NGINX March 2026
4. **containerd 2.0 required** - Kubernetes 1.35 is last release supporting containerd 1.x
5. **Async I/O everywhere** - PostgreSQL 18's AIO subsystem, Linux kernel io_uring maturity
6. **MCP (Model Context Protocol)** - AWS and Docker both integrating MCP servers for AI-assisted development
7. **OpenTofu gaining momentum** - CNCF Sandbox, client-side encryption, diverging from Terraform
8. **Valkey vs Redis** - Valkey 9.0 with 40% throughput gains, hybrid search roadmap; Redis staying proprietary-licensed
9. **Wire protocol modernization** - PostgreSQL first protocol update since 2003
10. **Linux 7.0 imminent** - Major version bump, mid-April 2026

---

## 6. TOOL REQUESTS FOR PROGRAMMING TEAMS

1. **cgroup v2 migration checker** - Tool to audit systems for cgroup v1 dependencies before systemd v259/K8s 1.35 upgrades
2. **Ingress-to-Gateway-API converter** - Automated migration tool for Kubernetes Ingress resources to Gateway API
3. **containerd version validator** - Pre-upgrade check for containerd 2.0 compatibility before K8s 1.36
4. **PostgreSQL 18 AIO benchmark suite** - Standardized benchmarks comparing AIO vs traditional I/O across workloads
5. **iptables-to-nftables rule converter** - For systemd/Docker nftables migration
6. **Terraform-to-OpenTofu migration tool** - Automated state/config migration with encryption setup
7. **Valkey vs Redis feature parity tracker** - Compare API compatibility between Redis 8.x and Valkey 9.x
8. **MCP server discovery tool** - Catalog and test available MCP servers (AWS, Docker, etc.)
9. **SQLite corruption scanner** - Tool to check existing databases for the 15-year corruption bug fixed in 3.52.0
10. **Kubernetes version compatibility matrix generator** - Auto-generate compatibility matrices across K8s, containerd, cgroup, and CNI versions
