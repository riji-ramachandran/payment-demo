"""End-to-end local test of the real Lambda handlers over fake AWS.

Run: python3 tests/local_e2e.py
Exits non-zero on any failed check.
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "services"))
sys.path.insert(0, HERE)

import _fakeaws  # noqa: E402

# Env must be set before importing the handlers (they read it at import time).
os.environ.update({
    "KMS_KEY_ID": "arn:aws:kms:eu-west-1:000000000000:key/demo",
    "TABLE_NAME": "WalletTable",
    "AUDIT_BUCKET": "audit-bucket",
    "EVENT_BUS": "wallet-events",
    "ISSUER_FN": "issuer-fn",
    "TOKEN_FN": "token-fn",
    "AUDIT_RETAIN_DAYS": "1",
})

_fakeaws.install()

import importlib.util  # noqa: E402


def _load(modname, relpath):
    # Load handler files by path; their package dirs (token/, etc.) collide
    # with stdlib names, so we avoid normal package import.
    spec = importlib.util.spec_from_file_location(modname, os.path.join(ROOT, "services", relpath))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[modname] = mod
    spec.loader.exec_module(mod)
    return mod


wallet = _load("wallet_handler", "wallet/handler.py")
token = _load("token_handler", "token/handler.py")
issuer = _load("issuer_handler", "issuer/handler.py")

# Wire the cross-function invoke registry.
_fakeaws.LAMBDA_REGISTRY["token-fn"] = token.handler
_fakeaws.LAMBDA_REGISTRY["issuer-fn"] = issuer.handler

PAN = "4242424242424242"
USER = "user-abc"

_passed = 0
_failed = 0


def check(name, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  PASS  {name}")
    else:
        _failed += 1
        print(f"  FAIL  {name}  {detail}")


def evt(route, body=None, sub=USER, with_auth=True):
    e = {"routeKey": route}
    if body is not None:
        e["body"] = json.dumps(body)
    if with_auth:
        e["requestContext"] = {"authorizer": {"jwt": {"claims": {"sub": sub}}}}
    return e


def body_of(resp):
    return json.loads(resp["body"])


# --- 1. health (unauthenticated) -------------------------------------------
r = wallet.handler(evt("GET /health", with_auth=False), None)
check("health 200 ok", r["statusCode"] == 200 and body_of(r) == {"ok": True}, r)

# --- 2. unauthorized protected route ---------------------------------------
r = wallet.handler(evt("GET /wallet", with_auth=False), None)
check("missing JWT -> 401", r["statusCode"] == 401, r)

# --- 3. add card ------------------------------------------------------------
r = wallet.handler(evt("POST /cards", {"pan": PAN, "exp": "12/27"}), None)
card = body_of(r).get("card", {})
check("POST /cards -> 201", r["statusCode"] == 201, r)
check("card has token", card.get("token", "").startswith("tok_"), card)
check("card last4=4242", card.get("last4") == "4242", card)
check("card brand=visa", card.get("brand") == "visa", card)
check("response has NO pan", PAN not in r["body"], r["body"])
TOKEN = card.get("token")

# --- 4. PAN is not persisted in plaintext anywhere -------------------------
ddb_dump = json.dumps(list(_fakeaws.STATE["ddb"].values()), default=str)
check("DynamoDB holds NO pan", PAN not in ddb_dump, "pan found in item")
card_item = next((i for i in _fakeaws.STATE["ddb"].values() if i.get("SK", "").startswith("CARD#")), {})
check("item has ciphertext", "ciphertext" in card_item, card_item)
check("item has no 'pan' attr", "pan" not in card_item, card_item)

# --- 5. invalid PAN rejected ------------------------------------------------
r = wallet.handler(evt("POST /cards", {"pan": "12345", "exp": "12/27"}), None)
check("short PAN -> 400", r["statusCode"] == 400, r)

# --- 6. list wallet ---------------------------------------------------------
r = wallet.handler(evt("GET /wallet"), None)
cards = body_of(r).get("cards", [])
check("GET /wallet -> 200", r["statusCode"] == 200, r)
check("wallet lists 1 card", len(cards) == 1 and cards[0]["token"] == TOKEN, cards)
check("wallet view has no ciphertext", "ciphertext" not in cards[0], cards[0])
check("wallet view has no pan", PAN not in r["body"], r["body"])

# --- 7. pay approved --------------------------------------------------------
r = wallet.handler(evt("POST /payments", {"token": TOKEN, "amount": 49.99}), None)
pay = body_of(r)
check("payment <=1000 -> 200", r["statusCode"] == 200, r)
check("payment approved", pay.get("approved") is True, pay)
check("payment has authCode", str(pay.get("authCode", "")).startswith("AUTH"), pay)
check("payment response no pan", PAN not in r["body"], r["body"])

# --- 8. pay declined over limit --------------------------------------------
r = wallet.handler(evt("POST /payments", {"token": TOKEN, "amount": 5000}), None)
pay = body_of(r)
check("payment >1000 -> 402", r["statusCode"] == 402, r)
check("decline reason limit_exceeded", pay.get("reason") == "limit_exceeded", pay)

# --- 9. pay unknown token -> 404 -------------------------------------------
r = wallet.handler(evt("POST /payments", {"token": "tok_doesnotexist", "amount": 10}), None)
check("unknown token -> 404", r["statusCode"] == 404, r)

# --- 10. events emitted, no PAN --------------------------------------------
evs = _fakeaws.STATE["events"]
dtypes = [e["DetailType"] for e in evs]
check("authorized event emitted", "payment.authorized" in dtypes, dtypes)
check("declined event emitted", "payment.declined" in dtypes, dtypes)
check("events carry no pan", PAN not in json.dumps(evs), evs)

# --- 11. audit chain verifies, carries no PAN ------------------------------
r = wallet.handler(evt("GET /audit"), None)
aud = body_of(r)
check("GET /audit -> 200", r["statusCode"] == 200, r)
check("audit verify.ok true", aud["verify"]["ok"] is True, aud["verify"])
# tokenize (1) + detokenize for the two payments that resolved a real card (2) = 3
check("audit has >=3 entries", aud["verify"]["entries"] >= 3, aud["verify"])
check("audit actions correct",
      {e["action"] for e in aud["entries"]} == {"tokenize", "detokenize"},
      [e["action"] for e in aud["entries"]])
check("audit carries no pan", PAN not in r["body"], r["body"])

# --- 12. audit objects written under Object Lock ---------------------------
locked = [o for o in _fakeaws.STATE["s3"].values() if o["locked"]]
check("audit objects Object-Locked", len(locked) >= 3 and all(o["retain"] for o in locked),
      f"{len(locked)} locked")

# --- 13. tamper detection ---------------------------------------------------
# Mutate one stored audit record; verify() must now fail.
first_key = sorted(_fakeaws.STATE["s3"].keys())[0]
rec = json.loads(_fakeaws.STATE["s3"][first_key]["body"])
rec["result"] = "TAMPERED"
_fakeaws.STATE["s3"][first_key]["body"] = json.dumps(rec).encode("utf-8")
r = wallet.handler(evt("GET /audit"), None)
check("tampering breaks verify", body_of(r)["verify"]["ok"] is False, body_of(r)["verify"])

# --- 14. unknown route -> 404 ----------------------------------------------
r = wallet.handler(evt("DELETE /cards"), None)
check("unknown route -> 404", r["statusCode"] == 404, r)

# --- issuer unit checks -----------------------------------------------------
check("issuer invalid_pan", issuer.handler({"pan": "abc", "amount": 10}, None)["reason"] == "invalid_pan")
check("issuer invalid_amount", issuer.handler({"pan": PAN, "amount": 0}, None)["reason"] == "invalid_amount")
check("issuer approves valid", issuer.handler({"pan": PAN, "amount": 10}, None)["approved"] is True)

print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
