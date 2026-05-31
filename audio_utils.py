"""音频格式转换：ComfyUI AUDIO ↔ WAV 文件"""
import os
import torch
import numpy as np
import soundfile as sf


def audio_to_temp_wav(audio_dict: dict, output_path: str) -> int:
    """将 ComfyUI AUDIO dict 写入临时 WAV 文件

    Args:
        audio_dict: {"waveform": tensor[1, C, S], "sample_rate": int}
        output_path: 输出 WAV 文件路径

    Returns:
        sample_rate: 采样率
    """
    waveform = audio_dict["waveform"]  # [1, C, S] or [B, C, S]
    sample_rate = int(audio_dict["sample_rate"])

    # 移除 batch 维度: [1, C, S] → [C, S]
    if waveform.dim() == 3:
        waveform = waveform.squeeze(0)

    # 转 numpy: [C, S] → [S, C] (soundfile 格式)
    audio_np = waveform.cpu().numpy().T  # [S, C] or [S,]

    # 确保输出目录存在
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    # 写入 WAV (float32)
    sf.write(output_path, audio_np, sample_rate, subtype="FLOAT")

    return sample_rate


def read_wav_to_audio(wav_path: str) -> dict:
    """从 WAV 文件读取并转换为 ComfyUI AUDIO dict

    Returns:
        {"waveform": tensor[1, C, S], "sample_rate": int}
    """
    data, sr = sf.read(wav_path, dtype="float32")  # [S, C] or [S,]

    if data.ndim == 1:
        # 单声道: [S] → [1, 1, S]
        tensor = torch.from_numpy(data).float().unsqueeze(0).unsqueeze(0)
    else:
        # 立体声: [S, C] → [1, C, S]
        tensor = torch.from_numpy(data.T).float().unsqueeze(0)

    return {
        "waveform": tensor,
        "sample_rate": int(sr),
    }


def create_silent_audio(sample_rate: int = 44100) -> dict:
    """创建静音 AUDIO dict（用于未使用的输出端口）"""
    return {
        "waveform": torch.zeros(1, 1, 1),
        "sample_rate": sample_rate,
    }


def cleanup_temp_dir(tmp_dir: str):
    """清理临时目录"""
    import shutil
    if os.path.exists(tmp_dir):
        try:
            shutil.rmtree(tmp_dir)
        except Exception:
            pass
