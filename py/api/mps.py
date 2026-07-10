from __future__ import annotations

import json
import time
from collections.abc import Iterable
from typing import Any

import requests

from .models import TencentBurnSubtitleTaskRequest, TencentCloudConfig, TencentInputSource, TencentSubtitleTaskRequest, TencentTaskSummary
from .tc3 import build_tc3_headers, canonical_json

MPS_SERVICE = "mps"
PROCESS_MEDIA_ACTION = "ProcessMedia"
DESCRIBE_TASK_DETAIL_ACTION = "DescribeTaskDetail"


def _post_mps(config: TencentCloudConfig, action: str, payload: dict[str, Any]) -> dict[str, Any]:
    headers, body = build_tc3_headers(
        secret_id=config.secret_id,
        secret_key=config.secret_key,
        service=MPS_SERVICE,
        host=config.mps_host,
        action=action,
        version=config.mps_version,
        region=config.region,
        payload=payload,
    )
    response = requests.post(
        f"https://{config.mps_host}/",
        headers=headers,
        data=body.encode("utf-8"),
        timeout=config.request_timeout,
    )
    try:
        decoded = response.json()
    except ValueError as exc:
        raise RuntimeError(f"{action} returned non-JSON HTTP {response.status_code}: {response.text[:500]}") from exc
    top = decoded.get("Response")
    if response.status_code != 200 or not isinstance(top, dict):
        raise RuntimeError(f"{action} HTTP {response.status_code}: {decoded}")
    if "Error" in top:
        error = top.get("Error") or {}
        raise RuntimeError(
            f"{action} Code={error.get('Code')}, Message={error.get('Message')}, RequestId={top.get('RequestId')}"
        )
    return top


def _build_output_storage(config: TencentCloudConfig) -> dict[str, Any] | None:
    if config.has_cos_output():
        return {
            "Type": "COS",
            "CosOutputStorage": {
                "Bucket": config.cos_bucket,
                "Region": config.region,
            },
        }
    return None


def _build_oss_std_ext_info(config: TencentCloudConfig) -> str:
    if not config.has_oss_config():
        raise RuntimeError("OSS output requested but OSS config is incomplete.")
    payload = {
        "cos_info": {
            "storage_type": "oss",
            "bucket": config.oss_bucket,
            "region": config.region,
            "id": config.oss_access_key_id,
            "key": config.oss_access_key_secret,
            "host": config.oss_endpoint,
        }
    }
    return canonical_json(payload)


def build_smart_subtitle_user_ext_para(*, accurate_mode: bool = False, need_wordlist: bool = False, adapt_words: str = "", target_language: str = "") -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if accurate_mode:
        payload["accurate_mode"] = 1
    if need_wordlist:
        payload["need_wordlist"] = 1
    if adapt_words.strip():
        payload["adapt_words"] = adapt_words.strip()
    if target_language.strip():
        payload["translate_dst_language"] = target_language.strip()
    return payload


def create_smart_subtitle_task(
    config: TencentCloudConfig,
    input_source: TencentInputSource,
    *,
    definition: int | None = None,
    accurate_mode: bool = False,
    need_wordlist: bool = False,
    adapt_words: str = "",
    target_language: str = "",
    output_dir: str = "",
) -> tuple[TencentTaskSummary, dict[str, Any], dict[str, Any]]:
    user_ext_para = build_smart_subtitle_user_ext_para(
        accurate_mode=accurate_mode,
        need_wordlist=need_wordlist,
        adapt_words=adapt_words,
        target_language=target_language,
    )
    request = TencentSubtitleTaskRequest(
        input_source=input_source,
        definition=int(definition or config.subtitle_definition),
        user_ext_para=user_ext_para,
        output_dir=output_dir.strip() or f"/{config.cos_output_prefix.strip('/')}/",
    )
    payload = {
        "InputInfo": request.input_source.to_input_info(),
        "SmartSubtitlesTask": {
            "Definition": request.definition,
            "UserExtPara": canonical_json(request.user_ext_para) if request.user_ext_para else "",
        },
        "OutputDir": request.output_dir,
    }
    output_storage = _build_output_storage(config)
    if output_storage is not None:
        payload["OutputStorage"] = output_storage
    elif config.has_oss_config():
        payload["StdExtInfo"] = _build_oss_std_ext_info(config)
    else:
        raise RuntimeError("No valid output storage is configured for SmartSubtitlesTask.")
    response = _post_mps(config, PROCESS_MEDIA_ACTION, payload)
    task_id = str(response.get("TaskId") or "").strip()
    if not task_id:
        raise RuntimeError(f"ProcessMedia returned no TaskId: {response}")
    summary = TencentTaskSummary(task_id=task_id, status="SUBMITTED", raw={"submit": response})
    return summary, payload, response


