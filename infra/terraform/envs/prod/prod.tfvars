# Production values — plain env secrets (no AWS Secrets Manager).
# terraform plan -var-file=prod.tfvars
# terraform apply -var-file=prod.tfvars

region = "ap-south-1"
env    = "prod"
name   = "ai-mesh-prod"

gateway_image = "935951870001.dkr.ecr.ap-south-1.amazonaws.com/ai-mesh-gateway:v1.0.0"
control_image = "935951870001.dkr.ecr.ap-south-1.amazonaws.com/ai-mesh-control:v1.0.0"
worker_image  = "935951870001.dkr.ecr.ap-south-1.amazonaws.com/ai-mesh-control:v1.0.0"

# ACM cert in ap-south-1 (gateway + api hostnames) — replace after you request the cert
certificate_arn = "arn:aws:acm:ap-south-1:935951870001:certificate/REPLACE_ME"

# Database (RDS config + vector share this login)
db_username = "ai_mesh"
db_password = "JCVmj2JMycb2W2iZmxAyImuLA9wm2es"

# App secrets (injected as ECS environment variables)
django_secret_key      = "HYx6cRhtJuPV5Wgqqc+KeFdXQcnhMVKB0HKMz6oqWTHoJ84sq9MQaMGayQwk1ndt"
policy_signing_key     = "57d2b2941ef1d15c4fc801b6881dc567c73018a8a3fce6e84b8cf22c5eb1f2a0"
field_encryption_key   = "FDkygVECrkwz2qwxl385PtvMj5idY/pSTFsJzUHdZ2s="
gateway_internal_api_key = "0b143ad754f427e0e96cbce66e2efd72d43fa02984b8e08e"

# Gateway sizing — EC2 c7i.4xlarge ASG
gateway_cpu         = 2048
gateway_memory      = 4096
gateway_min         = 8
gateway_max         = 200
gateway_asg_min     = 3
gateway_asg_max     = 50
gateway_spot_weight = 70
web_concurrency     = "4"

single_nat = false
