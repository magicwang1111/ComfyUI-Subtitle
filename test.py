import json
import os
import sys
import tempfile

COMFY_ROOT = r"D:\ComfyUI"
REPO_ROOT = r"D:\ComfyUI\custom_nodes\ComfyUI-Subtitle"
LOCAL_VIDEO_PATH = r"D:\测试素材\20260707腾讯文档\seedance_00006_.mp4"

sys.path.insert(0, COMFY_ROOT)
sys.path.insert(0, REPO_ROOT)

from py.api import (
    TencentInputSource,
    create_burn_subtitle_task,
    create_smart_subtitle_task,
    download_file,
    load_tencent_cloud_config,
    sign_cos_url,
    upload_file_to_oss,
    wait_for_task,
)
from py.nodes import _build_burn_subtitle_ass_text, _read_text_file


def emit(payload):
    print(json.dumps(payload, ensure_ascii=False), flush=True)


config = load_tencent_cloud_config()
emit({
    "stage": "config_loaded",
    "region": config.region,
    "cos_output_bucket": config.cos_bucket,
    "oss_input_bucket": config.oss_bucket,
})

temp_dir = tempfile.mkdtemp(prefix="comfyui_subtitle_test_")
emit({"stage": "temp_dir", "path": temp_dir})

oss_video_url, oss_video_object_key = upload_file_to_oss(config, LOCAL_VIDEO_PATH)
emit({"stage": "video_uploaded", "storage": "OSS", "object_key": oss_video_object_key})

input_source = TencentInputSource(
    source_type="URL",
    url=oss_video_url,
    local_file_path=LOCAL_VIDEO_PATH,
)

subtitle_submit_summary, _, _ = create_smart_subtitle_task(config, input_source)
emit({"stage": "subtitle_submitted", "task_id": subtitle_submit_summary.task_id})

subtitle_summary, _ = wait_for_task(config, subtitle_submit_summary.task_id, max_wait_seconds=540)
emit({
    "stage": "subtitle_finished",
    "status": subtitle_summary.status,
    "subtitle_url_count": len(subtitle_summary.subtitle_urls),
    "video_url_count": len(subtitle_summary.video_urls),
})

if not subtitle_summary.subtitle_urls:
    raise RuntimeError("Subtitle task returned no subtitle URLs.")

signed_subtitle_url = sign_cos_url(config, subtitle_summary.subtitle_urls[0])
generated_subtitle_local_path = download_file(
    signed_subtitle_url,
    temp_dir,
    filename_prefix="integration_generated_subtitle",
)
generated_subtitle_text = _read_text_file(generated_subtitle_local_path)
emit({
    "stage": "subtitle_downloaded",
    "subtitle_local_path": generated_subtitle_local_path,
    "chars": len(generated_subtitle_text),
})

burn_ass_text = _build_burn_subtitle_ass_text(
    generated_subtitle_text,
    font_name="simkai.ttf",
    font_size=24,
    font_color="#FFFFFF",
    font_alpha=0.9,
    background_alpha=0.2,
    subtitle_position="bottom",
    target_language="",
)
burn_ass_local_path = os.path.join(temp_dir, "integration_burn.ass")
with open(burn_ass_local_path, "w", encoding="utf-8") as handle:
    handle.write(burn_ass_text)
emit({
    "stage": "burn_ass_written",
    "ass_local_path": burn_ass_local_path,
    "chars": len(burn_ass_text),
})

burn_subtitle_signed_url, burn_subtitle_object_key = upload_file_to_oss(config, burn_ass_local_path)
emit({"stage": "burn_ass_uploaded", "storage": "OSS", "object_key": burn_subtitle_object_key})
burn_submit_summary, _, _ = create_burn_subtitle_task(
    config,
    input_source,
    subtitle_url=burn_subtitle_signed_url,
)
emit({"stage": "burn_submitted", "task_id": burn_submit_summary.task_id})

burn_summary, _ = wait_for_task(config, burn_submit_summary.task_id, max_wait_seconds=540)
emit({
    "stage": "burn_finished",
    "status": burn_summary.status,
    "video_url_count": len(burn_summary.video_urls),
    "subtitle_url_count": len(burn_summary.subtitle_urls),
})

if not burn_summary.video_urls:
    raise RuntimeError("Burn task returned no video URLs.")

signed_video_url = sign_cos_url(config, burn_summary.video_urls[0])
final_video_local_path = download_file(
    signed_video_url,
    temp_dir,
    filename_prefix="integration_burn_video",
)
emit({
    "stage": "done",
    "subtitle_task_id": subtitle_submit_summary.task_id,
    "burn_task_id": burn_submit_summary.task_id,
    "final_video_local_path": final_video_local_path,
    "temp_dir": temp_dir,
})
