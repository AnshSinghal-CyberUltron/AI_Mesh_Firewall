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
output "audit_firehose_stream"  { value = module.audit.firehose_stream_name }
output "audit_s3_bucket"        { value = module.audit.audit_bucket_name }
output "ui_s3_bucket"           { value = module.frontend.ui_bucket_name }
output "cloudfront_domain_name" { value = module.frontend.cloudfront_domain_name }
output "cloudfront_distribution_id" { value = module.frontend.cloudfront_distribution_id }
