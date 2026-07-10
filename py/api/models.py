from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class TencentCloudConfig:
    secret_id: str
    secret_key: str
    region: str = "ap-guangzhou"
    mps_host: str = "mps.tencentcloudapi.com"
    mps_version: str = "2019-06-12"
    request_timeout: int = 120
    poll_interval: float = 5.0
    max_wait_seconds: int = 3600
    cos_bucket: str = ""
    cos_input_prefix: str = "subtitle-input/"
    cos_output_prefix: str = "subtitle-output/"
    cos_burn_output_prefix: str = "subtitle-burn-output/"
    subtitle_definition: int = 122
    transcode_definition: int = 101005
    area: str = "china"
    oss_endpoint: str = ""
    oss_access_key_id: str = ""
    oss_access_key_secret: str = ""
    oss_bucket: str = ""
    oss_prefix: str = "kling_uploads"
    oss_signed_url_expires: int = 86400

    def has_cos_output(self) -> bool:
        return bool(str(self.cos_bucket).strip())

    def has_oss_config(self) -> bool:
        return bool(
            str(self.oss_endpoint).strip()
            and str(self.oss_access_key_id).strip()
            and str(self.oss_access_key_secret).strip()
            and str(self.oss_bucket).strip()
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TencentCosObject:
    bucket: str
    region: str
    object_key: str
    url: str
    local_file_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TencentInputSource:
    source_type: str
    url: str = ""
    cos_object: TencentCosObject | None = None
    local_file_path: str = ""

    def to_input_info(self) -> dict[str, Any]:
        if self.source_type.upper() == "URL":
            return {
                "Type": "URL",
                "UrlInputInfo": {
                    "Url": self.url,
                },
            }
        if self.source_type.upper() == "COS" and self.cos_object is not None:
            return {
                "Type": "COS",
                "CosInputInfo": {
                    "Bucket": self.cos_object.bucket,
                    "Region": self.cos_object.region,
                    "Object": self.cos_object.object_key,
                },
            }
        raise ValueError(f"Unsupported TencentInputSource: {self}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_type": self.source_type,
            "url": self.url,
            "cos_object": self.cos_object.to_dict() if self.cos_object else None,
            "local_file_path": self.local_file_path,
        }


@dataclass
class TencentSubtitleTaskRequest:
    input_source: TencentInputSource
    definition: int
    user_ext_para: dict[str, Any] = field(default_factory=dict)
    output_dir: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["input_source"] = self.input_source.to_dict()
        return data


@dataclass
class TencentBurnSubtitleTaskRequest:
    input_source: TencentInputSource
    subtitle_url: str
    definition: int
    output_dir: str = ""
    subtitle_style: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["input_source"] = self.input_source.to_dict()
        return data


@dataclass
class TencentTaskSummary:
    task_id: str
    status: str = "SUBMITTED"
    subtitle_urls: list[str] = field(default_factory=list)
    video_urls: list[str] = field(default_factory=list)
    message: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
