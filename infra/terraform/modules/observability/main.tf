variable "name" {
  type        = string
  description = "Resource prefix, e.g. ai-mesh-firewall"
}

variable "region" {
  type    = string
  default = "ap-south-1"
}

variable "alarm_email" {
  type        = string
  description = "SNS email subscription for infra alarms"
}

variable "instance_id" {
  type        = string
  description = "Demo EC2 instance id for EventBridge alarm gating"
}

variable "log_retention_days" {
  type    = number
  default = 14
}

variable "grace_seconds" {
  type        = number
  default     = 900
  description = "Boot grace before stack alarms can fire"
}

variable "existing_instance_role_name" {
  type        = string
  description = "IAM role attached to the EC2 instance profile"
}

variable "tags" {
  type    = map(string)
  default = {}
}

locals {
  log_prefix = "/ai-mesh-firewall/ec2"
  services   = ["gateway", "control", "workers", "nginx", "host"]
}

resource "aws_cloudwatch_log_group" "service" {
  for_each          = toset(local.services)
  name              = "${local.log_prefix}/${each.key}"
  retention_in_days = each.key == "nginx" ? min(var.log_retention_days, 7) : var.log_retention_days
  tags              = merge(var.tags, { Name = "${var.name}-${each.key}-logs" })
}

resource "aws_ssm_parameter" "cloudwatch_agent_config" {
  name  = "${local.log_prefix}/cloudwatch-agent/config"
  type  = "String"
  value = file("${path.module}/templates/cloudwatch-agent.json.tpl")
  tags  = var.tags
}

resource "aws_sns_topic" "critical" {
  name = "${var.name}-demo-critical"
  tags = var.tags
}

resource "aws_sns_topic_subscription" "critical_email" {
  topic_arn = aws_sns_topic.critical.arn
  protocol  = "email"
  endpoint  = var.alarm_email
}

