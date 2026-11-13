output "raw_bucket" {
  value = google_storage_bucket.raw.name
}

output "harmonized_bucket" {
  value = google_storage_bucket.harmonized.name
}

output "raw_topic" {
  value = google_pubsub_topic.raw_ingest.name
}

output "cluster" {
  value = google_container_cluster.main.name
}

output "image_repository" {
  value = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}"
}

output "service_accounts" {
  value = { for k, sa in google_service_account.sa : k => sa.email }
}
