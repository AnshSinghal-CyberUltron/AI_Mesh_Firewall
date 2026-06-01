variable "region" {
  type    = string
  default = "ap-south-1" # MUST match Bedrock region
}

variable "env" {
  type    = string
  default = "prod"
}

variable "name" {
  type    = string
  default = "ai-mesh-prod"
}

# ── Image references (push to ECR first, then set the tags) ──
variable "gateway_image" { type = string }
variable "control_image" { type = string }
variable "worker_image"  { type = string }

# ── TLS / DNS ──
variable "certificate_arn" {
  type        = string
  description = "ACM cert ARN (ap-south-1) covering the gateway + control hostnames"
}

# ── Secrets (Secrets Manager ARNs) ──
variable "db_secret_arn" {
  type        = string
  description = "Secrets Manager ARN with {\"username\":..,\"password\":..}"
}

variable "app_secret_arns" {
  type        = map(string)
  description = "Container-secret name -> Secrets Manager ARN (e.g. DJANGO_SECRET_KEY)"
  default     = {}
}

# ── Sizing knobs ──
variable "gateway_cpu"       { type = number, default = 4096 }
variable "gateway_memory"    { type = number, default = 8192 }
variable "gateway_min"       { type = number, default = 2 }
variable "gateway_max"       { type = number, default = 8 }
variable "web_concurrency"   { type = string, default = "4" } # gunicorn workers == vCPU

variable "single_nat" { type = bool, default = false }
