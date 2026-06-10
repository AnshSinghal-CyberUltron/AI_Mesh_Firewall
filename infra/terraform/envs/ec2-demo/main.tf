module "observability" {
  source = "../../modules/observability"

  name                        = var.name
  region                      = var.region
  alarm_email                 = var.alarm_email
  instance_id                 = var.instance_id
  existing_instance_role_name = var.existing_instance_role_name
  log_retention_days          = var.log_retention_days
  grace_seconds               = var.grace_seconds

  tags = {
    Project     = "ai-mesh-firewall"
    Environment = "ec2-demo"
    ManagedBy   = "terraform"
  }
}

