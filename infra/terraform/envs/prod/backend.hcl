# Remote state backend — supply via:  terraform init -backend-config=backend.hcl
# Create the bucket + lock table once, out of band, before first init.
bucket         = "ai-mesh-tfstate-<ACCOUNT>"
key            = "ai-mesh-firewall/prod/terraform.tfstate"
region         = "ap-south-1"
dynamodb_table = "ai-mesh-tflock"
encrypt        = true
