"""EventBridge publisher for payment events.

Payloads carry token, amount and result — never a PAN.
"""

import json
import os

import boto3

_events = boto3.client("events")

EVENT_BUS = os.environ.get("EVENT_BUS")


def put_payment_event(detail_type: str, detail: dict) -> None:
    """Emit a wallet.payments event (payment.authorized / payment.declined)."""
    _events.put_events(
        Entries=[
            {
                "Source": "wallet.payments",
                "DetailType": detail_type,
                "Detail": json.dumps(detail),
                "EventBusName": EVENT_BUS,
            }
        ]
    )
