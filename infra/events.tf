# Custom event bus, a rule matching payment outcomes, and an SNS topic that
# fans out receipts. Payloads carry token/amount/result only — never PAN.
resource "aws_cloudwatch_event_bus" "wallet" {
  name = "wallet-events"
  tags = local.tags
}

resource "aws_sns_topic" "payments" {
  name = "payment-notifications"
  tags = local.tags
}

resource "aws_cloudwatch_event_rule" "payments" {
  name           = "${local.name}-payment-events"
  event_bus_name = aws_cloudwatch_event_bus.wallet.name

  event_pattern = jsonencode({
    source        = ["wallet.payments"]
    "detail-type" = ["payment.authorized", "payment.declined"]
  })
}

resource "aws_cloudwatch_event_target" "sns" {
  rule           = aws_cloudwatch_event_rule.payments.name
  event_bus_name = aws_cloudwatch_event_bus.wallet.name
  arn            = aws_sns_topic.payments.arn
}

# Allow EventBridge to publish to the SNS topic.
data "aws_iam_policy_document" "sns_topic" {
  statement {
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.payments.arn]
    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }
  }
}

resource "aws_sns_topic_policy" "payments" {
  arn    = aws_sns_topic.payments.arn
  policy = data.aws_iam_policy_document.sns_topic.json
}

# Optional email subscription for the demo.
resource "aws_sns_topic_subscription" "email" {
  count     = var.notification_email == "" ? 0 : 1
  topic_arn = aws_sns_topic.payments.arn
  protocol  = "email"
  endpoint  = var.notification_email
}
