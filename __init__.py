"""ComfyUI-MSST-WebUI: MSST WebUI 音频源分离节点 (V3 API)"""
from .nodes import comfy_entrypoint

__all__ = ["comfy_entrypoint"]

# ComfyUI 前端静态文件目录（JS 扩展）
WEB_DIRECTORY = "./js"
