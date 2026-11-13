# Private network. Nothing has a public IP; egress goes through Cloud NAT and
# Google APIs are reached over Private Google Access.
resource "google_compute_network" "vpc" {
  name                    = local.name
  auto_create_subnetworks = false
  depends_on              = [google_project_service.enabled]
}

resource "google_compute_subnetwork" "main" {
  name                     = "${local.name}-${var.region}"
  network                  = google_compute_network.vpc.id
  region                   = var.region
  ip_cidr_range            = "10.10.0.0/20"
  private_ip_google_access = true

  secondary_ip_range {
    range_name    = "pods"
    ip_cidr_range = "10.20.0.0/16"
  }
  secondary_ip_range {
    range_name    = "services"
    ip_cidr_range = "10.30.0.0/20"
  }

  log_config {
    aggregation_interval = "INTERVAL_10_MIN"
    flow_sampling        = 0.5
    metadata             = "INCLUDE_ALL_METADATA"
  }
}

resource "google_compute_router" "nat" {
  name    = local.name
  network = google_compute_network.vpc.id
  region  = var.region
}

resource "google_compute_router_nat" "nat" {
  name                               = local.name
  router                             = google_compute_router.nat.name
  region                             = var.region
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"

  log_config {
    enable = true
    filter = "ERRORS_ONLY"
  }
}

# Default deny. Workloads talk to each other through the mesh, on explicit rules.
resource "google_compute_firewall" "deny_ingress" {
  name          = "${local.name}-deny-all-ingress"
  network       = google_compute_network.vpc.id
  direction     = "INGRESS"
  priority      = 65000
  source_ranges = ["0.0.0.0/0"]
  deny {
    protocol = "all"
  }
}

resource "google_compute_firewall" "allow_internal" {
  name          = "${local.name}-allow-internal"
  network       = google_compute_network.vpc.id
  direction     = "INGRESS"
  priority      = 1000
  source_ranges = ["10.10.0.0/20", "10.20.0.0/16"]
  allow {
    protocol = "tcp"
    ports    = ["8080", "8081", "8000", "12345-12346"] # services, gateway, API, Dataflow shuffle
  }
}
