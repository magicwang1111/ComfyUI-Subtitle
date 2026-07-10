from __future__ import annotations

import json
import os
import shutil
import urllib.parse
from pathlib import Path

import folder_paths

from .api import (
    TencentInputSource,
    create_burn_subtitle_task,
    create_smart_subtitle_task,
    download_file,
    load_tencent_cloud_config,
    upload_file_to_oss,
    wait_for_task,
)

NODE_CATEGORY = "Tencent/Subtitle"
VIDEO_EXTENSIONS = {"mp4", "mov", "mkv", "avi", "webm", "m4v"}
DEFAULT_VIDEO_FILENAME_PREFIX = "tencent/subtitle/video"
DEFAULT_SUBTITLE_FILENAME_PREFIX = "tencent/subtitle/subtitle"
DEFAULT_PREVIEW_FILENAME_PREFIX = "tencent/subtitle/preview"


def _list_input_videos():
    input_dir = folder_paths.get_input_directory()
    files = []
    if os.path.isdir(input_dir):
        for name in os.listdir(input_dir):
            full_path = os.path.join(input_dir, name)
            if not os.path.isfile(full_path):
                continue
            parts = name.rsplit(".", 1)
            if len(parts) == 2 and parts[1].lower() in VIDEO_EXTENSIONS:
                files.append(name)
    files = sorted(files)
    return files or [""]


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


def _resolve_video_input(video: str, file_path: str) -> str:
    raw_file_path = str(file_path or "").strip()
    if raw_file_path:
        normalized = os.path.abspath(os.path.expanduser(raw_file_path))
        if not os.path.isfile(normalized):
            raise ValueError(f"file_path is not a valid local video file: {normalized}")
        return normalized

    selected_video = str(video or "").strip()
    if not selected_video:
        raise ValueError("Provide a local file_path or upload/select a video from the input directory.")
    if not folder_paths.exists_annotated_filepath(selected_video):
        raise ValueError(f"Invalid uploaded video file: {selected_video}")
    return folder_paths.get_annotated_filepath(selected_video)


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


def _cues_to_ass(cues, *, font_name: str, font_size: int, font_color: str, font_alpha: float, background_alpha: float, subtitle_position: str) -> str:
    primary_color = _normalize_color_to_ass(font_color, font_alpha)
    back_color = _normalize_color_to_ass("#000000", background_alpha)
    alignment = _position_to_ass_alignment(subtitle_position)
    safe_font_name = str(font_name or "SimHei").strip() or "SimHei"
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "PlayResX: 1920",
        "PlayResY: 1080",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding",
        f"Style: Default,{safe_font_name},{int(font_size)},{primary_color},&H000000FF,&H00000000,{back_color},0,0,0,0,100,100,0,0,3,1,0,{alignment},40,40,40,1",
        "",
        "[Events]",
        "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text",
    ]
    for start, end, text_lines in cues:
        text = "\\N".join(line.replace("{", "(").replace("}", ")") for line in text_lines)
        lines.append(f"Dialogue: 0,{_format_ass_timestamp(start)},{_format_ass_timestamp(end)},Default,,0,0,0,,{text}")
    return "\n".join(lines) + "\n"


def _build_local_subtitle_text(original_text: str, subtitle_format: str, *, font_name: str, font_size: int, font_color: str, font_alpha: float, background_alpha: float, subtitle_position: str) -> tuple[str, str]:
    cues = _parse_subtitle_cues(original_text)
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
        ), "ass"
    if original_text.lstrip().startswith("WEBVTT"):
        return original_text if original_text.endswith("\n") else original_text + "\n", "vtt"
    return _cues_to_vtt(cues), "vtt"


def _build_burn_subtitle_ass_text(original_text: str, *, font_name: str, font_size: int, font_color: str, font_alpha: float, background_alpha: float, subtitle_position: str) -> str:
    cues = _parse_subtitle_cues(original_text)
    return _cues_to_ass(
        cues,
        font_name=font_name,
        font_size=font_size,
        font_color=font_color,
        font_alpha=font_alpha,
        background_alpha=background_alpha,
        subtitle_position=subtitle_position,
    )


