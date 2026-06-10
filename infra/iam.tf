data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

# ---- Wallet role: DynamoDB read, invoke Token, put events, read audit. No KMS, no PAN.
resource "aws_iam_role" "wallet" {
  name               = "${local.name}-wallet-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = local.tags
}

resource "aws_iam_role_policy_attachment" "wallet_logs" {
  role       = aws_iam_role.wallet.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "wallet" {
  statement {
    sid       = "WalletDdbRead"
    actions   = ["dynamodb:Query", "dynamodb:GetItem"]
    resources = [aws_dynamodb_table.wallet.arn]
  }
  statement {
    sid       = "InvokeToken"
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.token.arn]
  }
  statement {
    sid       = "PutEvents"
    actions   = ["events:PutEvents"]
    resources = [aws_cloudwatch_event_bus.wallet.arn]
  }
  statement {
    sid       = "ReadAudit"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.audit.arn]
  }
  statement {
    sid       = "GetAuditObjects"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.audit.arn}/*"]
  }
}

resource "aws_iam_role_policy" "wallet" {
  name   = "${local.name}-wallet-policy"
  role   = aws_iam_role.wallet.id
  policy = data.aws_iam_policy_document.wallet.json
}

# ---- Token role (CDE): KMS on the CMK, DynamoDB get/put, S3 put audit, invoke Issuer.
resource "aws_iam_role" "token" {
  name               = "${local.name}-token-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = local.tags
}

resource "aws_iam_role_policy_attachment" "token_logs" {
  role       = aws_iam_role.token.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "token" {
  statement {
    sid       = "KmsUse"
    actions   = ["kms:Encrypt", "kms:Decrypt", "kms:GenerateDataKey"]
    resources = [aws_kms_key.pan.arn]
  }
  statement {
    sid       = "TokenDdb"
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem"]
    resources = [aws_dynamodb_table.wallet.arn]
  }
  statement {
    sid       = "PutAudit"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.audit.arn}/*"]
  }
  statement {
    sid       = "ReadAuditHead"
    actions   = ["dynamodb:GetItem"]
    resources = [aws_dynamodb_table.wallet.arn]
  }
  statement {
    sid       = "InvokeIssuer"
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.issuer.arn]
  }
}

resource "aws_iam_role_policy" "token" {
  name   = "${local.name}-token-policy"
  role   = aws_iam_role.token.id
  policy = data.aws_iam_policy_document.token.json
}

# ---- Issuer role: logs only. No persistence, no special IAM.
resource "aws_iam_role" "issuer" {
  name               = "${local.name}-issuer-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = local.tags
}

resource "aws_iam_role_policy_attachment" "issuer_logs" {
  role       = aws_iam_role.issuer.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}
