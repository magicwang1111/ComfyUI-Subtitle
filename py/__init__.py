from .nodes import (
    TencentPreviewVideoNode,
    TencentSubtitleBurnNode,
)

NODE_CLASS_MAPPINGS = {
    "TencentSubtitleBurn": TencentSubtitleBurnNode,
    "TencentPreviewVideo": TencentPreviewVideoNode,
    "Tencent Subtitle Burn": TencentSubtitleBurnNode,
    "Tencent Preview Video": TencentPreviewVideoNode,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "TencentSubtitleBurn": "Tencent Subtitle Burn",
    "TencentPreviewVideo": "Tencent Preview Video",
    "Tencent Subtitle Burn": "Tencent Subtitle Burn (Legacy Workflow)",
    "Tencent Preview Video": "Tencent Preview Video (Legacy Workflow)",
}
