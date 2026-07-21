from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .models import TencentCloudConfig

ROOT_DIR = Path(__file__).resolve().parents[2]
CONFIG_JSON_PATH = ROOT_DIR / "config.local.json"
DEFAULT_REGION = "ap-guangzhou"
DEFAULT_COS_BUCKET = "goumee-1444407842"


def _json_value_present(config_data: dict[str, Any], key: str) -> bool:
    if key not in config_data:
        return False
    value = config_data[key]
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _load_json_config() -> dict[str, Any]:
    if not CONFIG_JSON_PATH.exists():
        return {}

    try:
        with CONFIG_JSON_PATH.open("r", encoding="utf-8-sig") as handle:
            data = json.load(handle)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{CONFIG_JSON_PATH.name} is not valid JSON: {exc}") from exc
    except OSError as exc:
        raise ValueError(f"Failed to read {CONFIG_JSON_PATH.name}: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError(f"{CONFIG_JSON_PATH.name} must contain a top-level JSON object.")
    return data


def _load_env_value(*keys: str) -> str:
    for key in keys:
        value = os.getenv(key, "").strip()
        if value:
            return value
    return ""


def _config_or_env_value(config_data: dict[str, Any], json_key: str, env_keys: str | tuple[str, ...], default: Any = "") -> Any:
    if _json_value_present(config_data, json_key):
        return config_data[json_key]
    if isinstance(env_keys, str):
        env_keys = (env_keys,)
    env_value = _load_env_value(*env_keys)
    if env_value:
        return env_value
    return default


def _parse_int(value: Any, field_name: str, minimum: int | None = None) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be an integer.")
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an integer.") from exc
    if minimum is not None and parsed < minimum:
        raise ValueError(f"{field_name} must be greater than or equal to {minimum}.")
    return parsed


def _parse_float(value: Any, field_name: str, minimum: float | None = None) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be a number.")
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a number.") from exc
    if minimum is not None and parsed < minimum:
        raise ValueError(f"{field_name} must be greater than or equal to {minimum}.")
    return parsed


def _normalize_prefix(value: Any, default: str) -> str:
    normalized = str(value or "").strip().strip("/")
    if not normalized:
        normalized = default.strip("/")
    return normalized + "/"


def _validate_oss_group(merged: dict[str, Any]) -> None:
    oss_fields = {
        "oss_endpoint": str(_config_or_env_value(merged, "oss_endpoint", "OSS_ENDPOINT", "")).strip(),
        "oss_access_key_id": str(_config_or_env_value(merged, "oss_access_key_id", "OSS_ACCESS_KEY_ID", "")).strip(),
        "oss_access_key_secret": str(_config_or_env_value(merged, "oss_access_key_secret", "OSS_ACCESS_KEY_SECRET", "")).strip(),
        "oss_bucket": str(_config_or_env_value(merged, "oss_bucket", "OSS_BUCKET", "")).strip(),
    }
    present = [key for key, value in oss_fields.items() if value]
    missing = [key for key, value in oss_fields.items() if not value]
    if present and missing:
        raise ValueError("OSS upload config is incomplete; set " + ", ".join(missing) + ".")


def load_tencent_cloud_config(overrides: dict[str, Any] | None = None) -> TencentCloudConfig:
    config_data = _load_json_config()
    overrides = overrides or {}
    merged = dict(config_data)
    merged.update({key: value for key, value in overrides.items() if value not in (None, "")})

    secret_id = str(_config_or_env_value(merged, "tencent_secret_id", ("TENCENTCLOUD_SECRET_ID", "TENCENT_SECRET_ID"), "")).strip()
    secret_key = str(_config_or_env_value(merged, "tencent_secret_key", ("TENCENTCLOUD_SECRET_KEY", "TENCENT_SECRET_KEY"), "")).strip()
    if not secret_id:
        raise ValueError("Tencent secret id is required. Add tencent_secret_id to config.local.json or set TENCENTCLOUD_SECRET_ID.")
    if not secret_key:
        raise ValueError("Tencent secret key is required. Add tencent_secret_key to config.local.json or set TENCENTCLOUD_SECRET_KEY.")

    _validate_oss_group(merged)

    cos_bucket = str(_config_or_env_value(merged, "tencent_cos_bucket", "TENCENT_COS_BUCKET", DEFAULT_COS_BUCKET)).strip()
    if not cos_bucket:
        raise ValueError("Tencent COS bucket is required. Add tencent_cos_bucket to config.local.json or set TENCENT_COS_BUCKET.")

    return TencentCloudConfig(
        secret_id=secret_id,
        secret_key=secret_key,
        region=str(_config_or_env_value(merged, "tencent_region", "TENCENT_REGION", DEFAULT_REGION)).strip(),
        mps_host=str(_config_or_env_value(merged, "tencent_mps_host", "TENCENT_MPS_HOST", "mps.tencentcloudapi.com")).strip(),
        mps_version=str(_config_or_env_value(merged, "tencent_mps_version", "TENCENT_MPS_VERSION", "2019-06-12")).strip(),
        request_timeout=_parse_int(_config_or_env_value(merged, "tencent_request_timeout", "TENCENT_REQUEST_TIMEOUT", 120), "tencent_request_timeout", minimum=5),
        poll_interval=_parse_float(_config_or_env_value(merged, "tencent_poll_interval", "TENCENT_POLL_INTERVAL", 5.0), "tencent_poll_interval", minimum=0.1),
        max_wait_seconds=_parse_int(_config_or_env_value(merged, "tencent_max_wait_seconds", "TENCENT_MAX_WAIT_SECONDS", 3600), "tencent_max_wait_seconds", minimum=30),
        cos_bucket=cos_bucket,
        cos_input_prefix=_normalize_prefix(_config_or_env_value(merged, "tencent_cos_input_prefix", "TENCENT_COS_INPUT_PREFIX", "subtitle-input/"), "subtitle-input/"),
        cos_output_prefix=_normalize_prefix(_config_or_env_value(merged, "tencent_cos_output_prefix", "TENCENT_COS_OUTPUT_PREFIX", "subtitle-output/"), "subtitle-output/"),
        cos_burn_output_prefix=_normalize_prefix(_config_or_env_value(merged, "tencent_cos_burn_output_prefix", "TENCENT_COS_BURN_OUTPUT_PREFIX", "subtitle-burn-output/"), "subtitle-burn-output/"),
        subtitle_definition=_parse_int(_config_or_env_value(merged, "tencent_subtitle_definition", "TENCENT_SUBTITLE_DEFINITION", 122), "tencent_subtitle_definition", minimum=1),
        transcode_definition=_parse_int(_config_or_env_value(merged, "tencent_transcode_definition", "TENCENT_TRANSCODE_DEFINITION", 101005), "tencent_transcode_definition", minimum=1),
        area=str(_config_or_env_value(merged, "area", "AREA", "china")).strip(),
        oss_endpoint=str(_config_or_env_value(merged, "oss_endpoint", "OSS_ENDPOINT", "")).strip(),
        oss_access_key_id=str(_config_or_env_value(merged, "oss_access_key_id", "OSS_ACCESS_KEY_ID", "")).strip(),
        oss_access_key_secret=str(_config_or_env_value(merged, "oss_access_key_secret", "OSS_ACCESS_KEY_SECRET", "")).strip(),
        oss_bucket=str(_config_or_env_value(merged, "oss_bucket", "OSS_BUCKET", "")).strip(),
        oss_prefix=_normalize_prefix(_config_or_env_value(merged, "oss_prefix", "OSS_PREFIX", "GouMee-subtitle-input-tmp"), "GouMee-subtitle-input-tmp"),
        oss_signed_url_expires=_parse_int(_config_or_env_value(merged, "oss_signed_url_expires", "OSS_SIGNED_URL_EXPIRES", 86400), "oss_signed_url_expires", minimum=1),
    )
