# ─────────────────────────────────────────────────────────────────────────────
# Example production values. Copy, fill in the ARNs/tags, then:
#   terraform init -backend-config=backend.hcl
#   terraform plan  -var-file=prod.tfvars
#   terraform apply -var-file=prod.tfvars
# ─────────────────────────────────────────────────────────────────────────────

region = "ap-south-1"
env    = "prod"
name   = "ai-mesh-prod"

# ECR image URIs (push images first; pin to immutable digests/tags, not :latest)
gateway_image = "<ACCOUNT>.dkr.ecr.ap-south-1.amazonaws.com/ai-mesh-gateway:v1.0.0"
control_image = "<ACCOUNT>.dkr.ecr.ap-south-1.amazonaws.com/ai-mesh-control:v1.0.0"
worker_image  = "<ACCOUNT>.dkr.ecr.ap-south-1.amazonaws.com/ai-mesh-control:v1.0.0"

# ACM certificate in ap-south-1 covering gateway + control hostnames
certificate_arn = "arn:aws:acm:ap-south-1:<ACCOUNT>:certificate/<CERT_ID>"

# Secrets Manager
db_secret_arn = "arn:aws:secretsmanager:ap-south-1:<ACCOUNT>:secret:ai-mesh/db-XXXXXX"
app_secret_arns = {
  DJANGO_SECRET_KEY = "arn:aws:secretsmanager:ap-south-1:<ACCOUNT>:secret:ai-mesh/django-XXXXXX"
  # GATEWAY_JWT_SECRET = "arn:aws:secretsmanager:ap-south-1:<ACCOUNT>:secret:ai-mesh/jwt-XXXXXX"
}

# Gateway sizing — start here, scale on the CPU target-tracking policy.
gateway_cpu     = 4096 # 4 vCPU
gateway_memory  = 8192 # 8 GiB
gateway_min     = 2
gateway_max     = 8
web_concurrency = "4" # gunicorn workers; keep == gateway vCPU

# Cost: set true in non-peak/staging to use one NAT GW instead of one per AZ.
single_nat = false
