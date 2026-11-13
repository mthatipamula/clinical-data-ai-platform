# VPC Service Controls: even a valid credential cannot read PHI from outside the
# perimeter or copy it to a project outside it. Created only when an access
# policy is supplied, because it needs organization-level rights.
locals {
  restricted_services = [
    "storage.googleapis.com", "bigquery.googleapis.com", "spanner.googleapis.com",
    "pubsub.googleapis.com", "aiplatform.googleapis.com", "dataflow.googleapis.com",
    "cloudkms.googleapis.com", "artifactregistry.googleapis.com", "container.googleapis.com",
    "logging.googleapis.com",
  ]
  perimeter_config = {
    resources           = ["projects/${data.google_project.this.number}"]
    restricted_services = local.restricted_services
  }
}

resource "google_access_context_manager_service_perimeter" "phi" {
  count  = var.access_policy_id == "" ? 0 : 1
  parent = "accessPolicies/${var.access_policy_id}"
  name   = "accessPolicies/${var.access_policy_id}/servicePerimeters/${replace(local.name, "-", "_")}_phi"
  title  = "${local.name} PHI perimeter"

  use_explicit_dry_run_spec = var.perimeter_dry_run

  dynamic "spec" {
    for_each = var.perimeter_dry_run ? [local.perimeter_config] : []
    content {
      resources           = spec.value.resources
      restricted_services = spec.value.restricted_services
      vpc_accessible_services {
        enable_restriction = true
        allowed_services   = ["RESTRICTED-SERVICES"]
      }
    }
  }

  dynamic "status" {
    for_each = var.perimeter_dry_run ? [] : [local.perimeter_config]
    content {
      resources           = status.value.resources
      restricted_services = status.value.restricted_services
      vpc_accessible_services {
        enable_restriction = true
        allowed_services   = ["RESTRICTED-SERVICES"]
      }
    }
  }
}
