"""ComfyUI 节点定义：MSSTSeparate、UVRSeparate、MSSaveAudio"""
import json
import os
import subprocess
import tempfile
import logging

logger = logging.getLogger("ComfyUI-MSST-WebUI")

# 最大支持 6 对输出 (AUDIO + STRING)
MAX_STEMS = 6

# ── 模块导入时: 初始化模型注册表并生成 JS 模型数据 ──
from . import model_registry
from .audio_utils import audio_to_temp_wav, read_wav_to_audio, create_silent_audio, cleanup_temp_dir
from .config_manager import get_python_exe, get_msst_root

# 延迟初始化：首次需要模型数据时再扫描
_ALL_MSST_NAMES = []
_ALL_VR_NAMES = []


def _ensure_model_data():
    """确保模型数据已加载（失败时不报错，仅提供空列表）"""
    if not _ALL_MSST_NAMES:
        try:
            model_registry.ensure_init()
            _ALL_MSST_NAMES.extend(model_registry.get_msst_display_names())
            _ALL_VR_NAMES.extend(model_registry.get_vr_display_names())
        except Exception as e:
            logger.warning(f"模型数据加载失败（节点将显示空列表）: {e}")
            # 提供一个占位消息，让用户知道需要配置
            if not _ALL_MSST_NAMES:
                _ALL_MSST_NAMES.append("-- 请先配置 config.json --")


def _get_msst_ffmpeg() -> str:
    """获取 MSST 便携包自带的 ffmpeg 路径"""
    try:
        msst_root = get_msst_root()
        candidate = os.path.join(msst_root, "ffmpeg", "bin", "ffmpeg.exe")
        if os.path.isfile(candidate):
            return candidate
        candidate = os.path.join(msst_root, "ffmpeg", "ffmpeg.exe")
        if os.path.isfile(candidate):
            return candidate
        # 回退到系统 ffmpeg
        return "ffmpeg"
    except Exception:
        return "ffmpeg"


def _save_mp3_with_msst_ffmpeg(audio_np, sr: int, output_path: str):
    """使用 MSST 自带的 ffmpeg 编码 MP3（参考 MSST save_audio 实现）"""
    import subprocess
    ffmpeg = _get_msst_ffmpeg()
    channels = audio_np.shape[1] if audio_np.ndim > 1 else 1
    subprocess.run(
        [ffmpeg, "-y", "-f", "f32le",
         "-ar", str(sr),
         "-ac", str(channels),
         "-i", "pipe:0",
         "-codec:a", "libmp3lame",
         "-b:a", "320k",
         output_path],
        input=audio_np.astype("float32").tobytes(),
        check=True, capture_output=True, timeout=60,
    )


