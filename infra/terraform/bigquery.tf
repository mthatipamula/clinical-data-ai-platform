# Three datasets, three blast radii. `harmonized` holds FHIR with full
# provenance; `products` holds what the applications read; `audit` holds the
# evidence trail and is writable only by the services that produce it.
resource "google_bigquery_dataset" "ds" {
  for_each   = toset(["harmonized", "products", "audit"])
  dataset_id = "${replace(local.name, "-", "_")}_${each.value}"
  location   = var.region
  labels     = local.labels

  default_encryption_configuration {
    kms_key_name = google_kms_crypto_key.store["bigquery"].id
  }
  depends_on = [google_kms_crypto_key_iam_member.service_agent]
}

resource "google_bigquery_table" "fhir_resources" {
  dataset_id          = google_bigquery_dataset.ds["harmonized"].dataset_id
  table_id            = "fhir_resources"
  deletion_protection = true
  labels              = local.labels
  clustering          = ["eid", "resource_type"]

  time_partitioning {
    type  = "DAY"
    field = "harmonized_at"
  }
  require_partition_filter = true

  schema = jsonencode([
    { name = "resource_type", type = "STRING", mode = "REQUIRED" },
    { name = "resource_id", type = "STRING", mode = "REQUIRED" },
    { name = "eid", type = "STRING", mode = "REQUIRED", description = "Enterprise ID from the master patient index" },
    { name = "source_system", type = "STRING", mode = "REQUIRED" },
    { name = "tenant", type = "STRING", mode = "REQUIRED" },
    { name = "ingest_id", type = "STRING", mode = "REQUIRED", description = "Raw payload this record was derived from" },
    { name = "raw_sha256", type = "STRING", mode = "REQUIRED" },
    { name = "pipeline_version", type = "STRING", mode = "REQUIRED" },
    { name = "harmonized_at", type = "TIMESTAMP", mode = "REQUIRED" },
    { name = "body", type = "JSON", mode = "REQUIRED", description = "FHIR R4 resource" },
  ])
}

resource "google_bigquery_table" "measure_results" {
  dataset_id          = google_bigquery_dataset.ds["products"].dataset_id
  table_id            = "measure_results"
  deletion_protection = true
  labels              = local.labels
  clustering          = ["measure_id", "eid"]

  schema = jsonencode([
    { name = "eid", type = "STRING", mode = "REQUIRED" },
    { name = "measure_id", type = "STRING", mode = "REQUIRED" },
    { name = "measure_version", type = "STRING", mode = "REQUIRED" },
    { name = "period_start", type = "DATE" },
    { name = "period_end", type = "DATE" },
    { name = "numerator", type = "BOOL", mode = "REQUIRED" },
    { name = "inverse", type = "BOOL", mode = "REQUIRED" },
    { name = "detail", type = "STRING" },
    { name = "evidence", type = "STRING", mode = "REPEATED" },
  ])
}

resource "google_bigquery_table" "audit_log" {
  dataset_id          = google_bigquery_dataset.ds["audit"].dataset_id
  table_id            = "audit_log"
  deletion_protection = true
  labels              = local.labels

  time_partitioning {
    type  = "DAY"
    field = "ts"
  }

  schema = jsonencode([
    { name = "seq", type = "INT64", mode = "REQUIRED" },
    { name = "ts", type = "TIMESTAMP", mode = "REQUIRED" },
    { name = "actor", type = "STRING", mode = "REQUIRED" },
    { name = "actor_type", type = "STRING", mode = "REQUIRED", description = "user, agent or service" },
    { name = "action", type = "STRING", mode = "REQUIRED" },
    { name = "resource", type = "STRING", mode = "REQUIRED" },
    { name = "purpose", type = "STRING" },
    { name = "outcome", type = "STRING", mode = "REQUIRED" },
    { name = "detail", type = "JSON" },
    { name = "prev_hash", type = "STRING", mode = "REQUIRED" },
    { name = "hash", type = "STRING", mode = "REQUIRED" },
  ])
}

# Google's own audit logs (who called which API) land beside ours.
resource "google_logging_project_sink" "audit" {
  name                   = "${local.name}-audit"
  destination            = "bigquery.googleapis.com/projects/${var.project_id}/datasets/${google_bigquery_dataset.ds["audit"].dataset_id}"
  filter                 = "logName:\"cloudaudit.googleapis.com\""
  unique_writer_identity = true

  bigquery_options {
    use_partitioned_tables = true
  }
}

resource "google_bigquery_dataset_iam_member" "sink_writer" {
  dataset_id = google_bigquery_dataset.ds["audit"].dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = google_logging_project_sink.audit.writer_identity
}

# Record data access, not only admin activity, for the services that hold PHI.
resource "google_project_iam_audit_config" "data_access" {
  for_each = toset(["storage.googleapis.com", "bigquery.googleapis.com", "spanner.googleapis.com", "aiplatform.googleapis.com"])
  project  = var.project_id
  service  = each.value

  audit_log_config {
    log_type = "DATA_READ"
  }
  audit_log_config {
    log_type = "DATA_WRITE"
  }
}
