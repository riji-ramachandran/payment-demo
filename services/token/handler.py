"""Token Service Lambda — the only component that holds a plaintext PAN.

Invoked directly by the Wallet Service (boto3 lambda.invoke) with a payload
{ "action": "tokenize" | "authorize", ... }. The PAN never crosses API
Gateway and is never logged, returned, or placed in an exception message.

PCI scope: this Lambda is in the CDE. It has KMS Encrypt/Decrypt on the CMK,
DynamoDB GetItem/PutItem, S3 PutObject on the audit bucket, and InvokeFunction
on the Issuer only.
"""

import json
import os
import re
import secrets

import boto3

from common import audit, kms, vault

_lambda = boto3.client("lambda")

ISSUER_FN = os.environ.get("ISSUER_FN")

_PAN_RE = re.compile(r"^\d{13,19}$")

_BRAND_PREFIX = (
    ("4", "visa"),
    ("34", "amex"),
    ("37", "amex"),
    ("51", "mastercard"),
    ("52", "mastercard"),
    ("53", "mastercard"),
    ("54", "mastercard"),
    ("55", "mastercard"),
)


def _brand(pan: str) -> str:
    for prefix, brand in _BRAND_PREFIX:
        if pan.startswith(prefix):
            return brand
    return "unknown"


def _normalize(pan: str) -> str:
    return re.sub(r"\s+", "", pan or "")


def tokenize(payload: dict) -> dict:
    pan = _normalize(payload.get("pan"))
    exp = payload.get("exp")
    user_id = payload["userId"]

    if not _PAN_RE.match(pan):
        return {"error": "invalid_pan", "status": 400}

    last4 = pan[-4:]
    brand = _brand(pan)
    ciphertext = kms.encrypt(pan)  # PAN encrypted before any persistence
    token = "tok_" + secrets.token_hex(8)

    from datetime import datetime, timezone

    vault.put_card(
        user_id,
        token,
        {
            "ciphertext": ciphertext,
            "last4": last4,
            "brand": brand,
            "exp": exp,
            "createdAt": datetime.now(timezone.utc).isoformat(),
        },
    )
    audit.write(actor=f"user:{user_id}", action="tokenize", token=token, result="stored")

    del pan  # discard plaintext PAN from memory
    return {"token": token, "last4": last4, "brand": brand, "exp": exp}


def authorize(payload: dict) -> dict:
    token = payload["token"]
    amount = payload["amount"]
    user_id = payload["userId"]

    card = vault.get_card(user_id, token)
    if not card:
        return {"error": "not_found", "status": 404}

    pan = kms.decrypt(card["ciphertext"])
    audit.write(
        actor=f"user:{user_id}",
        action="detokenize",
        token=token,
        result="released-to-issuer",
    )

    try:
        resp = _lambda.invoke(
            FunctionName=ISSUER_FN,
            InvocationType="RequestResponse",
            Payload=json.dumps({"pan": pan, "amount": amount}).encode("utf-8"),
        )
        issuer_result = json.loads(resp["Payload"].read())
    finally:
        del pan  # discard plaintext PAN immediately after the issuer call

    return {"token": token, "amount": amount, **issuer_result}


_ACTIONS = {"tokenize": tokenize, "authorize": authorize}


def handler(event, _context):
    action = event.get("action")
    fn = _ACTIONS.get(action)
    if not fn:
        return {"error": "unknown_action", "status": 400}
    return fn(event)