def create_burn_subtitle_task(
    config: TencentCloudConfig,
    input_source: TencentInputSource,
    *,
    subtitle_url: str,
    definition: int | None = None,
    output_dir: str = "",
    subtitle_style: dict[str, Any] | None = None,
) -> tuple[TencentTaskSummary, dict[str, Any], dict[str, Any]]:
    normalized_subtitle_url = str(subtitle_url or "").strip()
    if not normalized_subtitle_url:
        raise ValueError("subtitle_url is required for burn subtitle task.")

    request = TencentBurnSubtitleTaskRequest(
        input_source=input_source,
        subtitle_url=normalized_subtitle_url,
        definition=int(definition or config.transcode_definition),
        output_dir=output_dir.strip() or f"/{config.cos_burn_output_prefix.strip('/')}/",
        subtitle_style=subtitle_style or {},
    )
    subtitle_template = {"Path": request.subtitle_url}
    subtitle_template.update(request.subtitle_style)
    payload = {
        "InputInfo": request.input_source.to_input_info(),
        "OutputDir": request.output_dir,
        "MediaProcessTask": {
            "TranscodeTaskSet": [
                {
                    "Definition": request.definition,
                    "OverrideParameter": {
                        "SubtitleTemplate": subtitle_template,
                    },
                }
            ]
        },
    }
    output_storage = _build_output_storage(config)
    if output_storage is not None:
        payload["OutputStorage"] = output_storage
    elif config.has_oss_config():
        payload["StdExtInfo"] = _build_oss_std_ext_info(config)
    else:
        raise RuntimeError("No valid output storage is configured for burn subtitle task.")
    response = _post_mps(config, PROCESS_MEDIA_ACTION, payload)
    task_id = str(response.get("TaskId") or "").strip()
    if not task_id:
        raise RuntimeError(f"ProcessMedia burn subtitle returned no TaskId: {response}")
    summary = TencentTaskSummary(task_id=task_id, status="SUBMITTED", raw={"submit": response})
    return summary, payload, response


def describe_task_detail(config: TencentCloudConfig, task_id: str) -> dict[str, Any]:
    normalized_task_id = str(task_id or "").strip()
    if not normalized_task_id:
        raise ValueError("task_id is required.")
    return _post_mps(config, DESCRIBE_TASK_DETAIL_ACTION, {"TaskId": normalized_task_id})


def _collect_strings(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, str):
        candidate = value.strip()
        if candidate.startswith("http://") or candidate.startswith("https://") or candidate.startswith("/"):
            found.append(candidate)
        return found
    if isinstance(value, dict):
        for item in value.values():
            found.extend(_collect_strings(item))
        return found
    if isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray)):
        for item in value:
            found.extend(_collect_strings(item))
    return found


def _dedupe_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _extract_status(response: dict[str, Any]) -> tuple[str, str]:
    candidates = [
        response.get("Status"),
        response.get("WorkflowTask", {}).get("Status") if isinstance(response.get("WorkflowTask"), dict) else None,
        response.get("TaskInfo", {}).get("Status") if isinstance(response.get("TaskInfo"), dict) else None,
    ]
    for candidate in candidates:
        if candidate:
            return str(candidate).strip().upper(), ""

    raw_text = json.dumps(response, ensure_ascii=False)
    if "FAIL" in raw_text.upper():
        return "FAILED", "Task detail contains failure markers."
    return "UNKNOWN", ""


def normalize_task_detail(task_id: str, response: dict[str, Any]) -> TencentTaskSummary:
    status, message = _extract_status(response)
    all_paths = _dedupe_keep_order(_collect_strings(response))
    subtitle_urls = [value for value in all_paths if any(value.lower().endswith(ext) for ext in (".vtt", ".srt", ".ass", ".ssa"))]
    video_urls = [value for value in all_paths if any(value.lower().endswith(ext) for ext in (".mp4", ".mov", ".mkv", ".m3u8"))]
    return TencentTaskSummary(
        task_id=str(task_id),
        status=status,
        subtitle_urls=subtitle_urls,
        video_urls=video_urls,
        message=message,
        raw=response,
    )


def wait_for_task(
    config: TencentCloudConfig,
    task_id: str,
    *,
    poll_interval: float | None = None,
    max_wait_seconds: int | None = None,
) -> tuple[TencentTaskSummary, dict[str, Any]]:
    started = time.monotonic()
    interval = float(poll_interval or config.poll_interval)
    timeout_seconds = int(max_wait_seconds or config.max_wait_seconds)
    while True:
        response = describe_task_detail(config, task_id)
        summary = normalize_task_detail(task_id, response)
        if summary.status in {"SUCCESS", "FINISH", "FINISHED"}:
            return summary, response
        if summary.status in {"FAILED", "FAIL", "ABORTED"}:
            raise RuntimeError(summary.message or f"Tencent MPS task failed: {summary.to_dict()}")
        if time.monotonic() - started > timeout_seconds:
            raise TimeoutError(f"Tencent MPS task {task_id} did not finish within {timeout_seconds} seconds.")
        time.sleep(interval)
