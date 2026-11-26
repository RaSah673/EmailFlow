# EmailFlow development and review standards

These standards apply to everyone changing or reviewing EmailFlow, including AI contributors. Use the [README](../README.md) for architecture and local setup.

## Code and interfaces

- Keep changes focused. Use readable Python and TypeScript with explicit imports, descriptive names, and functions or components that each handle a clear responsibility.
- Validate input at the API boundary and use parameterized SQL. Keep API responses, database schemas, and Kafka task payloads compatible with existing clients, stored data, and queued tasks. Document contract changes and include any migration they require.
- Keep list APIs paginated and return only the fields each view needs. Bound network timeouts and polling rates. Show failures and stale data explicitly. Verify dashboard workflows with a keyboard.
- Require an explicit choice to inject a process fault. Keep recipients synthetic and SMTP confined to MailCatcher. Keep credentials out of Git and campaign content out of routine logs and traces.

## Scheduling and recovery

- Store a timezone-aware schedule per campaign and use the database clock to decide when it is due. Dispatch deterministic recipient ranges and preserve separate standard and urgent Kafka queues.
- Make the decision to start dispatch durable before tasks can reach workers. A successful cancellation must prevent delivery, including when the scheduler loses its database transaction after Kafka accepts a task.
- Preserve one durable receipt per campaign recipient and one completion per range under concurrent retries. Update counters in the same transaction as the corresponding insert and commit the Kafka offset only after durable completion.
- Distinguish transient failures, permanent task failures, and uncertain SMTP outcomes. Keep permanent failures from stalling unrelated campaigns and leave uncertain SMTP sends available for reconciliation without automatic resend.
- Keep Kafka topics, consumer groups, partitions, and KEDA settings aligned. Confirm the target cluster and namespace explicitly in deployment commands. Record observed replica counts and elapsed time when making scale or throughput claims.

## Verification before merge

Install the repository hooks once from an activated Python environment.

```bash
python -m pip install pre-commit==4.5.1
python -m pre_commit install
python -m pre_commit run --all-files
```

The [CI workflow](../.github/workflows/ci.yml) runs these hooks, checks Bash syntax, runs backend tests against disposable PostgreSQL 16 with pgvector, and builds the frontend from the lockfile. For a local PostgreSQL test, set `EMAILFLOW_TEST_DATABASE_URL` to a disposable database named `emailflow_ci` on PostgreSQL 16 with pgvector available. Run the unit test command below. The PostgreSQL test rolls back its fixture data and schema changes.

| Change | Evidence to collect |
| --- | --- |
| Python logic | Run `python -m unittest discover -s tests -v`. Add focused regression coverage for changed behavior. |
| Dashboard | Run `npm ci` and `npm run build` from `frontend/`. Exercise the affected view against the API, including loading, empty and error states where relevant. |
| Scheduling, database, Kafka, or recovery | Run `python scripts/verify-local.py` against the local Kind stack. Verify the affected failure window and assert durable receipt counts, completion counts, and incident resolution. |
| Deployment, dependencies, or scaling | Rebuild the affected images, verify startup or rollout in Kind, and keep dependency resolution reproducible. Use `python scripts/verify-local.py --million` when validating million-email scale-out claims. |
| Documentation only | Check that links resolve, Markdown renders, and commands match the repository. |

## Reviewing commits

1. Review the diff against its base and follow changed behavior through the dashboard, API, database, Kafka, and Kubernetes where applicable.
2. Prioritize findings by impact. **P1** covers data loss, duplicate or unintended delivery, or a major workflow outage. **P2** covers a user-visible regression, recoverable operational failure, or material missing verification. **P3** covers maintainability and reproducibility gaps with limited immediate impact.
3. Give each finding a file and line, the triggering condition, the user or operator impact, and a concrete direction for the fix. Lead with the highest-impact findings.
4. Record the checks run, their results, and any verification still needed. Resolve P1 findings before merge and record the disposition of P2 findings.
