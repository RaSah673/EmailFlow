# EmailFlow — Local email delivery operations MVP

A React/TypeScript + FastAPI + PostgreSQL + Apache Kafka sandbox with Kind Kubernetes, KEDA, durable simulated mail acceptance, MailCatcher SMTP samples, and OpenTelemetry. **No real recipients and no external mail provider.** This is a single-workspace engineering MVP, not a production multi-tenant SaaS.

## 1. Run on your PC

Requirements: Docker Desktop (Linux containers; WSL2 recommended on Windows), Kind, kubectl, Helm, Bash and Python 3 for integration tests. Allow roughly 8–12 GB RAM and 6–8 CPU cores for experimentation; more resources are needed for sustained 24+12 workers. Internet is needed on first build to pull images/packages. On Windows run scripts from WSL2 with Docker Desktop integration enabled. No cloud credentials required.

```bash
cd emailflow-mvp
bash scripts/start-local.sh
```

In a second terminal (keep it open):

```bash
bash scripts/ports.sh
```

Open http://localhost:8080 (dashboard), http://localhost:1080 (MailCatcher), http://localhost:16686 (Jaeger). API is proxied through `/api/`. All port-forward endpoints bind to loopback. To delete the cluster and all its local data, run `bash scripts/stop-local.sh` and confirm. To rebuild after edits, run the start script and `kubectl --context kind-emailflow -n emailflow rollout restart deployment/api deployment/gateway deployment/scheduler deployment/worker-standard deployment/worker-urgent deployment/mirror deployment/frontend`.

## 2. Use the dashboard

1. **New campaign**: enter a name, subject and body, choose number of synthetic recipients (1–10,000,000), choose standard or urgent and select a local scheduled date/time. Browser converts to UTC; database compares against its own clock. Set emails per Kafka task (default 100) and MailCatcher sample count (default 5, maximum 100). Submit.
2. **Campaigns**: inspect status and progress. Click any row to inspect paginated unique receipts; cancel only before dispatch. Multiple campaigns can share the same timestamp. The campaign is a schedule group, not a million individually timed rows.
3. **Simulation lab**: select the million-email urgent preset or either process-crash preset, adjust its schedule, and submit. Each fault is injected only once per campaign. Keep observability open.
4. **Observability**: check actual desired/ready deployment replicas, Kafka lag measured in tasks (not emails), persistent incident timeline, recent urgent worker traces, Jaeger trace links, and collector log tail. Separate standard and urgent queues autoscale independently.
5. **MailCatcher**: inspect sampled real SMTP messages (synthetic `.test` addresses). All simulated deliveries are recorded in the durable gateway inbox, while MailCatcher only receives the requested sample count to avoid overwhelming an in-memory test server. No claim is made that MailCatcher contains every simulated email.

## 3. Architecture and invariants

```text
React -> Kubernetes Service -> API (2 pods) -> PostgreSQL
                         scheduler (2 pods) -> Kafka standard (12 partitions)
                           indexed due scan -> Kafka urgent (24 partitions)
                                 KEDA -> separate worker deployments
                         workers -> gateway Service (2 pods) -> durable receipts
                                  -> completed chunks -> Kafka offset commit
                         sampled receipt -> mirror -> MailCatcher
                         all Python services -> OTel collector -> Jaeger / logs
```

- `campaigns` has one `scheduled_at` per campaign, an indexed pending range cursor, and counters. Schedulers claim due rows using `FOR UPDATE SKIP LOCKED`, create up to 32 deterministic range tasks per transaction, await broker acks, then advance the cursor. A failure between Kafka publish and DB cursor commit causes replay, not missing ranges. A scheduled row is an implicit outbox; it is not an atomic DB/Kafka transaction. `chunk_size` and quantity are immutable. The scheduler alternates urgent and standard to avoid total standard starvation; old campaigns in a priority can delay newer ones.
- One million recipients at 100 per task = 10,000 Kafka tasks, not one million scheduled timestamp lookups. Worker sends one HTTP request per range. Durable acceptance is a transaction inserting individual receipt keys `(campaign_id, seq)` using `ON CONFLICT DO NOTHING`, incrementing only newly accepted counts, and creating bounded sample jobs. Receipt IDs are deterministic. Gateway verifies ranges and due time. The gateway is the authoritative simulated destination, not a real SMTP provider.
- Workers use consumer groups and manual offset commits after acceptance and idempotent chunk completion. On failure, retry current task with exponential backoff; after 45 seconds of failed processing, exit and let Kubernetes restart/rejoin. Poison messages will block a partition in this MVP; no automated DLQ yet. Kafka may redeliver; the durable outcome stays unique. The gateway returns the full range acceptance count even on replay.
- KEDA watches lag, Kubernetes HPA adjusts worker replicas (1–24 urgent; 1–12 standard). Actual startup latency and resource limits apply. Default 30ms simulated per-task latency helps demonstrate bursts; tune `SIMULATED_CHUNK_LATENCY_MS` in worker deployments. Set to zero for throughput benchmarking. No deadline completion guarantee or pre-warming. More than 24 concurrent urgent consumers requires increasing partitions, scaling limit, DB capacity and cluster nodes. `PARTITIONS` in `backend/app/common.py`, `k8s/scaling.yaml` and Kafka existing topic partition counts must be updated consistently. One local Kafka broker and one Postgres instance are intentionally not HA.
- Gateway and API requests are distributed by Kubernetes ClusterIP Services; Kafka distributes partitions across worker pods; scheduler rows use DB locking. Existing persistent HTTP connections may reuse one pod. Scaling worker pods does not add physical cluster capacity.
- OpenTelemetry HTTP and manual spans propagate through Kafka message headers and HTTP requests. Logs and counters export via OTLP. Jaeger stores traces in memory; collector logs/metrics are debug output, not a long-term telemetry backend. Incidents are persisted in Postgres; trace IDs correlate to Jaeger. Expected transient failures can be viewed in logs. Collector and Jaeger data can be dropped/restarted without affecting correctness. Autoscaling uses actual Kafka lag, not an AI decision based on logs.

