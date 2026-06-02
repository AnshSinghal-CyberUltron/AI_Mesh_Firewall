###############################################################################
# modules/network — VPC, 3-AZ subnets, NAT, VPC endpoints, security groups
#
# Public subnets:  NLB + ALB + NAT gateways
# Private subnets: ECS Fargate tasks (gateway/control/workers) + EC2 MCP pool
# Data subnets:    RDS + ElastiCache (no internet route)
###############################################################################

variable "name" {

  type = string

}
variable "vpc_cidr" {
  type = string
  default = "10.40.0.0/16"
}
variable "az_count" {
  type = number
  default = 3
}
variable "single_nat" {
  type = bool
  default = false
} # true = cheaper, less HA
variable "tags" {
  type = map(string)
  default = {
}
}

data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  azs = slice(data.aws_availability_zones.available.names, 0, var.az_count)
  # /20 public, /20 private, /22 data per AZ from the /16.
  public_cidrs  = [for i in range(var.az_count) : cidrsubnet(var.vpc_cidr, 4, i)]
  private_cidrs = [for i in range(var.az_count) : cidrsubnet(var.vpc_cidr, 4, i + 4)]
  data_cidrs    = [for i in range(var.az_count) : cidrsubnet(var.vpc_cidr, 6, i + 32)]
}

resource "aws_vpc" "this" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = merge(var.tags, { Name = "${var.name}-vpc" })
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id
  tags   = merge(var.tags, { Name = "${var.name}-igw" })
}

resource "aws_subnet" "public" {
  count                   = var.az_count
  vpc_id                  = aws_vpc.this.id
  cidr_block              = local.public_cidrs[count.index]
  availability_zone       = local.azs[count.index]
  map_public_ip_on_launch = true
  tags = merge(var.tags, { Name = "${var.name}-public-${local.azs[count.index]}", Tier = "public" })
}

resource "aws_subnet" "private" {
  count             = var.az_count
  vpc_id            = aws_vpc.this.id
  cidr_block        = local.private_cidrs[count.index]
  availability_zone = local.azs[count.index]
  tags = merge(var.tags, { Name = "${var.name}-private-${local.azs[count.index]}", Tier = "private" })
}

resource "aws_subnet" "data" {
  count             = var.az_count
  vpc_id            = aws_vpc.this.id
  cidr_block        = local.data_cidrs[count.index]
  availability_zone = local.azs[count.index]
  tags = merge(var.tags, { Name = "${var.name}-data-${local.azs[count.index]}", Tier = "data" })
}

# ── Public routing ──
resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.this.id
  }
  tags = merge(var.tags, { Name = "${var.name}-public-rt" })
}

resource "aws_route_table_association" "public" {
  count          = var.az_count
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

# ── NAT (one per AZ for HA, or a single shared NAT to save cost) ──
resource "aws_eip" "nat" {
  count  = var.single_nat ? 1 : var.az_count
  domain = "vpc"
  tags   = merge(var.tags, { Name = "${var.name}-nat-eip-${count.index}" })
}

resource "aws_nat_gateway" "this" {
  count         = var.single_nat ? 1 : var.az_count
  allocation_id = aws_eip.nat[count.index].id
  subnet_id     = aws_subnet.public[count.index].id
  tags          = merge(var.tags, { Name = "${var.name}-nat-${count.index}" })
  depends_on    = [aws_internet_gateway.this]
}

resource "aws_route_table" "private" {
  count  = var.az_count
  vpc_id = aws_vpc.this.id
  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.this[var.single_nat ? 0 : count.index].id
  }
  tags = merge(var.tags, { Name = "${var.name}-private-rt-${count.index}" })
}

resource "aws_route_table_association" "private" {
  count          = var.az_count
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private[count.index].id
}

# Data subnets get the same private egress (for RDS patching via NAT is not
# needed; keep them isolated — no 0.0.0.0/0 route). Associate to a local-only RT.
resource "aws_route_table" "data" {
  vpc_id = aws_vpc.this.id
  tags   = merge(var.tags, { Name = "${var.name}-data-rt" })
}

