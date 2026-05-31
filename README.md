# ComfyUI-MSST-WebUI

在 ComfyUI 中调用 MSST WebUI 进行音频源分离，支持人声/伴奏分离、多轨分离、降噪、去混响、Karaoke 等。

**架构**：通过子进程调用 MSST 便携包自带的 Python 环境执行推理，与 ComfyUI 环境完全隔离。

## 节点列表

| 节点 | 说明 |
|---|---|
| **MSST Load Audio** | 加载音频文件，输出 AUDIO + 文件夹路径 + 文件名 |
| **MSST Audio Separate** | MSST 模型音频分离（vocal_models / multi_stem_models / single_stem_models） |
| **UVR Audio Separate** | UVR/VR 模型音频分离 |
| **MSST Save Audio** | 保存音频到指定路径 |

## 安装

1. 克隆到 ComfyUI 的 `custom_nodes/` 目录：

```bash
cd ComfyUI/custom_nodes/
git clone <仓库地址> ComfyUI-MSST-WebUI
```

2. 编辑 `config.json`，设置 `msst_root` 指向 MSST WebUI 安装目录：

```json
{
  "msst_root": "D:\\MSST_WebUI_1.7.0_v2_cu128",
  "python_env": ""
}
```

- `msst_root`：MSST WebUI 安装目录（**必填**）
- `python_env`：Python 环境路径，留空自动使用 `{msst_root}/workenv`

3. 重启 ComfyUI。

## 基本用法

### 加载音频 → 分离 → 保存

1. 添加 **MSST Load Audio** 节点，填入音频文件路径
2. 添加 **MSST Audio Separate** 节点：
   - `audio` → 连接 Load Audio 的 `audio` 输出
   - `model_category` → 选择模型类型
   - `model_name` → 自动筛选出对应类型的可用模型
   - `base_filename` → 连接 Load Audio 的 `filename` 输出
3. 每个输出端口旁会显示音轨名（如 `vocals`、`instrumental`），连接需要的音轨到 **MSST Save Audio**
4. 将分离节点的 `xxx_fn` 输出连接到 Save Audio 的 `filename` 输入
5. 设置 Save Audio 的 `folder_path` 和 `format`，运行

### 串联推理（级联）

分离的输出可直接接入下一个分离节点继续处理：

```
MSLoadAudio → MSSTSeparate(vocal_models)
    ├── stem_0 (vocals) → MSSTSeparate(single_stem_models, dereverb)
    │                         └── stem_0 (noreverb) → MSSaveAudio
    └── stem_1 (instrumental) → MSSaveAudio
```

串联时：将上一级的 `xxx_fn` 输出连接到下一级的 `base_filename` 输入，文件名会自动累加（如 `song` → `song_vocals` → `song_vocals_noreverb`）。

### UVR 模型分离

添加 **UVR Audio Separate** 节点，选择 VR 模型即可。

## 示例工作流

`examples/msst-audio-separate-example.json` — 包含加载/分离/级联/保存的完整流程。

## 文件命名规则

每轨输出文件名格式：`{base_filename}_{音轨名}`

例如：`song` + `vocals` → `song_vocals`

## 注意事项

- 需要 MSST WebUI 便携包（含 workenv Python 环境）
- 首次使用请确保 `config.json` 中 `msst_root` 路径正确
- 推理使用 MSST 便携包自带的 Python 环境，与 ComfyUI 环境完全隔离
- 模型列表和音轨名称通过 MSST Python 子进程动态发现，确保准确
- 输出端口旁绘制的蓝色文字标明每个端口的实际音轨名
- VRAM 有限的用户建议使用 `device=cpu`
