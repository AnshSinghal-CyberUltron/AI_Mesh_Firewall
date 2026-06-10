output "sns_topic_arn" {
  value       = module.observability.sns_topic_arn
  description = "Subscribe and confirm email for infra alarms"
}

output "log_group_names" {
  value       = module.observability.log_group_names
  description = "Map service -> CloudWatch log group (use in EC2 .env)"
}

output "ssm_agent_config" {
  value       = module.observability.ssm_agent_config
  description = "SSM parameter for CloudWatch Agent fetch-config"
}
