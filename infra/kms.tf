# Customer-managed CMK used to encrypt the PAN. The key policy grants
# Encrypt/Decrypt only to the Token Service role (PCI Req 3.4 / least privilege).
# Account root retains administrative access so the key remains manageable.
resource "aws_kms_key" "pan" {
  description             = "${local.name} PAN encryption CMK"
  enable_key_rotation     = true
  deletion_window_in_days = 7

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AccountAdmin"
        Effect    = "Allow"
        Principal = { AWS = "arn:aws:iam::${local.account_id}:root" }
        Action    = "kms:*"
        Resource  = "*"
      },
      {
        Sid       = "TokenServiceUse"
        Effect    = "Allow"
        Principal = { AWS = aws_iam_role.token.arn }
        Action    = ["kms:Encrypt", "kms:Decrypt", "kms:GenerateDataKey"]
        Resource  = "*"
      }
    ]
  })

  tags = local.tags
}

resource "aws_kms_alias" "pan" {
  name          = "alias/${local.name}-pan"
  target_key_id = aws_kms_key.pan.key_id
}
