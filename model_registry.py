"""模型注册表——通过 MSST Python 子进程获取模型列表"""
import os
import json
import subprocess
import logging

logger = logging.getLogger("ComfyUI-MSST-WebUI")

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_JS_DIR = os.path.join(_THIS_DIR, "js")
_MODEL_DATA_JSON = os.path.join(_JS_DIR, "model_data.json")

_cache = {"MSST": [], "VR": []}


def _call_list_models() -> dict:
    """调用 infer_worker.py --action list_models（在 MSST Python 下运行）"""
    from .config_manager import get_msst_root, get_python_exe
    msst_root = get_msst_root()
    python_exe = get_python_exe()
    worker = os.path.join(_THIS_DIR, "infer_worker.py")

    result = subprocess.run(
        [python_exe, "-u", worker, "--action", "list_models", "--msst_root", msst_root],
        cwd=msst_root,
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"list_models 失败:\n{result.stderr[:500]}")
    data = json.loads(result.stdout)
    if "error" in data:
        raise RuntimeError(f"list_models 错误: {data['error']}")
    return data


def refresh() -> bool:
    """刷新模型列表 → 更新缓存 + model_data.json"""
    global _cache
    try:
        data = _call_list_models()
        _cache = data

        # 写入 JS 数据文件
        all_data = {}
        for m in data.get("MSST", []) + data.get("VR", []):
            all_data[m["display_name"]] = {
                "instruments": m["instruments"],
                "category": m["category"],
            }
        os.makedirs(_JS_DIR, exist_ok=True)
        with open(_MODEL_DATA_JSON, "w", encoding="utf-8") as f:
            json.dump(all_data, f, ensure_ascii=False)

        msst_n = len(data.get("MSST", []))
        vr_n = len(data.get("VR", []))
        logger.info(f"模型刷新完成: {msst_n} MSST + {vr_n} VR")
        return True
    except Exception as e:
        logger.warning(f"模型刷新失败: {e}")
        # 读取缓存文件
        if os.path.isfile(_MODEL_DATA_JSON):
            try:
                with open(_MODEL_DATA_JSON) as f:
                    all_data = json.load(f)
                logger.info(f"使用缓存: {len(all_data)} 个模型")
            except Exception:
                pass
        return False


# 首次导入时刷新
_refreshed = False
try:
    _refreshed = refresh()
except Exception:
    pass


def get_msst_display_names(category: str | None = None) -> list[str]:
    global _cache
    names = []
    for m in _cache.get("MSST", []):
        if category is not None and m.get("category") != category:
            continue
        names.append(m["display_name"])
    if not names:
        names.append("-- 请配置 config.json --")
    return names


def get_vr_display_names() -> list[str]:
    global _cache
    names = [m["display_name"] for m in _cache.get("VR", [])]
    if not names:
        names.append("-- 请配置 config.json --")
    return names


def get_model_info(display_name: str) -> dict | None:
    global _cache
    for m in _cache.get("MSST", []) + _cache.get("VR", []):
        if m["display_name"] == display_name:
            return m
    return None


def ensure_init():
    if not _refreshed:
        refresh()
