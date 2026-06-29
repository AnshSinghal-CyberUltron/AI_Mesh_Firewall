variable "name" {
  type    = string
  default = "ai-mesh-firewall"
}

variable "region" {
  type    = string
  default = "ap-south-1"
}

variable "alarm_email" {
  type        = string
  description = "Confirm SNS subscription from this inbox once"
}

variable "instance_id" {
  type        = string
  description = "Demo EC2 instance id (i-xxxxxxxx)"
}

variable "existing_instance_role_name" {
  type    = string
  default = "ai-mesh-InstanceRole"
}

variable "log_retention_days" {
  type    = number
  default = 14
}

variable "grace_seconds" {
  type    = number
  default = 900
}
