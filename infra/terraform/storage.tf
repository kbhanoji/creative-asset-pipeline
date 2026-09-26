# One bucket per purpose (TDD 5.1). No public access; uniform bucket-level access.
locals {
  versioned = toset(["config", "prompts", "approved", "audit"])
}

resource "google_storage_bucket" "b" {
  for_each                    = var.buckets
  project                     = var.project_id
  name                        = each.value
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = !var.deletion_protection
  labels                      = merge(local.labels, { purpose = each.key })

  versioning {
    enabled = contains(local.versioned, each.key)
  }

  dynamic "lifecycle_rule" {
    for_each = each.key == "landing" ? [var.landing_retention_days] : each.key == "quarantine" ? [var.quarantine_retention_days] : []
    content {
      condition { age = lifecycle_rule.value }
      action { type = "Delete" }
    }
  }

  dynamic "retention_policy" {
    for_each = each.key == "audit" ? [1] : []
    content {
      retention_period = var.audit_retention_days * 86400
      is_locked        = var.lock_audit_retention
    }
  }

  depends_on = [google_project_service.apis]
}
