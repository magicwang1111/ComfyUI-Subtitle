from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import time
from typing import Any


def canonical_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sign_tc3(secret_key: str, service: str, date_str: str, string_to_sign: str) -> str:
    secret_date = hmac.new(("TC3" + secret_key).encode("utf-8"), date_str.encode("utf-8"), hashlib.sha256).digest()
    secret_service = hmac.new(secret_date, service.encode("utf-8"), hashlib.sha256).digest()
    secret_signing = hmac.new(secret_service, b"tc3_request", hashlib.sha256).digest()
    return hmac.new(secret_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()


def build_tc3_headers(*, secret_id: str, secret_key: str, service: str, host: str, action: str, version: str, region: str, payload: dict[str, Any], timestamp: int | None = None) -> tuple[dict[str, str], str]:
    timestamp = int(timestamp or time.time())
    date_str = dt.datetime.fromtimestamp(timestamp, dt.timezone.utc).strftime("%Y-%m-%d")
    body = canonical_json(payload)
    canonical_request = "\n".join([
        "POST",
        "/",
        "",
        f"content-type:application/json; charset=utf-8\nhost:{host}\nx-tc-action:{action.lower()}\n",
        "content-type;host;x-tc-action",
        _sha256_hex(body),
    ])
    credential_scope = f"{date_str}/{service}/tc3_request"
    string_to_sign = "\n".join([
        "TC3-HMAC-SHA256",
        str(timestamp),
        credential_scope,
        _sha256_hex(canonical_request),
    ])
    signature = _sign_tc3(secret_key, service, date_str, string_to_sign)
    authorization = (
        f"TC3-HMAC-SHA256 Credential={secret_id}/{credential_scope}, "
        f"SignedHeaders=content-type;host;x-tc-action, Signature={signature}"
    )
    headers = {
        "Authorization": authorization,
        "Content-Type": "application/json; charset=utf-8",
        "Host": host,
        "X-TC-Action": action,
        "X-TC-Version": version,
        "X-TC-Region": region,
        "X-TC-Timestamp": str(timestamp),
    }
    return headers, body
