locals {
  labels = merge(var.labels, { customer = var.customer_id, environment = var.environment, managed-by = "terraform" })

  apis = [
    "cloudresourcemanager.googleapis.com",
    "serviceusage.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "compute.googleapis.com",
    "iap.googleapis.com",
    "storage.googleapis.com",
    "bigquery.googleapis.com",
    "aiplatform.googleapis.com",
    "discoveryengine.googleapis.com",
    "run.googleapis.com",
    "cloudbuild.googleapis.com",
    "artifactregistry.googleapis.com",
    "eventarc.googleapis.com",
    "pubsub.googleapis.com",
    "secretmanager.googleapis.com",
    "logging.googleapis.com",
    "monitoring.googleapis.com",
    "cloudtrace.googleapis.com",
    "cloudscheduler.googleapis.com",
    "sqladmin.googleapis.com",
  ]
}

# The project itself is created (and deleted) by the installer with gcloud, not by
# Terraform: `cap infra apply` / install.sh creates it, links billing and the state
# bucket, and `cap teardown` deletes it. Terraform manages everything inside it.
data "google_project" "this" {
  project_id = var.project_id
}

resource "google_project_service" "apis" {
  for_each                   = toset(local.apis)
  project                    = data.google_project.this.project_id
  service                    = each.value
  disable_on_destroy         = false
  disable_dependent_services = false
}

resource "google_artifact_registry_repository" "images" {
  project       = var.project_id
  location      = var.region
  repository_id = "cap"
  format        = "DOCKER"
  labels        = local.labels

  # every `cap deploy` builds new images; keep only the most recent ones
  cleanup_policy_dry_run = false
  dynamic "cleanup_policies" {
    for_each = var.container_images_keep > 0 ? [1] : []
    content {
      id     = "delete-older-than-1-day"
      action = "DELETE"
      condition {
        tag_state  = "ANY"
        older_than = "86400s"
      }
    }
  }
  dynamic "cleanup_policies" {
    for_each = var.container_images_keep > 0 ? [1] : []
    content {
      id     = "keep-most-recent"
      action = "KEEP"
      most_recent_versions {
        keep_count = var.container_images_keep
      }
    }
  }
  depends_on = [google_project_service.apis]
}
