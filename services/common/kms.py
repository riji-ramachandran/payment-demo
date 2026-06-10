"""KMS encrypt/decrypt helpers for the PAN.

The PAN is < 4 KB so we use direct KMS Encrypt/Decrypt rather than envelope
encryption. Ciphertext is base64-encoded for storage as a DynamoDB string.

PCI Req 3.4: the plaintext PAN exists only transiently in the Token Service.
Never log plaintext, never put it in an exception message.
"""

import base64
import os

import boto3

_kms = boto3.client("kms")

KMS_KEY_ID = os.environ.get("KMS_KEY_ID")


def encrypt(plaintext: str) -> str:
    """Encrypt a plaintext string with the CMK; return base64 ciphertext."""
    resp = _kms.encrypt(KeyId=KMS_KEY_ID, Plaintext=plaintext.encode("utf-8"))
    return base64.b64encode(resp["CiphertextBlob"]).decode("ascii")


def decrypt(ciphertext_b64: str) -> str:
    """Decrypt base64 ciphertext back to the plaintext string."""
    blob = base64.b64decode(ciphertext_b64)
    resp = _kms.decrypt(KeyId=KMS_KEY_ID, CiphertextBlob=blob)
    return resp["Plaintext"].decode("utf-8")
