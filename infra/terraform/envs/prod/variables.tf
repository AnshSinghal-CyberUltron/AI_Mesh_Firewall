variable "region" {
  type = string
  default = "ap-south-1" # MUST match Bedrock region
}
variable "env" {

  type = string
  default = "prod"

}
variable "name" {

  type = string
  default = "ai-mesh-prod"

} # ── Image references (push to ECR first, then set the tags) ──
variable "gateway_image" {
  type = string
}
variable "control_image" {
  type = string
}
variable "worker_image" {
  type = string
} # ── TLS / DNS ──
variable "certificate_arn" {
  type = string
  description = "ACM cert ARN (ap-south-1) covering the gateway + control hostnames"
}

# ── App secrets (plain env vars on ECS tasks — no Secrets Manager) ──
variable "db_username" {
  type    = string
  default = "ai_mesh"
}
variable "db_password" {
  type      = string
  sensitive = true
}
variable "django_secret_key" {
  type      = string
  sensitive = true
}
variable "policy_signing_key" {
  type      = string
  sensitive = true
}
variable "field_encryption_key" {
  type      = string
  sensitive = true
}
variable "gateway_internal_api_key" {
  type      = string
  sensitive = true
  default   = ""
}

# ── Sizing knobs (2 vCPU / 4 GiB per gateway task on c7i.4xlarge ASG) ──
variable "gateway_cpu" {
  type = number
  default = 2048
}
variable "gateway_memory" {
  type = number
  default = 4096
}
variable "gateway_min" {
  type = number
  default = 8
}
variable "gateway_max" {
  type = number
  default = 200
}
variable "web_concurrency" {
  type = string
  default = "4"
}
variable "gateway_asg_min" {
  type = number
  default = 3
}
variable "gateway_asg_max" {
  type = number
  default = 50
}
variable "gateway_spot_weight" {
  type = number
  default = 70
}
variable "single_nat" {

  type = bool
  default = false

}