# Raw zone: payloads exactly as received, kept for replay and for audit.
resource "google_storage_bucket" "raw" {
  name                        = "${var.project_id}-${local.name}-raw"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  labels                      = local.labels

  versioning {
    enabled = true
  }
  encryption {
    default_kms_key_name = google_kms_crypto_key.store["storage"].id
  }
  retention_policy {
    retention_period = var.raw_retention_days * 86400
  }
  lifecycle_rule {
    condition {
      age = 90
    }
    action {
      type          = "SetStorageClass"
      storage_class = "COLDLINE"
    }
  }
  depends_on = [google_kms_crypto_key_iam_member.service_agent]
}

# Harmonized zone and Dataflow staging.
resource "google_storage_bucket" "harmonized" {
  name                        = "${var.project_id}-${local.name}-harmonized"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  labels                      = local.labels

  versioning {
    enabled = true
  }
  encryption {
    default_kms_key_name = google_kms_crypto_key.store["storage"].id
  }
  depends_on = [google_kms_crypto_key_iam_member.service_agent]
}
