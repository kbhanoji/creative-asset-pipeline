# All values are rendered by `cap infra render` from the customer YAML into
# terraform.<customer>-<env>.tfvars.json. Don't edit that file by hand.

variable "customer_id" { type = string }
variable "environment" { type = string }
variable "project_id" { type = string }
variable "region" { type = string }
variable "zone" { type = string }
variable "labels" {
  type    = map(string)
  default = {}
}

variable "bigquery_location" { type = string }
variable "dataset" { type = string }
variable "deletion_protection" {
  type    = bool
  default = true
}

variable "buckets" {
  description = "purpose => bucket name"
  type        = map(string)
}
variable "landing_retention_days" { type = number }
variable "quarantine_retention_days" { type = number }
variable "audit_retention_days" { type = number }
variable "lock_audit_retention" { type = bool }

variable "iam_members" {
  description = "Flattened persona grants: [{member, role}]"
  type        = list(object({ member = string, role = string }))
  default     = []
}

variable "creative_studio" {
  type = object({
    enabled         = bool
    machine_type    = string
    boot_disk_gb    = number
    image_family    = string
    image_project   = string
    public_ip       = bool
    repo_url        = string
    repo_ref        = string
    install_command = string
    ports           = list(number)
  })
}

variable "gcc_idle_stop" {
  description = "Dev: lineage router may stop Creative Studio's Cloud SQL when idle (monitoring.viewer + cloudsql.editor)"
  type        = bool
  default     = false
}

variable "container_images_keep" {
  description = "Artifact Registry cleanup: keep N most recent images (older than 1 day deleted). 0 = keep all."
  type        = number
  default     = 5
}
