###############################################################################
# envs/prod — production composition for AI Mesh Firewall
###############################################################################

data "aws_caller_identity" "current" {}

locals {
  tags = {
    Project = "ai-mesh-firewall"
    Env     = var.env
  }
}

# ── KMS key for data-at-rest (RDS, Redis, SQS, EFS, logs) ──
resource "aws_kms_key" "main" {
  description             = "${var.name} data encryption"
  deletion_window_in_days = 14
  enable_key_rotation     = true
  tags                    = local.tags
}

resource "aws_kms_alias" "main" {
  name          = "alias/${var.name}"
  target_key_id = aws_kms_key.main.key_id
}

# ── Centralized CloudWatch log group ──
resource "aws_cloudwatch_log_group" "app" {
  name              = "/ecs/${var.name}"
  retention_in_days = 30
  kms_key_id        = aws_kms_key.main.arn
  tags              = local.tags
}

###############################################################################
# IAM — task execution role (pull image, read secrets, write logs) + task roles
###############################################################################

resource "aws_iam_role" "execution" {
  name = "${var.name}-exec"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
  tags = local.tags
}

resource "aws_iam_role_policy_attachment" "execution_base" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "execution_secrets" {
  role = aws_iam_role.execution.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = concat([var.db_secret_arn], values(var.app_secret_arns))
      }, {
      Effect   = "Allow"
      Action   = ["kms:Decrypt"]
      Resource = [aws_kms_key.main.arn]
    }]
  })
}

# Task role — gateway needs Bedrock + SQS + Firehose + EFS (MCP).
resource "aws_iam_role" "task" {
  name = "${var.name}-task"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
  tags = local.tags
}

resource "aws_iam_role_policy" "task" {
  role = aws_iam_role.task.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
        Resource = ["*"]
      },
      {
        Effect = "Allow"
        Action = [
          "sqs:SendMessage", "sqs:ReceiveMessage", "sqs:DeleteMessage",
          "sqs:GetQueueAttributes", "sqs:GetQueueUrl",
        ]
        Resource = [module.data.celery_queue_arn]
      },
      {
        Effect   = "Allow"
        Action   = ["firehose:PutRecord", "firehose:PutRecordBatch"]
        Resource = ["*"]
      },
      {
        Effect   = "Allow"
        Action   = ["elasticfilesystem:ClientMount", "elasticfilesystem:ClientWrite"]
        Resource = ["*"]
      },
    ]
  })
}

###############################################################################
# Modules
###############################################################################

module "network" {
  source            = "../../modules/network"
  name              = var.name
  az_count          = 3
  single_nat        = var.single_nat
  tags              = local.tags
}

module "loadbalancers" {
  source            = "../../modules/loadbalancers"
  name              = var.name
  vpc_id            = module.network.vpc_id
  public_subnet_ids = module.network.public_subnet_ids
  nlb_sg_id         = module.network.nlb_sg_id
  alb_sg_id         = module.network.alb_sg_id
  certificate_arn   = var.certificate_arn
  tags              = local.tags
}

module "data" {
  source          = "../../modules/data"
  name            = var.name
  data_subnet_ids = module.network.data_subnet_ids
  data_sg_id      = module.network.data_sg_id
  kms_key_arn     = aws_kms_key.main.arn
  db_secret_arn   = var.db_secret_arn
  tags            = local.tags
}

module "ecs_cluster" {
  source             = "../../modules/ecs_cluster"
  name               = var.name
  private_subnet_ids = module.network.private_subnet_ids
  app_sg_id          = module.network.app_sg_id
  tags               = local.tags
}

# Shared env for the gateway data plane.
locals {
  gateway_env = {
    AI_MESH_CONTROL_URL          = "http://${module.loadbalancers.alb_dns_name}"
    GATEWAY_REDIS_URL            = "rediss://${module.data.redis_primary_endpoint}:6379/0"
    REDIS_URL                    = "rediss://${module.data.redis_primary_endpoint}:6379/0"
    DATABASE_URL                 = "postgresql://${module.data.vector_db_endpoint}:5432/ai_mesh_vectors"
    GATEWAY_AUTH_ENABLED         = "true"
    GATEWAY_ORG_ONLY_INFERENCE   = "true"
    GATEWAY_RAG_ENABLED          = "true"
    ENABLE_TIER2                 = "true"
    BEDROCK_REGION               = var.region
    # ── Tunables from the code fixes (§7) ──
    WEB_CONCURRENCY                  = var.web_concurrency
    GATEWAY_SCANNER_THREAD_POOL_SIZE = "8"
    GATEWAY_BEDROCK_THREAD_POOL_SIZE = "16"
    GATEWAY_VAULT_POOL_MAX           = "8"
    GATEWAY_LAST_USED_DEBOUNCE_SECONDS = "60"
    GATEWAY_TIER2_CACHE_TTL_SECONDS  = "300"
    GATEWAY_TIER2_SAMPLE_RATE        = "1.0" # raise cost savings by lowering (e.g. 0.25)
  }
  app_secrets = merge(var.app_secret_arns, {})
}

