"""In-memory fake of the slice of boto3 the handlers use.

Installs a fake `boto3` (and `boto3.dynamodb.conditions`) into sys.modules so
the real handler/common code runs unmodified, with no network and no creds.
Exposes STATE for assertions and a lambda registry for cross-function invoke.
"""

import base64
import json
import sys
import types

# ---- shared in-memory state ------------------------------------------------

STATE = {
    "ddb": {},        # (PK, SK) -> item dict
    "s3": {},         # key -> {"body": bytes, "locked": bool, "retain": any}
    "events": [],     # list of emitted EventBridge entries
}

LAMBDA_REGISTRY = {}  # function name -> python callable(event, context)


def reset():
    STATE["ddb"].clear()
    STATE["s3"].clear()
    STATE["events"].clear()


# ---- DynamoDB condition expressions ----------------------------------------

class _Cond:
    def __init__(self, clauses):
        self.clauses = clauses  # list of (attr, op, value)

    def __and__(self, other):
        return _Cond(self.clauses + other.clauses)


class _KeyAttr:
    def __init__(self, name):
        self.name = name

    def eq(self, v):
        return _Cond([(self.name, "eq", v)])

    def begins_with(self, v):
        return _Cond([(self.name, "begins_with", v)])


def _Key(name):
    return _KeyAttr(name)


# ---- DynamoDB resource ------------------------------------------------------

class _Table:
    def __init__(self, name):
        self.name = name

    def put_item(self, Item):
        key = (Item["PK"], Item["SK"])
        STATE["ddb"][key] = dict(Item)
        return {}

    def get_item(self, Key):
        item = STATE["ddb"].get((Key["PK"], Key["SK"]))
        return {"Item": dict(item)} if item else {}

    def query(self, KeyConditionExpression):
        cond = KeyConditionExpression
        out = []
        for item in STATE["ddb"].values():
            ok = True
            for attr, op, val in cond.clauses:
                actual = item.get(attr)
                if op == "eq" and actual != val:
                    ok = False
                elif op == "begins_with" and not str(actual).startswith(val):
                    ok = False
            if ok:
                out.append(dict(item))
        return {"Items": out}


class _DynamoResource:
    def Table(self, name):
        return _Table(name)


# ---- KMS client -------------------------------------------------------------

class _Kms:
    def encrypt(self, KeyId, Plaintext):
        # reversible fake ciphertext
        blob = b"CT|" + Plaintext
        return {"CiphertextBlob": blob}

    def decrypt(self, CiphertextBlob, KeyId=None):
        assert CiphertextBlob.startswith(b"CT|")
        return {"Plaintext": CiphertextBlob[3:]}


# ---- S3 client --------------------------------------------------------------

class _Body:
    def __init__(self, data):
        self._data = data

    def read(self):
        return self._data


class _Paginator:
    def paginate(self, Bucket, Prefix=""):
        contents = [{"Key": k} for k in STATE["s3"] if k.startswith(Prefix)]
        yield {"Contents": contents}


class _S3:
    def put_object(self, Bucket, Key, Body, ContentType=None,
                   ObjectLockMode=None, ObjectLockRetainUntilDate=None):
        STATE["s3"][Key] = {
            "body": Body,
            "locked": ObjectLockMode == "COMPLIANCE",
            "retain": ObjectLockRetainUntilDate,
        }
        return {}

    def get_object(self, Bucket, Key):
        return {"Body": _Body(STATE["s3"][Key]["body"])}

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        return _Paginator()


# ---- EventBridge client -----------------------------------------------------

class _Events:
    def put_events(self, Entries):
        STATE["events"].extend(Entries)
        return {"FailedEntryCount": 0}


# ---- Lambda client ----------------------------------------------------------

class _Lambda:
    def invoke(self, FunctionName, InvocationType, Payload):
        fn = LAMBDA_REGISTRY[FunctionName]
        event = json.loads(Payload)
        result = fn(event, None)
        return {"Payload": _Body(json.dumps(result).encode("utf-8"))}


# ---- fake boto3 module ------------------------------------------------------

_CLIENTS = {"kms": _Kms, "s3": _S3, "events": _Events, "lambda": _Lambda}


def _client(name, *a, **k):
    return _CLIENTS[name]()


def _resource(name, *a, **k):
    assert name == "dynamodb"
    return _DynamoResource()


def install():
    boto3 = types.ModuleType("boto3")
    boto3.client = _client
    boto3.resource = _resource

    ddb_mod = types.ModuleType("boto3.dynamodb")
    cond_mod = types.ModuleType("boto3.dynamodb.conditions")
    cond_mod.Key = _Key
    ddb_mod.conditions = cond_mod
    boto3.dynamodb = ddb_mod

    sys.modules["boto3"] = boto3
    sys.modules["boto3.dynamodb"] = ddb_mod
    sys.modules["boto3.dynamodb.conditions"] = cond_mod
    return boto3
