# Tokenized Card Wallet — Build Specification

**Audience:** an implementation agent (e.g. Claude Code) building this from scratch.
**Goal:** ship a tokenized card-wallet sample application on an AWS + Python serverless stack, model its architecture in FINOS CALM, and enforce PCI DSS compliance as a validation gate in CI.

This document is the single source of truth for the build. Where it says MUST, treat it as a hard requirement. Where it says SHOULD or STRETCH, implement only if time allows. Do not store, log, or return a raw PAN anywhere except the single in-memory moment described in section 7.4.

A working Node reference implementation already exists at `app/` and a validated CALM model exists at `architecture/`, `controls/`, and `patterns/`. The job is to (a) reimplement the app as Python AWS Lambdas behind real AWS services, (b) provision it with infrastructure as code, and (c) keep the CALM PCI gate green in CI. Reuse the reference behaviour exactly; only the runtime and infrastructure change.

---

## 1. Objective and context

Build the simplest payment application that genuinely exercises PCI DSS controls: a card wallet. A cardholder adds a payment card; the service tokenizes the card number (PAN), encrypts the underlying value with KMS, and persists only a token plus ciphertext. A payment briefly detokenizes to request authorization from an issuer, and every access to cardholder data is written to a tamper-evident audit log.

The architecture is represented in CALM so the design is machine-readable and version-controlled. A CALM pattern encodes PCI DSS requirements 3, 4, 7/8 and 10 as structural rules, so `calm validate` fails the build when the declared architecture is non-compliant.

Honest boundary: CALM validates the *declared* architecture, not the running system. It confirms the model says PAN is encrypted at rest and that this satisfies the PCI pattern. It does not verify the deployed code actually encrypts. Runtime attestation is out of scope (see section 13).

---

## 2. Architecture overview

The rendered diagram is in `docs/architecture.svg` / `docs/architecture.png`. Components:

| Component | AWS service | Role | In PCI scope (CDE) |
|---|---|---|---|
| Customer App | S3 + CloudFront | Static wallet UI | No |
| API Gateway | API Gateway HTTP API | Front door, TLS termination, routing | No (boundary) |
| Authorizer | Cognito User Pool (JWT) | Authenticate and authorize requests | No (boundary) |
| Wallet Service | Lambda (Python 3.12) | Wallet lifecycle, orchestration | No |
| Token Service | Lambda (Python 3.12) | Tokenize / detokenize PAN | Yes |
| Token Vault | DynamoDB | Store token + ciphertext, never PAN | Yes |
| KMS | AWS KMS (CMK) | Encrypt / decrypt PAN | Yes |
| Audit Log | S3 + Object Lock | Tamper-evident, write-once access log | Yes |
| Issuer | Lambda (Python 3.12) | Mock issuer / funding, approve or decline | No |
| Wallet Cache | ElastiCache (Redis) | Non-sensitive metadata cache (STRETCH) | No |
| Events | EventBridge | Emit payment events | No |
| Notifications | SNS | Fan-out payment receipts | No |

Primary flow (add card then pay): Customer App to API Gateway over TLS, JWT verified by Cognito, routed to Wallet Service, which hands the PAN to Token Service. Token Service calls KMS to encrypt, writes the token plus ciphertext to the Token Vault, writes an audit record, and on payment detokenizes to call the Issuer. Payment results are published to EventBridge and on to SNS.

---

## 3. Technology stack

- Runtime: Python 3.12 on AWS Lambda. SDK: `boto3`.
- API: AWS API Gateway HTTP API (not REST API) with a Cognito JWT authorizer.
- Data: DynamoDB (on-demand billing). Crypto: AWS KMS customer-managed key. Audit: S3 with Object Lock in compliance mode and versioning enabled.
- Async: EventBridge custom event bus plus an SNS topic.
- IaC: AWS SAM (`template.yaml`). CDK (Python) is an acceptable alternative if preferred; keep resource names and IAM identical.
- Region: parameterize; default `eu-west-1`.
- CALM: `@finos/calm-cli` (Node), run in CI only.
- No secrets in source. No PAN in CloudWatch logs.

---

## 4. Repository layout

