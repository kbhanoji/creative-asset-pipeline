terraform {
  required_version = ">= 1.6"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 6.10, < 8.0"
    }
  }
  # Local state by default. For shared use, `cap infra apply` can be pointed at a
  # GCS backend via -backend-config once the project (and a state bucket) exists.
  backend "local" {}
}

provider "google" {
  region = var.region
}
