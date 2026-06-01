###############################################################################
# modules/ecs_service — reusable Fargate service
#
# Used for the gateway, control plane, and Celery workers. Parametrized over
# image, CPU/memory, env, secrets, port, load-balancer target group, autoscaling
# bounds, and capacity-provider strategy.
###############################################################################

variable "name"             { type = string }
variable "cluster_arn"      { type = string }
variable "subnet_ids"       { type = list(string) }
variable "security_group_id" { type = string }
variable "image"            { type = string }
variable "cpu"              { type = number, default = 4096 } # 4 vCPU
variable "memory"           { type = number, default = 8192 } # 8 GiB
variable "container_port"   { type = number, default = 8300 }
variable "desired_count"    { type = number, default = 2 }
variable "min_count"        { type = number, default = 2 }
variable "max_count"        { type = number, default = 8 }
variable "target_group_arn" { type = string, default = "" } # "" = no LB (workers)
variable "command"          { type = list(string), default = [] }
variable "environment"      { type = map(string), default = {} }
variable "secret_arns"      { type = map(string), default = {} } # name -> secret arn
variable "execution_role_arn" { type = string }
variable "task_role_arn"      { type = string }
variable "region"             { type = string }
variable "log_group"          { type = string }
variable "capacity_strategy" {
  type = list(object({ capacity_provider = string, base = number, weight = number }))
  default = []
}
variable "autoscale_cpu_target" { type = number, default = 55 }
variable "health_path" { type = string, default = "/health" }
variable "tags" { type = map(string), default = {} }

locals {
  has_lb = var.target_group_arn != ""
}

resource "aws_ecs_task_definition" "this" {
  family                   = var.name
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.cpu
  memory                   = var.memory
  execution_role_arn       = var.execution_role_arn
  task_role_arn            = var.task_role_arn
  runtime_platform {
    cpu_architecture        = "X86_64" # Presidio/MCP native deps are amd64
    operating_system_family = "LINUX"
  }
  container_definitions = jsonencode([{
    name      = var.name
    image     = var.image
    essential = true
    command   = length(var.command) > 0 ? var.command : null
    portMappings = local.has_lb ? [{
      containerPort = var.container_port
      protocol      = "tcp"
    }] : []
    environment = [for k, v in var.environment : { name = k, value = v }]
    secrets     = [for k, arn in var.secret_arns : { name = k, valueFrom = arn }]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = var.log_group
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = var.name
      }
    }
    healthCheck = local.has_lb ? {
      command     = ["CMD-SHELL", "python -c 'import urllib.request,sys; urllib.request.urlopen(\"http://127.0.0.1:${var.container_port}${var.health_path}\"); ' || exit 1"]
      interval    = 15
      timeout     = 5
      retries     = 3
      startPeriod = 40
    } : null
  }])
  tags = var.tags
}

resource "aws_ecs_service" "this" {
  name            = var.name
  cluster         = var.cluster_arn
  task_definition = aws_ecs_task_definition.this.arn
  desired_count   = var.desired_count
  propagate_tags  = "SERVICE"

  dynamic "capacity_provider_strategy" {
    for_each = var.capacity_strategy
    content {
      capacity_provider = capacity_provider_strategy.value.capacity_provider
      base              = capacity_provider_strategy.value.base
      weight            = capacity_provider_strategy.value.weight
    }
  }
  # Fall back to plain FARGATE when no strategy supplied.
  launch_type = length(var.capacity_strategy) == 0 ? "FARGATE" : null

  network_configuration {
    subnets          = var.subnet_ids
    security_groups  = [var.security_group_id]
    assign_public_ip = false
  }

  dynamic "load_balancer" {
    for_each = local.has_lb ? [1] : []
    content {
      target_group_arn = var.target_group_arn
      container_name   = var.name
      container_port   = var.container_port
    }
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }
  # Keep capacity during rolling deploys so streams aren't dropped.
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200
  health_check_grace_period_seconds  = local.has_lb ? 60 : null

  lifecycle {
    ignore_changes = [desired_count] # managed by autoscaling
  }
  tags = var.tags
}

###############################################################################
# Autoscaling (CPU target tracking + request-per-target for LB services)
###############################################################################

resource "aws_appautoscaling_target" "this" {
  max_capacity       = var.max_count
  min_capacity       = var.min_count
  resource_id        = "service/${split("/", var.cluster_arn)[1]}/${aws_ecs_service.this.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  service_namespace  = "ecs"
}

resource "aws_appautoscaling_policy" "cpu" {
  name               = "${var.name}-cpu"
  policy_type        = "TargetTrackingScaling"
  resource_id        = aws_appautoscaling_target.this.resource_id
  scalable_dimension = aws_appautoscaling_target.this.scalable_dimension
  service_namespace  = aws_appautoscaling_target.this.service_namespace
  target_tracking_scaling_policy_configuration {
    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageCPUUtilization"
    }
    target_value       = var.autoscale_cpu_target
    scale_in_cooldown  = 120
    scale_out_cooldown = 30 # scale out fast, in slow
  }
}

output "service_name"        { value = aws_ecs_service.this.name }
output "task_definition_arn" { value = aws_ecs_task_definition.this.arn }