## 4. Crash recovery

| Crash window | Durable facts | Recovery |
|---|---|---|
| Gateway accepted, no ACK | Receipt + one-time fault claim + incident committed; gateway actually exits with code 42 before response | Worker retries same range; insert is idempotent, returns already accepted; matching incident resolved when completion recorded |
| Worker received ACK, then crashes | Gateway receipt durable; worker commits fault claim and incident then exits 42 before completion/offset | Kubernetes restarts/rebalances; Kafka replays; gateway deduplicates; worker writes completion and commits offset |
| Worker committed completion, before offset | Receipt and unique completion durable | Replay does not increment counters; commit offset after durable state |
| Scheduler published, before cursor commit | Kafka has range, DB cursor may be old | Scheduler republishes identical range; gateway and worker deduplicate |
| MailCatcher SMTP acceptance uncertain | Mirror job sending or unknown | No automatic SMTP resend. After 2 min abandoned jobs become unknown; inspect MailCatcher by deterministic Message-ID and reconcile manually. A missing sample is possible. |

**Guarantee:** exactly one durable simulated receipt per campaign recipient, conditional on persistent PostgreSQL data and eventual successful retry. Kafka alone cannot guarantee exactly-once external side effects. Standard SMTP may accept a message and lose the reply; retrying may create duplicates. `Message-ID` is not a universal SMTP idempotency key. Production needs an idempotent provider/relay with a durable queryable acceptance receipt, an uncertain-delivery reconciliation policy, and independent delivery/bounce tracking. Do not point this MVP at real SMTP. MailCatcher samples are best-effort and are explicitly distinct from the unique durable receipts.

## 5. Verify locally

```bash
python3 -m unittest discover -s tests -v
# Run with ports.sh active and the cluster ready:
python3 scripts/verify-local.py
# Includes a million-email load case and checks observed desired replicas > 1:
python3 scripts/verify-local.py --million
```

Integration script verifies no early acceptance (sample check), exact distinct receipt counts, correct completed chunks, resolved crash incidents and autoscaling for the million-case. Runtime is environment dependent. Observe pods and HPA independently:

```bash
kubectl --context kind-emailflow -n emailflow get pods -w
kubectl --context kind-emailflow -n emailflow get hpa,scaledobject
kubectl --context kind-emailflow -n emailflow logs -l app=worker-urgent --tail=50 --prefix
kubectl --context kind-emailflow -n emailflow logs -l app=gateway --tail=50 --prefix
kubectl --context kind-emailflow -n emailflow logs deployment/otel --tail=80
```

The load test stores a real row per simulated acceptance and generates real Kafka traffic. Throughput must be measured, not assumed. Campaign aggregate counters currently create a hot row and may limit single-campaign throughput. Next iteration: sharded counters, a separate leased dispatch/outbox table, batched gateway commits, partitioned receipt tables, DLQ, and metrics dashboards with persistent storage. Use production cluster, replicated Kafka/DB, HA, TLS, authentication/tenant isolation, RBAC, quotas, secrets and backups before exposing publicly. Local defaults use plaintext credentials and unauthenticated API intentionally. The limit of 10 million emails per campaign is a validation bound, not a certified capacity.

## 6. Source map

- `frontend/src/main.tsx`, `style.css`: all four dashboard pages, campaign dialog, receipts and simulation controls.
- `backend/app/api.py`: campaign CRUD, summary, incidents, infrastructure and traces.
- `backend/app/schema.sql`: durable schema; initialization under advisory lock.
- `backend/app/scheduler.py`: indexed scheduled groups to Kafka range tasks.
- `backend/app/worker.py`: manual commits, retries and fault injection.
- `backend/app/gateway.py`: idempotent durable acceptance and gateway crash simulation.
- `backend/app/mirror.py`: bounded SMTP projection.
- `backend/app/common.py`: DB pool, Kafka provisioning, instrumentation, incidents.
- `k8s/app.yaml`, `scaling.yaml`, `kind.yaml`, `otel-config.yaml`: local infrastructure.
- `scripts/`: deployment, loopback forwarding, cleanup, integration tests.

## References

- Kafka delivery semantics: https://kafka.apache.org/22/design/design/
- KEDA Kafka scaler: https://keda.sh/docs/2.17/scalers/apache-kafka/
- SMTP RFC 5321, section 4.5.3.2.6: https://datatracker.ietf.org/doc/html/rfc5321
- OTel Python exporters: https://opentelemetry.io/docs/languages/python/exporters/
- MailCatcher: https://github.com/sj26/mailcatcher

## Validation status at packaging

Python compilation, pure domain unit tests, YAML parsing, shell syntax and TypeScript transpilation (syntax only) are checked during packaging. Full frontend dependency install/typecheck/build, DB/Kafka integration, container image builds and real KEDA scaling are **not run** here: package registry DNS/network unavailable, Docker/Kubernetes not installed. Use the included verification script on your local machine. This is a source delivery, not a certified deployment or performance benchmark.
