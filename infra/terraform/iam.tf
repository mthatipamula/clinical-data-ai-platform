# One service account per workload, each with the narrowest roles that let it do
# its job. No workload holds a project-level editor or owner role.
locals {
  workloads = {
    gateway    = "Ingest gateway and identity resolution"
    harmonizer = "Dataflow harmonization and measures"
    api        = "Data product API"
    ops-agent  = "AI operations agent (config/agents.json: ops-triage-agent)"
    deployer   = "Release pipeline"
  }
  # Kubernetes service accounts allowed to act as each Google service account.
  ksa = { gateway = "cdap/gateway", api = "cdap/api", "ops-agent" = "cdap/ops-agent" }
}

resource "google_service_account" "sa" {
  for_each     = local.workloads
  account_id   = "sa-${each.key}"
  display_name = each.value
}

resource "google_service_account_iam_member" "workload_identity" {
  for_each           = local.ksa
  service_account_id = google_service_account.sa[each.key].name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${var.project_id}.svc.id.goog[${each.value}]"
  depends_on         = [google_container_cluster.main]
}

# --- gateway: write raw objects, publish events, read/write the identity index
resource "google_storage_bucket_iam_member" "gateway_raw" {
  bucket = google_storage_bucket.raw.name
  role   = "roles/storage.objectCreator"
  member = google_service_account.sa["gateway"].member
}

resource "google_pubsub_topic_iam_member" "gateway_publish" {
  topic  = google_pubsub_topic.raw_ingest.id
  role   = "roles/pubsub.publisher"
  member = google_service_account.sa["gateway"].member
}

resource "google_spanner_database_iam_member" "gateway_mpi" {
  instance = google_spanner_instance.mpi.name
  database = google_spanner_database.mpi.name
  role     = "roles/spanner.databaseUser"
  member   = google_service_account.sa["gateway"].member
}

# --- harmonizer: read raw, consume events, write harmonized data
resource "google_storage_bucket_iam_member" "harmonizer_raw" {
  bucket = google_storage_bucket.raw.name
  role   = "roles/storage.objectViewer"
  member = google_service_account.sa["harmonizer"].member
}

resource "google_storage_bucket_iam_member" "harmonizer_out" {
  bucket = google_storage_bucket.harmonized.name
  role   = "roles/storage.objectAdmin"
  member = google_service_account.sa["harmonizer"].member
}

resource "google_pubsub_subscription_iam_member" "harmonizer_sub" {
  subscription = google_pubsub_subscription.harmonizer.id
  role         = "roles/pubsub.subscriber"
  member       = google_service_account.sa["harmonizer"].member
}

resource "google_bigquery_dataset_iam_member" "harmonizer_write" {
  for_each   = toset(["harmonized", "products"])
  dataset_id = google_bigquery_dataset.ds[each.value].dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = google_service_account.sa["harmonizer"].member
}

resource "google_project_iam_member" "harmonizer_worker" {
  project = var.project_id
  role    = "roles/dataflow.worker"
  member  = google_service_account.sa["harmonizer"].member
}

# --- api: read data products, append audit, call Vertex AI
resource "google_bigquery_dataset_iam_member" "api_read" {
  for_each   = toset(["harmonized", "products"])
  dataset_id = google_bigquery_dataset.ds[each.value].dataset_id
  role       = "roles/bigquery.dataViewer"
  member     = google_service_account.sa["api"].member
}

resource "google_bigquery_dataset_iam_member" "api_audit" {
  dataset_id = google_bigquery_dataset.ds["audit"].dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = google_service_account.sa["api"].member
}

resource "google_project_iam_member" "api_roles" {
  for_each = toset(["roles/bigquery.jobUser", "roles/aiplatform.user"])
  project  = var.project_id
  role     = each.value
  member   = google_service_account.sa["api"].member
}

# --- ops agent: read-only diagnostics. It holds no role that can change the
# platform. Changes it proposes are carried out by the deployer identity after a
# human approves, so stolen agent credentials cannot scale, roll back or delete.
resource "google_project_iam_member" "ops_agent_read" {
  for_each = toset(["roles/monitoring.viewer", "roles/logging.viewer", "roles/dataflow.viewer", "roles/pubsub.viewer", "roles/bigquery.resourceViewer"])
  project  = var.project_id
  role     = each.value
  member   = google_service_account.sa["ops-agent"].member
}

resource "google_project_iam_member" "ops_agent_vertex" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = google_service_account.sa["ops-agent"].member
}

# --- deployer: the only identity that changes running workloads
resource "google_project_iam_member" "deployer" {
  for_each = toset(["roles/container.developer", "roles/dataflow.developer", "roles/artifactregistry.writer"])
  project  = var.project_id
  role     = each.value
  member   = google_service_account.sa["deployer"].member
}

# Separation of duties: approvers may trigger the deployer; they cannot become
# any data-plane identity, and engineers who write changes are not in this group.
resource "google_service_account_iam_member" "approvers_use_deployer" {
  count              = var.approver_group == "" ? 0 : 1
  service_account_id = google_service_account.sa["deployer"].name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = var.approver_group
}
