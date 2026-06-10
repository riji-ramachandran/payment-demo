output "api_base_url" {
  description = "Base URL of the HTTP API."
  value       = aws_apigatewayv2_stage.default.invoke_url
}

output "user_pool_id" {
  description = "Cognito User Pool id."
  value       = aws_cognito_user_pool.this.id
}

output "app_client_id" {
  description = "Cognito app client id (JWT audience)."
  value       = aws_cognito_user_pool_client.this.id
}

output "audit_bucket" {
  description = "S3 audit bucket name."
  value       = aws_s3_bucket.audit.id
}

output "demo_user_email" {
  description = "Seeded demo username."
  value       = var.demo_user_email
}

# Helper to mint a demo JWT once deployed:
# aws cognito-idp initiate-auth --auth-flow USER_PASSWORD_AUTH \
#   --client-id <app_client_id> \
#   --auth-parameters USERNAME=<demo_user_email>,PASSWORD=<demo_user_password> \
#   --query 'AuthenticationResult.IdToken' --output text
output "get_token_hint" {
  description = "Command to obtain a demo IdToken."
  value       = "aws cognito-idp initiate-auth --auth-flow USER_PASSWORD_AUTH --client-id ${aws_cognito_user_pool_client.this.id} --auth-parameters USERNAME=${var.demo_user_email},PASSWORD=<password> --query AuthenticationResult.IdToken --output text"
}
