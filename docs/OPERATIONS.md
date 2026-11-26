# EmailFlow operations guide

Use the [README](../README.md) to start, use, and test EmailFlow. This guide covers alternate clusters, recovery, security scanning, and recorded verification.

## Cluster and ports

The default Kind cluster is `emailflow`. The start script and port forwards use `.emailflow-kubeconfig` unless `KUBECONFIG` is set. For direct `kubectl` commands against the default cluster, export `KUBECONFIG="$PWD/.emailflow-kubeconfig"`.

Use a separate cluster only when rehearsing an upgrade or running isolated tests. Set the same environment in each terminal that starts, forwards, or tests it.

```bash
export EMAILFLOW_CLUSTER_NAME=emailflow-v2-test
export KUBECONFIG="$HOME/.emailflow-v2-test-kubeconfig"
export EMAILFLOW_DASHBOARD_PORT=18080
export EMAILFLOW_MAIL_PORT=11080
export EMAILFLOW_JAEGER_PORT=17686
export EMAILFLOW_ORIGIN=http://localhost:18080
```

Run the [startup commands](../README.md#run-locally) with these settings. Open MailCatcher and Jaeger directly on the alternate ports. The dashboard links use default ports. Use `bash scripts/stop-local.sh` only to delete the selected cluster and its local data.

## Crash recovery

| Crash window | Durable facts | Recovery |
|---|---|---|
| Gateway accepted before ACK | The gateway commits the receipt and incident, then exits with code 42 | The worker retries the range. The gateway returns the existing acceptance and the worker resolves the incident after completion |
| Worker received ACK before completion | The receipt is durable. The worker commits the incident and exits with code 42 | Kubernetes restarts the worker. Kafka replays the range and the gateway returns the existing acceptance |
| Worker committed completion before offset | The receipt and unique completion are durable | The worker reuses the completion record and commits the offset |
| Scheduler published before cursor commit | Kafka holds the range. The cursor may lag | The scheduler republishes the same range. The gateway and worker deduplicate it |
| MailCatcher SMTP acceptance uncertain | The mirror job is sending or unknown | Abandoned jobs become unknown after two minutes. Inspect the Message-ID in MailCatcher and reconcile manually |

PostgreSQL enforces one simulated receipt per campaign recipient when data persists and retries succeed. MailCatcher samples are best-effort. Keep SMTP pointed at MailCatcher. A production relay needs its own idempotency keys, durable acceptance receipts, uncertain-delivery reconciliation, and bounce tracking.

## Upgrade or recover an existing installation

To rehearse, create a dedicated Kind test cluster and restore a v1 logical dump into its PostgreSQL 16 pgvector database before running `python scripts/migrate-db.py`. Compare campaign, receipt, completion, and incident counts before and after. Verify backfill, role access, and delivery on the restored copy. Keep the running v1 cluster online during the rehearsal.

For an explicitly approved v1 upgrade, prevent new submissions, drain Kafka and active writers, and take a final backup. The migration reuses the PostgreSQL 16 PVC and rotates the legacy owner password. Secrets for runtime roles are generated once. `start-local.sh` checks for the backup, stopped writers, and removed worker ScaledObjects before switching an existing v1 database.

```bash
export KUBECONFIG=/path/to/the/existing/kubeconfig
bash scripts/backup-local.sh
kubectl --context kind-emailflow -n emailflow delete scaledobject worker-standard worker-urgent
kubectl --context kind-emailflow -n emailflow scale deployment/api deployment/scheduler deployment/gateway deployment/worker-standard deployment/worker-urgent deployment/mirror --replicas=0
# Wait for the writer pods to terminate before proceeding.
EMAILFLOW_UPGRADE=1 EMAILFLOW_UPGRADE_BACKUP=backups/emailflow-YYYYMMDDTHHMMSSZ.dump bash scripts/start-local.sh
bash scripts/users.sh create admin --role admin
```

Keep the pre-upgrade database backup, v1 manifests and image references, and a coordinated record of Kafka state. For rollback, stop writers, restore the backup to a separate PostgreSQL instance, reconcile Kafka topics and offsets, and recover the v1 database configuration before restarting v1 against restored data. For v2 recovery, restore the corresponding service credentials and MFA encryption key from protected storage. Loss of the MFA key requires resetting enrolled users. Backups contain campaign content and encrypted authentication data. Encrypt and control access to them, and store key escrow separately.

## Archive storage and ranking

The indexer backfills accepted campaigns and runs independently of delivery. It stores one English full-text document per campaign with a GIN index, plus overlapping message chunk vectors in `vector(384)` with a cosine HNSW index. A transaction writes the document and its vectors together. The indexer defers failed campaigns so a failing document can retry without blocking later work. Receipts remain per recipient. Individualized messages would require indexing by content or message identity.

The CPU embedding container loads BAAI/bge-small-en-v1.5 from its build-time model cache. Runtime model fetching is disabled, and the embedding pod has a deny-all egress policy. The model is English-oriented and token limits can truncate character chunks. Pin and verify the exact model revision and artifact hash before production.

Authenticated search uses bounded semantic, lexical, recent, and priority candidate sets, applies recipient/priority/date filters, deduplicates the union, and reranks it with the best candidate cosine match, normalized full-text rank, 30-day exponential recency, and urgency. Default weights are 60%, 25%, 10%, and 5%. Weights normalize to a total of one. Search uses parameterized SQL, a statement timeout, and bounded model requests. If embedding is unavailable, search uses the remaining enabled candidate sources. A semantic-only search returns a retryable error. Candidate retrieval is approximate. Check `EXPLAIN (ANALYZE, BUFFERS)` on representative data and filters before sizing the archive.

## Authentication and infrastructure controls

- Create users with `bash scripts/users.sh create <username> --role admin|operator|viewer`. User administration uses a short-lived pod and separate database role. Its `reset`, `revoke`, `disable`, `enable`, `role`, `list`, and `cleanup` actions manage enrollment and sessions. Keep enrollment secrets private. There is no default account or public signup.
- Argon2id stores password hashes. Every sign-in requires a TOTP code. Fernet encrypts MFA secrets, and a locked database row tracks the latest accepted TOTP step. Opaque 256-bit session tokens are hashed in PostgreSQL. The cookie is HttpOnly and SameSite Strict, with Secure enabled for HTTPS origins. Sessions expire after 30 minutes idle or eight hours absolute. Mutating requests require the configured Origin and a per-session CSRF token.
- Role checks run on the API. Login has account and workspace throttles, search allows 20 requests per minute per user, and scheduling has per-user rate and workspace backlog limits. The proxy and JSON endpoints bound request sizes. Validation errors omit submitted values. The frontend adds CSP and browser security headers. The audit trail records authentication, administrative actions, campaign changes, and search events without storing search text.
- PostgreSQL migrations run as owner. Runtime duties use separate database roles, Kubernetes Secrets, explicit internal bearer credentials, and ingress NetworkPolicies. The SMTP mirror can reach only MailCatcher, PostgreSQL, telemetry, and DNS. The application pods run non-root with read-only filesystems, dropped capabilities, seccomp, and resource limits. The cluster administrator can access raw data, Secrets, and direct loopback infrastructure port forwards.

## Security scanning and release requirements

Install Trivy and use an isolated scanner environment. Run the source, dependency, secret, manifest, image, and SBOM checks after building images. Run the passive ZAP baseline against a running loopback instance with Docker host networking available.

```bash
python3 -m venv .venv-security
source .venv-security/bin/activate
pip install bandit semgrep pip-audit
bash scripts/scan-security.sh
bash scripts/scan-dast.sh
```

Review reports and CycloneDX SBOMs under `security/reports/`. The scanner script returns a nonzero status for findings or scanner errors. Keep reports and backups out of Git. CI runs source, build, and image checks using [the security workflow](.github/workflows/security.yml). The ZAP baseline covers unauthenticated traffic. Authenticated authorization and penetration checks remain release requirements.

Before exposing this application, add and verify HTTPS/HSTS, service TLS or mTLS, PostgreSQL TLS, Kafka SASL_SSL and ACLs including KEDA, tenant isolation and RLS for archive vectors, phishing-resistant identity and recovery, key rotation and escrow, encrypted storage and backups, immutable audit export, data retention, HA and recovery drills, and representative throughput and failover tests. Resolve remaining image and configuration scan findings. Pin Python transitive dependencies with verified hashes, workflow actions and images by digest, and the model artifact. Establish signing and authenticated penetration testing.

## Recorded verification


Results from the 26 November 2026 integration run. Initialize a fresh cluster and repeat runtime checks before using a v2 deployment.

| Check | Result |
| --- | --- |
| Python tests and build checks | 22 tests passed, including direct ASGI checks and the dispatch reservation regression. Python compilation, Bash syntax, Kubernetes YAML parsing, and frontend typecheck and production build passed. |
| Archive SQL and migration | Seeded the v1 schema and ran the v2 schema and role grants twice in embedded PostgreSQL 19 with pgvector 0.8.7. The migration preserved three campaigns and five unique receipts. Verified role grants, hybrid and lexical results, and filters. The small filtered hybrid plan used primary-key and heap scans. Separate index eligibility checks selected HNSW and GIN when sequential scans were disabled. |
| API and model | The real FastAPI app, embedded PostgreSQL socket, and offline BGE service passed the local smoke test. Verified semantic and lexical ranking, changes to order from weights, receipt counts and deduplication, no early gateway acceptance, lexical fallback during model outage, password and TOTP login, TOTP replay denial, roles, Origin/CSRF, rate limits, idle and absolute expiry, and logout. |
| Source and dependency scans | Bandit reported zero medium-or-higher findings. Semgrep reported zero findings. After updating Vite, FastAPI/Starlette, OpenTelemetry, cryptography, FastEmbed, and Pillow, npm audit and both Python dependency audits reported zero known vulnerabilities. Trivy’s source scan reported no secrets and flagged Kubernetes configuration. The manifest rescan retained two high findings for writable root filesystems in the Kafka and PostgreSQL images. |
| Images and dynamic scan | The three application images built during integration. CycloneDX dependency SBOMs were generated for the backend, embedding service, and frontend. Rebuilding the final image revisions and completing image vulnerability scans and image SBOMs were blocked by Docker errors and host disk exhaustion. The Kafka scan also failed while downloading Trivy’s Java database. The ZAP baseline requires a reachable v2 dashboard. |
| Kind and v1 deployment | A separate Kind node reached Ready. Subsequent Kubernetes calls timed out after Docker lost access to disk, and the v2 integration command received `WinError 10061` on port 18080. The live v1 database was backed up and `pg_restore --list` validated the archive listing. Restoring that archive on PostgreSQL 16, v2 pod startup and CNI enforcement, Kafka crash recovery, and both KEDA load cases remain unverified for v2. The existing v1 database and credentials stayed in place. |

The CI test suite also passed locally against PostgreSQL 16 with pgvector in a disposable container. It verified repeatable schema migration, receipt preservation, and archive search against a seeded v1 fixture. PostgreSQL tests and Kafka startup and topic creation passed with read-only roots and dedicated writable mounts. The subsequent Trivy manifest scan found zero high or critical findings. The [CI workflow](../.github/workflows/ci.yml) includes the PostgreSQL test and checks for failed cluster reads during upgrade preflight.

Local results are under `security/reports/`. Dependency scans depend on the advisory database and resolved versions at scan time. The seeded query plan describes a small fixture.
