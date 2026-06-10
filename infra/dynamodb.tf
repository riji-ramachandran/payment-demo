# WalletTable — single-table design holding card items and the audit head
# pointer. On-demand billing, point-in-time recovery on.
resource "aws_dynamodb_table" "wallet" {
  name         = "${local.name}-WalletTable"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "PK"
  range_key    = "SK"

  attribute {
    name = "PK"
    type = "S"
  }

  attribute {
    name = "SK"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  tags = local.tags
}
