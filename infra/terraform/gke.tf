# Private Autopilot cluster for the gateway, API and web app. Workloads reach
# Google APIs as their own service accounts through Workload Identity; there are
# no node credentials to steal and no key files to rotate.
resource "google_container_cluster" "main" {
  name                = local.name
  location            = var.region
  enable_autopilot    = true
  network             = google_compute_network.vpc.id
  subnetwork          = google_compute_subnetwork.main.id
  deletion_protection = true
  resource_labels     = local.labels

  ip_allocation_policy {
    cluster_secondary_range_name  = "pods"
    services_secondary_range_name = "services"
  }
  private_cluster_config {
    enable_private_nodes    = true
    enable_private_endpoint = true
    master_ipv4_cidr_block  = "172.16.0.0/28"
  }
  master_authorized_networks_config {
    cidr_blocks {
      cidr_block   = google_compute_subnetwork.main.ip_cidr_range
      display_name = "in-vpc deploy runners"
    }
  }
  release_channel {
    channel = "REGULAR"
  }
  # Only images built and signed by the release pipeline may run.
  binary_authorization {
    evaluation_mode = "PROJECT_SINGLETON_POLICY_ENFORCE"
  }
  depends_on = [google_project_service.enabled]
}
