# services (extractable microservices)

Phase 2+ extraction from `gateway/` when scale/SLO triggers fire. Each service:

- Own Dockerfile and port
- Calls via HTTP/gRPC from gateway (bulkhead + circuit breaker)
- Same contracts in `docs/contracts/`

| Service | Port (dev) | Module |
|---------|------------|--------|
| guardrails | 8310 | 1.2, 1.7 input/output scan |
| mcp-broker | 8311 | 1.4 MCP guardrails |
| vector-retrieval | 8312 | 1.3 vector firewall |
| telemetry-ingest | 8313 | async event sink (Mongo/Kinesis pilot) |

Day-1: logic may still live in `gateway/`; these folders hold scaffold + migration target.
