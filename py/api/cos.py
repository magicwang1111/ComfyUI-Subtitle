from __future__ import annotations

import hashlib
import hmac
import mimetypes
import os
import time
import uuid
from pathlib import Path
from urllib.parse import quote, urlparse

import requests

from .models import TencentCloudConfig, TencentCosObject


def _normalize_local_file_path(file_path: str) -> str:
    normalized = os.path.abspath(os.path.expanduser(str(file_path or "").strip()))
    if not normalized:
        raise ValueError("local_file_path is required.")
    if not os.path.isfile(normalized):
        raise ValueError(f"Local file does not exist: {normalized}")
    return normalized


def _guess_content_type(file_path: str) -> str:
    return mimetypes.guess_type(file_path)[0] or "application/octet-stream"


def build_cos_object_key(config: TencentCloudConfig, local_file_path: str, object_key: str | None = None) -> str:
    if object_key:
        return str(object_key).strip().lstrip("/")
    filename = Path(local_file_path).name
    safe_filename = "".join(char if char.isalnum() or char in {".", "_", "-"} else "_" for char in filename).strip("._") or "media"
    date_folder = time.strftime("%Y%m%d", time.gmtime())
    return f"{config.cos_input_prefix}{date_folder}/{uuid.uuid4().hex}-{safe_filename}".lstrip("/")


def build_cos_url(bucket: str, region: str, object_key: str) -> str:
    quoted_key = quote(object_key.lstrip("/"), safe="/")
    return f"https://{bucket}.cos.{region}.myqcloud.com/{quoted_key}"


def _cos_signature(secret_key: str, sign_key: str) -> str:
    return hmac.new(secret_key.encode("utf-8"), sign_key.encode("utf-8"), hashlib.sha1).hexdigest()


def _sha1_bytes(file_path: str) -> tuple[str, int]:
    sha1 = hashlib.sha1()
    total_size = 0
    with open(file_path, "rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            total_size += len(chunk)
            sha1.update(chunk)
    return sha1.hexdigest(), total_size


def _build_cos_authorization(config: TencentCloudConfig, http_method: str, object_key: str, headers: dict[str, str], query: dict[str, str] | None = None, now: int | None = None, valid_for_seconds: int = 3600) -> str:
    now = int(now or time.time())
    sign_time = f"{now};{now + valid_for_seconds}"
    key_time = sign_time
    sign_key = _cos_signature(config.secret_key, key_time)

    normalized_headers = {key.lower(): " ".join(str(value).strip().split()) for key, value in headers.items()}
    header_list = ";".join(sorted(normalized_headers))
    http_headers = "&".join(f"{quote(key, safe='').lower()}={quote(normalized_headers[key], safe='-_.~')}" for key in sorted(normalized_headers))

    normalized_query = {str(key).lower(): str(value).strip() for key, value in (query or {}).items()}
    url_param_list = ";".join(sorted(normalized_query))
    http_parameters = "&".join(f"{quote(key, safe='').lower()}={quote(normalized_query[key], safe='-_.~')}" for key in sorted(normalized_query))

    http_string = "\n".join([
        http_method.lower(),
        "/" + object_key.lstrip("/"),
        http_parameters,
        http_headers,
        "",
    ])
    string_to_sign = "\n".join([
        "sha1",
        sign_time,
        hashlib.sha1(http_string.encode("utf-8")).hexdigest(),
        "",
    ])
    signature = hmac.new(sign_key.encode("utf-8"), string_to_sign.encode("utf-8"), hashlib.sha1).hexdigest()
    return (
        f"q-sign-algorithm=sha1&q-ak={config.secret_id}&q-sign-time={sign_time}&q-key-time={key_time}"
        f"&q-header-list={header_list}&q-url-param-list={url_param_list}&q-signature={signature}"
    )


def sign_cos_url(config: TencentCloudConfig, resource_url: str, valid_for_seconds: int = 3600) -> str:
    object_key = urlparse(str(resource_url or "")).path.lstrip("/")
    if not object_key:
        raise ValueError("resource_url must include a COS object path.")
    headers = {
        "Host": f"{config.cos_bucket}.cos.{config.region}.myqcloud.com",
    }
    authorization = _build_cos_authorization(
        config,
        "GET",
        object_key,
        headers,
        valid_for_seconds=valid_for_seconds,
    )
    return build_cos_url(config.cos_bucket, config.region, object_key) + "?" + authorization


def upload_file_to_cos(config: TencentCloudConfig, local_file_path: str, object_key: str | None = None) -> TencentCosObject:
    normalized_path = _normalize_local_file_path(local_file_path)
    resolved_object_key = build_cos_object_key(config, normalized_path, object_key)
    content_type = _guess_content_type(normalized_path)
    sha1_hex, total_size = _sha1_bytes(normalized_path)
    upload_url = build_cos_url(config.cos_bucket, config.region, resolved_object_key)

    headers = {
        "Host": f"{config.cos_bucket}.cos.{config.region}.myqcloud.com",
        "Content-Type": content_type,
        "Content-Length": str(total_size),
        "x-cos-content-sha1": sha1_hex,
    }
    authorization = _build_cos_authorization(config, "PUT", resolved_object_key, headers)
    headers["Authorization"] = authorization

    with open(normalized_path, "rb") as handle:
        response = requests.put(upload_url, data=handle, headers=headers, timeout=config.request_timeout)
    if response.status_code not in {200, 201}:
        raise RuntimeError(f"COS upload failed with HTTP {response.status_code}: {response.text[:500]}")

    return TencentCosObject(
        bucket=config.cos_bucket,
        region=config.region,
        object_key="/" + resolved_object_key.lstrip("/"),
        url=upload_url,
        local_file_path=normalized_path,
    )


def upload_video_to_cos(config: TencentCloudConfig, local_file_path: str, object_key: str | None = None) -> TencentCosObject:
    return upload_file_to_cos(config, local_file_path, object_key=object_key)


def download_file(file_url: str, output_dir: str, filename_prefix: str = "tencent-subtitle") -> str:
    normalized_url = str(file_url or "").strip()
    if not normalized_url:
        raise ValueError("file_url is required.")
    parsed = urlparse(normalized_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"file_url is not a valid HTTP(S) URL: {normalized_url}")

    os.makedirs(output_dir, exist_ok=True)
    suffix = Path(parsed.path).suffix or ".bin"
    safe_prefix = "".join(char if char.isalnum() or char in {"_", "-"} else "_" for char in filename_prefix).strip("_-") or "tencent-subtitle"
    target_path = os.path.join(output_dir, f"{safe_prefix}_{uuid.uuid4().hex[:12]}{suffix}")

    response = requests.get(normalized_url, timeout=120)
    response.raise_for_status()
    with open(target_path, "wb") as handle:
        handle.write(response.content)
    return target_path
