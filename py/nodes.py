from __future__ import annotations

import json
import os
import shutil
import subprocess
import textwrap
import urllib.parse
from pathlib import Path

import folder_paths

from .api import (
    TencentInputSource,
    create_smart_subtitle_task,
    download_file,
    load_tencent_cloud_config,
    sign_cos_url,
    upload_file_to_oss,
    wait_for_task,
)

NODE_CATEGORY = "Tencent/Subtitle"
DEFAULT_VIDEO_FILENAME_PREFIX = "tencent/subtitle/video"
DEFAULT_SUBTITLE_FILENAME_PREFIX = "tencent/subtitle/subtitle"
DEFAULT_PREVIEW_FILENAME_PREFIX = "video/timeline"
DEFAULT_FILENAME_PREFIX = "tencent_subtitle"
SUBTITLE_FONTS = ["simkai.ttf", "simhei.ttf", "simsun.ttf", "msyh.ttf", "msyhbd.ttf", "hkjgt.ttf", "dhttx.ttf"]
TARGET_LANGUAGES = ["auto", "zh", "en", "ja", "ko", "fr", "de", "es", "pt", "ru", "it", "th", "vi", "id", "ms", "ar", "hi"]
SUBTITLE_LANGUAGE_MODES = ["auto", "source", "translation", "bilingual"]


def _log_subtitle_burn(stage: str, **details) -> None:
    """Write concise, non-sensitive task progress to the ComfyUI console."""
    payload = {"stage": stage, **details}
    print(f"[TencentSubtitleBurn] {json.dumps(payload, ensure_ascii=False, default=str)}", flush=True)


def _saved_result(filename, subfolder, folder_type):
    return {
        "filename": filename,
        "subfolder": subfolder,
        "type": folder_type,
    }


def _build_local_media_view_url(filename, subfolder, folder_type):
    query = [
        f"type={urllib.parse.quote(str(folder_type), safe='')}",
        f"filename={urllib.parse.quote(str(filename), safe='')}",
    ]
    if subfolder:
        query.append(f"subfolder={urllib.parse.quote(str(subfolder), safe='')}")
    return "/api/view?" + "&".join(query)


def _register_output_asset(file_path):
    try:
        import app.assets.services.ingest as asset_ingest
    except Exception:
        return

    ingest_existing_file = getattr(asset_ingest, "ingest_existing_file", None)
    if not callable(ingest_existing_file):
        return

    try:
        ingest_existing_file(file_path)
    except Exception:
        return


def _read_text_file(file_path: str) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            with open(file_path, "r", encoding=encoding) as handle:
                return handle.read()
        except UnicodeDecodeError:
            continue
    raise ValueError(f"Failed to read subtitle file with supported encodings: {file_path}")


def _resolve_local_video_path(value: str) -> str:
    candidate = str(value or "").strip()
    if folder_paths.exists_annotated_filepath(candidate):
        return folder_paths.get_annotated_filepath(candidate)

    normalized = os.path.abspath(os.path.expanduser(candidate))
    if not os.path.isfile(normalized):
        raise ValueError(f"Local video file does not exist: {normalized}")
    return normalized


def _resolve_vhs_video_path(prompt, unique_id) -> str:
    if not isinstance(prompt, dict):
        raise ValueError("VHS input requires ComfyUI prompt context.")
    current_node = prompt.get(str(unique_id))
    inputs = current_node.get("inputs") if isinstance(current_node, dict) else None
    link = inputs.get("vhs_video_info") if isinstance(inputs, dict) else None
    if not isinstance(link, list) or len(link) < 2:
        raise ValueError("VHS input must be linked from a VHS Load Video node.")

    source_node = prompt.get(str(link[0]))
    source_inputs = source_node.get("inputs") if isinstance(source_node, dict) else None
    source_type = source_node.get("class_type") if isinstance(source_node, dict) else ""
    if source_type not in {"VHS_LoadVideo", "VHS_LoadVideoFFmpeg", "VHS_LoadVideoPath", "VHS_LoadVideoFFmpegPath"}:
        raise ValueError("vhs_video_info must come from VHS Load Video or VHS Load Video (Path).")
    video_path = source_inputs.get("video") if isinstance(source_inputs, dict) else ""
    if not isinstance(video_path, str) or not video_path.strip():
        raise ValueError("The linked VHS Load Video node must use a local video path.")
    return _resolve_local_video_path(video_path)


