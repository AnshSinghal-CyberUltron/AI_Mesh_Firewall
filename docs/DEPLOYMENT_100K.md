# Deployment guide — 100k concurrent users

Baseline after cost/SRE/security triage. Tune with load tests (10k → 25k → 50k → 100k).

## Assumptions

- 100k **connected** sessions; ~2–3.5k req/s peak ingress
- ~8% active streaming (~8k streams)
- 3 AZ, N+1 headroom
- Graviton-first where ARM-compatible

## Recommended AWS layout

| Tier | Component | Instance | Count (start) | Autoscale max |
|------|-----------|----------|---------------|---------------|
| Edge | ALB + WAF | managed | 1 | — |
| Data | Gateway | `c7g.4xlarge` | 18 (6/AZ) | 48 |
| Data | Guardrails | `c7i.4xlarge` | 12 (4/AZ) | 24 |
| Data | MCP broker | `c7g.2xlarge` | 6 (2/AZ) | 12 |
| Data | Vector retrieval | `r7gd.4xlarge` | 6 | 15 |
| Control | Control API | `c7g.2xlarge` | 6 (2/AZ) | 12 |
| Async | Workers (mixed) | `c7g.2xlarge` / `c7i.4xlarge` | 20 | 40 |
| Cache | Redis | ElastiCache `r7g.xlarge` × 6 | 3 shard + replica | — |
| OLTP | Postgres | Aurora `r7g.4xlarge` + 2 readers | 1+2 | readers |
| Queue | RabbitMQ | Amazon MQ Multi-AZ | 3 | — |
| Telemetry | Mongo (optional) | `r7g.large` × 3 | pilot only | — |

## When to add workers (not more monoliths)

Scale **Celery worker replicas** when:

- `platform.batch` oldest message age > 10s for 10 min
- `scan.tier2` depth > 100 for 2 min
- Worker CPU > 70% with queue depth rising

Scale **gateway replicas** when:

- p95 added latency > 25 ms
- in-flight per pod > 5k connections
- 5xx > 0.3% over 5 min

## Local / dev sizing

Use root `docker-compose.yml` (single worker, single gateway) — sufficient for development only.
