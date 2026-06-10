"""Mock Issuer Lambda.

Approves when the PAN is 13-19 digits and 0 < amount <= 1000. Declines
otherwise with a reason. No persistence, no special IAM. Represents the
external card network / funding source in the architecture.
"""

import re
import secrets

_PAN_RE = re.compile(r"^\d{13,19}$")
_LIMIT = 1000


def handler(event, _context):
    pan = event.get("pan", "")
    amount = event.get("amount")

    if not _PAN_RE.match(pan or ""):
        return {"approved": False, "reason": "invalid_pan"}

    if not isinstance(amount, (int, float)) or amount <= 0:
        return {"approved": False, "reason": "invalid_amount"}

    if amount > _LIMIT:
        return {"approved": False, "reason": "limit_exceeded"}

    auth_code = "AUTH" + secrets.token_hex(3).upper()
    return {"approved": True, "authCode": auth_code}