def _resolve_video_input(local_video: str, video_file: str, video_url: str, vhs_video_info=None, prompt=None, unique_id=None) -> str:
    local_values = [str(value or "").strip() for value in (local_video, video_file) if str(value or "").strip()]
    normalized_url = str(video_url or "").strip()
    has_vhs_input = vhs_video_info is not None
    if len(local_values) + bool(normalized_url) + has_vhs_input != 1:
        raise ValueError("Provide exactly one source: local_video, video_file, video_url, or vhs_video_info.")
    if normalized_url:
        parsed = urllib.parse.urlparse(normalized_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("video_url must be a valid HTTP(S) URL.")
        return download_file(normalized_url, folder_paths.get_temp_directory(), filename_prefix="tencent_subtitle_input")
    if has_vhs_input:
        return _resolve_vhs_video_path(prompt, unique_id)
    return _resolve_local_video_path(local_values[0])


def _make_output_target(filename_prefix: str, suffix: str):
    output_dir = folder_paths.get_output_directory()
    full_output_folder, filename, counter, subfolder, _ = folder_paths.get_save_image_path(filename_prefix, output_dir)
    os.makedirs(full_output_folder, exist_ok=True)
    final_name = f"{filename}_{counter:05}_.{suffix.lstrip('.')}"
    return full_output_folder, subfolder, final_name, os.path.join(full_output_folder, final_name)


def _write_text_output(text: str, filename_prefix: str, suffix: str) -> str:
    _, _, _, target_path = _make_output_target(filename_prefix, suffix)
    with open(target_path, "w", encoding="utf-8") as handle:
        handle.write(text)
    try:
        _register_output_asset(target_path)
    except Exception:
        pass
    return target_path


def _find_binary_path(explicit_path: str, *names: str) -> str:
    configured_path = str(explicit_path or "").strip()
    if configured_path and os.path.isfile(configured_path):
        return os.path.abspath(configured_path)
    resolved_path = next((shutil.which(name) for name in names if shutil.which(name)), None)
    if resolved_path:
        return resolved_path
    raise RuntimeError(f"Required local binary was not found: {names[0]}")


def _find_ffmpeg_path(explicit_path: str = "") -> str:
    try:
        return _find_binary_path(explicit_path, "ffmpeg", "ffmpeg.exe")
    except RuntimeError:
        pass
    ffmpeg_path = None
    try:
        from imageio_ffmpeg import get_ffmpeg_exe

        ffmpeg_path = get_ffmpeg_exe()
    except Exception:
        pass
    if ffmpeg_path:
        return ffmpeg_path
    raise RuntimeError("Local FFmpeg is required for subtitle burning. Configure ffmpeg_path or add ffmpeg to PATH.")


def _probe_ffmpeg_encoder(ffmpeg_path: str, encoder: str) -> bool:
    try:
        subprocess.run(
            [
                ffmpeg_path,
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=c=black:s=640x360:r=30:d=0.2",
                "-frames:v",
                "1",
                "-c:v",
                encoder,
                "-f",
                "null",
                "-",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
        return True
    except Exception:
        return False


def _resolve_ffmpeg_encoder(ffmpeg_path: str, configured_encoder: str) -> str:
    encoder = str(configured_encoder or "auto").strip() or "auto"
    if encoder != "auto":
        return encoder
    for candidate in ("h264_nvenc", "h264_qsv", "h264_amf"):
        if _probe_ffmpeg_encoder(ffmpeg_path, candidate):
            return candidate
    return "libx264"


def _ffmpeg_output_args(encoder: str) -> list[str]:
    args = ["-c:v", encoder]
    if encoder in {"libx264", "libx265"}:
        args.extend(["-preset", "medium", "-crf", "18"])
    elif encoder.endswith("_nvenc"):
        args.extend(["-preset", "p5", "-cq", "18"])
    else:
        args.extend(["-preset", "medium", "-crf", "18"])
    return [*args, "-pix_fmt", "yuv420p"]


def _burn_subtitle_with_ffmpeg(
    input_video_path: str,
    ass_file_path: str,
    output_video_path: str,
    *,
    configured_ffmpeg_path: str = "",
    configured_ffprobe_path: str = "",
    configured_encoder: str = "auto",
    configured_hwaccel: str = "",
) -> tuple[str, str, str]:
    ffmpeg_path = _find_ffmpeg_path(configured_ffmpeg_path)
    ffprobe_path = _find_binary_path(configured_ffprobe_path, "ffprobe", "ffprobe.exe")
    encoder = _resolve_ffmpeg_encoder(ffmpeg_path, configured_encoder)
    ass_path = os.path.abspath(ass_file_path)
    ass_dir = os.path.dirname(ass_path)
    ass_filename = os.path.basename(ass_path).replace("'", r"\'").replace(":", r"\:")
    input_args = ["-hwaccel", configured_hwaccel] if str(configured_hwaccel or "").strip() else []
    output_path = os.path.abspath(output_video_path)
    command = [
        ffmpeg_path,
        "-v",
        "error",
        "-y",
        *input_args,
        "-i",
        os.path.abspath(input_video_path),
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-vf",
        f"ass=filename='{ass_filename}'",
        *_ffmpeg_output_args(encoder),
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        output_path,
    ]
    result = subprocess.run(command, cwd=ass_dir, capture_output=True, text=True)
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "Unknown FFmpeg error").strip()
        raise RuntimeError(f"Local FFmpeg subtitle burn failed: {message[-2000:]}")
    if not os.path.isfile(output_path) or os.path.getsize(output_path) <= 0:
        raise RuntimeError("Local FFmpeg subtitle burn completed without a valid output video.")
    probe = subprocess.run(
        [ffprobe_path, "-v", "error", "-show_entries", "stream=codec_type", "-of", "json", output_path],
        capture_output=True,
        text=True,
    )
    if probe.returncode != 0 or not any(stream.get("codec_type") == "video" for stream in json.loads(probe.stdout or "{}").get("streams", [])):
        raise RuntimeError("Local FFmpeg subtitle burn output does not contain a valid video stream.")
    return ffmpeg_path, ffprobe_path, encoder


def _parse_timestamp_to_seconds(raw: str) -> float:
    text = str(raw or "").strip().replace(",", ".")
    parts = text.split(":")
    if len(parts) == 2:
        hours = 0
        minutes, seconds = parts
    elif len(parts) == 3:
        hours, minutes, seconds = parts
    else:
        raise ValueError(f"Unsupported subtitle timestamp: {raw}")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def _format_srt_timestamp(seconds: float) -> str:
    total_ms = int(round(seconds * 1000))
    hours = total_ms // 3600000
    total_ms %= 3600000
    minutes = total_ms // 60000
    total_ms %= 60000
    secs = total_ms // 1000
    millis = total_ms % 1000
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"


def _format_ass_timestamp(seconds: float) -> str:
    total_cs = int(round(seconds * 100))
    hours = total_cs // 360000
    total_cs %= 360000
    minutes = total_cs // 6000
    total_cs %= 6000
    secs = total_cs // 100
    centis = total_cs % 100
    return f"{hours}:{minutes:02}:{secs:02}.{centis:02}"


def _parse_subtitle_cues(text: str):
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    cues = []
    i = 0
    while i < len(lines):
        line = lines[i].strip().lstrip("﻿")
        if not line or line.upper() == "WEBVTT":
            i += 1
            continue
        if line.startswith("NOTE"):
            i += 1
            while i < len(lines) and lines[i].strip():
                i += 1
            continue
        if "-->" not in line:
            if i + 1 < len(lines) and "-->" in lines[i + 1]:
                i += 1
                line = lines[i].strip()
            else:
                i += 1
                continue
        start_raw, end_raw = [part.strip() for part in line.split("-->", 1)]
        end_raw = end_raw.split()[0]
        text_lines = []
        i += 1
        while i < len(lines) and lines[i].strip() != "":
            text_lines.append(lines[i].rstrip())
            i += 1
        if text_lines:
            cues.append((_parse_timestamp_to_seconds(start_raw), _parse_timestamp_to_seconds(end_raw), text_lines))
    return cues


def _cues_to_srt(cues) -> str:
    blocks = []
    for index, (start, end, text_lines) in enumerate(cues, start=1):
        blocks.append(
            "\n".join(
                [
                    str(index),
                    f"{_format_srt_timestamp(start)} --> {_format_srt_timestamp(end)}",
                    "\n".join(text_lines),
                ]
            )
        )
    return "\n\n".join(blocks).strip() + "\n"


def _cues_to_vtt(cues) -> str:
    blocks = ["WEBVTT\n"]
    for start, end, text_lines in cues:
        blocks.append(
            "\n".join(
                [
                    f"{_format_srt_timestamp(start).replace(',', '.')} --> {_format_srt_timestamp(end).replace(',', '.')}",
                    "\n".join(text_lines),
                    "",
                ]
            )
        )
    return "\n".join(blocks).strip() + "\n"


def _normalize_color_to_ass(color: str, opacity: float) -> str:
    raw = str(color or "#FFFFFF").strip().lower()
    if raw.startswith("0x"):
        raw = raw[2:]
    if raw.startswith("#"):
        raw = raw[1:]
    raw = raw[:6].ljust(6, "f")
    red = raw[0:2]
    green = raw[2:4]
    blue = raw[4:6]
    opacity = max(0.0, min(1.0, float(opacity)))
    alpha = int(round((1.0 - opacity) * 255))
    return f"&H{alpha:02X}{blue.upper()}{green.upper()}{red.upper()}"


def _position_to_ass_alignment(position: str) -> int:
    mapping = {
        "bottom": 2,
        "top": 8,
        "middle": 5,
        "bottom-left": 1,
        "bottom-right": 3,
        "top-left": 7,
        "top-right": 9,
    }
    return mapping.get(str(position or "bottom").strip().lower(), 2)


def _cues_to_ass(cues, *, font_name: str, font_size: int, font_color: str, font_alpha: float, background_alpha: float, subtitle_position: str, auto_wrap: bool = True) -> str:
    primary_color = _normalize_color_to_ass(font_color, font_alpha)
    alignment = _position_to_ass_alignment(subtitle_position)
    safe_font_name = str(font_name or "SimHei").strip() or "SimHei"
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "PlayResX: 1920",
        "PlayResY: 1080",
        f"WrapStyle: {0 if auto_wrap else 2}",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding",
        # BorderStyle 1 renders a black outline around the glyphs.  The former
        # BorderStyle 3 rendered BackColour as a rectangular black subtitle box.
        f"Style: Default,{safe_font_name},{int(font_size)},{primary_color},&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,{alignment},40,40,40,1",
        "",
        "[Events]",
        "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text",
    ]
    for start, end, text_lines in cues:
        text = "\\N".join(line.replace("{", "(").replace("}", ")") for line in text_lines)
        lines.append(f"Dialogue: 0,{_format_ass_timestamp(start)},{_format_ass_timestamp(end)},Default,,0,0,0,,{text}")
    return "\n".join(lines) + "\n"


def _select_subtitle_language(cues, target_language: str, subtitle_language_mode: str = "auto"):
    mode = str(subtitle_language_mode or "auto").strip().lower()
    if mode == "bilingual":
        return cues
    if mode == "translation":
        line_index = -1
    elif mode == "source":
        line_index = 0
    else:
        line_index = -1 if str(target_language or "").strip().lower() not in {"", "auto"} else 0
    return [(start, end, [text_lines[line_index]]) for start, end, text_lines in cues if text_lines]


def _wrap_subtitle_cues(cues, max_chars_per_line: int):
    width = max(1, int(max_chars_per_line))
    wrapped_cues = []
    for start, end, text_lines in cues:
        wrapped_lines = []
        for line in text_lines:
            wrapped_lines.extend(textwrap.wrap(line, width=width, break_long_words=True, break_on_hyphens=False) or [""])
        wrapped_cues.append((start, end, wrapped_lines))
    return wrapped_cues


def _build_local_subtitle_text(original_text: str, subtitle_format: str, *, font_name: str, font_size: int, font_color: str, font_alpha: float, background_alpha: float, subtitle_position: str, target_language: str = "", subtitle_language_mode: str = "auto", auto_wrap: bool = True, max_chars_per_line: int = 16) -> tuple[str, str]:
    cues = _select_subtitle_language(_parse_subtitle_cues(original_text), target_language, subtitle_language_mode)
    if auto_wrap:
        cues = _wrap_subtitle_cues(cues, max_chars_per_line)
    fmt = str(subtitle_format or "vtt").strip().lower()
    if fmt == "srt":
        return _cues_to_srt(cues), "srt"
    if fmt == "ass":
        return _cues_to_ass(
            cues,
            font_name=font_name,
            font_size=font_size,
            font_color=font_color,
            font_alpha=font_alpha,
            background_alpha=background_alpha,
            subtitle_position=subtitle_position,
            auto_wrap=auto_wrap,
        ), "ass"
    return _cues_to_vtt(cues), "vtt"


def _build_burn_subtitle_ass_text(original_text: str, *, font_name: str, font_size: int, font_color: str, font_alpha: float, background_alpha: float, subtitle_position: str, target_language: str = "", subtitle_language_mode: str = "auto", auto_wrap: bool = True, max_chars_per_line: int = 16) -> str:
    cues = _select_subtitle_language(_parse_subtitle_cues(original_text), target_language, subtitle_language_mode)
    if auto_wrap:
        cues = _wrap_subtitle_cues(cues, max_chars_per_line)
    return _cues_to_ass(
        cues,
        font_name=font_name,
        font_size=font_size,
        font_color=font_color,
        font_alpha=font_alpha,
        background_alpha=background_alpha,
        subtitle_position=subtitle_position,
        auto_wrap=auto_wrap,
    )


class TencentSubtitleBurnNode:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "local_video": ("STRING", {"default": "", "multiline": False, "placeholder": "Upload a local video or enter its full path"}),
                "subtitle_format": (["vtt", "srt", "ass"],),
                "subtitle_position": (["bottom", "top", "middle", "bottom-left", "bottom-right", "top-left", "top-right"],),
                "font_name": (SUBTITLE_FONTS, {"default": "simkai.ttf"}),
                "font_size": ("INT", {"default": 24, "min": 1, "max": 4096}),
                "font_color": ("STRING", {"default": "#FFFFFF", "multiline": False}),
                "font_alpha": ("FLOAT", {"default": 0.9, "min": 0.0, "max": 1.0, "step": 0.05}),
                "background_alpha": ("FLOAT", {"default": 0.2, "min": 0.0, "max": 1.0, "step": 0.05}),
                "accurate_mode": ("BOOLEAN", {"default": False}),
                "need_wordlist": ("BOOLEAN", {"default": False}),
                "adapt_words": ("STRING", {"default": "", "multiline": True}),
                "target_language": (TARGET_LANGUAGES, {"default": "auto", "tooltip": "auto outputs the recognized source language; another value outputs only that translation."}),
                "subtitle_language_mode": (SUBTITLE_LANGUAGE_MODES, {"default": "auto", "tooltip": "auto preserves the existing target-language behavior; source keeps original text; translation keeps translated text; bilingual keeps both lines."}),
                "auto_wrap": ("BOOLEAN", {"default": True, "tooltip": "Automatically wrap long subtitles to fit the video width."}),
                "max_chars_per_line": ("INT", {"default": 16, "min": 4, "max": 80, "step": 1, "tooltip": "When auto_wrap is enabled, insert a line break after this many characters."}),
            },
            "optional": {
                "video_file": ("STRING", {"forceInput": True}),
                "video_url": ("STRING", {"forceInput": True}),
                "vhs_video_info": ("VHS_VIDEOINFO",),
            },
            "hidden": {
                "prompt": "PROMPT",
                "unique_id": "UNIQUE_ID",
            },
        }

    RETURN_TYPES = ("STRING", "STRING", "STRING", "STRING", "STRING", "STRING")
    RETURN_NAMES = ("video_file_path", "subtitle_file_path", "video_url", "subtitle_url", "status", "raw_json")
    FUNCTION = "run"
    CATEGORY = NODE_CATEGORY

    def run(
        self,
        local_video,
        subtitle_format,
        subtitle_position,
        font_name,
        font_size,
        font_color,
        font_alpha,
        background_alpha,
        accurate_mode,
        need_wordlist,
        adapt_words,
        target_language,
        subtitle_language_mode="auto",
        auto_wrap=True,
        max_chars_per_line=16,
        video_file="",
        video_url="",
        vhs_video_info=None,
        prompt=None,
        unique_id=None,
    ):
        current_stage = "config_loading"
        try:
            config = load_tencent_cloud_config()
            filename_prefix = DEFAULT_FILENAME_PREFIX
            _log_subtitle_burn("config_loaded", region=config.region, cos_bucket=config.cos_bucket, oss_bucket=config.oss_bucket, target_language=target_language or "auto")

            current_stage = "input_resolving"
            local_video_input = _resolve_video_input(local_video, video_file, video_url, vhs_video_info, prompt, unique_id)
            _log_subtitle_burn("input_ready", local_video_path=local_video_input, file_size_bytes=os.path.getsize(local_video_input))

            if not config.has_cos_output():
                raise ValueError("Current subtitle node requires Tencent COS output. Please complete tencent_cos_bucket in config.local.json.")
            if not config.has_oss_config():
                raise ValueError("Current subtitle node requires OSS input. Please complete oss_* fields in config.local.json.")

            current_stage = "video_uploading"
            oss_video_url, oss_video_object_key = upload_file_to_oss(config, local_video_input)
            _log_subtitle_burn("video_uploaded", storage="OSS", object_key=oss_video_object_key)
            input_source = TencentInputSource(source_type="URL", url=oss_video_url, local_file_path=local_video_input)

            current_stage = "subtitle_submitting"
            subtitle_submit_summary, _, subtitle_submit_response = create_smart_subtitle_task(
                config,
                input_source,
                accurate_mode=accurate_mode,
                need_wordlist=need_wordlist,
                adapt_words=adapt_words,
                target_language=target_language,
            )
            _log_subtitle_burn("subtitle_submitted", task_id=subtitle_submit_summary.task_id)
            current_stage = "subtitle_waiting"
            subtitle_summary, subtitle_response = wait_for_task(
                config,
                subtitle_submit_summary.task_id,
                on_progress=lambda summary, elapsed: _log_subtitle_burn(
                    "subtitle_status", task_id=summary.task_id, status=summary.status, elapsed_seconds=round(elapsed, 1)
                ),
            )
            if not subtitle_summary.subtitle_urls:
                raise RuntimeError(f"Subtitle task completed without subtitle URLs: {subtitle_summary.to_dict()}")
            _log_subtitle_burn("subtitle_finished", task_id=subtitle_summary.task_id, subtitle_count=len(subtitle_summary.subtitle_urls))

            current_stage = "subtitle_downloading"
            remote_subtitle_url = sign_cos_url(config, subtitle_summary.subtitle_urls[0])
            generated_subtitle_local_path = download_file(remote_subtitle_url, folder_paths.get_temp_directory(), filename_prefix=f"{filename_prefix}_generated_subtitle")
            generated_subtitle_text = _read_text_file(generated_subtitle_local_path)
            subtitle_cue_count = len(_parse_subtitle_cues(generated_subtitle_text))
            _log_subtitle_burn("subtitle_downloaded", local_path=generated_subtitle_local_path, cue_count=subtitle_cue_count)

            current_stage = "subtitle_writing"
            local_subtitle_text, local_subtitle_suffix = _build_local_subtitle_text(
                generated_subtitle_text,
                subtitle_format,
                font_name=font_name,
                font_size=font_size,
                font_color=font_color,
                font_alpha=font_alpha,
                background_alpha=background_alpha,
                subtitle_position=subtitle_position,
                target_language=target_language,
                subtitle_language_mode=subtitle_language_mode,
                auto_wrap=auto_wrap,
                max_chars_per_line=max_chars_per_line,
            )
            local_subtitle_path = _write_text_output(local_subtitle_text, f"{DEFAULT_SUBTITLE_FILENAME_PREFIX}_{filename_prefix}", local_subtitle_suffix)

            burn_ass_text = _build_burn_subtitle_ass_text(
                generated_subtitle_text,
                font_name=font_name,
                font_size=font_size,
                font_color=font_color,
                font_alpha=font_alpha,
                background_alpha=background_alpha,
                subtitle_position=subtitle_position,
                target_language=target_language,
                subtitle_language_mode=subtitle_language_mode,
                auto_wrap=auto_wrap,
                max_chars_per_line=max_chars_per_line,
            )
            burn_ass_local_path = _write_text_output(burn_ass_text, f"{DEFAULT_SUBTITLE_FILENAME_PREFIX}_{filename_prefix}_burn", "ass")
            _log_subtitle_burn("burn_ass_written", local_path=burn_ass_local_path, cue_count=subtitle_cue_count)

            current_stage = "local_burn_preparing"
            _, _, _, local_video_path = _make_output_target(f"{DEFAULT_VIDEO_FILENAME_PREFIX}_{filename_prefix}", "mp4")
            _log_subtitle_burn("local_burn_started", input_video_path=local_video_input, ass_file_path=burn_ass_local_path)
            current_stage = "local_burn_running"
            ffmpeg_path, ffprobe_path, ffmpeg_encoder = _burn_subtitle_with_ffmpeg(
                local_video_input,
                burn_ass_local_path,
                local_video_path,
                configured_ffmpeg_path=config.ffmpeg_path,
                configured_ffprobe_path=config.ffprobe_path,
                configured_encoder=config.ffmpeg_encoder,
                configured_hwaccel=config.ffmpeg_hwaccel,
            )
            try:
                _register_output_asset(local_video_path)
            except Exception:
                pass

            current_stage = "final_video_uploading"
            final_video_url, oss_output_object_key = upload_file_to_oss(config, local_video_path, prefix=config.oss_output_prefix)
            _log_subtitle_burn("final_video_uploaded", storage="OSS", object_key=oss_output_object_key)
            _log_subtitle_burn("done", subtitle_task_id=subtitle_summary.task_id, burn_engine="local_ffmpeg", ffmpeg_encoder=ffmpeg_encoder, output_path=local_video_path)

            raw_payload = {
                "oss_video_object_key": oss_video_object_key,
                "oss_output_object_key": oss_output_object_key,
                "subtitle_submit": subtitle_submit_response,
                "subtitle_result": subtitle_response,
                "burn_engine": "local_ffmpeg",
                "ffmpeg_path": ffmpeg_path,
                "ffprobe_path": ffprobe_path,
                "ffmpeg_encoder": ffmpeg_encoder,
                "ffmpeg_hwaccel": config.ffmpeg_hwaccel,
            }

            return (
                local_video_path,
                local_subtitle_path,
                final_video_url,
                remote_subtitle_url,
                "FINISH",
                json.dumps(raw_payload, ensure_ascii=False, indent=2),
            )
        except Exception as exc:
            _log_subtitle_burn("failed", failed_stage=current_stage, error_type=type(exc).__name__, message=str(exc))
            raise


