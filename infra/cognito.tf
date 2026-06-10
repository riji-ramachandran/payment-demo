# Cognito User Pool + app client. The HTTP API JWT authorizer validates
# tokens from this pool (PCI Req 7/8 at the boundary). One demo user is seeded
# so the live demo can obtain a JWT via USER_PASSWORD_AUTH.
resource "aws_cognito_user_pool" "this" {
  name = "${local.name}-users"

  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]

  password_policy {
    minimum_length    = 8
    require_lowercase = true
    require_uppercase = true
    require_numbers   = true
    require_symbols   = false
  }

  tags = local.tags
}

resource "aws_cognito_user_pool_client" "this" {
  name         = "${local.name}-client"
  user_pool_id = aws_cognito_user_pool.this.id

  generate_secret = false
  explicit_auth_flows = [
    "ALLOW_USER_PASSWORD_AUTH",
    "ALLOW_REFRESH_TOKEN_AUTH",
  ]
}

# Seed a demo user with a permanent password.
resource "aws_cognito_user" "demo" {
  user_pool_id = aws_cognito_user_pool.this.id
  username     = var.demo_user_email
  password     = var.demo_user_password

  attributes = {
    email          = var.demo_user_email
    email_verified = "true"
  }
}
