#!/usr/bin/env python3
"""MSST 工作进程——在 MSST 便携包 Python 环境下运行。

支持两种模式：
  --action separate    执行音频分离
  --action list_models 列出已安装的模型（复用 MSST 原生 webui.utils）
"""
import argparse
import json
import os
import sys
import traceback


def setup_env(msst_root: str):
    """设置 sys.path 和工作目录"""
    if msst_root and os.path.isdir(msst_root):
        if msst_root not in sys.path:
            sys.path.insert(0, msst_root)
        os.chdir(msst_root)


# ═══════════════════════════════════════════════
# 模式 1：列出已安装的模型（复用 MSST 原生 API）
# ═══════════════════════════════════════════════
def list_models(msst_root: str) -> dict:
    """使用 MSST webui.utils 中的现成函数获取模型列表和音轨信息"""
    setup_env(msst_root)

    from webui.utils import load_msst_model, load_vr_model, get_msst_model, get_vr_model, load_configs

    result = {"MSST": [], "VR": []}

    # ── MSST 模型 ──
    for name in load_msst_model():
        try:
            model_path, config_path, model_type, _ = get_msst_model(name)
        except Exception as e:
            print(f"WARN: skip {name}: {e}", file=sys.stderr)
            continue

        # 从 model_path 推断分类（如 pretrain/vocal_models/xxx.ckpt → vocal_models）
        category = "unknown"
        path_parts = model_path.replace("\\", "/").split("/")
        if len(path_parts) >= 2:
            category = path_parts[-2]  # pretrain/xxx/model.ckpt → xxx

        # 从 YAML 配置解析音轨名（MSST 原生 load_configs）
        instruments = ["vocals", "instrumental"]  # fallback
        if config_path and os.path.isfile(config_path):
            try:
                cfg = load_configs(config_path)
                inst = cfg.training.get("instruments")
                if inst and isinstance(inst, (list, tuple)):
                    instruments = [str(i) for i in inst]
            except Exception:
                pass

        result["MSST"].append({
            "display_name": name,
            "model_class": "MSST",
            "model_type": model_type,
            "config_path": config_path if os.path.isfile(config_path) else "",
            "model_path": model_path,
            "instruments": instruments,
            "category": category,
        })

    # ── VR 模型 ──
    for name in load_vr_model():
        try:
            primary, secondary, _, vr_dir = get_vr_model(name)
        except Exception as e:
            print(f"WARN: skip VR {name}: {e}", file=sys.stderr)
            continue

        model_path = os.path.join(vr_dir, name) if vr_dir else os.path.join(
            msst_root, "pretrain", "VR_Models", name)
        result["VR"].append({
            "display_name": name,
            "model_class": "VR",
            "model_type": "VR",
            "config_path": "",
            "model_path": model_path,
            "instruments": [primary, secondary],
            "category": "VR_Models",
        })

    return result


# ═══════════════════════════════════════════════
# 模式 2：执行音频分离
# ═══════════════════════════════════════════════
def run_separation(args):
    setup_env(args.msst_root)
    import numpy as np
    import librosa
    import soundfile as sf

    mix, sr = librosa.load(args.input_wav, sr=None, mono=False)

    if args.model_class == "MSST":
        from inference.msst_infer import MSSeparator
        separator = MSSeparator(
            model_type=args.model_type,
            config_path=args.config_path,
            model_path=args.model_path,
            device=args.device,
            output_format="wav",
            use_tta=False,
            store_dirs="",
            debug=False,
        )
    else:
        from modules.vocal_remover.separator import Separator
        separator = Separator(
            model_file=args.model_path,
            output_dir="",
            output_format="wav",
            use_cpu=(args.device == "cpu"),
        )

    results = separator.separate(mix)
    separator.del_cache()

    manifest = {}
    os.makedirs(args.output_dir, exist_ok=True)
    for stem_name, audio_data in results.items():
        out_path = os.path.join(args.output_dir, f"{stem_name}.wav")
        sf.write(out_path, audio_data, sr)
        manifest[stem_name] = f"{stem_name}.wav"

    with open(os.path.join(args.output_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f)


# ═══════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser(description="MSST-WebUI 工作进程")
    parser.add_argument("--action", default="separate", choices=["separate", "list_models"])
    parser.add_argument("--model_class", choices=["MSST", "VR"])
    parser.add_argument("--model_type", default="")
    parser.add_argument("--config_path", default="")
    parser.add_argument("--model_path", default="")
    parser.add_argument("--input_wav", default="")
    parser.add_argument("--output_dir", default="")
    parser.add_argument("--device", default="auto")
    parser.add_argument("--msst_root", default="")
    args = parser.parse_args()

    try:
        if args.action == "list_models":
            data = list_models(args.msst_root)
            print(json.dumps(data, ensure_ascii=False))
        else:
            run_separation(args)
    except Exception as e:
        if args.action == "list_models":
            print(json.dumps({"error": f"{type(e).__name__}: {str(e)}"}))
        else:
            with open(os.path.join(args.output_dir, "error.txt"), "w", encoding="utf-8") as f:
                f.write(f"{type(e).__name__}: {str(e)}\n{traceback.format_exc()}")
        sys.exit(1)


if __name__ == "__main__":
    main()