```
calm-pci-wallet/
├── README.md                       # quickstart, demo script, how to contribute
├── architecture/
│   ├── wallet-architecture.json            # CALM model (compliant) — EXISTS, validated
│   └── wallet-architecture-noncompliant.json  # negative test — EXISTS
├── controls/                       # PCI control-requirement schemas — EXIST
│   ├── pci-pan-at-rest.requirement.json
│   ├── pci-encryption-in-transit.requirement.json
│   └── pci-access-control.requirement.json
├── patterns/
│   └── pci-dss.pattern.json        # PCI compliance gate — EXISTS, validated
├── app/                            # Node reference implementation — EXISTS, runnable
│   └── src/{server,tokenVault,kms,issuer,audit}.js
├── services/                       # TO BUILD: Python Lambdas
│   ├── wallet/handler.py
│   ├── token/handler.py
│   ├── issuer/handler.py
│   └── common/{kms.py,vault.py,audit.py,events.py}
├── infra/
│   └── template.yaml               # TO BUILD: SAM template
├── web/                            # TO BUILD: minimal static UI (STRETCH)
├── docs/
│   ├── architecture.svg / .png     # EXISTS
│   └── BUILD_SPEC.md               # this document
└── .github/workflows/calm.yml      # TO BUILD: CI gate
```

---

## 5. API contract

All routes except `/health` require `Authorization: Bearer <JWT>` from the Cognito user pool. The authenticated user id is the Cognito `sub` claim; call it `userId`. Content type is `application/json`.

### POST /cards — add a card
Request:
```json
{ "pan": "4242424242424242", "exp": "12/27" }
```
Response 201:
```json
{ "card": { "token": "tok_ab12...", "last4": "4242", "brand": "visa", "exp": "12/27" } }
```
Rules: the PAN MUST be tokenized and encrypted before any persistence. The response MUST NOT contain the PAN. Validate the PAN is 13–19 digits; reject otherwise with 400.

### GET /wallet — list tokenized cards for the user
Response 200:
```json
{ "cards": [ { "token": "tok_ab12...", "last4": "4242", "brand": "visa", "exp": "12/27" } ] }
```
MUST never include PAN or ciphertext.

### POST /payments — authorize a payment
Request:
```json
{ "token": "tok_ab12...", "amount": 49.99 }
```
Response 200 (approved) or 402 (declined):
```json
{ "token": "tok_ab12...", "amount": 49.99, "approved": true, "authCode": "AUTH123456" }
```
Rules: resolve the token to ciphertext for this `userId`, decrypt via KMS, send the PAN to the Issuer, discard the PAN immediately. 404 if the token does not belong to the user.

### GET /audit — return the hash-chained audit log (demo convenience)
Response 200:
```json
{ "verify": { "ok": true, "entries": 3 }, "entries": [ { "actor": "...", "action": "tokenize", "token": "tok_...", "result": "stored", "ts": "...", "prevHash": "...", "hash": "..." } ] }
```
MUST never include PAN.

### GET /health — unauthenticated liveness check
Response 200: `{ "ok": true }`

---

## 6. Data model

### DynamoDB table: `WalletTable`
- Billing: PAY_PER_REQUEST. Point-in-time recovery: ON.
- Primary key: `PK` (partition, string), `SK` (sort, string).

Card item:
```
PK = "USER#<userId>"
SK = "CARD#<token>"
ciphertext  (S)  base64 KMS ciphertext blob of the PAN     # PCI Req 3.4: never plaintext
last4       (S)  last 4 digits of PAN                       # permitted under PCI
brand       (S)  visa | mastercard | amex | unknown
exp         (S)  "MM/YY"
createdAt   (S)  ISO 8601
```

Audit head pointer (tracks the latest hash for chaining):
```
PK = "AUDIT#HEAD"
SK = "AUDIT#HEAD"
prevHash (S)  hex sha-256 of the last audit record
```

What is NEVER stored: the raw PAN, the full card number in any attribute, CVV (never collected). Only `last4` and the encrypted blob are persisted.

### S3 audit object (one per event)
- Bucket `audit-log-<accountId>-<region>`, Object Lock enabled (compliance mode), versioning ON, public access blocked.
- Key: `audit/<isoTimestamp>-<hashPrefix>.json`
- Written with `ObjectLockMode=COMPLIANCE` and a `RetainUntilDate` (e.g. now + 7 years for PCI retention; use now + 1 day for the demo to avoid long locks).
- Body is the audit record JSON described in section 7.7.