# ═══════════════════════════════════════════════
# 节点 1: MSSTSeparate
# ═══════════════════════════════════════════════
class MSSTSeparate:
    """MSST 音频分离节点（支持 vocal_models / multi_stem_models / single_stem_models）"""

    @classmethod
    def INPUT_TYPES(cls):
        _ensure_model_data()
        return {
            "required": {
                "audio": ("AUDIO",),
                "model_category": (
                    ["vocal_models", "multi_stem_models", "single_stem_models"],
                    {"default": "vocal_models"},
                ),
                "model_name": (_ALL_MSST_NAMES,),
                "device": (["auto", "cuda", "cpu"], {"default": "auto"}),
                "base_filename": ("STRING", {"default": "audio", "multiline": False}),
            }
        }

    RETURN_TYPES = tuple(
        ["AUDIO", "STRING"] * MAX_STEMS + ["STRING"]
    )
    # 交错排列: AUDIO, STRING, AUDIO, STRING, ...
    _names = []
    for i in range(MAX_STEMS):
        _names.append(f"stem_{i}")
        _names.append(f"stem_{i}_fn")
    _names.append("model_info")
    RETURN_NAMES = tuple(_names)
    _tips = []
    for i in range(MAX_STEMS):
        _tips.append(f"第 {i+1} 轨音频")
        _tips.append(f"第 {i+1} 轨文件名")
    _tips.append("模型元信息 JSON")
    OUTPUT_TOOLTIPS = tuple(_tips)
    FUNCTION = "separate"
    CATEGORY = "audio/separation"

    def separate(self, audio, model_category, model_name, device, base_filename):
        """执行 MSST 音频分离"""
        model_info = model_registry.get_model_info(model_name)
        if not model_info:
            raise ValueError(f"未知模型: {model_name}")

        instruments = model_info.get("instruments", ["vocals", "instrumental"])
        return self._run_separation(
            audio=audio,
            model_name=model_name,
            model_info=model_info,
            device=device,
            base_filename=base_filename,
            instruments=instruments,
        )

    def _run_separation(self, audio, model_name, model_info, device, base_filename, instruments):
        """运行子进程进行分离并处理结果"""
        tmp_dir = tempfile.mkdtemp(prefix="msst_")
        try:
            # 1. 写临时 WAV
            input_wav = os.path.join(tmp_dir, "input.wav")
            sr = audio_to_temp_wav(audio, input_wav)

            # 2. 调用 MSST 子进程
            worker = os.path.join(os.path.dirname(__file__), "infer_worker.py")
            python_exe = get_python_exe()
            msst_root = get_msst_root()

            cmd = [
                python_exe, worker,
                "--model_class", model_info.get("model_class", "MSST"),
                "--model_type", model_info.get("model_type", ""),
                "--config_path", model_info.get("config_path", ""),
                "--model_path", model_info["model_path"],
                "--input_wav", input_wav,
                "--output_dir", tmp_dir,
                "--device", device,
                "--msst_root", msst_root,
            ]

            result = subprocess.run(
                cmd,
                cwd=msst_root,
                capture_output=True, text=True, timeout=600,
            )

            # 检查错误
            error_file = os.path.join(tmp_dir, "error.txt")
            if os.path.exists(error_file):
                with open(error_file, "r", encoding="utf-8") as f:
                    err_msg = f.read()
                raise RuntimeError(f"MSST 推理失败:\n{err_msg}")

            if result.returncode != 0:
                raise RuntimeError(
                    f"MSST 子进程异常退出 (code={result.returncode}):\n"
                    f"stdout: {result.stdout[:500]}\n"
                    f"stderr: {result.stderr[:500]}"
                )

            # 3. 读取结果清单
            manifest_path = os.path.join(tmp_dir, "manifest.json")
            if not os.path.exists(manifest_path):
                raise RuntimeError("子进程未生成 manifest.json")
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)

            # 4. 按 instruments 顺序排列输出
            stem_names = [s for s in instruments if s in manifest]
            outputs = []
            for i in range(MAX_STEMS):
                if i < len(stem_names):
                    stem = stem_names[i]
                    wav_path = os.path.join(tmp_dir, manifest[stem])
                    audio_dict = read_wav_to_audio(wav_path)
                    fn_str = f"{base_filename}_{stem}"
                else:
                    audio_dict = create_silent_audio(sr)
                    fn_str = ""
                outputs.append(audio_dict)
                outputs.append(fn_str)

            # model_info JSON
            info_json = json.dumps({
                "model_name": model_name,
                "instruments": stem_names,
                "stem_map": {s: i for i, s in enumerate(stem_names)},
                "model_class": model_info.get("model_class", "MSST"),
            }, ensure_ascii=False)
            outputs.append(info_json)

            return tuple(outputs)

        finally:
            cleanup_temp_dir(tmp_dir)


# ═══════════════════════════════════════════════
# 节点 2: UVRSeparate
# ═══════════════════════════════════════════════
class UVRSeparate:
    """UVR/VR 音频分离节点"""

    @classmethod
    def INPUT_TYPES(cls):
        _ensure_model_data()
        return {
            "required": {
                "audio": ("AUDIO",),
                "model_name": (_ALL_VR_NAMES,),
                "device": (["auto", "cuda", "cpu"], {"default": "auto"}),
                "base_filename": ("STRING", {"default": "audio", "multiline": False}),
            }
        }

    RETURN_TYPES = ("AUDIO", "STRING", "AUDIO", "STRING", "STRING")
    RETURN_NAMES = ("stem_0", "stem_0_fn", "stem_1", "stem_1_fn", "model_info")
    OUTPUT_TOOLTIPS = ("主音轨音频", "主音轨文件名", "次音轨音频", "次音轨文件名", "模型元信息 JSON")
    FUNCTION = "separate"
    CATEGORY = "audio/separation"

    def separate(self, audio, model_name, device, base_filename):
        model_info = model_registry.get_model_info(model_name)
        if not model_info:
            raise ValueError(f"未知 VR 模型: {model_name}")

        instruments = model_info.get("instruments", ["Vocals", "Instrumental"])
        # VR 模型固定 2 轨
        tmp_dir = tempfile.mkdtemp(prefix="msst_")
        try:
            input_wav = os.path.join(tmp_dir, "input.wav")
            sr = audio_to_temp_wav(audio, input_wav)

            worker = os.path.join(os.path.dirname(__file__), "infer_worker.py")
            python_exe = get_python_exe()
            msst_root = get_msst_root()

            cmd = [
                python_exe, worker,
                "--model_class", "VR",
                "--model_type", "VR",
                "--config_path", "",
                "--model_path", model_info["model_path"],
                "--input_wav", input_wav,
                "--output_dir", tmp_dir,
                "--device", device,
                "--msst_root", msst_root,
            ]
            result = subprocess.run(
                cmd, cwd=msst_root,
                capture_output=True, text=True, timeout=600,
            )

            error_file = os.path.join(tmp_dir, "error.txt")
            if os.path.exists(error_file):
                with open(error_file, "r", encoding="utf-8") as f:
                    raise RuntimeError(f"UVR 推理失败:\n{f.read()}")
            if result.returncode != 0:
                raise RuntimeError(f"UVR 子进程异常退出: {result.stderr[:500]}")

            manifest_path = os.path.join(tmp_dir, "manifest.json")
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)

            # 按 instruments 顺序排列
            stem_map = {}
            for i, stem in enumerate(instruments):
                if stem in manifest:
                    audio_dict = read_wav_to_audio(os.path.join(tmp_dir, manifest[stem]))
                    fn_str = f"{base_filename}_{stem}"
                else:
                    audio_dict = create_silent_audio(sr)
                    fn_str = ""
                stem_map[f"stem_{i}"] = (audio_dict, fn_str)

            info_json = json.dumps({
                "model_name": model_name,
                "instruments": instruments,
                "stem_map": {s: i for i, s in enumerate(instruments)},
            }, ensure_ascii=False)

            return (
                stem_map.get("stem_0", (create_silent_audio(sr), ""))[0],
                stem_map.get("stem_0", (create_silent_audio(sr), ""))[1],
                stem_map.get("stem_1", (create_silent_audio(sr), ""))[0],
                stem_map.get("stem_1", (create_silent_audio(sr), ""))[1],
                info_json,
            )

        finally:
            cleanup_temp_dir(tmp_dir)


