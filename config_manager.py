"""配置管理：读取 config.json，解析 msst_root 和 python_env"""
import os
import json
import shutil
import platform
import logging

logger = logging.getLogger("ComfyUI-MSST-WebUI")

CONFIG_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
EXAMPLE_FILE = os.path.join(CONFIG_DIR, "config.json.example")

_config = None


def _ensure_config() -> dict:
    """加载配置（带缓存）"""
    global _config
    if _config is not None:
        return _config
    if not os.path.exists(CONFIG_FILE):
        if os.path.exists(EXAMPLE_FILE):
            shutil.copy(EXAMPLE_FILE, CONFIG_FILE)
            logger.info(f"已从模板创建配置文件: {CONFIG_FILE}")
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            _config = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.warning(f"配置文件加载失败: {e}，使用空配置")
        _config = {}
    return _config


def get_msst_root() -> str:
    """获取 MSST WebUI 根目录"""
    cfg = _ensure_config()
    root = cfg.get("msst_root", "").strip()
    if not root:
        raise RuntimeError(
            "config.json 中 msst_root 未设置。\n"
            "请编辑 custom_nodes/ComfyUI-MSST-WebUI/config.json，\n"
            "将 msst_root 指向你的 MSST WebUI 安装目录。"
        )
    if not os.path.isdir(root):
        raise RuntimeError(
            f"MSST 根目录不存在: {root}\n"
            "请检查 config.json 中的 msst_root 设置。"
        )
    return os.path.abspath(root)


def get_python_exe() -> str:
    """获取 MSST 便携包自带的 Python 解释器路径"""
    cfg = _ensure_config()
    env_path = cfg.get("python_env", "").strip()

    if env_path:
        # 相对路径以 msst_root 为基准
        if not os.path.isabs(env_path):
            env_dir = os.path.join(get_msst_root(), env_path)
        else:
            env_dir = env_path
    else:
        env_dir = os.path.join(get_msst_root(), "workenv")

    if platform.system() == "Windows":
        candidates = [
            os.path.join(env_dir, "Scripts", "python.exe"),
            os.path.join(env_dir, "python.exe"),
        ]
    else:
        candidates = [
            os.path.join(env_dir, "bin", "python3"),
            os.path.join(env_dir, "bin", "python"),
        ]
    exe = None
    for c in candidates:
        if os.path.isfile(c):
            exe = c
            break

    if exe is None or not os.path.isfile(exe):
        searched = "\n  ".join(candidates)
        raise RuntimeError(
            f"Python 解释器未找到，已搜索:\n  {searched}\n"
            "请检查 config.json 中的 python_env 设置，\n"
            "或确认 MSST 便携包中自带了 workenv 环境。"
        )
    return exe


