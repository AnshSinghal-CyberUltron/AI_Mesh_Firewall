# infra

- `docker/` — production compose overlays, nginx, health probes
- `terraform/` — AWS modules (VPC, EKS/EC2 ASG, Aurora, ElastiCache, Amazon MQ)
- `scripts/` — deploy, rotate keys, load-test kickoff

Target: 3-AZ hybrid deployment per `docs/DEPLOYMENT_100K.md`.