module "gateway" {
  source            = "../../modules/ecs_service"
  name              = "${var.name}-gateway"
  cluster_arn       = module.ecs_cluster.cluster_arn
  subnet_ids        = module.network.private_subnet_ids
  security_group_id = module.network.app_sg_id
  image             = var.gateway_image
  cpu               = var.gateway_cpu
  memory            = var.gateway_memory
  container_port    = 8300
  desired_count     = var.gateway_min
  min_count         = var.gateway_min
  max_count         = var.gateway_max
  target_group_arn  = module.loadbalancers.gateway_target_group_arn
  environment       = local.gateway_env
  secret_arns       = local.app_secrets
  execution_role_arn = aws_iam_role.execution.arn
  task_role_arn      = aws_iam_role.task.arn
  region             = var.region
  log_group          = aws_cloudwatch_log_group.app.name
  autoscale_cpu_target = 55
  capacity_strategy = [
    { capacity_provider = "FARGATE", base = 2, weight = 1 },
    { capacity_provider = "FARGATE_SPOT", base = 0, weight = 3 },
  ]
  tags = local.tags
}

module "control" {
  source            = "../../modules/ecs_service"
  name              = "${var.name}-control"
  cluster_arn       = module.ecs_cluster.cluster_arn
  subnet_ids        = module.network.private_subnet_ids
  security_group_id = module.network.app_sg_id
  image             = var.control_image
  cpu               = 2048
  memory            = 4096
  container_port    = 8000
  desired_count     = 2
  min_count         = 2
  max_count         = 4
  target_group_arn  = module.loadbalancers.control_target_group_arn
  health_path       = "/health/"
  environment = {
    DATABASE_URL = "postgresql://${module.data.config_proxy_endpoint}:5432/ai_mesh"
    REDIS_URL    = "rediss://${module.data.redis_primary_endpoint}:6379/0"
    CELERY_BROKER_URL = "sqs://"
  }
  secret_arns        = local.app_secrets
  execution_role_arn = aws_iam_role.execution.arn
  task_role_arn      = aws_iam_role.task.arn
  region             = var.region
  log_group          = aws_cloudwatch_log_group.app.name
  capacity_strategy  = [{ capacity_provider = "FARGATE", base = 2, weight = 1 }]
  tags               = local.tags
}

module "workers" {
  source            = "../../modules/ecs_service"
  name              = "${var.name}-workers"
  cluster_arn       = module.ecs_cluster.cluster_arn
  subnet_ids        = module.network.private_subnet_ids
  security_group_id = module.network.app_sg_id
  image             = var.worker_image
  cpu               = 1024
  memory            = 2048
  desired_count     = 2
  min_count         = 1
  max_count         = 4
  target_group_arn  = "" # no LB
  command           = ["celery", "-A", "ai_mesh_control", "worker", "-l", "info"]
  environment = {
    DATABASE_URL      = "postgresql://${module.data.config_proxy_endpoint}:5432/ai_mesh"
    REDIS_URL         = "rediss://${module.data.redis_primary_endpoint}:6379/0"
    CELERY_BROKER_URL = "sqs://"
    SQS_QUEUE_URL     = module.data.celery_queue_url
  }
  secret_arns        = local.app_secrets
  execution_role_arn = aws_iam_role.execution.arn
  task_role_arn      = aws_iam_role.task.arn
  region             = var.region
  log_group          = aws_cloudwatch_log_group.app.name
  capacity_strategy  = [{ capacity_provider = "FARGATE_SPOT", base = 0, weight = 1 }]
  tags               = local.tags
}

module "mcp_pool" {
  source             = "../../modules/ecs_mcp_pool"
  name               = var.name
  cluster_arn        = module.ecs_cluster.cluster_arn
  subnet_ids         = module.network.private_subnet_ids
  data_subnet_ids    = module.network.data_subnet_ids
  security_group_id  = module.network.app_sg_id
  image              = var.gateway_image # MCP runs the gateway image (npx/uvx baked in)
  capacity_provider  = module.ecs_cluster.mcp_capacity_provider
  execution_role_arn = aws_iam_role.execution.arn
  task_role_arn      = aws_iam_role.task.arn
  region             = var.region
  log_group          = aws_cloudwatch_log_group.app.name
  environment        = local.gateway_env
  secret_arns        = local.app_secrets
  tags               = local.tags
}
