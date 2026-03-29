"""DG-Lab V3 波形数据预设

每条波形数据为 8 字节 HEX 格式，代表 100ms 的脉冲数据。
格式: 4字节频率 + 4字节强度百分比
"""

import random
from pathlib import Path
from typing import Optional

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

# 用户上传波形：键为波形名（文件名 stem），值为协议帧数组
CUSTOM_WAVE_PRESETS: dict[str, list[str]] = {}


def _normalize_strength(value: float) -> int:
    return max(0, min(100, int(round(value))))


def _convert_wave_frequency(input_freq: int) -> int:
    """将输入频率(10-1000)换算为协议频率字节值(10-240)。"""
    if 10 <= input_freq <= 100:
        return input_freq
    if 101 <= input_freq <= 600:
        return ((input_freq - 100) // 5) + 100
    if 601 <= input_freq <= 1000:
        return ((input_freq - 600) // 10) + 200
    return 10


def _frame_to_hex(freqs: list[int], strengths: list[int]) -> str:
    freq_hex = "".join(f"{value:02X}" for value in freqs)
    strength_hex = "".join(f"{value:02X}" for value in strengths)
    return f"{freq_hex}{strength_hex}"


def parse_dungeonlab_pulse(content: str) -> list[str]:
    """解析 DG-Lab App 导出的 .pulse 文件为协议帧数组。"""
    content = content.strip()
    if not content.startswith("Dungeonlab+pulse"):
        return []

    try:
        _, rest = content.split(":", 1)
    except ValueError:
        return []

    segments = rest.split("+section+")
    strengths_all: list[float] = []
    for seg in segments:
        if "/" not in seg:
            continue
        _, data = seg.split("/", 1)
        tokens = [t for t in data.split(",") if t.strip()]
        for token in tokens:
            token = token.strip()
            if not token:
                continue
            try:
                val_str = token.split("-", 1)[0]
                strengths_all.append(float(val_str))
            except ValueError:
                continue

    if not strengths_all:
        return []

    pulse_frames: list[str] = []
    protocol_freq = _convert_wave_frequency(80)
    i = 0
    while i < len(strengths_all):
        group = strengths_all[i:i + 4]
        if len(group) < 4:
            group += [group[-1]] * (4 - len(group))

        strengths = [_normalize_strength(v) for v in group]
        freqs = [protocol_freq, protocol_freq, protocol_freq, protocol_freq]
        pulse_frames.append(_frame_to_hex(freqs, strengths))
        i += 4

    return pulse_frames[:80]


def clear_custom_waves() -> None:
    """清空已加载的用户上传波形。"""
    CUSTOM_WAVE_PRESETS.clear()


def collect_uploaded_wave_paths(config, uploaded_wave_files_dir: Path) -> list[Path]:
    """从 AstrBot 默认上传目录收集 uploaded_wave_files 对应文件。"""
    raw_files = []
    if hasattr(config, "get"):
        raw_files = config.get("uploaded_wave_files", [])

    if not isinstance(raw_files, list):
        return []

    file_names: list[str] = []
    for item in raw_files:
        if isinstance(item, str) and item.strip():
            file_names.append(Path(item.strip()).name)
            continue

        if not isinstance(item, dict):
            continue

        # 只取文件名，统一从默认上传目录读取。
        for key in ("name", "filename", "path", "file_path", "filepath", "saved_path", "value"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                file_names.append(Path(value.strip()).name)
                break

    resolved_paths: list[Path] = []
    for file_name in file_names:
        if not file_name:
            continue
        file_path = (uploaded_wave_files_dir / file_name).resolve()
        if file_path.exists() and file_path.is_file():
            resolved_paths.append(file_path)

    return resolved_paths


def reload_uploaded_waves(config, uploaded_wave_files_dir: Path, logger) -> int:
    """加载配置上传的 .pulse 波形文件。"""
    try:
        uploaded_paths = collect_uploaded_wave_paths(config, uploaded_wave_files_dir)
        uploaded_wave_count = load_pulse_files(
            uploaded_files=uploaded_paths,
            logger=logger,
        )
        logger.info(f"用户上传波形加载完成，共 {uploaded_wave_count} 个")
        return uploaded_wave_count
    except Exception as e:
        logger.error(f"加载用户上传波形失败: {e}")
        return 0


def _dedupe_custom_wave_name(base_name: str) -> str:
    """避免与内置/已加载波形重名，返回可用名称。"""
    candidate = base_name
    index = 1
    builtin_en_lower = {name.lower() for name in WAVE_NAME_MAP.keys()}
    builtin_cn_lower = {name.lower() for name in WAVE_NAME_MAP_REVERSE.keys()} | {name.lower() for name in WAVE_PRESETS.keys()}

    def _conflicted(name: str) -> bool:
        lower_name = name.lower()
        if lower_name in builtin_en_lower or lower_name in builtin_cn_lower:
            return True
        for custom_name in CUSTOM_WAVE_PRESETS.keys():
            if custom_name.lower() == lower_name:
                return True
        return False

    while (
        _conflicted(candidate)
    ):
        candidate = f"{base_name}_{index}"
        index += 1
    return candidate


def load_pulse_files(
    uploaded_files: list[Path],
    logger,
) -> int:
    """从上传文件列表加载 .pulse 波形并合并到用户波形集合。"""
    clear_custom_waves()

    loaded = 0
    for src in uploaded_files:
        if not src.exists() or not src.is_file():
            logger.warning(f"上传波形文件不存在或不可读: {src}")
            continue
        if src.suffix.lower() != ".pulse":
            logger.warning(f"跳过非 .pulse 文件: {src.name}")
            continue

        try:
            text = src.read_text(encoding="utf-8").strip()
        except OSError as e:
            logger.warning(f"读取 .pulse 文件失败: {src.name} - {e}")
            continue

        pulses = parse_dungeonlab_pulse(text)
        if not pulses:
            logger.warning(f"跳过空波形文件: {src.name}")
            continue

        preset_name = _dedupe_custom_wave_name(src.stem.strip() or "custom_wave")
        CUSTOM_WAVE_PRESETS[preset_name] = pulses
        loaded += 1
        logger.info(f"已加载 .pulse 波形: {preset_name} ({len(pulses)} 帧)")

    return loaded


def _resolve_custom_wave_name(name: str) -> Optional[str]:
    if name in CUSTOM_WAVE_PRESETS:
        return name

    lower_name = name.lower()
    for custom_name in CUSTOM_WAVE_PRESETS.keys():
        if custom_name.lower() == lower_name:
            return custom_name
    return None


def get_wave_names() -> list[str]:
    """获取所有可用波形名称（内置英文名 + 用户上传名）。"""
    names = list(WAVE_NAME_MAP.keys())
    names.extend(sorted(CUSTOM_WAVE_PRESETS.keys()))
    return names


def get_wave_data(name: str) -> list[str]:
    """根据名称获取波形数据，支持内置中英文名与用户上传名。"""
    # 先尝试中文名
    if name in WAVE_PRESETS:
        return WAVE_PRESETS[name]
    # 尝试英文名
    cn_name = WAVE_NAME_MAP.get(name)
    if cn_name and cn_name in WAVE_PRESETS:
        return WAVE_PRESETS[cn_name]
    # 尝试用户上传名
    custom_name = _resolve_custom_wave_name(name)
    if custom_name:
        return CUSTOM_WAVE_PRESETS[custom_name]
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
    """获取所有波形（内置+用户上传）的描述性文字，供大模型参考。"""
    descriptions = []
    for en_name, cn_name in WAVE_NAME_MAP.items():
        data = WAVE_PRESETS.get(cn_name, [])
        duration_ms = len(data) * 100
        descriptions.append(f"- {en_name} ({cn_name}): 持续 {duration_ms}ms，共 {len(data)} 帧")

    for custom_name in sorted(CUSTOM_WAVE_PRESETS.keys()):
        data = CUSTOM_WAVE_PRESETS.get(custom_name, [])
        duration_ms = len(data) * 100
        descriptions.append(f"- {custom_name} : 持续 {duration_ms}ms，共 {len(data)} 帧")

    return "\n".join(descriptions)
