###############################################################################
# Gateway inference pool — EC2 c7i.4xlarge ASG + ECS capacity provider
#
# Fargate cannot satisfy floor-8 / max-200 gateway tasks with 70% Spot on
# c7i.4xlarge. Tasks run at 2 vCPU / 4 GiB each (bin-packed on 16-vCPU hosts).
###############################################################################

variable "gateway_instance_type" {

  type    = string

  default = "c7i.4xlarge"

}
variable "gateway_asg_min" {
  type = number
  default = 3
}
variable "gateway_asg_max" {
  type = number
  default = 50
}
variable "gateway_asg_desired" {
  type = number
  default = 3
}
variable "gateway_spot_weight" {
  type = number
  default = 70
} # percent Spot above OD base

resource "aws_launch_template" "gateway" {
  name_prefix   = "${var.name}-gw-"
  image_id      = data.aws_ssm_parameter.ecs_ami.value
  instance_type = var.gateway_instance_type
  iam_instance_profile {
    arn = aws_iam_instance_profile.ecs_instance.arn
  }
  vpc_security_group_ids = [var.app_sg_id]
  user_data = base64encode(<<-EOT
    #!/bin/bash
    echo "ECS_CLUSTER=${var.name}" >> /etc/ecs/ecs.config
    echo "ECS_ENABLE_SPOT_INSTANCE_DRAINING=true" >> /etc/ecs/ecs.config
    echo "ECS_RESERVED_MEMORY=1024" >> /etc/ecs/ecs.config
    echo "ECS_INSTANCE_ATTRIBUTES={\"gateway\":\"true\"}" >> /etc/ecs/ecs.config
  EOT
  )
  block_device_mappings {
    device_name = "/dev/xvda"
    ebs {
      volume_size = 100
      volume_type = "gp3"
      encrypted   = true
    }
  }
  tag_specifications {
    resource_type = "instance"
    tags          = merge(var.tags, { Name = "${var.name}-gateway-node" })
  }
}

resource "aws_autoscaling_group" "gateway" {
  name                = "${var.name}-gateway-asg"
  vpc_zone_identifier = var.private_subnet_ids
  min_size            = var.gateway_asg_min
  max_size            = var.gateway_asg_max
  desired_capacity    = var.gateway_asg_desired
  protect_from_scale_in = true

  mixed_instances_policy {
    launch_template {
      launch_template_specification {
        launch_template_id = aws_launch_template.gateway.id
        version            = "$Latest"
      }
      override {
        instance_type = var.gateway_instance_type
      }
    }
    instances_distribution {
      on_demand_base_capacity                  = max(1, var.az_count)
      on_demand_percentage_above_base_capacity = 100 - var.gateway_spot_weight
      spot_allocation_strategy                 = "capacity-optimized"
    }
  }

  tag {
    key                 = "AmazonECSManaged"
    value               = "true"
    propagate_at_launch = true
  }
  tag {
    key                 = "Name"
    value               = "${var.name}-gateway-asg"
    propagate_at_launch = true
  }
}

resource "aws_ecs_capacity_provider" "gateway" {
  name = "${var.name}-gateway-cp"
  auto_scaling_group_provider {
    auto_scaling_group_arn         = aws_autoscaling_group.gateway.arn
    managed_termination_protection = "ENABLED"
    managed_scaling {
      status                    = "ENABLED"
      target_capacity           = 80
      minimum_scaling_step_size = 1
      maximum_scaling_step_size = 4
    }
  }
  tags = var.tags
}
