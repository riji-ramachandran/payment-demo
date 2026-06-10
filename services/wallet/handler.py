"""Wallet Service Lambda — HTTP routing and orchestration.

Handles the API Gateway HTTP API (payload format 2.0) proxy events for the
four authenticated routes plus unauthenticated /health. It does not touch the
PAN: POST /cards and POST /payments forward the request to the Token Service
via a direct Lambda invoke; the PAN never re-enters API Gateway.

IAM: dynamodb:Query/GetItem, lambda:InvokeFunction on the Token Service,
events:PutEvents on the bus. No KMS, no S3, no PAN.
"""

import json
import os

import boto3

from common import audit, events, vault

_lambda = boto3.client("lambda")

TOKEN_FN = os.environ.get("TOKEN_FN")


# ---- helpers ---------------------------------------------------------------

def _resp(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(body),
    }


def _user_id(event: dict) -> str | None:
    try:
        return event["requestContext"]["authorizer"]["jwt"]["claims"]["sub"]
    except (KeyError, TypeError):
        return None


def _body(event: dict) -> dict:
    raw = event.get("body") or "{}"
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _invoke_token(payload: dict) -> dict:
    resp = _lambda.invoke(
        FunctionName=TOKEN_FN,
        InvocationType="RequestResponse",
        Payload=json.dumps(payload).encode("utf-8"),
    )
    return json.loads(resp["Payload"].read())


def _card_view(item: dict) -> dict:
    """Non-sensitive projection — never ciphertext, never PAN."""
    return {
        "token": item.get("token"),
        "last4": item.get("last4"),
        "brand": item.get("brand"),
        "exp": item.get("exp"),
    }


# ---- route handlers --------------------------------------------------------

def _add_card(event, user_id):
    body = _body(event)
    result = _invoke_token(
        {"action": "tokenize", "pan": body.get("pan"), "exp": body.get("exp"), "userId": user_id}
    )
    if result.get("error"):
        return _resp(result.get("status", 400), {"error": result["error"]})
    return _resp(201, {"card": result})


def _list_wallet(event, user_id):
    items = vault.list_cards(user_id)
    return _resp(200, {"cards": [_card_view(i) for i in items]})


def _pay(event, user_id):
    body = _body(event)
    result = _invoke_token(
        {"action": "authorize", "token": body.get("token"), "amount": body.get("amount"), "userId": user_id}
    )
    if result.get("error"):
        return _resp(result.get("status", 400), {"error": result["error"]})

    approved = result.get("approved", False)
    detail = {"token": result.get("token"), "amount": result.get("amount"), "approved": approved}
    events.put_payment_event(
        "payment.authorized" if approved else "payment.declined", detail
    )
    return _resp(200 if approved else 402, result)


def _audit(event, user_id):
    return _resp(200, {"verify": audit.verify(), "entries": audit.entries()})


# route table keyed by "METHOD /path"
_ROUTES = {
    "POST /cards": _add_card,
    "GET /wallet": _list_wallet,
    "POST /payments": _pay,
    "GET /audit": _audit,
}


def handler(event, _context):
    route_key = event.get("routeKey", "")

    if route_key == "GET /health":
        return _resp(200, {"ok": True})

    fn = _ROUTES.get(route_key)
    if not fn:
        return _resp(404, {"error": "not_found"})

    user_id = _user_id(event)
    if not user_id:
        return _resp(401, {"error": "unauthorized"})

    return fn(event, user_id)