---

## 7. Component specifications

### 7.1 API Gateway (HTTP API)
Routes map to integrations:
- `POST /cards`, `GET /wallet`, `POST /payments` to Wallet Service Lambda (Lambda proxy integration).
- `GET /audit` to Wallet Service Lambda.
- `GET /health` to Wallet Service Lambda, no authorizer.
Attach a Cognito JWT authorizer to all routes except `/health`. The authorizer audience is the user pool app client id; issuer is the user pool. The Lambda reads `event.requestContext.authorizer.jwt.claims.sub` for `userId`.

### 7.2 Cognito
- One User Pool, one app client (no client secret, USER_PASSWORD_AUTH enabled for demo login).
- Seed one demo user via a deploy script so the live demo can obtain a JWT.
- The JWT authorizer enforces PCI Req 7/8 at the boundary; the CALM `pci-access-control` control documents this.

### 7.3 Wallet Service Lambda (`services/wallet/handler.py`)
Responsibilities: HTTP routing for the four routes, auth-context extraction, orchestration. It does not touch the PAN beyond passing the request body to the Token Service.
- `POST /cards`: validate body, invoke Token Service `tokenize` with `{ pan, exp, userId }`, return the token view.
- `GET /wallet`: query DynamoDB `PK = USER#<userId>`, return non-sensitive fields.
- `POST /payments`: invoke Token Service `authorize` with `{ token, amount, userId }`, return result; publish a `payment.authorized` or `payment.declined` event to EventBridge.
- `GET /audit`: read audit records (from S3 list or the in-table chain head) and return with chain verification.
Invocation of the Token Service SHOULD be a direct Lambda invoke (`boto3` `lambda.invoke`) or a shared module import; do not route PAN through API Gateway again.
IAM: `dynamodb:Query/GetItem` on the table, `lambda:InvokeFunction` on the Token Service, `events:PutEvents` on the bus. No KMS, no PAN.

### 7.4 Token Service Lambda (`services/token/handler.py`) — in CDE
This is the only component that ever holds a plaintext PAN, and only transiently.
- `tokenize({ pan, exp, userId })`:
  1. Normalize PAN (strip spaces). Derive `last4` and `brand`.
  2. `ciphertext = kms.encrypt(KeyId, Plaintext=pan)` — PAN is < 4 KB so direct KMS encrypt is fine. Base64 the `CiphertextBlob`.
  3. Generate `token = "tok_" + 16 hex chars` (`secrets.token_hex(8)`).
  4. `PutItem` the card item. Never write the PAN.
  5. Write an audit record `action=tokenize`.
  6. Return `{ token, last4, brand, exp }`. Discard the PAN from memory.
- `authorize({ token, amount, userId })`:
  1. `GetItem` `PK=USER#userId, SK=CARD#token`. 404 if missing.
  2. `pan = kms.decrypt(ciphertext)`. Write audit `action=detokenize, result=released-to-issuer`.
  3. Invoke Issuer with `{ pan, amount }`. Discard the PAN.
  4. Return the issuer result.
PAN handling rules (PCI): never log the PAN, never put it in an exception message, never return it. Configure the Lambda to avoid logging request bodies.
IAM: `kms:Encrypt`, `kms:Decrypt`, `kms:GenerateDataKey` on the CMK only; `dynamodb:GetItem/PutItem` on the table; `s3:PutObject` on the audit bucket; `lambda:InvokeFunction` on the Issuer.

### 7.5 KMS (`services/common/kms.py`)
- One customer-managed symmetric key (CMK) with a key policy granting `Encrypt`/`Decrypt` only to the Token Service execution role.
- Enable automatic key rotation.
- Helper functions `encrypt(plaintext) -> base64 str` and `decrypt(base64 str) -> plaintext` wrapping `boto3` KMS.
- The Node reference uses local AES-256-GCM as a stand-in; replace it with real KMS calls. This satisfies CALM `pci-pan-at-rest` with `key-management: aws-kms`.

### 7.6 Token Vault (`services/common/vault.py`)
- Thin DynamoDB data-access layer: `put_card`, `get_card`, `list_cards`. No business logic.
- Enforces that only `ciphertext`, `last4`, `brand`, `exp`, `createdAt` are written.

