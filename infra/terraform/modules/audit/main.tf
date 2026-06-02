###############################################################################
# modules/audit — Kinesis Data Firehose → S3 for gateway telemetry / audit batches
###############################################################################

variable "name" {

  type = string

}
variable "kms_key_arn" {
  type = string
}
variable "tags" {
  type = map(string)
  default = {
}
}

resource "aws_s3_bucket" "audit" {
  bucket = "${var.name}-audit-${data.aws_caller_identity.current.account_id}"
  tags   = merge(var.tags, { Name = "${var.name}-audit" })
}

resource "aws_s3_bucket_versioning" "audit" {
  bucket = aws_s3_bucket.audit.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "audit" {
  bucket = aws_s3_bucket.audit.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = var.kms_key_arn
    }
  }
}

resource "aws_s3_bucket_public_access_block" "audit" {
  bucket                  = aws_s3_bucket.audit.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

data "aws_caller_identity" "current" {}

resource "aws_iam_role" "firehose" {
  name = "${var.name}-firehose"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "firehose.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
  tags = var.tags
}

resource "aws_iam_role_policy" "firehose" {
  role = aws_iam_role.firehose.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:AbortMultipartUpload", "s3:GetBucketLocation", "s3:GetObject",
          "s3:ListBucket", "s3:ListBucketMultipartUploads", "s3:PutObject",
        ]
        Resource = [aws_s3_bucket.audit.arn, "${aws_s3_bucket.audit.arn}/*"]
      },
      {
        Effect   = "Allow"
        Action   = ["kms:Encrypt", "kms:Decrypt", "kms:GenerateDataKey"]
        Resource = [var.kms_key_arn]
      },
    ]
  })
}

resource "aws_kinesis_firehose_delivery_stream" "audit" {
  name        = "${var.name}-audit"
  destination = "extended_s3"

  extended_s3_configuration {
    role_arn            = aws_iam_role.firehose.arn
    bucket_arn          = aws_s3_bucket.audit.arn
    prefix              = "audit/year=!{timestamp:yyyy}/month=!{timestamp:MM}/day=!{timestamp:dd}/"
    error_output_prefix = "errors/!{firehose:error-output-type}/year=!{timestamp:yyyy}/month=!{timestamp:MM}/day=!{timestamp:dd}/"
    buffering_size      = 64
    buffering_interval  = 300
    compression_format  = "GZIP"
    kms_key_arn         = var.kms_key_arn
  }

  tags = var.tags
}

output "firehose_stream_name" { value = aws_kinesis_firehose_delivery_stream.audit.name }
output "firehose_stream_arn" { value = aws_kinesis_firehose_delivery_stream.audit.arn }
output "audit_bucket_name"   { value = aws_s3_bucket.audit.id }
output "audit_bucket_arn"    { value = aws_s3_bucket.audit.arn }
