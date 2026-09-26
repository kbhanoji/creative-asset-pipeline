# Private network for the Creative Studio VM: no public IP by default, SSH/UI through IAP,
# outbound internet (GitHub, container registries) through Cloud NAT.
resource "google_compute_network" "vpc" {
  count                   = var.creative_studio.enabled ? 1 : 0
  project                 = var.project_id
  name                    = "cap-vpc"
  auto_create_subnetworks = false
  depends_on              = [google_project_service.apis]
}

resource "google_compute_subnetwork" "subnet" {
  count                    = var.creative_studio.enabled ? 1 : 0
  project                  = var.project_id
  name                     = "cap-subnet-${var.region}"
  region                   = var.region
  network                  = google_compute_network.vpc[0].id
  ip_cidr_range            = "10.10.0.0/24"
  private_ip_google_access = true
}

resource "google_compute_router" "router" {
  count   = var.creative_studio.enabled ? 1 : 0
  project = var.project_id
  name    = "cap-router"
  region  = var.region
  network = google_compute_network.vpc[0].id
}

resource "google_compute_router_nat" "nat" {
  count                              = var.creative_studio.enabled ? 1 : 0
  project                            = var.project_id
  name                               = "cap-nat"
  router                             = google_compute_router.router[0].name
  region                             = var.region
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"
}

resource "google_compute_firewall" "iap" {
  count         = var.creative_studio.enabled ? 1 : 0
  project       = var.project_id
  name          = "cap-allow-iap"
  network       = google_compute_network.vpc[0].name
  source_ranges = ["35.235.240.0/20"] # Google IAP TCP forwarding range
  target_tags   = ["creative-studio"]
  allow {
    protocol = "tcp"
    ports    = [for p in var.creative_studio.ports : tostring(p)]
  }
}
