# Ingest events carry a pointer to the raw object, never the payload.
resource "google_pubsub_topic" "raw_ingest" {
  name                       = "${local.name}-raw-ingest"
  kms_key_name               = google_kms_crypto_key.store["pubsub"].id
  message_retention_duration = "604800s"
  labels                     = local.labels

  message_storage_policy {
    allowed_persistence_regions = [var.region]
  }
  depends_on = [google_kms_crypto_key_iam_member.service_agent]
}

resource "google_pubsub_topic" "dead_letter" {
  name         = "${local.name}-raw-ingest-dead-letter"
  kms_key_name = google_kms_crypto_key.store["pubsub"].id
  labels       = local.labels

  message_storage_policy {
    allowed_persistence_regions = [var.region]
  }
  depends_on = [google_kms_crypto_key_iam_member.service_agent]
}

resource "google_pubsub_subscription" "harmonizer" {
  name                         = "${local.name}-raw-ingest-harmonizer"
  topic                        = google_pubsub_topic.raw_ingest.id
  ack_deadline_seconds         = 120
  enable_message_ordering      = true
  enable_exactly_once_delivery = true
  labels                       = local.labels

  dead_letter_policy {
    dead_letter_topic     = google_pubsub_topic.dead_letter.id
    max_delivery_attempts = 5
  }
  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "600s"
  }
}

resource "google_pubsub_subscription" "dead_letter_review" {
  name                       = "${local.name}-dead-letter-review"
  topic                      = google_pubsub_topic.dead_letter.id
  message_retention_duration = "1209600s"
  labels                     = local.labels
}
