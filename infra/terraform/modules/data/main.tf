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

variable "name"             { type = string }
variable "data_subnet_ids" { type = list(string) }
variable "data_sg_id"      { type = string }
variable "kms_key_arn"     { type = string }
variable "db_secret_arn"   { type = string } # Secrets Manager: {username,password}
variable "tags"            { type = map(string), default = {} }

# Sizing (override per-env)
variable "config_db_instance_class" { type = string, default = "db.r6g.large" }
variable "vector_db_instance_class" { type = string, default = "db.r6g.xlarge" }
variable "redis_node_type"          { type = string, default = "cache.r7g.large" }
variable "redis_replica_count"      { type = number, default = 2 }

data "aws_secretsmanager_secret_version" "db" {
  secret_id = var.db_secret_arn
}

locals {
  db = jsondecode(data.aws_secretsmanager_secret_version.db.secret_string)
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
  username                     = local.db.username
  password                     = local.db.password
  db_subnet_group_name         = aws_db_subnet_group.this.name
  vpc_security_group_ids       = [var.data_sg_id]
  backup_retention_period      = 14
  performance_insights_enabled = true
  deletion_protection          = true
  apply_immediately            = false
  auto_minor_version_upgrade   = true
  tags                         = merge(var.tags, { Name = "${var.name}-config" })
}

# RDS Proxy = managed PgBouncer; absorbs connection storms from many gunicorn
# workers across many tasks so Postgres never runs out of backends.
resource "aws_db_proxy" "config" {
  name                   = "${var.name}-config-proxy"
  engine_family          = "POSTGRESQL"
  role_arn               = aws_iam_role.proxy.arn
  vpc_subnet_ids         = var.data_subnet_ids
  vpc_security_group_ids = [var.data_sg_id]
  require_tls            = true
  auth {
    auth_scheme = "SECRETS"
    iam_auth    = "DISABLED"
    secret_arn  = var.db_secret_arn
  }
  tags = var.tags
}

resource "aws_db_proxy_default_target_group" "config" {
  db_proxy_name = aws_db_proxy.config.name
  connection_pool_config {
    max_connections_percent      = 90
    max_idle_connections_percent = 50
    connection_borrow_timeout    = 120
  }
}

resource "aws_db_proxy_target" "config" {
  db_proxy_name          = aws_db_proxy.config.name
  target_group_name      = aws_db_proxy_default_target_group.config.name
  db_instance_identifier = aws_db_instance.config.identifier
}

resource "aws_iam_role" "proxy" {
  name = "${var.name}-rds-proxy"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "rds.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
  tags = var.tags
}

resource "aws_iam_role_policy" "proxy_secrets" {
  role = aws_iam_role.proxy.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = [var.db_secret_arn]
      }, {
      Effect   = "Allow"
      Action   = ["kms:Decrypt"]
      Resource = [var.kms_key_arn]
    }]
  })
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
  username                     = local.db.username
  password                     = local.db.password
  db_subnet_group_name         = aws_db_subnet_group.this.name
  vpc_security_group_ids       = [var.data_sg_id]
  backup_retention_period      = 7
  performance_insights_enabled = true
  deletion_protection          = true
  tags                         = merge(var.tags, { Name = "${var.name}-vector" })
}

# ── ElastiCache Redis (single primary + read replicas, NOT cluster mode) ──
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
  maxmemory_policy           = "volatile-lru"
  tags                       = merge(var.tags, { Name = "${var.name}-redis" })
}

# ── SQS broker for Celery (telemetry/audit batch workers) ──
resource "aws_sqs_queue" "celery_dlq" {
  name                      = "${var.name}-celery-dlq"
  message_retention_seconds = 1209600
  kms_master_key_id         = var.kms_key_arn
  tags                      = var.tags
}

resource "aws_sqs_queue" "celery" {
  name                       = "${var.name}-celery"
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

output "config_db_endpoint"   { value = aws_db_instance.config.address }
output "config_proxy_endpoint" { value = aws_db_proxy.config.endpoint }
output "vector_db_endpoint"    { value = aws_db_instance.vector.address }
output "redis_primary_endpoint" { value = aws_elasticache_replication_group.this.primary_endpoint_address }
output "redis_reader_endpoint"  { value = aws_elasticache_replication_group.this.reader_endpoint_address }
output "celery_queue_url"       { value = aws_sqs_queue.celery.url }
output "celery_queue_arn"       { value = aws_sqs_queue.celery.arn }
