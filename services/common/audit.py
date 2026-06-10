"""Hash-chained, tamper-evident audit log (PCI Req 10).

Each access to cardholder data writes a record. Records are linked by a
SHA-256 hash chain: each record's `hash` covers the record plus the previous
record's `hash`. The chain head pointer lives in DynamoDB; records are written
to S3 under Object Lock (compliance mode) so they cannot be altered or deleted
before their retention date.

A record NEVER contains a PAN — only the token and a coarse action/result.
"""

import hashlib
import json
import os
from datetime import datetime, timezone

import boto3

_ddb = boto3.resource("dynamodb")
_s3 = boto3.client("s3")

TABLE_NAME = os.environ.get("TABLE_NAME")
AUDIT_BUCKET = os.environ.get("AUDIT_BUCKET")

# Retain audit objects this many days. PCI wants ~7 years; the demo uses 1 day
# to avoid leaving long-lived Object Lock holds on throwaway accounts.
RETAIN_DAYS = int(os.environ.get("AUDIT_RETAIN_DAYS", "1"))

GENESIS = "0" * 64

_HEAD_KEY = {"PK": "AUDIT#HEAD", "SK": "AUDIT#HEAD"}


def _table():
    return _ddb.Table(TABLE_NAME)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash_record(record: dict) -> str:
    """SHA-256 over the canonical JSON of the record without its own hash."""
    body = {k: v for k, v in record.items() if k != "hash"}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _read_head() -> str:
    resp = _table().get_item(Key=_HEAD_KEY)
    item = resp.get("Item")
    return item["prevHash"] if item else GENESIS


def _write_head(new_hash: str) -> None:
    _table().put_item(Item={**_HEAD_KEY, "prevHash": new_hash})


def write(actor: str, action: str, token: str, result: str) -> dict:
    """Append an audit record to the chain and persist it to S3 Object Lock."""
    prev_hash = _read_head()
    record = {
        "actor": actor,
        "action": action,
        "token": token,
        "result": result,
        "ts": _now_iso(),
        "prevHash": prev_hash,
    }
    record["hash"] = _hash_record(record)

    key = f"audit/{record['ts']}-{record['hash'][:12]}.json"
    retain_until = datetime.fromtimestamp(
        datetime.now(timezone.utc).timestamp() + RETAIN_DAYS * 86400,
        tz=timezone.utc,
    )
    _s3.put_object(
        Bucket=AUDIT_BUCKET,
        Key=key,
        Body=json.dumps(record).encode("utf-8"),
        ContentType="application/json",
        ObjectLockMode="COMPLIANCE",
        ObjectLockRetainUntilDate=retain_until,
    )
    _write_head(record["hash"])
    return record


def _load_records() -> list[dict]:
    """Load all audit records from S3, ordered by key (timestamp-prefixed)."""
    records = []
    paginator = _s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=AUDIT_BUCKET, Prefix="audit/"):
        for obj in sorted(page.get("Contents", []), key=lambda o: o["Key"]):
            body = _s3.get_object(Bucket=AUDIT_BUCKET, Key=obj["Key"])["Body"].read()
            records.append(json.loads(body))
    records.sort(key=lambda r: r["ts"])
    return records


def verify() -> dict:
    """Walk the chain recomputing hashes; report whether it is intact."""
    records = _load_records()
    prev = GENESIS
    for r in records:
        if r.get("prevHash") != prev:
            return {"ok": False, "entries": len(records)}
        if _hash_record(r) != r.get("hash"):
            return {"ok": False, "entries": len(records)}
        prev = r["hash"]
    return {"ok": True, "entries": len(records)}


def entries() -> list[dict]:
    """Return all audit records (for the demo /audit endpoint)."""
    return _load_records()