resource "aws_iam_policy" "observability" {
  name        = "${var.name}-ec2-observability"
  description = "CloudWatch Logs + host metrics for demo EC2"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "CloudWatchLogsShip"
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents",
          "logs:DescribeLogStreams",
          "logs:DescribeLogGroups"
        ]
        Resource = [
          for lg in aws_cloudwatch_log_group.service : "${lg.arn}:*"
        ]
      },
      {
        Sid      = "CloudWatchHostMetrics"
        Effect   = "Allow"
        Action   = ["cloudwatch:PutMetricData"]
        Resource = "*"
        Condition = {
          StringEquals = {
            "cloudwatch:namespace" = "AIMeshFirewall/EC2"
          }
        }
      },
      {
        Sid    = "SSMCloudWatchAgentConfig"
        Effect = "Allow"
        Action = ["ssm:GetParameter", "ssm:GetParameters"]
        Resource = [
          aws_ssm_parameter.cloudwatch_agent_config.arn
        ]
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "observability" {
  role       = var.existing_instance_role_name
  policy_arn = aws_iam_policy.observability.arn
}

resource "aws_cloudwatch_metric_alarm" "status_check" {
  alarm_name          = "${var.name}-demo-status-check"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 2
  metric_name         = "StatusCheckFailed"
  namespace           = "AWS/EC2"
  period              = 300
  statistic           = "Maximum"
  threshold           = 1
  treat_missing_data  = "notBreaching"
  alarm_description   = "EC2 status check failed while demo instance is running"
  dimensions = {
    InstanceId = var.instance_id
  }
  alarm_actions = [aws_sns_topic.critical.arn]
  ok_actions    = [aws_sns_topic.critical.arn]
}

resource "aws_cloudwatch_metric_alarm" "host_mem" {
  alarm_name          = "${var.name}-demo-host-mem"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  metric_name         = "mem_used_percent"
  namespace           = "AIMeshFirewall/EC2"
  period              = 300
  statistic           = "Average"
  threshold           = 90
  treat_missing_data  = "notBreaching"
  alarm_description   = "Host memory above 90% for 15 minutes"
  alarm_actions       = [aws_sns_topic.critical.arn]
}

resource "aws_cloudwatch_metric_alarm" "host_disk" {
  alarm_name          = "${var.name}-demo-root-disk"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "disk_used_percent"
  namespace           = "AIMeshFirewall/EC2"
  period              = 300
  statistic           = "Average"
  threshold           = 85
  treat_missing_data  = "notBreaching"
  alarm_description   = "Root or docker disk above 85%"
  alarm_actions       = [aws_sns_topic.critical.arn]
}

resource "aws_cloudwatch_metric_alarm" "stack_ready" {
  alarm_name          = "${var.name}-demo-stack-down"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 2
  metric_name         = "StackReady"
  namespace           = "AIMeshFirewall/EC2"
  period              = 300
  statistic           = "Minimum"
  threshold           = 1
  treat_missing_data  = "notBreaching"
  alarm_description   = "StackReady=0 for 10+ min after deploy (gateway/control/nginx unhealthy)"
  alarm_actions       = [aws_sns_topic.critical.arn]
}

resource "aws_cloudwatch_log_metric_filter" "gateway_errors" {
  name           = "${var.name}-gateway-errors"
  log_group_name = aws_cloudwatch_log_group.service["gateway"].name
  pattern        = "ERROR"
  metric_transformation {
    name      = "GatewayErrorCount"
    namespace = "AIMeshFirewall/EC2"
    value     = "1"
  }
}

resource "aws_cloudwatch_metric_alarm" "gateway_errors" {
  alarm_name          = "${var.name}-demo-gateway-errors"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "GatewayErrorCount"
  namespace           = "AIMeshFirewall/EC2"
  period              = 300
  statistic           = "Sum"
  threshold           = 10
  treat_missing_data  = "notBreaching"
  alarm_description   = "Gateway ERROR log burst"
  alarm_actions       = [aws_sns_topic.critical.arn]
}

resource "aws_iam_role" "alarm_lifecycle" {
  name = "${var.name}-alarm-lifecycle"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
  tags = var.tags
}

resource "aws_iam_role_policy" "alarm_lifecycle" {
  role = aws_iam_role.alarm_lifecycle.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "cloudwatch:EnableAlarmActions",
          "cloudwatch:DisableAlarmActions"
        ]
        Resource = "arn:aws:cloudwatch:${var.region}:*:alarm:${var.name}-demo-*"
      },
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "*"
      }
    ]
  })
}

data "aws_caller_identity" "current" {}

resource "aws_cloudwatch_event_rule" "ec2_stopped" {
  name        = "${var.name}-demo-alarms-off"
  description = "Disable demo alarms when EC2 stops (no false emails)"
  event_pattern = jsonencode({
    source      = ["aws.ec2"]
    detail-type = ["EC2 Instance State-change Notification"]
    detail = {
      instance-id = [var.instance_id]
      state       = ["stopping", "stopped", "shutting-down", "terminated"]
    }
  })
  tags = var.tags
}

resource "aws_cloudwatch_event_rule" "ec2_running" {
  name        = "${var.name}-demo-alarms-on"
  description = "Re-enable demo alarms when EC2 reaches running"
  event_pattern = jsonencode({
    source      = ["aws.ec2"]
    detail-type = ["EC2 Instance State-change Notification"]
    detail = {
      instance-id = [var.instance_id]
      state       = ["running"]
    }
  })
  tags = var.tags
}

# Wire Lambda enable/disable in a follow-up; until then use scripts/demo-ec2-lifecycle.sh

output "sns_topic_arn" {
  value = aws_sns_topic.critical.arn
}

output "log_group_names" {
  value = { for k, lg in aws_cloudwatch_log_group.service : k => lg.name }
}

output "ssm_agent_config" {
  value = aws_ssm_parameter.cloudwatch_agent_config.name
}
