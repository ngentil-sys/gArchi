terraform {
  required_version = ">= 1.9.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 8.7"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = "~> 8.7"
    }
  }

  # State lives in GCS: terraform init -backend-config="bucket=<project>-tfstate" -backend-config="prefix=garchi"
  backend "gcs" {}
}

provider "google" {
  project               = var.project_id
  region                = var.region
  default_labels        = local.labels
  user_project_override = true
  billing_project       = var.project_id
}

provider "google-beta" {
  project               = var.project_id
  region                = var.region
  default_labels        = local.labels
  user_project_override = true
  billing_project       = var.project_id
}
