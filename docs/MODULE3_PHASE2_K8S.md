# Module 3 — Phase 2 K8s (Spreadsheet 3.2)

Local Kind + Helm implement:

1. Network policies (Cilium eBPF **or** Kubernetes NetworkPolicy)
2. Envoy mTLS sidecars
3. River embedding inspection worker
4. Vector DB localhost gauntlet
5. Telemetry → Module 3 ingest → Module 2 SOC

## Bootstrap

```bash
# Match compose AGENT_API_KEY / OrganizationAgentKey
export AGENT_API_KEY=...
export ORG_SLUG=zeroshield
export CONTROL_URL=http://host.docker.internal:8100
bash scripts/module3_kind_up.sh
```

Requires: Docker Desktop, kind, kubectl, helm, openssl (or Docker `alpine/openssl`). Compose `control` + `workers` must be up for SOC incidents.

### Networking notes (Windows / Docker Desktop)

| Mode | How | Policy CRD |
|------|-----|------------|
| **Default (recommended locally)** | Kind default CNI | `networking.k8s.io/NetworkPolicy` (`networkPolicy.cilium=false`) |
| **Cilium** | `KIND_USE_CILIUM=1` on Linux/VM | `CiliumNetworkPolicy` |

Cilium with `disableDefaultCNI` often sticks in Init on Docker Desktop WSL2; use the default path locally.

Control must allow Kind’s Host header. Compose sets:

`ALLOWED_HOSTS=localhost,127.0.0.1,control,host.docker.internal`

## Verify

```bash
kubectl -n ai-mesh-m3 get pods
# vector-db Service must expose 8443 only (not 8000)
kubectl -n ai-mesh-m3 get svc vector-db
# Windows-friendly:
python scripts/module3_phase2_e2e.py
# or:
bash scripts/module3_phase2_e2e.sh
```

Assertions: Kind workloads Ready, NetworkPolicy present, topology non-empty, network drops, River quarantine, Module 2 incidents.

## UI

M3.2 `/infrastructure/k8s-firewall` shows topology, drops, and embedding queue from live ingest (no `seed_module3`). Empty state points at `scripts/module3_kind_up.sh`.

## Tear down

```bash
bash scripts/module3_kind_down.sh
```

## Chart values

See [deploy/module3-k8s/values.yaml](../deploy/module3-k8s/values.yaml) and [deploy/module3-k8s/README.md](../deploy/module3-k8s/README.md).
