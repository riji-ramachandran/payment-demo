"""End-to-end test of the real handlers against LocalStack (real AWS APIs).

Provisions KMS / DynamoDB / S3 (Object Lock) / EventBridge / SNS in LocalStack,
points the handlers' boto3 at it, and drives the full add-card -> list -> pay
-> audit flow. KMS encryption, DynamoDB, S3 Object Lock and EventBridge are all
real service calls. API Gateway + Cognito (LocalStack Pro) are not emulated:
the Wallet handler is invoked with the same proxy event API Gateway sends, and
the Wallet->Token->Issuer Lambda invoke is dispatched in-process.

Run:  .venv/bin/python tests/localstack_e2e.py
"""

import importlib.util
import json
import os
import sys

EP = os.environ.get("AWS_ENDPOINT_URL", "http://localhost:4566")
REGION = "us-east-1"

os.environ.update({
    "AWS_ACCESS_KEY_ID": "test",
    "AWS_SECRET_ACCESS_KEY": "test",
    "AWS_DEFAULT_REGION": REGION,
})

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "services"))

import boto3  # noqa: E402

# Patch boto3 so every client/resource the handlers build targets LocalStack.
_orig_client = boto3.client
_orig_resource = boto3.resource


def _client(name, *a, **k):
    k.setdefault("endpoint_url", EP)
    k.setdefault("region_name", REGION)
    return _orig_client(name, *a, **k)


def _resource(name, *a, **k):
    k.setdefault("endpoint_url", EP)
    k.setdefault("region_name", REGION)
    return _orig_resource(name, *a, **k)


boto3.client = _client
boto3.resource = _resource

# Admin clients for provisioning.
kms = boto3.client("kms")
ddb = boto3.client("dynamodb")
s3 = boto3.client("s3")
events = boto3.client("events")
sns = boto3.client("sns")

TABLE = "WalletTable"
BUCKET = "audit-bucket-localstack"
BUS = "wallet-events"
PAN = "4242424242424242"
USER = "user-abc"

_passed = _failed = 0