### 7.7 Audit Log (`services/common/audit.py`) — in CDE
- Hash-chained, tamper-evident. Each record:
```json
{ "actor": "user:<userId>", "action": "tokenize|detokenize", "token": "tok_...", "result": "stored|released-to-issuer", "ts": "<iso>", "prevHash": "<hex>", "hash": "<hex>" }
```
- `hash = sha256(json.dumps(record_without_hash))`. `prevHash` is the previous record's hash; genesis is 64 zeros.
- Read `prevHash` from the `AUDIT#HEAD` DynamoDB item, compute the new hash, write the object to S3 with Object Lock, then update `AUDIT#HEAD`.
- `verify()` walks the chain and recomputes hashes; any mismatch means tampering.
- This satisfies CALM `pci-pan-at-rest` audit intent and PCI Req 10.

### 7.8 Issuer Lambda (`services/issuer/handler.py`)
Mock issuer. Approve when the PAN matches `^\d{13,19}$`, `0 < amount <= 1000`. Decline otherwise with `reason` in `{invalid_pan, invalid_amount, limit_exceeded}`. Return `{ approved, authCode }` on approval. No persistence, no special IAM. Represents SafePay / GCP in the diagram.

### 7.9 EventBridge + SNS
- Custom event bus `wallet-events`. Wallet Service emits source `wallet.payments`, detail-types `payment.authorized` / `payment.declined`.
- A rule matches both detail-types and targets an SNS topic `payment-notifications`.
- SNS delivers to an email or SMS subscription (subscribe one address at deploy for the demo). Notification payload contains token, amount, result; never PAN.

### 7.10 Wallet Cache (ElastiCache) — STRETCH
- Redis for non-sensitive wallet metadata to reduce DynamoDB reads on `GET /wallet`. Out of CDE; MUST never cache PAN or ciphertext. Skip for the first working version.

---

## 8. PCI DSS control mapping

| PCI DSS requirement | Implementation | CALM control | Where in model |
|---|---|---|---|
| Req 3 / 3.4 — render PAN unreadable at rest | KMS-encrypted ciphertext in DynamoDB; PAN never persisted | `pci-pan-at-rest` (`storage-format: tokenized`, `encryption-at-rest: true`, `key-management: aws-kms`) | node `token-vault` |
| Req 4 — encrypt CHD in transit | TLS 1.2+ on all links carrying CHD | `pci-encryption-in-transit` (`tls-version: 1.2`/`1.3`) | relationships `app-to-apigw`, `token-to-vault` |
| Req 7 / 8 — restrict access to CDE | Cognito JWT authorizer, least-privilege IAM per Lambda | `pci-access-control` (`authentication: oauth2`, `least-privilege: true`) | node `authorizer` |
| Req 10 — log access to CHD | Hash-chained audit records in S3 Object Lock | documented via audit node and chain | node `audit-log` |

The pattern `patterns/pci-dss.pattern.json` enforces Req 3, 4 and 7/8 structurally. A model lacking any of these, or declaring `storage-format: plaintext` or `tls-version` below 1.2, fails validation.

---

## 9. CALM architecture as code

The model and gate already exist and validate. The implementation agent MUST keep them accurate as the build evolves, and MUST NOT weaken the pattern to make a non-compliant design pass.

Commands:
```bash
npm install -g @finos/calm-cli

# structural validation
calm validate -a architecture/wallet-architecture.json

# PCI compliance gate (must pass)
calm validate -p patterns/pci-dss.pattern.json -a architecture/wallet-architecture.json

# negative test (must FAIL — proves the gate works)
calm validate -p patterns/pci-dss.pattern.json -a architecture/wallet-architecture-noncompliant.json
```

Model-to-code mapping: each CALM node maps to a component in section 7 by `unique-id`. When you add or move a component, update `nodes`, `relationships`, and any affected `controls` in `wallet-architecture.json`, then re-run validation. The CALM model is part of the definition of done, not documentation written afterward.

### CI gate (`.github/workflows/calm.yml`)
On every push and PR:
1. Install Node and `@finos/calm-cli`.
2. Run structural validation; fail on error.
3. Run the PCI pattern validation against the compliant model; fail on error.
4. Run the PCI pattern validation against the non-compliant model and assert it fails (negative test); fail the job if it unexpectedly passes.

---

## 10. Infrastructure as code (SAM outline)

