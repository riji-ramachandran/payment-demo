# Build the deployment packages (handler + shared common/) before zipping.
resource "null_resource" "build" {
  triggers = {
    wallet = filemd5("${path.module}/../services/wallet/handler.py")
    token  = filemd5("${path.module}/../services/token/handler.py")
    issuer = filemd5("${path.module}/../services/issuer/handler.py")
    kms    = filemd5("${path.module}/../services/common/kms.py")
    vault  = filemd5("${path.module}/../services/common/vault.py")
    audit  = filemd5("${path.module}/../services/common/audit.py")
    events = filemd5("${path.module}/../services/common/events.py")
  }

  provisioner "local-exec" {
    command = "bash ${path.module}/build.sh"
  }
}

data "archive_file" "wallet" {
  type        = "zip"
  source_dir  = "${path.module}/build/wallet"
  output_path = "${path.module}/build/wallet.zip"
  depends_on  = [null_resource.build]
}

data "archive_file" "token" {
  type        = "zip"
  source_dir  = "${path.module}/build/token"
  output_path = "${path.module}/build/token.zip"
  depends_on  = [null_resource.build]
}

data "archive_file" "issuer" {
  type        = "zip"
  source_dir  = "${path.module}/build/issuer"
  output_path = "${path.module}/build/issuer.zip"
  depends_on  = [null_resource.build]
}

# ---- Issuer (no dependents in IAM beyond the Token invoke) -----------------
resource "aws_lambda_function" "issuer" {
  function_name    = "${local.name}-issuer"
  role             = aws_iam_role.issuer.arn
  runtime          = "python3.12"
  handler          = "handler.handler"
  filename         = data.archive_file.issuer.output_path
  source_code_hash = data.archive_file.issuer.output_base64sha256
  timeout          = 10
  memory_size      = 128

  tags = local.tags
}

# ---- Token Service (CDE) ---------------------------------------------------
resource "aws_lambda_function" "token" {
  function_name    = "${local.name}-token"
  role             = aws_iam_role.token.arn
  runtime          = "python3.12"
  handler          = "handler.handler"
  filename         = data.archive_file.token.output_path
  source_code_hash = data.archive_file.token.output_base64sha256
  timeout          = 15
  memory_size      = 256

  environment {
    variables = {
      TABLE_NAME        = aws_dynamodb_table.wallet.name
      KMS_KEY_ID        = aws_kms_key.pan.arn
      AUDIT_BUCKET      = aws_s3_bucket.audit.id
      ISSUER_FN         = aws_lambda_function.issuer.function_name
      AUDIT_RETAIN_DAYS = tostring(var.audit_retain_days)
    }
  }

  tags = local.tags
}

# ---- Wallet Service --------------------------------------------------------
resource "aws_lambda_function" "wallet" {
  function_name    = "${local.name}-wallet"
  role             = aws_iam_role.wallet.arn
  runtime          = "python3.12"
  handler          = "handler.handler"
  filename         = data.archive_file.wallet.output_path
  source_code_hash = data.archive_file.wallet.output_base64sha256
  timeout          = 15
  memory_size      = 256

  environment {
    variables = {
      TABLE_NAME   = aws_dynamodb_table.wallet.name
      TOKEN_FN     = aws_lambda_function.token.function_name
      EVENT_BUS    = aws_cloudwatch_event_bus.wallet.name
      AUDIT_BUCKET = aws_s3_bucket.audit.id
    }
  }

  tags = local.tags
}

# Allow API Gateway to invoke the Wallet Service.
resource "aws_lambda_permission" "apigw_wallet" {
  statement_id  = "AllowApiGwInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.wallet.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.http.execution_arn}/*/*"
}
