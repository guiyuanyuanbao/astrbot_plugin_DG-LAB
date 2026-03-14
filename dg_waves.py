"""DG-Lab V3 波形数据预设

每条波形数据为 8 字节 HEX 格式，代表 100ms 的脉冲数据。
格式: 4字节频率 + 4字节强度百分比
"""

import random

# 预设波形 (V3 格式 - 8字节HEX)
WAVE_PRESETS: dict[str, list[str]] = {
    "呼吸": [
        "0A0A0A0A00000000",
        "0A0A0A0A14141414",
        "0A0A0A0A28282828",
        "0A0A0A0A3C3C3C3C",
        "0A0A0A0A50505050",
        "0A0A0A0A64646464",
        "0A0A0A0A64646464",
        "0A0A0A0A64646464",
        "0A0A0A0A00000000",
        "0A0A0A0A00000000",
        "0A0A0A0A00000000",
        "0A0A0A0A00000000",
    ],
    "潮汐": [
        "0A0A0A0A00000000",
        "0B0B0B0B10101010",
        "0D0D0D0D21212121",
        "0E0E0E0E32323232",
        "1010101042424242",
        "1212121253535353",
        "1313131364646464",
        "151515155C5C5C5C",
        "1616161654545454",
        "181818184C4C4C4C",
        "1A1A1A1A44444444",
        "1A1A1A1A00000000",
        "1B1B1B1B10101010",
        "1D1D1D1D21212121",
        "1E1E1E1E32323232",
        "2020202042424242",
        "2222222253535353",
        "2323232364646464",
        "252525255C5C5C5C",
        "2626262654545454",
        "282828284C4C4C4C",
        "2A2A2A2A44444444",
        "0A0A0A0A00000000",
    ],
    "连击": [
        "0A0A0A0A64646464",
        "0A0A0A0A00000000",
        "0A0A0A0A64646464",
        "0A0A0A0A42424242",
        "0A0A0A0A21212121",
        "0A0A0A0A00000000",
        "0A0A0A0A00000000",
        "0A0A0A0A00000000",
    ],
    "快速按捏": [
        "0A0A0A0A00000000",
        "0A0A0A0A64646464",
        "0A0A0A0A00000000",
        "0A0A0A0A64646464",
        "0A0A0A0A00000000",
        "0A0A0A0A64646464",
        "0A0A0A0A00000000",
        "0A0A0A0A64646464",
    ],
    "按捏渐强": [
        "0A0A0A0A00000000",
        "0A0A0A0A1C1C1C1C",
        "0A0A0A0A00000000",
        "0A0A0A0A34343434",
        "0A0A0A0A00000000",
        "0A0A0A0A49494949",
        "0A0A0A0A00000000",
        "0A0A0A0A57575757",
        "0A0A0A0A00000000",
        "0A0A0A0A64646464",
        "0A0A0A0A00000000",
    ],
    "心跳节奏": [
        "7070707064646464",
        "7070707064646464",
        "7070707064646464",
        "7070707064646464",
        "7070707064646464",
        "7070707064646464",
        "0A0A0A0A00000000",
        "0A0A0A0A00000000",
        "0A0A0A0A00000000",
        "0A0A0A0A00000000",
        "0A0A0A0A00000000",
        "0A0A0A0A4B4B4B4B",
        "0A0A0A0A53535353",
        "0A0A0A0A5B5B5B5B",
        "0A0A0A0A64646464",
        "0A0A0A0A00000000",
        "0A0A0A0A00000000",
        "0A0A0A0A00000000",
        "0A0A0A0A00000000",
        "0A0A0A0A00000000",
    ],
    "压缩": [
        "4A4A4A4A64646464",
        "4545454564646464",
        "4040404064646464",
        "3B3B3B3B64646464",
        "3636363664646464",
        "3232323264646464",
        "2D2D2D2D64646464",
        "2828282864646464",
        "2323232364646464",
        "1E1E1E1E64646464",
        "1A1A1A1A64646464",
        "0A0A0A0A64646464",
        "0A0A0A0A64646464",
        "0A0A0A0A64646464",
        "0A0A0A0A64646464",
        "0A0A0A0A64646464",
        "0A0A0A0A64646464",
        "0A0A0A0A64646464",
        "0A0A0A0A64646464",
        "0A0A0A0A64646464",
        "0A0A0A0A64646464",
    ],
    "节奏步伐": [
        "0A0A0A0A00000000",
        "0A0A0A0A14141414",
        "0A0A0A0A28282828",
        "0A0A0A0A3C3C3C3C",
        "0A0A0A0A50505050",
        "0A0A0A0A64646464",
        "0A0A0A0A00000000",
        "0A0A0A0A19191919",
        "0A0A0A0A32323232",
        "0A0A0A0A4B4B4B4B",
        "0A0A0A0A64646464",
        "0A0A0A0A00000000",
        "0A0A0A0A21212121",
        "0A0A0A0A42424242",
        "0A0A0A0A64646464",
        "0A0A0A0A00000000",
        "0A0A0A0A32323232",
        "0A0A0A0A64646464",
        "0A0A0A0A00000000",
        "0A0A0A0A64646464",
        "0A0A0A0A00000000",
        "0A0A0A0A64646464",
        "0A0A0A0A00000000",
        "0A0A0A0A64646464",
        "0A0A0A0A00000000",
        "0A0A0A0A64646464",
        "0A0A0A0A00000000",
    ],
}

