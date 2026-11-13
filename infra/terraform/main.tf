locals {
  name   = "cdap-${var.env}"
  labels = merge({ app = "cdap", env = var.env, data-class = "phi" }, var.labels)

  services = [
    "aiplatform.googleapis.com", "artifactregistry.googleapis.com", "bigquery.googleapis.com",
    "cloudkms.googleapis.com", "compute.googleapis.com", "container.googleapis.com",
    "dataflow.googleapis.com", "iam.googleapis.com", "logging.googleapis.com",
    "monitoring.googleapis.com", "pubsub.googleapis.com", "spanner.googleapis.com",
    "storage.googleapis.com", "binaryauthorization.googleapis.com",
  ]
}

data "google_project" "this" {}

resource "google_project_service" "enabled" {
  for_each           = toset(local.services)
  service            = each.value
  disable_on_destroy = false
}

resource "google_artifact_registry_repository" "images" {
  location      = var.region
  repository_id = local.name
  format        = "DOCKER"
  labels        = local.labels
  depends_on    = [google_project_service.enabled]
}
