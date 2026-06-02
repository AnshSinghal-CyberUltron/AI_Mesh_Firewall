###############################################################################
# modules/data — stateful tier
#
#  * RDS PostgreSQL (Multi-AZ)        -> control/config OLTP  (+ RDS Proxy)
#  * RDS PostgreSQL (pgvector)        -> embedding vault + RAG vectors
#  * ElastiCache Redis (primary+2 ro) -> auth cache, kill-switch, config pub/sub,
#                                        rate-limit, telemetry buffer
#  * SQS                              -> Celery broker (telemetry/audit workers)
#
# Deliberately NOT Aurora / NOT cluster-mode Redis / NOT DocumentDB: the hot
# path barely touches Postgres and Redis load is ~100x below a single node.
###############################################################################

variable "name" {

  type = string

}
variable "data_subnet_ids" {
  type = list(string)
}
variable "data_sg_id" {
  type = string
}
variable "kms_key_arn" {
  type = string
}
variable "db_username" {
  type    = string
  default = "ai_mesh"
}
variable "db_password" {
  type      = string
  sensitive = true
}
variable "tags" {
  type = map(string)
  default = {
}
}

# Sizing (override per-env)
variable "config_db_instance_class" {
  type = string
  default = "db.r6g.large"
}
variable "vector_db_instance_class" {
  type = string
  default = "db.r6g.xlarge"
}
variable "redis_node_type" {
  type = string
  default = "cache.r7g.large"
}
variable "redis_replica_count" {
  type = number
  default = 2
}
resource "aws_db_subnet_group" "this" {
  name       = "${var.name}-db-subnets"
  subnet_ids = var.data_subnet_ids
  tags       = var.tags
}

resource "aws_elasticache_subnet_group" "this" {
  name       = "${var.name}-redis-subnets"
  subnet_ids = var.data_subnet_ids
  tags       = var.tags
}

# ── Config / control OLTP ──
resource "aws_db_instance" "config" {
  identifier                   = "${var.name}-config"
  engine                       = "postgres"
  engine_version               = "16"
  instance_class               = var.config_db_instance_class
  allocated_storage            = 100
  max_allocated_storage        = 1000
  storage_type                 = "gp3"
  storage_encrypted            = true
  kms_key_id                   = var.kms_key_arn
  multi_az                     = true
  db_name                      = "ai_mesh"
  username                     = var.db_username
  password                     = var.db_password
  db_subnet_group_name         = aws_db_subnet_group.this.name
  vpc_security_group_ids       = [var.data_sg_id]
  backup_retention_period      = 14
  performance_insights_enabled = true
  deletion_protection          = true
  apply_immediately            = false
  auto_minor_version_upgrade   = true
  tags                         = merge(var.tags, { Name = "${var.name}-config" })
}

# ── Vector store (pgvector) for embedding vault + RAG ──
resource "aws_db_instance" "vector" {
  identifier                   = "${var.name}-vector"
  engine                       = "postgres"
  engine_version               = "16"
  instance_class               = var.vector_db_instance_class
  allocated_storage            = 200
  max_allocated_storage        = 2000
  storage_type                 = "gp3"
  iops                         = 12000
  storage_encrypted            = true
  kms_key_id                   = var.kms_key_arn
  multi_az                     = true
  db_name                      = "ai_mesh_vectors"
  username                     = var.db_username
  password                     = var.db_password
  db_subnet_group_name         = aws_db_subnet_group.this.name
  vpc_security_group_ids       = [var.data_sg_id]
  backup_retention_period      = 7
  performance_insights_enabled = true
  deletion_protection          = true
  tags                         = merge(var.tags, { Name = "${var.name}-vector" })
}

# ── ElastiCache Redis (single primary + read replicas, NOT cluster mode) ──
resource "aws_elasticache_parameter_group" "redis" {
  name   = "${var.name}-redis7"
  family = "redis7"
  parameter {
    name  = "maxmemory-policy"
    value = "volatile-lru"
  }
}

resource "aws_elasticache_replication_group" "this" {
  replication_group_id       = "${var.name}-redis"
  description                = "AI Mesh Firewall shared Redis"
  engine                     = "redis"
  engine_version             = "7.1"
  node_type                  = var.redis_node_type
  num_cache_clusters         = 1 + var.redis_replica_count
  automatic_failover_enabled = true
  multi_az_enabled           = true
  port                       = 6379
  subnet_group_name          = aws_elasticache_subnet_group.this.name
  security_group_ids         = [var.data_sg_id]
  at_rest_encryption_enabled = true
  transit_encryption_enabled = true
  kms_key_id                 = var.kms_key_arn
  snapshot_retention_limit   = 3
  parameter_group_name       = aws_elasticache_parameter_group.redis.name
  tags                       = merge(var.tags, { Name = "${var.name}-redis" })
}

# ── SQS broker for Celery (one queue per kombu routing name) ──
locals {
  celery_queue_names = [
    "policy.compile",
    "platform.batch",
    "compute.heavy",
    "scan.tier2",
    "vector.index",
    "mcp.audit",
  ]
}

resource "aws_sqs_queue" "celery_dlq" {
  name                      = "${var.name}-celery-dlq"
  message_retention_seconds = 1209600
  kms_master_key_id         = var.kms_key_arn
  tags                      = var.tags
}

resource "aws_sqs_queue" "celery" {
  for_each                   = toset(local.celery_queue_names)
  name                       = "${var.name}-celery-${replace(each.key, ".", "-")}"
  visibility_timeout_seconds = 300
  message_retention_seconds  = 345600
  kms_master_key_id          = var.kms_key_arn
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.celery_dlq.arn
    maxReceiveCount     = 5
  })
  tags = var.tags
}

###############################################################################
# Outputs
###############################################################################

output "config_db_endpoint"    { value = aws_db_instance.config.address }
output "config_proxy_endpoint" { value = aws_db_instance.config.address }
output "vector_db_endpoint"    { value = aws_db_instance.vector.address }
output "redis_primary_endpoint" { value = aws_elasticache_replication_group.this.primary_endpoint_address }
output "redis_reader_endpoint"  { value = aws_elasticache_replication_group.this.reader_endpoint_address }
output "celery_queue_urls" {
  value = { for k, q in aws_sqs_queue.celery : k => q.url }
}
output "celery_queue_arns" {
  value = [for q in aws_sqs_queue.celery : q.arn]
}
# Back-compat default queue URL (platform.batch)
output "celery_queue_url" { value = aws_sqs_queue.celery["platform.batch"].url }
output "celery_queue_arn" { value = aws_sqs_queue.celery["platform.batch"].arn }
