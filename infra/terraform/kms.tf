# Customer-managed keys. One key per data store, so access to a store can be
# revoked by disabling its key without touching the others.
resource "google_kms_key_ring" "cdap" {
  name       = local.name
  location   = var.region
  depends_on = [google_project_service.enabled]
}

resource "google_kms_crypto_key" "store" {
  for_each        = toset(["storage", "bigquery", "pubsub", "spanner"])
  name            = each.value
  key_ring        = google_kms_key_ring.cdap.id
  rotation_period = "7776000s" # 90 days
  labels          = local.labels

  lifecycle {
    prevent_destroy = true
  }
}

locals {
  service_agents = {
    storage  = "serviceAccount:service-${data.google_project.this.number}@gs-project-accounts.iam.gserviceaccount.com"
    bigquery = "serviceAccount:bq-${data.google_project.this.number}@bigquery-encryption.iam.gserviceaccount.com"
    pubsub   = "serviceAccount:service-${data.google_project.this.number}@gcp-sa-pubsub.iam.gserviceaccount.com"
    spanner  = "serviceAccount:service-${data.google_project.this.number}@gcp-sa-spanner.iam.gserviceaccount.com"
  }
}

resource "google_kms_crypto_key_iam_member" "service_agent" {
  for_each      = local.service_agents
  crypto_key_id = google_kms_crypto_key.store[each.key].id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = each.value
}
