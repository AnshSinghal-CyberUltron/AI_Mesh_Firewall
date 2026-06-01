# AI Mesh Firewall — AWS Terraform

Production IaC for the **AI Mesh Firewall** product (control plane + gateway
inference firewall + Celery workers + MCP stdio pool). Region: **ap-south-1**
(co-located with Amazon Bedrock to remove cross-region latency/egress).

Implements the post-triage architecture in
[`../../docs/AWS_DEPLOYMENT_ARCHITECTURE.md`](../../docs/AWS_DEPLOYMENT_ARCHITECTURE.md).

## Layout

```
modules/
  network/         VPC, 3-AZ subnets, NAT, VPC endpoints, security groups
  loadbalancers/   NLB (data plane, no SSE buffering) + ALB+WAF (control plane)
  data/            RDS Postgres Multi-AZ (config) + RDS pgvector + ElastiCache + SQS
  ecs_cluster/     ECS cluster, Fargate + Fargate-Spot + EC2 (c7i) capacity provider
  ecs_service/     Reusable Fargate service (gateway / control / workers)
  ecs_mcp_pool/    EC2-backed ECS service with EFS-warmed uv/npm cache + token vol
envs/
  prod/            Composition: wires every module together for production
```

## Key decisions (validated by 5 contradicting triage agents)

- **x86, not Graviton:** Presidio images amd64-only; MCP `npx`/`uvx` fetch
  arch-specific native binaries at runtime.
- **ECS, not EKS:** matches the existing docker-compose topology; no k8s in repo.
- **NLB for inference; ALB+WAF only for control:** the gateway *is* the WAF.
  WAF on inference adds latency + cost for no benefit; ALB buffers SSE.
- **RDS not Aurora, single-primary Redis not cluster, no DocumentDB:** hot path
  barely touches Postgres; Redis load ~100× below one node.
- **Right-sized:** 10k concurrent ≈ 250–750 RPS ≈ 8–16 vCPU steady. 10k governs
  socket capacity (async awaits), not CPU.

## Prerequisite code fixes (already implemented in the gateway)

The 1000-user collapse was a **code** problem. Fixed before scaling infra:
gunicorn multi-worker, dedicated Bedrock thread pool, vault connection pool,
`last_used_at` debounce, Tier-2 response cache + opt-in sampling. See
architecture doc §7.

## Usage

```bash
# 1. Build + push images to ECR (gateway, control, worker)
#    then set the *_image variables in envs/prod/prod.tfvars

cd envs/prod
terraform init -backend-config=backend.hcl
terraform plan  -var-file=prod.tfvars
terraform apply -var-file=prod.tfvars
```

`backend.hcl` and `prod.tfvars` contain account-specific values — fill them in
(examples provided). Secrets (DB password, Bedrock creds) come from AWS Secrets
Manager, never from tfvars.
