variable "project_id" {
  type        = string
  description = "Google Cloud project for this environment. One project per environment keeps IAM, quotas and audit logs separate."
}

variable "env" {
  type        = string
  description = "Environment name: dev, stage or prod."
  validation {
    condition     = contains(["dev", "stage", "prod"], var.env)
    error_message = "env must be dev, stage or prod."
  }
}

variable "region" {
  type        = string
  default     = "us-east4"
  description = "Region for all regional resources. PHI stays in US regions."
}

variable "spanner_processing_units" {
  type        = number
  default     = 100
  description = "Spanner capacity for the identity index. 100 PU is the smallest unit; 1000 PU equals one node."
}

variable "raw_retention_days" {
  type        = number
  default     = 2555
  description = "How long raw payloads are retained (default seven years)."
}

variable "access_policy_id" {
  type        = string
  default     = ""
  description = "Access Context Manager policy ID. When set, a VPC Service Controls perimeter is created around the project. Requires organization-level permissions."
}

variable "perimeter_dry_run" {
  type        = bool
  default     = true
  description = "Create the perimeter in dry-run mode first: violations are logged, not blocked. Switch to false once logs are clean."
}

variable "approver_group" {
  type        = string
  default     = ""
  description = "Google group whose members may approve agent actions and production changes, e.g. group:cdap-approvers@example.com."
}

variable "labels" {
  type        = map(string)
  default     = {}
  description = "Extra labels for cost attribution."
}
