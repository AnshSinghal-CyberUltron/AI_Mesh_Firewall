output "nlb_dns_name" {
  description = "Data-plane (gateway/inference) endpoint — point your inference DNS here"
  value       = module.loadbalancers.nlb_dns_name
}

output "alb_dns_name" {
  description = "Control-plane (Django admin/API) endpoint"
  value       = module.loadbalancers.alb_dns_name
}

output "config_db_endpoint"    { value = module.data.config_db_endpoint }
output "config_proxy_endpoint" { value = module.data.config_proxy_endpoint }
output "vector_db_endpoint"    { value = module.data.vector_db_endpoint }
output "redis_primary_endpoint" { value = module.data.redis_primary_endpoint }
output "celery_queue_url"       { value = module.data.celery_queue_url }
output "ecs_cluster_name"       { value = module.ecs_cluster.cluster_name }
output "mcp_efs_id"             { value = module.mcp_pool.efs_id }
output "kms_key_arn"            { value = aws_kms_key.main.arn }
