"""ComfyUI 节点定义：MSSTSeparate、UVRSeparate、MSSaveAudio (V3 API)"""
import json
import os
import subprocess
import tempfile
import logging
from typing_extensions import override

from comfy_api.latest import ComfyExtension, io

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
        return "ffmpeg"
    except Exception:
        return "ffmpeg"


def _save_mp3_with_msst_ffmpeg(audio_np, sr: int, output_path: str):
    """使用 MSST 自带的 ffmpeg 编码 MP3（参考 MSST save_audio 实现）"""
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


def _run_separation(audio, model_name, model_info, device, base_filename, instruments):
    """执行 MSST/UVR 子进程分离并处理结果"""
    tmp_dir = tempfile.mkdtemp(prefix="msst_")
    try:
        input_wav = os.path.join(tmp_dir, "input.wav")
        sr = audio_to_temp_wav(audio, input_wav)

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

        error_file = os.path.join(tmp_dir, "error.txt")
        if os.path.exists(error_file):
            with open(error_file, "r", encoding="utf-8") as f:
                raise RuntimeError(f"MSST 推理失败:\n{f.read()}")

        if result.returncode != 0:
            raise RuntimeError(
                f"MSST 子进程异常退出 (code={result.returncode}):\n"
                f"stdout: {result.stdout[:500]}\n"
                f"stderr: {result.stderr[:500]}"
            )

        manifest_path = os.path.join(tmp_dir, "manifest.json")
        if not os.path.exists(manifest_path):
            raise RuntimeError("子进程未生成 manifest.json")
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)

        stem_names = [s for s in instruments if s in manifest]

        info_json = json.dumps({
            "model_name": model_name,
            "instruments": stem_names,
            "stem_map": {s: i for i, s in enumerate(stem_names)},
            "model_class": model_info.get("model_class", "MSST"),
        }, ensure_ascii=False)
        outputs = [info_json]

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

        return outputs, sr

    finally:
        cleanup_temp_dir(tmp_dir)


# ═══════════════════════════════════════════════
# 节点 1: MSSTSeparate
# ═══════════════════════════════════════════════
class MSSTSeparate(io.ComfyNode):
    """MSST 音频分离节点（支持 vocal_models / multi_stem_models / single_stem_models）"""

    @classmethod
    def define_schema(cls):
        _ensure_model_data()
        # 输出交错排列: model_info, stem_0(AUDIO), stem_0_fn(STRING), stem_1(AUDIO), ...
        _outputs = [io.String.Output("model_info", tooltip="模型元信息 JSON")]
        for i in range(MAX_STEMS):
            _outputs.append(io.Audio.Output(f"stem_{i}", tooltip=f"第 {i+1} 轨音频"))
            _outputs.append(io.String.Output(f"stem_{i}_fn", tooltip=f"第 {i+1} 轨文件名"))
        return io.Schema(
            node_id="MSSTSeparate",
            display_name="MSST Audio Separate",
            category="audio/separation",
            inputs=[
                io.Audio.Input("audio"),
                io.Combo.Input("model_category",
                    options=["vocal_models", "multi_stem_models", "single_stem_models"],
                    default="vocal_models"),
                io.Combo.Input("model_name",
                    options=_ALL_MSST_NAMES),
                io.Combo.Input("device",
                    options=["auto", "cuda", "cpu"],
                    default="auto"),
                io.String.Input("base_filename", default="audio"),
            ],
            outputs=_outputs,
        )

    @classmethod
    def execute(cls, audio, model_category, model_name, device, base_filename):
        model_info = model_registry.get_model_info(model_name)
        if not model_info:
            raise ValueError(f"未知模型: {model_name}")
        instruments = model_info.get("instruments", ["vocals", "instrumental"])
        outputs, _ = _run_separation(
            audio=audio, model_name=model_name, model_info=model_info,
            device=device, base_filename=base_filename, instruments=instruments,
        )
        return io.NodeOutput(*outputs)