# 波形名称映射（用于大模型友好识别）
WAVE_NAME_MAP = {
    "breathe": "呼吸",
    "tide": "潮汐",
    "combo": "连击",
    "fast_pinch": "快速按捏",
    "pinch_crescendo": "按捏渐强",
    "heartbeat": "心跳节奏",
    "compress": "压缩",
    "rhythm_step": "节奏步伐",
}

# 反向映射
WAVE_NAME_MAP_REVERSE = {v: k for k, v in WAVE_NAME_MAP.items()}


def get_wave_names() -> list[str]:
    """获取所有波形名称（英文名）"""
    return list(WAVE_NAME_MAP.keys())


def get_wave_data(name: str) -> list[str]:
    """根据名称获取波形数据，支持中英文名"""
    # 先尝试中文名
    if name in WAVE_PRESETS:
        return WAVE_PRESETS[name]
    # 尝试英文名
    cn_name = WAVE_NAME_MAP.get(name)
    if cn_name and cn_name in WAVE_PRESETS:
        return WAVE_PRESETS[cn_name]
    return []


def _protocol_freq_to_input(freq_byte: int) -> int:
    """将协议频率字节值映射回自定义 tool 的输入频率（确定性参考值）。"""
    if freq_byte <= 100:
        return freq_byte
    if freq_byte <= 200:
        return (freq_byte - 100) * 5 + 100
    return (freq_byte - 200) * 10 + 600


def _decode_frame_hex_to_model_format(frame_hex: str) -> dict:
    """将 8 字节 HEX 波形解码为模型约定的 frame 格式。"""
    if len(frame_hex) != 16:
        raise ValueError("frame_hex 长度必须为 16 个十六进制字符")

    freq_bytes = [int(frame_hex[i:i + 2], 16) for i in range(0, 8, 2)]
    strength_bytes = [int(frame_hex[i:i + 2], 16) for i in range(8, 16, 2)]

    return {
        "freqs": [_protocol_freq_to_input(freq) for freq in freq_bytes],
        "strengths": strength_bytes,
    }


def get_wave_model_reference_examples() -> str:
    """随机返回 1 个预设波形前 4 帧的模型约定格式，供生成自定义波形参考。"""
    candidates = list(WAVE_NAME_MAP.items())
    if not candidates:
        return "- 无可用波形参考"

    en_name, cn_name = random.choice(candidates)
    source_frames = WAVE_PRESETS.get(cn_name, [])[:4]
    model_frames = [_decode_frame_hex_to_model_format(frame) for frame in source_frames]
    return f"- {en_name} ({cn_name}): {model_frames}"


def get_wave_descriptions() -> str:
    """获取所有波形的描述性文字，供大模型参考"""
    descriptions = []
    for en_name, cn_name in WAVE_NAME_MAP.items():
        data = WAVE_PRESETS.get(cn_name, [])
        duration_ms = len(data) * 100
        descriptions.append(f"- {en_name} ({cn_name}): 持续 {duration_ms}ms，共 {len(data)} 帧")
    return "\n".join(descriptions)
