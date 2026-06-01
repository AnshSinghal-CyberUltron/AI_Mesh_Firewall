###############################################################################
# modules/loadbalancers
#
# Data plane  : NLB (L4 TLS passthrough/termination). No SSE buffering, long
#               idle timeout for streaming, 10k+ long-lived connections.
# Control plane: ALB (L7) + AWS WAF for the Django dashboard/login only.
#
# The gateway IS the application firewall, so the inference path is NOT behind
# WAF (avoids added latency + per-request WAF cost on the high-volume path).
###############################################################################

variable "name"               { type = string }
variable "vpc_id"             { type = string }
variable "public_subnet_ids" { type = list(string) }
variable "nlb_sg_id"         { type = string }
variable "alb_sg_id"         { type = string }
variable "certificate_arn"   { type = string } # ACM cert for both LBs
variable "tags"              { type = map(string), default = {} }

###############################################################################
# Data-plane NLB → gateway target group (IP targets for Fargate)
###############################################################################

resource "aws_lb" "data" {
  name                             = "${var.name}-nlb"
  load_balancer_type               = "network"
  subnets                          = var.public_subnet_ids
  security_groups                  = [var.nlb_sg_id] # SG support on NLB (2023+)
  enable_cross_zone_load_balancing = true
  idle_timeout                     = 350 # streaming-friendly; > typical LLM turn
  tags                             = merge(var.tags, { Name = "${var.name}-nlb" })
}

resource "aws_lb_target_group" "gateway" {
  name                 = "${var.name}-gw-tg"
  port                 = 8300
  protocol             = "TCP"
  vpc_id               = var.vpc_id
  target_type          = "ip"
  deregistration_delay = 30
  # Connection draining + slow-start keep streams intact on deploys.
  health_check {
    protocol            = "HTTP"
    path                = "/health"
    port                = "8300"
    healthy_threshold   = 2
    unhealthy_threshold = 3
    interval            = 10
    timeout             = 6
    matcher             = "200-399"
  }
  tags = merge(var.tags, { Name = "${var.name}-gw-tg" })
}

resource "aws_lb_listener" "gateway_tls" {
  load_balancer_arn = aws_lb.data.arn
  port              = 443
  protocol          = "TLS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.certificate_arn
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.gateway.arn
  }
}

###############################################################################
# Control-plane ALB + WAF → control target group
###############################################################################

resource "aws_lb" "control" {
  name               = "${var.name}-alb"
  load_balancer_type = "application"
  subnets            = var.public_subnet_ids
  security_groups    = [var.alb_sg_id]
  idle_timeout       = 120
  tags               = merge(var.tags, { Name = "${var.name}-alb" })
}

resource "aws_lb_target_group" "control" {
  name                 = "${var.name}-ctl-tg"
  port                 = 8000
  protocol             = "HTTP"
  vpc_id               = var.vpc_id
  target_type          = "ip"
  deregistration_delay = 30
  health_check {
    protocol            = "HTTP"
    path                = "/health/"
    healthy_threshold   = 2
    unhealthy_threshold = 3
    interval            = 15
    timeout             = 5
    matcher             = "200-399"
  }
  tags = merge(var.tags, { Name = "${var.name}-ctl-tg" })
}

resource "aws_lb_listener" "control_https" {
  load_balancer_arn = aws_lb.control.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.certificate_arn
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.control.arn
  }
}

resource "aws_wafv2_web_acl" "control" {
  name        = "${var.name}-control-waf"
  scope       = "REGIONAL"
  description = "WAF for control-plane dashboard/login only"
  default_action {
    allow {}
  }
  rule {
    name     = "AWSManagedCommon"
    priority = 1
    override_action {
      none {}
    }
    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesCommonRuleSet"
        vendor_name = "AWS"
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.name}-waf-common"
      sampled_requests_enabled   = true
    }
  }
  rule {
    name     = "RateLimitLogin"
    priority = 2
    action {
      block {}
    }
    statement {
      rate_based_statement {
        limit              = 2000
        aggregate_key_type = "IP"
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.name}-waf-ratelimit"
      sampled_requests_enabled   = true
    }
  }
  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "${var.name}-control-waf"
    sampled_requests_enabled   = true
  }
  tags = var.tags
}

resource "aws_wafv2_web_acl_association" "control" {
  resource_arn = aws_lb.control.arn
  web_acl_arn  = aws_wafv2_web_acl.control.arn
}

###############################################################################
# Outputs
###############################################################################

output "gateway_target_group_arn" { value = aws_lb_target_group.gateway.arn }
output "control_target_group_arn" { value = aws_lb_target_group.control.arn }
output "nlb_dns_name"             { value = aws_lb.data.dns_name }
output "alb_dns_name"             { value = aws_lb.control.dns_name }
output "nlb_zone_id"              { value = aws_lb.data.zone_id }
output "alb_zone_id"              { value = aws_lb.control.zone_id }
