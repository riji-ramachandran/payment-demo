"""Token Vault — thin DynamoDB data-access layer.

Stores only the encrypted blob plus non-sensitive card metadata. No business
logic, no PAN. Enforces the allowed attribute set on write.
"""

import os

import boto3

_ddb = boto3.resource("dynamodb")

TABLE_NAME = os.environ.get("TABLE_NAME")

_ALLOWED = {"ciphertext", "last4", "brand", "exp", "createdAt"}


def _table():
    return _ddb.Table(TABLE_NAME)


def put_card(user_id: str, token: str, attrs: dict) -> None:
    """Persist a card item. Only the allowed (non-PAN) attributes are written."""
    item = {k: v for k, v in attrs.items() if k in _ALLOWED}
    item["PK"] = f"USER#{user_id}"
    item["SK"] = f"CARD#{token}"
    item["token"] = token
    _table().put_item(Item=item)


def get_card(user_id: str, token: str) -> dict | None:
    """Fetch a single card item for the user, or None if absent."""
    resp = _table().get_item(Key={"PK": f"USER#{user_id}", "SK": f"CARD#{token}"})
    return resp.get("Item")


def list_cards(user_id: str) -> list[dict]:
    """Return all card items for the user (non-sensitive view assembled by caller)."""
    from boto3.dynamodb.conditions import Key

    resp = _table().query(
        KeyConditionExpression=Key("PK").eq(f"USER#{user_id}")
        & Key("SK").begins_with("CARD#")
    )
    return resp.get("Items", [])
