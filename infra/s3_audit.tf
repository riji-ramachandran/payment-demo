# Tamper-evident audit bucket: Object Lock (compliance mode), versioning,
# full public-access block. Records written by the Token Service cannot be
# altered or deleted before their retention date (PCI Req 10).
resource "aws_s3_bucket" "audit" {
  bucket              = local.audit_bucket
  object_lock_enabled = true
  force_destroy       = false

  tags = local.tags
}

resource "aws_s3_bucket_versioning" "audit" {
  bucket = aws_s3_bucket.audit.id
  versioning_configuration {
    status = "Enabled"
  }
}

# Default retention so even direct PutObject calls are locked; the app also
# sets an explicit RetainUntilDate per object.
resource "aws_s3_bucket_object_lock_configuration" "audit" {
  bucket = aws_s3_bucket.audit.id

  rule {
    default_retention {
      mode = "COMPLIANCE"
      days = var.audit_retain_days
    }
  }
}

resource "aws_s3_bucket_public_access_block" "audit" {
  bucket                  = aws_s3_bucket.audit.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# SSE-S3 (not the PAN CMK): audit records carry no cardholder data, and the
# Wallet role (which reads them for /audit) must hold no KMS permissions.
resource "aws_s3_bucket_server_side_encryption_configuration" "audit" {
  bucket = aws_s3_bucket.audit.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}