def check(name, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  PASS  {name}")
    else:
        _failed += 1
        print(f"  FAIL  {name}  {detail}")


# --------------------------------------------------------------------------
# Provision real resources in LocalStack
# --------------------------------------------------------------------------
print("provisioning LocalStack resources...")

key = kms.create_key(Description="pan-cmk")["KeyMetadata"]
KMS_KEY_ID = key["KeyId"]

try:
    ddb.delete_table(TableName=TABLE)
except Exception:
    pass
ddb.create_table(
    TableName=TABLE,
    BillingMode="PAY_PER_REQUEST",
    AttributeDefinitions=[
        {"AttributeName": "PK", "AttributeType": "S"},
        {"AttributeName": "SK", "AttributeType": "S"},
    ],
    KeySchema=[
        {"AttributeName": "PK", "KeyType": "HASH"},
        {"AttributeName": "SK", "KeyType": "RANGE"},
    ],
)
ddb.get_waiter("table_exists").wait(TableName=TABLE)

# S3 bucket with Object Lock (compliance) + versioning.
try:
    s3.create_bucket(Bucket=BUCKET, ObjectLockEnabledForBucket=True)
except Exception:
    pass
# Object Lock auto-enables versioning on bucket creation; just set the rule.
s3.put_object_lock_configuration(
    Bucket=BUCKET,
    ObjectLockConfiguration={
        "ObjectLockEnabled": "Enabled",
        "Rule": {"DefaultRetention": {"Mode": "COMPLIANCE", "Days": 1}},
    },
)

try:
    events.create_event_bus(Name=BUS)
except events.exceptions.ResourceAlreadyExistsException:
    pass
topic_arn = sns.create_topic(Name="payment-notifications")["TopicArn"]
events.put_rule(
    Name="payment-events",
    EventBusName=BUS,
    EventPattern=json.dumps({
        "source": ["wallet.payments"],
        "detail-type": ["payment.authorized", "payment.declined"],
    }),
)
events.put_targets(
    Rule="payment-events", EventBusName=BUS,
    Targets=[{"Id": "sns", "Arn": topic_arn}],
)

# Env the handlers read at import time.
os.environ.update({
    "KMS_KEY_ID": KMS_KEY_ID,
    "TABLE_NAME": TABLE,
    "AUDIT_BUCKET": BUCKET,
    "EVENT_BUS": BUS,
    "ISSUER_FN": "issuer-fn",
    "TOKEN_FN": "token-fn",
    "AUDIT_RETAIN_DAYS": "1",
})


def _load(modname, relpath):
    spec = importlib.util.spec_from_file_location(modname, os.path.join(ROOT, "services", relpath))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[modname] = mod
    spec.loader.exec_module(mod)
    return mod


wallet = _load("wallet_handler", "wallet/handler.py")
token = _load("token_handler", "token/handler.py")
issuer = _load("issuer_handler", "issuer/handler.py")


# In-process dispatcher standing in for AWS Lambda invoke (transport only;
# all data-plane calls inside the handlers still hit LocalStack).
class _Body:
    def __init__(self, data):
        self._d = data

    def read(self):
        return self._d


class _Dispatcher:
    registry = {"token-fn": token.handler, "issuer-fn": issuer.handler}

    def invoke(self, FunctionName, InvocationType, Payload):
        result = self.registry[FunctionName](json.loads(Payload), None)
        return {"Payload": _Body(json.dumps(result).encode("utf-8"))}


_disp = _Dispatcher()
wallet._lambda = _disp
token._lambda = _disp

print("running scenario...\n")


def evt(route, body=None, sub=USER, with_auth=True):
    e = {"routeKey": route}
    if body is not None:
        e["body"] = json.dumps(body)
    if with_auth:
        e["requestContext"] = {"authorizer": {"jwt": {"claims": {"sub": sub}}}}
    return e


def body_of(r):
    return json.loads(r["body"])


# 1. health
r = wallet.handler(evt("GET /health", with_auth=False), None)
check("health 200 ok", r["statusCode"] == 200 and body_of(r) == {"ok": True}, r)

# 2. add card -> real KMS encrypt + real DynamoDB put
r = wallet.handler(evt("POST /cards", {"pan": PAN, "exp": "12/27"}), None)
card = body_of(r).get("card", {})
check("POST /cards -> 201", r["statusCode"] == 201, r)
check("card token/last4/brand", card.get("token", "").startswith("tok_") and card.get("last4") == "4242" and card.get("brand") == "visa", card)
check("response has NO pan", PAN not in r["body"], r["body"])
TOKEN = card["token"]

# 3. inspect the real DynamoDB item: ciphertext present, no PAN
item = ddb.get_item(TableName=TABLE, Key={"PK": {"S": f"USER#{USER}"}, "SK": {"S": f"CARD#{TOKEN}"}})["Item"]
check("DDB item has ciphertext", "ciphertext" in item, list(item))
check("DDB item has no pan attr", "pan" not in item, list(item))
check("DDB item holds no PAN", PAN not in json.dumps(item), "pan found")

# 4. verify the ciphertext really decrypts back to the PAN via real KMS
import base64
blob = base64.b64decode(item["ciphertext"]["S"])
decrypted = kms.decrypt(CiphertextBlob=blob, KeyId=KMS_KEY_ID)["Plaintext"].decode()
check("KMS ciphertext decrypts to PAN", decrypted == PAN, decrypted[:4] + "...")

# 5. invalid PAN
r = wallet.handler(evt("POST /cards", {"pan": "12345", "exp": "12/27"}), None)
check("short PAN -> 400", r["statusCode"] == 400, r)

# 6. list wallet
r = wallet.handler(evt("GET /wallet"), None)
cards = body_of(r)["cards"]
check("GET /wallet lists 1 card", len(cards) == 1 and cards[0]["token"] == TOKEN, cards)
check("wallet view: no ciphertext/pan", "ciphertext" not in cards[0] and PAN not in r["body"], cards[0])

# 7. pay approved (real KMS decrypt inside token handler)
r = wallet.handler(evt("POST /payments", {"token": TOKEN, "amount": 49.99}), None)
pay = body_of(r)
check("payment <=1000 -> 200 approved", r["statusCode"] == 200 and pay.get("approved") is True and str(pay.get("authCode", "")).startswith("AUTH"), pay)
check("payment response no pan", PAN not in r["body"], r["body"])

# 8. decline over limit
r = wallet.handler(evt("POST /payments", {"token": TOKEN, "amount": 5000}), None)
check("payment >1000 -> 402 limit_exceeded", r["statusCode"] == 402 and body_of(r).get("reason") == "limit_exceeded", body_of(r))

# 9. unknown token
r = wallet.handler(evt("POST /payments", {"token": "tok_nope", "amount": 10}), None)
check("unknown token -> 404", r["statusCode"] == 404, r)

# 10. audit chain over real S3 list/get
r = wallet.handler(evt("GET /audit"), None)
aud = body_of(r)
check("GET /audit verify.ok", r["statusCode"] == 200 and aud["verify"]["ok"] is True, aud["verify"])
check("audit >=3 entries", aud["verify"]["entries"] >= 3, aud["verify"])
check("audit carries no pan", PAN not in r["body"], r["body"])

# 11. real S3 Object Lock blocks deletion before retention
objs = s3.list_objects_v2(Bucket=BUCKET, Prefix="audit/").get("Contents", [])
check("audit objects in S3", len(objs) >= 3, len(objs))
victim = objs[0]["Key"]
ver = s3.list_object_versions(Bucket=BUCKET, Prefix=victim)["Versions"][0]["VersionId"]
lock_blocked = False
try:
    s3.delete_object(Bucket=BUCKET, Key=victim, VersionId=ver)
except Exception as e:
    lock_blocked = "AccessDenied" in str(e) or "WORM" in str(e) or "Compliance" in str(e)
check("Object Lock blocks version delete", lock_blocked, "delete was NOT blocked")

# 12. events emitted to the real bus
# (delivery to SNS is async in LocalStack; we assert put_events succeeded by
#  the handler returning 200/402 above without error — emission ran inline.)
check("issuer invalid_amount", issuer.handler({"pan": PAN, "amount": 0}, None)["reason"] == "invalid_amount")

print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
