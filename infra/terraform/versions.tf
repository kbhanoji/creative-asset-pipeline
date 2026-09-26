terraform {
  required_version = ">= 1.6"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 6.10, < 8.0"
    }
  }
  # State lives in a GCS bucket inside the project (<project>-cap-tfstate), created by
  # `cap infra apply` / install.sh before Terraform runs, so any session (Cloud Shell,
  # another admin) can resume or update. Configured with -backend-config=bucket/prefix.
  backend "gcs" {}
}

provider "google" {
  region = var.region
}
