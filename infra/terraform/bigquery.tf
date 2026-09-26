# Lineage & audit dataset (TDD 6.2). Tables are append-only; current state comes from views.
locals {
  schema_dir = "${path.module}/../../bigquery/schemas"
  view_dir   = "${path.module}/../../bigquery/views"
  tables     = { for f in fileset(local.schema_dir, "*.json") : trimsuffix(f, ".json") => jsondecode(file("${local.schema_dir}/${f}")) }
  view_vars  = { project = var.project_id, dataset = var.dataset }
  views_t1   = ["v_latest_score", "v_current_status", "v_prompt_versions"]
  views_t2   = ["v_asset_lifecycle"]
  views_t3   = ["v_provenance", "v_user_activity"]
}

resource "google_bigquery_dataset" "lineage" {
  project                    = var.project_id
  dataset_id                 = var.dataset
  location                   = var.bigquery_location
  description                = "Creative asset pipeline lineage and audit (${var.customer_id}/${var.environment})"
  labels                     = local.labels
  delete_contents_on_destroy = !var.deletion_protection
  depends_on                 = [google_project_service.apis]
}

resource "google_bigquery_table" "t" {
  for_each            = local.tables
  project             = var.project_id
  dataset_id          = google_bigquery_dataset.lineage.dataset_id
  table_id            = each.key
  description         = each.value.description
  schema              = jsonencode(each.value.columns)
  deletion_protection = var.deletion_protection
  labels              = local.labels

  time_partitioning {
    type  = "DAY"
    field = contains([for c in each.value.columns : c.name], "created_at") ? "created_at" : (contains([for c in each.value.columns : c.name], "scored_at") ? "scored_at" : "written_at")
  }
}

resource "google_bigquery_table" "v1" {
  for_each            = toset(local.views_t1)
  project             = var.project_id
  dataset_id          = google_bigquery_dataset.lineage.dataset_id
  table_id            = each.value
  deletion_protection = false
  view {
    query          = templatefile("${local.view_dir}/${each.value}.sql", local.view_vars)
    use_legacy_sql = false
  }
  depends_on = [google_bigquery_table.t]
}

resource "google_bigquery_table" "v2" {
  for_each            = toset(local.views_t2)
  project             = var.project_id
  dataset_id          = google_bigquery_dataset.lineage.dataset_id
  table_id            = each.value
  deletion_protection = false
  view {
    query          = templatefile("${local.view_dir}/${each.value}.sql", local.view_vars)
    use_legacy_sql = false
  }
  depends_on = [google_bigquery_table.v1]
}

resource "google_bigquery_table" "v3" {
  for_each            = toset(local.views_t3)
  project             = var.project_id
  dataset_id          = google_bigquery_dataset.lineage.dataset_id
  table_id            = each.value
  deletion_protection = false
  view {
    query          = templatefile("${local.view_dir}/${each.value}.sql", local.view_vars)
    use_legacy_sql = false
  }
  depends_on = [google_bigquery_table.v2]
}
