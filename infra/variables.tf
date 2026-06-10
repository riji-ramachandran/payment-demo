variable "region" {
  description = "AWS region to deploy into."
  type        = string
  default     = "eu-west-1"
}

variable "project" {
  description = "Name prefix for all resources."
  type        = string
  default     = "calm-pci-wallet"
}

variable "demo_user_email" {
  description = "Email for the seeded Cognito demo user (also used as username)."
  type        = string
  default     = "demo@example.com"
}

variable "demo_user_password" {
  description = "Permanent password for the seeded Cognito demo user (>= 8 chars, upper/lower/number)."
  type        = string
  default     = "Demo!Pass123"
  sensitive   = true
}

variable "notification_email" {
  description = "Email subscribed to the payment-notifications SNS topic. Leave empty to skip the subscription."
  type        = string
  default     = ""
}

variable "audit_retain_days" {
  description = "Object Lock retention for audit objects, in days. 1 for demos; ~2555 (7y) for real PCI retention."
  type        = number
  default     = 1
}
