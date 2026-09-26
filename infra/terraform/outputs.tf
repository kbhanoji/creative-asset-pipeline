output "project_id" { value = data.google_project.this.project_id }
output "project_number" { value = data.google_project.this.number }
output "buckets" { value = { for k, b in google_storage_bucket.b : k => b.url } }
output "dataset" { value = google_bigquery_dataset.lineage.dataset_id }
output "service_accounts" { value = { for k, s in google_service_account.sa : k => s.email } }
output "artifact_registry" { value = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}" }
output "creative_studio_vm" {
  value = var.creative_studio.enabled ? {
    name = google_compute_instance.creative_studio[0].name
    zone = var.zone
    ssh  = "gcloud compute ssh ${google_compute_instance.creative_studio[0].name} --zone ${var.zone} --project ${var.project_id} --tunnel-through-iap"
  } : null
}
