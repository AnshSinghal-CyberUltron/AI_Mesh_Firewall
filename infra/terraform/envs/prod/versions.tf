terraform {
  required_version = ">= 1.6.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.60"
    }
  }
  # Remote state — values supplied via `terraform init -backend-config=backend.hcl`
  backend "s3" {}
}

provider "aws" {
  region = var.region
  default_tags {
    tags = {
      Project   = "ai-mesh-firewall"
      ManagedBy = "terraform"
      Env       = var.env
    }
  }
}
