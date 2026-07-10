from .nodes import (
    TencentPreviewVideoNode,
    TencentSubtitleBurnNode,
)

NODE_CLASS_MAPPINGS = {
    "TencentSubtitleBurn": TencentSubtitleBurnNode,
    "TencentPreviewVideo": TencentPreviewVideoNode,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "TencentSubtitleBurn": "Tencent Subtitle Burn",
    "TencentPreviewVideo": "Tencent Preview Video",
}
