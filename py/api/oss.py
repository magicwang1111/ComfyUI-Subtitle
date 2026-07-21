from __future__ import annotations

import base64
import email.utils
import hmac
import mimetypes
import os
import re
import time
import urllib.parse
import uuid

import requests

from .models import TencentCloudConfig


def _normalize_local_file_path(file_path: str) -> str:
    normalized = os.path.abspath(os.path.expanduser(str(file_path or "").strip()))
    if not normalized:
        raise ValueError("file_path is required.")
    if not os.path.isfile(normalized):
        raise ValueError(f"Local file does not exist: {normalized}")
    return normalized


def _normalize_oss_endpoint(endpoint: str) -> tuple[str, str]:
    raw_endpoint = str(endpoint or "").strip().rstrip("/")
    if not raw_endpoint:
        raise ValueError("oss_endpoint is required.")
    if "://" not in raw_endpoint:
        raw_endpoint = f"https://{raw_endpoint}"
    parsed = urllib.parse.urlparse(raw_endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("oss_endpoint must be a valid OSS endpoint host or URL.")
    if parsed.path not in {"", "/"}:
        raise ValueError("oss_endpoint must not include a path.")
    return parsed.scheme, parsed.netloc


def _oss_signature(access_key_secret: str, string_to_sign: str) -> str:
    digest = hmac.new(
        access_key_secret.encode("utf-8"),
        string_to_sign.encode("utf-8"),
        "sha1",
    ).digest()
    return base64.b64encode(digest).decode("utf-8")


def _build_oss_object_key(file_path: str, prefix: str) -> str:
    filename = os.path.basename(os.fspath(file_path))
    safe_filename = re.sub(r"[^A-Za-z0-9._-]+", "_", filename).strip("._")
    if not safe_filename:
        safe_filename = "media"
    date_folder = time.strftime("%Y%m%d", time.gmtime())
    return f"{prefix.strip('/')}/{date_folder}/{uuid.uuid4().hex}-{safe_filename}".lstrip("/")


def _build_oss_url(config: TencentCloudConfig, object_key: str, query: dict[str, str] | None = None) -> str:
    scheme, endpoint_host = _normalize_oss_endpoint(config.oss_endpoint)
    quoted_key = urllib.parse.quote(object_key, safe="/")
    base_url = f"{scheme}://{config.oss_bucket}.{endpoint_host}/{quoted_key}"
    if not query:
        return base_url
    return base_url + "?" + urllib.parse.urlencode(query)


def _build_oss_authorization_header(config: TencentCloudConfig, method: str, object_key: str, content_type: str, date: str) -> str:
    string_to_sign = (
        f"{method}\n"
        f"\n"
        f"{content_type}\n"
        f"{date}\n"
        f"/{config.oss_bucket}/{object_key}"
    )
    signature = _oss_signature(config.oss_access_key_secret, string_to_sign)
    return f"OSS {config.oss_access_key_id}:{signature}"


def _build_oss_signed_download_url(config: TencentCloudConfig, object_key: str) -> str:
    expires = int(time.time()) + int(config.oss_signed_url_expires)
    string_to_sign = f"GET\n\n\n{expires}\n/{config.oss_bucket}/{object_key}"
    signature = _oss_signature(config.oss_access_key_secret, string_to_sign)
    return _build_oss_url(
        config,
        object_key,
        {
            "OSSAccessKeyId": config.oss_access_key_id,
            "Expires": str(expires),
            "Signature": signature,
        },
    )


def upload_file_to_oss(config: TencentCloudConfig, file_path: str, timeout: float | None = None, prefix: str | None = None) -> tuple[str, str]:
    if not config.has_oss_config():
        raise ValueError("OSS upload config is not configured.")
    normalized_path = _normalize_local_file_path(file_path)
    object_key = _build_oss_object_key(normalized_path, prefix or config.oss_prefix)
    content_type = mimetypes.guess_type(normalized_path)[0] or "application/octet-stream"
    upload_url = _build_oss_url(config, object_key)
    date = email.utils.formatdate(usegmt=True)
    authorization = _build_oss_authorization_header(config, "PUT", object_key, content_type, date)
    with open(normalized_path, "rb") as handle:
        response = requests.put(
            upload_url,
            data=handle,
            headers={
                "Authorization": authorization,
                "Content-Type": content_type,
                "Date": date,
            },
            timeout=float(timeout or config.request_timeout),
        )
    response.raise_for_status()
    return _build_oss_signed_download_url(config, object_key), object_key
