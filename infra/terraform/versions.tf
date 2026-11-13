terraform {
  required_version = ">= 1.7"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
  # State holds resource metadata for a regulated platform. Keep it in a
  # versioned, CMEK-encrypted bucket with access limited to the deploy pipeline:
  #   terraform init -backend-config="bucket=<state-bucket>" -backend-config="prefix=cdap/<env>"
  backend "gcs" {}
}

provider "google" {
  project = var.project_id
  region  = var.region
}
