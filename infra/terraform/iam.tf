# Least-privilege service accounts per component (TDD 15.1) + human persona grants from the YAML.
locals {
  service_accounts = {
    "sa-prompt-agent"    = "Prompt enrichment agent (Agent Engine) and pipeline tools"
    "sa-lineage-router"  = "Lineage & scoring router (Cloud Run)"
    "sa-creative-studio" = "Creative Studio VM"
    "sa-scheduler"       = "Cloud Scheduler: dev auto-stop/start of the Creative Studio database"
  }

  # bucket purpose => role, per service account
  bucket_grants = {
    "sa-prompt-agent" = {
      landing      = "roles/storage.objectViewer", "creative-studio" = "roles/storage.objectViewer",
      config       = "roles/storage.objectAdmin", prompts = "roles/storage.objectAdmin",
      intermediate = "roles/storage.objectAdmin", approved = "roles/storage.objectAdmin",
      rejected     = "roles/storage.objectAdmin", quarantine = "roles/storage.objectAdmin",
      audit        = "roles/storage.objectCreator"
    }
    "sa-lineage-router" = {
      landing      = "roles/storage.objectViewer", "creative-studio" = "roles/storage.objectViewer",
      config       = "roles/storage.objectAdmin", prompts = "roles/storage.objectViewer",
      intermediate = "roles/storage.objectAdmin", approved = "roles/storage.objectAdmin",
      rejected     = "roles/storage.objectAdmin", quarantine = "roles/storage.objectAdmin",
      audit        = "roles/storage.objectCreator"
    }
    "sa-creative-studio" = {
      "creative-studio" = "roles/storage.objectAdmin", landing = "roles/storage.objectViewer",
      config            = "roles/storage.objectViewer", prompts = "roles/storage.objectViewer",
      approved          = "roles/storage.objectViewer"
    }
  }

  project_roles = {
    "sa-prompt-agent"    = ["roles/aiplatform.user", "roles/bigquery.jobUser", "roles/logging.logWriter", "roles/cloudtrace.agent"]
    "sa-lineage-router"  = ["roles/aiplatform.user", "roles/bigquery.jobUser", "roles/logging.logWriter", "roles/eventarc.eventReceiver", "roles/run.invoker"]
    "sa-creative-studio" = ["roles/aiplatform.user", "roles/logging.logWriter", "roles/monitoring.metricWriter", "roles/artifactregistry.reader"]
    "sa-scheduler"       = ["roles/cloudsql.editor", "roles/run.invoker"]
  }

  bucket_bindings = merge([
    for sa, grants in local.bucket_grants : {
      # buckets not created here (e.g. GCC's own bucket when creative_studio.deployment=cloud_run) are skipped;
      # `cap deploy router` grants access to that bucket instead
      for purpose, role in grants : "${sa}|${purpose}|${role}" => { sa = sa, purpose = purpose, role = role }
      if contains(keys(var.buckets), purpose)
    }
  ]...)
  project_bindings = merge([
    for sa, roles in local.project_roles : { for r in roles : "${sa}|${r}" => { sa = sa, role = r } }
  ]...)
  human_bindings = { for m in var.iam_members : "${m.member}|${m.role}" => m }
}

resource "google_service_account" "sa" {
  for_each     = local.service_accounts
  project      = var.project_id
  account_id   = each.key
  display_name = each.value
  depends_on   = [google_project_service.apis]
}

resource "google_storage_bucket_iam_member" "sa" {
  for_each = local.bucket_bindings
  bucket   = google_storage_bucket.b[each.value.purpose].name
  role     = each.value.role
  member   = google_service_account.sa[each.value.sa].member
}

resource "google_project_iam_member" "sa" {
  for_each = local.project_bindings
  project  = var.project_id
  role     = each.value.role
  member   = google_service_account.sa[each.value.sa].member
}

resource "google_bigquery_dataset_iam_member" "editors" {
  for_each   = toset(["sa-prompt-agent", "sa-lineage-router"])
  project    = var.project_id
  dataset_id = google_bigquery_dataset.lineage.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = google_service_account.sa[each.value].member
}

# Cloud Storage service agent publishes the GCS events Eventarc delivers to the router.
data "google_storage_project_service_account" "gcs" {
  project    = var.project_id
  depends_on = [google_project_service.apis]
}

resource "google_project_iam_member" "gcs_pubsub" {
  project = var.project_id
  role    = "roles/pubsub.publisher"
  member  = "serviceAccount:${data.google_storage_project_service_account.gcs.email_address}"
}

# Human users / groups (personas in the YAML)
resource "google_project_iam_member" "humans" {
  for_each   = local.human_bindings
  project    = var.project_id
  role       = each.value.role
  member     = each.value.member
  depends_on = [google_project_service.apis]
}

# Dev idle stop: the router reads Cloud Run request metrics and stops GCC's Cloud SQL when idle.
resource "google_project_iam_member" "router_idle_stop" {
  for_each = var.gcc_idle_stop ? toset(["roles/monitoring.viewer", "roles/cloudsql.editor"]) : toset([])
  project  = var.project_id
  role     = each.value
  member   = google_service_account.sa["sa-lineage-router"].member
}
