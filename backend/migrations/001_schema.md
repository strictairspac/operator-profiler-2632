# Schema v1
SQLAlchemy metadata creates users, audits, datasets, plans, traces, renumber_requests. Natural names are unique; foreign keys reference parent entities. Each trace stores immutable JSON input snapshots. PostgreSQL persists metadata and teaching datasets; the bounded Python operators execute the plan.

## Renumber requests (v1.1)
`renumber_requests` stores a previewed bulk sensor renumber: dataset_id, status(pending/conflicted/completed), mapping, preview, baseline fingerprint, result/error, actor and timestamps. Confirmation locks the dataset and its plans, compares the baseline to reject stale previews, and rewrites dataset rows plus plan sensor_id conditions in one transaction.