class TencentPreviewVideoNode:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "file_path": ("STRING", {"forceInput": True, "default": ""}),
                "video_url": ("STRING", {"forceInput": True, "default": ""}),
            }
        }

    OUTPUT_NODE = True
    FUNCTION = "run"
    CATEGORY = NODE_CATEGORY
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("file_path",)

    def run(self, file_path, video_url="", filename_prefix=DEFAULT_PREVIEW_FILENAME_PREFIX, save_output=True):
        source_file_path = str(file_path or "").strip()
        source_video_url = str(video_url or "").strip()
        if not save_output:
            return {"ui": {"video_url": [source_video_url or source_file_path]}, "result": (source_file_path or "",)}

        if source_file_path and os.path.isfile(source_file_path):
            source_suffix = Path(source_file_path).suffix or ".mp4"
            _, subfolder, filename, target_path = _make_output_target(filename_prefix, source_suffix)
            shutil.copy2(source_file_path, target_path)
        elif source_video_url:
            downloaded_path = download_file(source_video_url, folder_paths.get_output_directory(), filename_prefix=filename_prefix)
            source_suffix = Path(downloaded_path).suffix or ".mp4"
            _, subfolder, filename, target_path = _make_output_target(filename_prefix, source_suffix)
            shutil.copy2(downloaded_path, target_path)
        else:
            raise ValueError("Preview node requires a valid local file_path or video_url.")

        try:
            _register_output_asset(target_path)
        except Exception:
            pass

        preview_url = _build_local_media_view_url(filename, subfolder, "output")
        return {
            "ui": {
                "images": [_saved_result(filename, subfolder, "output")],
                "video_url": [preview_url],
                "animated": (True,),
            },
            "result": (target_path,),
        }