# ═══════════════════════════════════════════════
# 节点 2: UVRSeparate
# ═══════════════════════════════════════════════
class UVRSeparate(io.ComfyNode):
    """UVR/VR 音频分离节点"""

    @classmethod
    def define_schema(cls):
        _ensure_model_data()
        return io.Schema(
            node_id="UVRSeparate",
            display_name="UVR Audio Separate",
            category="audio/separation",
            inputs=[
                io.Audio.Input("audio"),
                io.Combo.Input("model_name",
                    options=_ALL_VR_NAMES),
                io.Combo.Input("device",
                    options=["auto", "cuda", "cpu"],
                    default="auto"),
                io.String.Input("base_filename", default="audio"),
            ],
            outputs=[
                io.String.Output("model_info", tooltip="模型元信息 JSON"),
                io.Audio.Output("stem_0", tooltip="主音轨音频"),
                io.String.Output("stem_0_fn", tooltip="主音轨文件名"),
                io.Audio.Output("stem_1", tooltip="次音轨音频"),
                io.String.Output("stem_1_fn", tooltip="次音轨文件名"),
            ],
        )

    @classmethod
    def execute(cls, audio, model_name, device, base_filename):
        model_info = model_registry.get_model_info(model_name)
        if not model_info:
            raise ValueError(f"未知 VR 模型: {model_name}")
        instruments = model_info.get("instruments", ["Vocals", "Instrumental"])
        outputs, _ = _run_separation(
            audio=audio, model_name=model_name, model_info=model_info,
            device=device, base_filename=base_filename, instruments=instruments,
        )
        return io.NodeOutput(*outputs)


# ═══════════════════════════════════════════════
# 节点 3: MSLoadAudio
# ═══════════════════════════════════════════════
class MSLoadAudio(io.ComfyNode):
    """加载音频文件，输出 AUDIO + 路径 + 文件名"""

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MSLoadAudio",
            display_name="MSST Load Audio",
            category="audio/separation",
            inputs=[
                io.String.Input("file_path", default=""),
            ],
            outputs=[
                io.Audio.Output("audio", tooltip="加载的音频"),
                io.String.Output("folder_path", tooltip="文件所在文件夹路径"),
                io.String.Output("filename", tooltip="文件名（不含扩展名）"),
            ],
        )

    @classmethod
    def execute(cls, file_path):
        import soundfile as sf
        import torch

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
        return io.NodeOutput(audio_dict, folder, fname)


# ═══════════════════════════════════════════════
# 节点 4: MSSaveAudio
# ═══════════════════════════════════════════════
class MSSaveAudio(io.ComfyNode):
    """保存音频到指定路径"""

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MSSaveAudio",
            display_name="MSST Save Audio",
            category="audio/separation",
            is_output_node=True,
            inputs=[
                io.Audio.Input("audio"),
                io.String.Input("folder_path", default=""),
                io.String.Input("filename", default="audio"),
                io.Combo.Input("format",
                    options=["wav", "flac", "mp3"],
                    default="wav"),
            ],
            outputs=[
                io.String.Output("filepath", tooltip="文件保存完整路径"),
            ],
        )

    @classmethod
    def execute(cls, audio, folder_path, filename, format):
        import soundfile as sf
        import numpy as np
        import torch

        waveform = audio["waveform"]
        sr = int(audio["sample_rate"])
        if waveform.dim() == 3:
            waveform = waveform.squeeze(0)
        audio_np = waveform.cpu().numpy().T

        save_dir = folder_path.strip() if folder_path.strip() else "."
        os.makedirs(save_dir, exist_ok=True)
        ext = format.lower()
        save_path = os.path.join(save_dir, f"{filename}.{ext}")

        if ext == "flac":
            sf.write(save_path, audio_np, sr, subtype="PCM_24")
        elif ext == "mp3":
            _save_mp3_with_msst_ffmpeg(audio_np, sr, save_path)
        else:
            sf.write(save_path, audio_np, sr, subtype="FLOAT")

        logger.info(f"音频已保存: {save_path}")
        return io.NodeOutput(os.path.abspath(save_path))


# ═══════════════════════════════════════════════
# V3 扩展注册
# ═══════════════════════════════════════════════
class MSSTExtension(ComfyExtension):
    @override
    async def get_node_list(self) -> list[type[io.ComfyNode]]:
        return [MSLoadAudio, MSSTSeparate, UVRSeparate, MSSaveAudio]


async def comfy_entrypoint() -> MSSTExtension:
    return MSSTExtension()