resource "aws_route_table_association" "data" {
  count          = var.az_count
  subnet_id      = aws_subnet.data[count.index].id
  route_table_id = aws_route_table.data.id
}

# ── Gateway VPC endpoint (S3) so telemetry/Firehose/ECR pulls avoid NAT cost ──
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.this.id
  service_name      = "com.amazonaws.${data.aws_region.current.name}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = concat(aws_route_table.private[*].id, [aws_route_table.data.id])
  tags              = merge(var.tags, { Name = "${var.name}-s3-endpoint" })
}

data "aws_region" "current" {}

# Interface endpoints for ECR + Secrets + Bedrock keep traffic on the AWS
# backbone (lower latency, no NAT data charges for these high-volume calls).
locals {
  interface_endpoints = [
    "ecr.api", "ecr.dkr", "logs", "secretsmanager", "sts",
    "bedrock-runtime", "sqs", "kinesis-firehose",
  ]
}

resource "aws_security_group" "endpoints" {
  name_prefix = "${var.name}-vpce-"
  vpc_id      = aws_vpc.this.id
  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = merge(var.tags, { Name = "${var.name}-vpce-sg" })
}

resource "aws_vpc_endpoint" "interface" {
  for_each            = toset(local.interface_endpoints)
  vpc_id              = aws_vpc.this.id
  service_name        = "com.amazonaws.${data.aws_region.current.name}.${each.value}"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = aws_subnet.private[*].id
  security_group_ids  = [aws_security_group.endpoints.id]
  private_dns_enabled = true
  tags                = merge(var.tags, { Name = "${var.name}-${each.value}-endpoint" })
}

###############################################################################
# Security groups
###############################################################################

resource "aws_security_group" "nlb" {
  name_prefix = "${var.name}-nlb-"
  vpc_id      = aws_vpc.this.id
  description = "Data-plane NLB ingress (gateway API)"
  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = merge(var.tags, { Name = "${var.name}-nlb-sg" })
}

resource "aws_security_group" "alb" {
  name_prefix = "${var.name}-alb-"
  vpc_id      = aws_vpc.this.id
  description = "Control-plane ALB ingress (dashboard/login)"
  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = merge(var.tags, { Name = "${var.name}-alb-sg" })
}

resource "aws_security_group" "app" {
  name_prefix = "${var.name}-app-"
  vpc_id      = aws_vpc.this.id
  description = "ECS tasks (gateway/control/workers/mcp)"
  # Ingress from the load balancers only.
  ingress {
    from_port       = 8300
    to_port         = 8300
    protocol        = "tcp"
    security_groups = [aws_security_group.nlb.id]
    description     = "Gateway from NLB"
  }
  ingress {
    from_port       = 8000
    to_port         = 8000
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
    description     = "Control from ALB"
  }
  # Intra-app (gateway -> control, sidecars).
  ingress {
    from_port = 0
    to_port   = 65535
    protocol  = "tcp"
    self      = true
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = merge(var.tags, { Name = "${var.name}-app-sg" })
}

resource "aws_security_group" "data" {
  name_prefix = "${var.name}-data-"
  vpc_id      = aws_vpc.this.id
  description = "RDS + ElastiCache — only from app tasks"
  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.app.id]
    description     = "Postgres from app"
  }
  ingress {
    from_port       = 6379
    to_port         = 6379
    protocol        = "tcp"
    security_groups = [aws_security_group.app.id]
    description     = "Redis from app"
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = merge(var.tags, { Name = "${var.name}-data-sg" })
}

###############################################################################
# Outputs
###############################################################################

output "vpc_id"             { value = aws_vpc.this.id }
output "public_subnet_ids"  { value = aws_subnet.public[*].id }
output "private_subnet_ids" { value = aws_subnet.private[*].id }
output "data_subnet_ids"    { value = aws_subnet.data[*].id }
output "nlb_sg_id"          { value = aws_security_group.nlb.id }
output "alb_sg_id"          { value = aws_security_group.alb.id }
output "app_sg_id"          { value = aws_security_group.app.id }
output "data_sg_id"         { value = aws_security_group.data.id }
