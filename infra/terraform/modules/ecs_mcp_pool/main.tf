###############################################################################
# modules/ecs_mcp_pool — EC2-backed ECS service for MCP stdio servers
#
# MCP tool servers are launched per-org via `npx`/`uvx` as real subprocesses.
# They CANNOT run on Fargate cleanly because they:
#   * spawn child processes and fetch arch-specific native binaries at runtime,
#   * need a warm, shared uv/npm cache (cold fetch = multi-second init),
#   * persist per-org OAuth tokens across reconnects.
#
# So they run on the EC2 (c7i) capacity provider with:
#   * an EFS volume for the warm uv/npm caches (UV_CACHE_DIR / NPM_CONFIG_CACHE),
#   * an EFS volume for per-org OAuth tokens (MCP_REMOTE_CONFIG_DIR).
###############################################################################

variable "name"               { type = string }
variable "cluster_arn"        { type = string }
variable "subnet_ids"         { type = list(string) }
variable "security_group_id"  { type = string }
variable "data_subnet_ids"    { type = list(string) }
variable "image"              { type = string }
variable "capacity_provider"  { type = string }
variable "execution_role_arn" { type = string }
variable "task_role_arn"      { type = string }
variable "region"             { type = string }
variable "log_group"          { type = string }
variable "environment"        { type = map(string), default = {} }
variable "secret_arns"        { type = map(string), default = {} }
variable "cpu"                { type = number, default = 2048 }
variable "memory"             { type = number, default = 6144 }
variable "desired_count"      { type = number, default = 2 }
variable "tags"               { type = map(string), default = {} }

# ── EFS for warm caches + OAuth tokens ──
resource "aws_efs_file_system" "mcp" {
  creation_token = "${var.name}-mcp-efs"
  encrypted      = true
  lifecycle_policy {
    transition_to_ia = "AFTER_30_DAYS"
  }
  tags = merge(var.tags, { Name = "${var.name}-mcp-efs" })
}

resource "aws_efs_mount_target" "mcp" {
  count           = length(var.data_subnet_ids)
  file_system_id  = aws_efs_file_system.mcp.id
  subnet_id       = var.data_subnet_ids[count.index]
  security_groups = [var.security_group_id]
}

resource "aws_efs_access_point" "cache" {
  file_system_id = aws_efs_file_system.mcp.id
  posix_user {
    gid = 0
    uid = 0
  }
  root_directory {
    path = "/mcp-cache"
    creation_info {
      owner_gid   = 0
      owner_uid   = 0
      permissions = "0755"
    }
  }
  tags = merge(var.tags, { Name = "${var.name}-mcp-cache-ap" })
}

resource "aws_efs_access_point" "tokens" {
  file_system_id = aws_efs_file_system.mcp.id
  posix_user {
    gid = 0
    uid = 0
  }
  root_directory {
    path = "/mcp-orgs"
    creation_info {
      owner_gid   = 0
      owner_uid   = 0
      permissions = "0700"
    }
  }
  tags = merge(var.tags, { Name = "${var.name}-mcp-tokens-ap" })
}

resource "aws_ecs_task_definition" "mcp" {
  family                   = "${var.name}-mcp"
  requires_compatibilities = ["EC2"]
  network_mode             = "awsvpc"
  cpu                      = var.cpu
  memory                   = var.memory
  execution_role_arn       = var.execution_role_arn
  task_role_arn            = var.task_role_arn

  volume {
    name = "mcp-cache"
    efs_volume_configuration {
      file_system_id     = aws_efs_file_system.mcp.id
      transit_encryption = "ENABLED"
      authorization_config {
        access_point_id = aws_efs_access_point.cache.id
        iam             = "ENABLED"
      }
    }
  }
  volume {
    name = "mcp-tokens"
    efs_volume_configuration {
      file_system_id     = aws_efs_file_system.mcp.id
      transit_encryption = "ENABLED"
      authorization_config {
        access_point_id = aws_efs_access_point.tokens.id
        iam             = "ENABLED"
      }
    }
  }

  container_definitions = jsonencode([{
    name      = "${var.name}-mcp"
    image     = var.image
    essential = true
    portMappings = [{ containerPort = 8300, protocol = "tcp" }]
    environment = [for k, v in merge(var.environment, {
      UV_CACHE_DIR         = "/root/.cache/uv"
      NPM_CONFIG_CACHE     = "/root/.npm"
      MCP_REMOTE_CONFIG_DIR = "/tmp/mcp-orgs"
    }) : { name = k, value = v }]
    secrets = [for k, arn in var.secret_arns : { name = k, valueFrom = arn }]
    mountPoints = [
      { sourceVolume = "mcp-cache", containerPath = "/root/.cache/uv" },
      { sourceVolume = "mcp-cache", containerPath = "/root/.npm" },
      { sourceVolume = "mcp-tokens", containerPath = "/tmp/mcp-orgs" },
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = var.log_group
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = "${var.name}-mcp"
      }
    }
  }])
  tags = var.tags
}

resource "aws_ecs_service" "mcp" {
  name            = "${var.name}-mcp"
  cluster         = var.cluster_arn
  task_definition = aws_ecs_task_definition.mcp.arn
  desired_count   = var.desired_count

  capacity_provider_strategy {
    capacity_provider = var.capacity_provider
    base              = 1
    weight            = 1
  }

  network_configuration {
    subnets          = var.subnet_ids
    security_groups  = [var.security_group_id]
    assign_public_ip = false
  }
  # MCP holds long-lived per-org subprocess state; spread for resilience.
  ordered_placement_strategy {
    type  = "spread"
    field = "attribute:ecs.availability-zone"
  }
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200
  tags                               = var.tags
}

output "efs_id"       { value = aws_efs_file_system.mcp.id }
output "service_name" { value = aws_ecs_service.mcp.name }