# ═══════════════════════════════════════════════
# 节点 3: MSLoadAudio — 加载音频文件
# ═══════════════════════════════════════════════
class MSLoadAudio:
    """加载音频文件，输出 AUDIO + 路径 + 文件名"""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "file_path": ("STRING", {"default": "", "multiline": False}),
            }
        }

    RETURN_TYPES = ("AUDIO", "STRING", "STRING")
    RETURN_NAMES = ("audio", "folder_path", "filename")
    OUTPUT_TOOLTIPS = ("加载的音频", "文件所在文件夹路径", "文件名（不含扩展名）")
    FUNCTION = "load"
    CATEGORY = "audio/separation"

    def load(self, file_path):
        import soundfile as sf
        import torch
        import os

        file_path = file_path.strip().strip('"').strip("'")
        if not file_path or not os.path.isfile(file_path):
            raise ValueError(f"文件不存在: {file_path}")

        audio_np, sr = sf.read(file_path, dtype="float32")

        if audio_np.ndim == 1:
            tensor = torch.from_numpy(audio_np).unsqueeze(0).unsqueeze(0).float()
        else:
            tensor = torch.from_numpy(audio_np.T).unsqueeze(0).float()

        folder = os.path.dirname(file_path)
        fname = os.path.splitext(os.path.basename(file_path))[0]

        audio_dict = {"waveform": tensor, "sample_rate": int(sr)}
        return (audio_dict, folder, fname)


# ═══════════════════════════════════════════════
# 节点 4: MSSaveAudio
# ═══════════════════════════════════════════════
class MSSaveAudio:
    """保存音频到指定路径"""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "audio": ("AUDIO",),
                "folder_path": ("STRING", {"default": "", "multiline": False}),
                "filename": ("STRING", {"default": "audio", "multiline": False}),
                "format": (["wav", "flac", "mp3"], {"default": "wav"}),
            }
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("filepath",)
    OUTPUT_TOOLTIPS = ("文件保存完整路径",)
    FUNCTION = "save"
    CATEGORY = "audio/separation"
    OUTPUT_NODE = True

    def save(self, audio, folder_path, filename, format):
        import soundfile as sf
        import numpy as np
        import torch

        waveform = audio["waveform"]
        sr = int(audio["sample_rate"])
        if waveform.dim() == 3:
            waveform = waveform.squeeze(0)
        audio_np = waveform.cpu().numpy().T  # [S, C]

        save_dir = folder_path.strip() if folder_path.strip() else "."
        os.makedirs(save_dir, exist_ok=True)
        ext = format.lower()
        save_path = os.path.join(save_dir, f"{filename}.{ext}")

        if ext == "flac":
            sf.write(save_path, audio_np, sr, subtype="PCM_24")
        elif ext == "mp3":
            # 使用 MSST 自带的 ffmpeg（位于 msst_root/ffmpeg/bin/ 下）
            _save_mp3_with_msst_ffmpeg(audio_np, sr, save_path)
        else:
            sf.write(save_path, audio_np, sr, subtype="FLOAT")

        logger.info(f"音频已保存: {save_path}")
        return (os.path.abspath(save_path),)


# ── 节点注册映射 ──
NODE_CLASS_MAPPINGS = {
    "MSLoadAudio": MSLoadAudio,
    "MSSTSeparate": MSSTSeparate,
    "UVRSeparate": UVRSeparate,
    "MSSaveAudio": MSSaveAudio,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "MSLoadAudio": "MSST Load Audio",
    "MSSTSeparate": "MSST Audio Separate",
    "UVRSeparate": "UVR Audio Separate",
    "MSSaveAudio": "MSST Save Audio",
}
