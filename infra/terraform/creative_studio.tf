# Google Creative Studio (GCC) on a dedicated VM (SOW 8.1), from a pinned repo ref (TDD ADR-10).
data "google_compute_image" "os" {
  count   = var.creative_studio.enabled ? 1 : 0
  family  = var.creative_studio.image_family
  project = var.creative_studio.image_project
}

resource "google_compute_instance" "creative_studio" {
  count        = var.creative_studio.enabled ? 1 : 0
  project      = var.project_id
  name         = "creative-studio-${var.environment}"
  zone         = var.zone
  machine_type = var.creative_studio.machine_type
  tags         = ["creative-studio"]
  labels       = local.labels

  boot_disk {
    initialize_params {
      image = data.google_compute_image.os[0].self_link
      size  = var.creative_studio.boot_disk_gb
      type  = "pd-balanced"
    }
  }

  network_interface {
    subnetwork = google_compute_subnetwork.subnet[0].id
    dynamic "access_config" {
      for_each = var.creative_studio.public_ip ? [1] : []
      content {}
    }
  }

  service_account {
    email  = google_service_account.sa["sa-creative-studio"].email
    scopes = ["cloud-platform"]
  }

  shielded_instance_config {
    enable_secure_boot          = true
    enable_vtpm                 = true
    enable_integrity_monitoring = true
  }

  metadata = {
    enable-oslogin = "TRUE"
    startup-script = templatefile("${path.module}/../scripts/creative_studio_startup.sh.tftpl", {
      repo_url        = var.creative_studio.repo_url
      repo_ref        = var.creative_studio.repo_ref
      install_command = var.creative_studio.install_command
      project_id      = var.project_id
      region          = var.region
      output_bucket   = var.buckets["creative-studio"]
    })
  }

  allow_stopping_for_update = true
  depends_on                = [google_compute_router_nat.nat]
}