class TencentSubtitleBurnNode:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "video": (_list_input_videos(), {"video_upload": True}),
                "subtitle_format": (["vtt", "srt", "ass"],),
                "subtitle_position": (["bottom", "top", "middle", "bottom-left", "bottom-right", "top-left", "top-right"],),
                "font_name": ("STRING", {"default": "simkai.ttf", "multiline": False}),
                "font_size": ("INT", {"default": 24, "min": 1, "max": 4096}),
                "font_color": ("STRING", {"default": "#FFFFFF", "multiline": False}),
                "font_alpha": ("FLOAT", {"default": 0.9, "min": 0.0, "max": 1.0, "step": 0.05}),
                "background_alpha": ("FLOAT", {"default": 0.2, "min": 0.0, "max": 1.0, "step": 0.05}),
                "accurate_mode": ("BOOLEAN", {"default": False}),
                "need_wordlist": ("BOOLEAN", {"default": False}),
                "adapt_words": ("STRING", {"default": "", "multiline": True}),
                "target_language": ("STRING", {"default": "", "multiline": False}),
                "filename_prefix": ("STRING", {"default": "tencent_subtitle", "multiline": False}),
            },
            "optional": {
                "file_path": ("STRING", {"default": "", "multiline": False}),
                "subtitle_definition_id": ("INT", {"default": 0, "min": 0, "max": 99999999}),
                "transcode_definition_id": ("INT", {"default": 0, "min": 0, "max": 99999999}),
            },
        }

    RETURN_TYPES = ("STRING", "STRING", "STRING", "STRING", "STRING", "STRING")
    RETURN_NAMES = ("video_file_path", "subtitle_file_path", "video_url", "subtitle_url", "status", "raw_json")
    FUNCTION = "run"
    CATEGORY = NODE_CATEGORY

    def run(
        self,
        video,
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
        filename_prefix,
        file_path="",
        subtitle_definition_id=0,
        transcode_definition_id=0,
    ):
        config = load_tencent_cloud_config()
        local_video_input = _resolve_video_input(video, file_path)

        if not config.has_oss_config():
            raise ValueError("Current subtitle node is configured for OSS input. Please complete oss_* fields in config.local.json.")

        oss_video_url, _ = upload_file_to_oss(config, local_video_input)
        input_source = TencentInputSource(source_type="URL", url=oss_video_url, local_file_path=local_video_input)

        subtitle_submit_summary, _, subtitle_submit_response = create_smart_subtitle_task(
            config,
            input_source,
            definition=subtitle_definition_id or None,
            accurate_mode=accurate_mode,
            need_wordlist=need_wordlist,
            adapt_words=adapt_words,
            target_language=target_language,
        )
        subtitle_summary, subtitle_response = wait_for_task(config, subtitle_submit_summary.task_id)
        if not subtitle_summary.subtitle_urls:
            raise RuntimeError(f"Subtitle task completed without subtitle URLs: {subtitle_summary.to_dict()}")

        remote_subtitle_url = subtitle_summary.subtitle_urls[0]
        generated_subtitle_local_path = download_file(remote_subtitle_url, folder_paths.get_temp_directory(), filename_prefix=f"{filename_prefix}_generated_subtitle")
        generated_subtitle_text = _read_text_file(generated_subtitle_local_path)

        local_subtitle_text, local_subtitle_suffix = _build_local_subtitle_text(
            generated_subtitle_text,
            subtitle_format,
            font_name=font_name,
            font_size=font_size,
            font_color=font_color,
            font_alpha=font_alpha,
            background_alpha=background_alpha,
            subtitle_position=subtitle_position,
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
        )
        burn_ass_local_path = _write_text_output(burn_ass_text, f"{DEFAULT_SUBTITLE_FILENAME_PREFIX}_{filename_prefix}_burn", "ass")
        burn_subtitle_url, _ = upload_file_to_oss(config, burn_ass_local_path)

        burn_submit_summary, _, burn_submit_response = create_burn_subtitle_task(
            config,
            input_source,
            subtitle_url=burn_subtitle_url,
            definition=transcode_definition_id or None,
        )
        burn_summary, burn_response = wait_for_task(config, burn_submit_summary.task_id)
        if not burn_summary.video_urls:
            raise RuntimeError(f"Burn subtitle task completed without video URLs: {burn_summary.to_dict()}")

        remote_video_url = burn_summary.video_urls[0]
        local_video_path = download_file(remote_video_url, folder_paths.get_output_directory(), filename_prefix=f"{DEFAULT_VIDEO_FILENAME_PREFIX}_{filename_prefix}")
        try:
            _register_output_asset(local_video_path)
        except Exception:
            pass

        raw_payload = {
            "oss_video_url": oss_video_url,
            "burn_subtitle_url": burn_subtitle_url,
            "subtitle_submit": subtitle_submit_response,
            "subtitle_result": subtitle_response,
            "burn_submit": burn_submit_response,
            "burn_result": burn_response,
        }

        return (
            local_video_path,
            local_subtitle_path,
            remote_video_url,
            remote_subtitle_url,
            burn_summary.status,
            json.dumps(raw_payload, ensure_ascii=False, indent=2),
        )


class TencentPreviewVideoNode:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "file_path": ("STRING", {"forceInput": True, "default": ""}),
            },
            "optional": {
                "video_url": ("STRING", {"forceInput": True, "default": ""}),
                "filename_prefix": ("STRING", {"default": DEFAULT_PREVIEW_FILENAME_PREFIX}),
                "save_output": ("BOOLEAN", {"default": True}),
            },
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