`infra/template.yaml` (AWS SAM) provisions, at minimum:
- `AWS::Serverless::HttpApi` with a Cognito JWT authorizer; `/health` route with `Auth: { Authorizer: NONE }`.
- Three `AWS::Serverless::Function` resources (Python 3.12): Wallet, Token, Issuer, each with scoped policies from section 7.
- `AWS::DynamoDB::Table` `WalletTable` (PAY_PER_REQUEST, PITR on).
- `AWS::KMS::Key` CMK with rotation and a key policy granting only the Token role.
- `AWS::S3::Bucket` audit bucket with `ObjectLockEnabled: true`, versioning, and full public-access block.
- `AWS::Cognito::UserPool` and `UserPoolClient`.
- `AWS::Events::EventBus` `wallet-events`, an `AWS::Events::Rule`, and `AWS::SNS::Topic` `payment-notifications`.
Outputs: API base URL, user pool id, app client id, audit bucket name.

Provide a `samconfig.toml` and document `sam build && sam deploy --guided`. CDK Python is acceptable instead; if used, keep all logical names and IAM identical.

---

## 11. Build plan (ordered tasks)

1. Scaffold `services/` with the `common/` modules and three handlers; port the Node reference logic to Python verbatim (same behaviour, same audit chain).
2. Write `infra/template.yaml` (SAM) with DynamoDB, KMS, S3 Object Lock, Cognito, three Lambdas, HTTP API, EventBridge, SNS.
3. Wire KMS encrypt/decrypt in the Token Service; confirm no PAN is logged.
4. Implement the hash-chained audit writer to S3 Object Lock with the DynamoDB head pointer.
5. Deploy with SAM; seed a Cognito demo user; smoke-test all five endpoints with `curl` and a real JWT.
6. Update `architecture/wallet-architecture.json` so every node and relationship reflects the deployed system; re-run both validations.
7. Add `.github/workflows/calm.yml` with the three validation steps including the negative test.
8. STRETCH: minimal static `web/` UI on S3 + CloudFront; ElastiCache cache; CloudWatch dashboards.

---

## 12. Acceptance criteria

- `POST /cards` returns a token and `last4`; the PAN appears in no response, no log, and no DynamoDB attribute. Verified by inspecting CloudWatch logs and the item.
- `POST /payments` approves under the issuer rules and declines over limit with the correct `reason`; the PAN is decrypted only in the Token Service and never returned.
- `GET /audit` returns a chain whose `verify.ok` is true; altering any stored record breaks verification.
- Audit objects in S3 cannot be deleted or overwritten before the retention date (Object Lock proven).
- Each Lambda role has only the permissions in section 7 (no `*` resource on KMS or DynamoDB).
- `calm validate` passes structurally and against the PCI pattern for the compliant model.
- `calm validate` against the non-compliant model FAILS, and CI treats that failure as the expected (passing) negative test.
- The CI workflow is green on the compliant model and red if the model is made non-compliant.

---

## 13. Out of scope / non-goals

- Real card network connectivity, settlement, clearing, refunds, disputes.
- A full PCI DSS audit, QSA assessment, or SAQ. This demonstrates control representation, not certification.
- Runtime attestation that the deployed system matches the declared CALM model (declared vs actual gap). This is the evidence problem and is deliberately excluded; note it explicitly in any demo.
- Production hardening: WAF, rate limiting, VPC isolation of the CDE, secrets rotation beyond KMS, multi-region. Mention as future work.
- PCI scope reduction analysis beyond the CDE boundary drawn in the diagram.

---

## Appendix A. Environment variables

| Lambda | Variable | Purpose |
|---|---|---|
| Wallet | `TABLE_NAME` | DynamoDB table |
| Wallet | `TOKEN_FN` | Token Service function name |
| Wallet | `EVENT_BUS` | EventBridge bus name |
| Token | `TABLE_NAME` | DynamoDB table |
| Token | `KMS_KEY_ID` | CMK id/arn |
| Token | `AUDIT_BUCKET` | S3 audit bucket |
| Token | `ISSUER_FN` | Issuer function name |

## Appendix B. Local reference

The Node app at `app/` runs with zero dependencies (`node app/src/server.js`) and is the behavioural contract: same endpoints, same masking, same hash-chained audit. Use it to diff behaviour while porting to Python. Do not ship the Node app; it exists only as the reference and offline demo.
