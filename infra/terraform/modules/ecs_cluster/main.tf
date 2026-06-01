###############################################################################
# modules/ecs_cluster
#
# One ECS cluster with three capacity providers:
#   FARGATE       — On-Demand floor (latency-critical gateway baseline)
#   FARGATE_SPOT  — burst capacity (gateway scale-out, ~70% cheaper)
#   EC2 (c7i ASG) — MCP stdio pool (needs real processes + warm uv/npm cache)
#
# x86 (c7i), NOT Graviton: MCP npx/uvx fetch arch-specific native binaries and
# the Presidio sidecar images are amd64-only.
###############################################################################

variable "name"              { type = string }
variable "private_subnet_ids" { type = list(string) }
variable "app_sg_id"          { type = string }
variable "mcp_instance_type"  { type = string, default = "c7i.2xlarge" }
variable "mcp_min_size"       { type = number, default = 1 }
variable "mcp_max_size"       { type = number, default = 6 }
variable "mcp_desired"        { type = number, default = 2 }
variable "tags"               { type = map(string), default = {} }

resource "aws_ecs_cluster" "this" {
  name = var.name
  setting {
    name  = "containerInsights"
    value = "enabled"
  }
  tags = var.tags
}

# ── EC2 capacity provider for the MCP pool ──
data "aws_ssm_parameter" "ecs_ami" {
  name = "/aws/service/ecs/optimized-ami/amazon-linux-2023/recommended/image_id"
}

resource "aws_iam_role" "ecs_instance" {
  name = "${var.name}-ecs-instance"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
  tags = var.tags
}

resource "aws_iam_role_policy_attachment" "ecs_instance" {
  role       = aws_iam_role.ecs_instance.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonEC2ContainerServiceforEC2Role"
}

resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.ecs_instance.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "ecs_instance" {
  name = "${var.name}-ecs-instance"
  role = aws_iam_role.ecs_instance.name
}

resource "aws_launch_template" "mcp" {
  name_prefix   = "${var.name}-mcp-"
  image_id      = data.aws_ssm_parameter.ecs_ami.value
  instance_type = var.mcp_instance_type
  iam_instance_profile {
    arn = aws_iam_instance_profile.ecs_instance.arn
  }
  vpc_security_group_ids = [var.app_sg_id]
  # Register into this cluster; reserve headroom for npx/uvx subprocess spikes.
  user_data = base64encode(<<-EOT
    #!/bin/bash
    echo "ECS_CLUSTER=${var.name}" >> /etc/ecs/ecs.config
    echo "ECS_ENABLE_SPOT_INSTANCE_DRAINING=true" >> /etc/ecs/ecs.config
    echo "ECS_RESERVED_MEMORY=512" >> /etc/ecs/ecs.config
  EOT
  )
  block_device_mappings {
    device_name = "/dev/xvda"
    ebs {
      volume_size = 60
      volume_type = "gp3"
      encrypted   = true
    }
  }
  tag_specifications {
    resource_type = "instance"
    tags          = merge(var.tags, { Name = "${var.name}-mcp-node" })
  }
}

resource "aws_autoscaling_group" "mcp" {
  name                = "${var.name}-mcp-asg"
  vpc_zone_identifier = var.private_subnet_ids
  min_size            = var.mcp_min_size
  max_size            = var.mcp_max_size
  desired_capacity    = var.mcp_desired
  protect_from_scale_in = true # required for managed termination protection
  launch_template {
    id      = aws_launch_template.mcp.id
    version = "$Latest"
  }
  tag {
    key                 = "AmazonECSManaged"
    value               = "true"
    propagate_at_launch = true
  }
}

resource "aws_ecs_capacity_provider" "mcp" {
  name = "${var.name}-mcp-cp"
  auto_scaling_group_provider {
    auto_scaling_group_arn         = aws_autoscaling_group.mcp.arn
    managed_termination_protection = "ENABLED"
    managed_scaling {
      status                    = "ENABLED"
      target_capacity           = 80
      minimum_scaling_step_size = 1
      maximum_scaling_step_size = 2
    }
  }
  tags = var.tags
}

resource "aws_ecs_cluster_capacity_providers" "this" {
  cluster_name = aws_ecs_cluster.this.name
  capacity_providers = [
    "FARGATE",
    "FARGATE_SPOT",
    aws_ecs_capacity_provider.mcp.name,
  ]
  # Gateway default: keep an On-Demand floor, burst on Spot.
  default_capacity_provider_strategy {
    capacity_provider = "FARGATE"
    base              = 2
    weight            = 1
  }
  default_capacity_provider_strategy {
    capacity_provider = "FARGATE_SPOT"
    base              = 0
    weight            = 3
  }
}

output "cluster_id"           { value = aws_ecs_cluster.this.id }
output "cluster_name"         { value = aws_ecs_cluster.this.name }
output "cluster_arn"          { value = aws_ecs_cluster.this.arn }
output "mcp_capacity_provider" { value = aws_ecs_capacity_provider.mcp.name }
