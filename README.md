# Tokenized Card Wallet — AWS Serverless (Python + Terraform)

A minimal PCI-shaped card wallet: add a card and the PAN is tokenized,
encrypted with KMS, and stored as ciphertext + `last4` only; pay and the PAN is
briefly detokenized to ask a mock issuer for authorization; every access to
cardholder data is written to a hash-chained, Object-Locked audit log.

Built per [`private/BUILD_SPEC.md`](private/BUILD_SPEC.md). Runtime is Python
3.12 on Lambda; infrastructure is Terraform. (CALM modelling and CI are out of
scope for this build.)

## Layout

```
services/
  common/{kms,vault,audit,events}.py   shared modules (packaged into each fn)
  wallet/handler.py                    HTTP routing + orchestration (no PAN)
  token/handler.py                     tokenize / authorize — the only PAN holder (CDE)
  issuer/handler.py                    mock issuer: approve / decline
infra/
  *.tf                                 Terraform: DynamoDB, KMS, S3 Object Lock,
                                       Cognito, 3 Lambdas, HTTP API, EventBridge, SNS, IAM
  build.sh                             assembles Lambda zips (handler + common/)
```

## Architecture

```
Client ──TLS──▶ API Gateway (HTTP API) ──JWT(Cognito)──▶ Wallet Lambda
                                                            │  invoke
                                                            ▼
                                                         Token Lambda (CDE)
                                            KMS encrypt/decrypt │ DynamoDB │ S3 audit
                                                            │  invoke
                                                            ▼
                                                         Issuer Lambda
   Wallet ──▶ EventBridge (wallet-events) ──▶ SNS (payment-notifications)
```

- **PAN never leaves the Token Service in plaintext.** Wallet forwards the
  request body via a direct `lambda.invoke`; the PAN never re-enters API Gateway,
  is never logged, and is never returned.
- **Least privilege:** Wallet has no KMS; Token has KMS only on the one CMK;
  Issuer has logs only. No `*` resources on KMS or DynamoDB.
- **Audit:** SHA-256 hash chain, head pointer in DynamoDB, records in S3 with
  Object Lock (compliance mode). Tampering breaks `verify()`.

## Prerequisites

- Terraform >= 1.5, AWS credentials with permission to create the resources.
- AWS CLI (for minting a demo JWT and smoke tests).
- An Object-Lock-capable region (default `eu-west-1`).

## Deploy

```bash
cd infra
cp terraform.tfvars.example terraform.tfvars   # edit as needed
terraform init
terraform apply
```

Useful outputs: `api_base_url`, `app_client_id`, `demo_user_email`,
`audit_bucket`, `get_token_hint`.

## Smoke test

```bash
API=$(terraform -chdir=infra output -raw api_base_url)
CLIENT=$(terraform -chdir=infra output -raw app_client_id)
EMAIL=$(terraform -chdir=infra output -raw demo_user_email)

# 1) liveness (no auth)
curl -s "$API/health"

# 2) mint a demo JWT (password from your tfvars)
JWT=$(aws cognito-idp initiate-auth --auth-flow USER_PASSWORD_AUTH \
  --client-id "$CLIENT" \
  --auth-parameters USERNAME="$EMAIL",PASSWORD='Demo!Pass123' \
  --query 'AuthenticationResult.IdToken' --output text)
AUTH=(-H "Authorization: Bearer $JWT")

# 3) add a card — PAN goes in, only a token comes back
curl -s "${AUTH[@]}" -X POST "$API/cards" \
  -d '{"pan":"4242424242424242","exp":"12/27"}'

# 4) list tokenized cards (never PAN/ciphertext)
curl -s "${AUTH[@]}" "$API/wallet"

# 5) pay — approves <= 1000, declines (402) over limit
curl -s "${AUTH[@]}" -X POST "$API/payments" \
  -d '{"token":"tok_...","amount":49.99}'

# 6) audit chain — verify.ok must be true
curl -s "${AUTH[@]}" "$API/audit"
```

### What to verify (acceptance)

- `POST /cards` response and the DynamoDB item contain **no PAN** — only
  `ciphertext`, `last4`, `brand`, `exp`. Check CloudWatch logs: no PAN.
- `POST /payments` approves under issuer rules, declines over 1000 with a
  `reason`. PAN is decrypted only in the Token Service.
- `GET /audit` → `verify.ok == true`; mutating any S3 record breaks it.
- Audit objects cannot be deleted/overwritten before the retention date
  (Object Lock).

## Local testing (no AWS account)

Two suites exercise the **real handler code** without deploying:

```bash
# fast, zero-dependency: real handlers over an in-memory fake AWS
python3 tests/local_e2e.py

# higher fidelity: real handlers over LocalStack (real KMS/DynamoDB/S3/EventBridge)
chmod +x tests/run_localstack.sh
tests/run_localstack.sh           # boots LocalStack, runs both, tears down
tests/run_localstack.sh --keep    # leave LocalStack running
```

The LocalStack suite proves the PCI-critical behaviour for real: KMS ciphertext
decrypts back to the PAN, the DynamoDB item holds ciphertext and no PAN, the
audit hash-chain verifies over real S3, and **S3 Object Lock actually blocks
deleting an audit record**. API Gateway + Cognito are LocalStack Pro only, so
the Wallet handler is invoked with the proxy event API Gateway would send and
the inter-Lambda invoke is dispatched in-process. Authorizer/IAM enforcement
and the real Lambda runtime are only covered by a full `terraform apply`.

## Teardown

```bash
terraform -chdir=infra destroy
```

> The audit bucket has Object Lock + versioning and `force_destroy = false`, so
> locked objects block bucket deletion until their `RetainUntilDate` passes. Set
> `audit_retain_days = 1` for demos; for a forced teardown you must wait out the
> retention or set `force_destroy` and remove non-locked versions manually.

## Notes / out of scope

Real card-network connectivity, settlement, a QSA assessment, runtime
attestation, WAF/VPC isolation of the CDE, and the ElastiCache metadata cache
are all out of scope (see `private/BUILD_SPEC.md` §13).
