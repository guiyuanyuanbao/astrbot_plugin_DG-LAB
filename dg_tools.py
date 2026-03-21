"""DG-Lab LLM Tools

定义供大模型调用的工具，用于控制郊狼设备的强度和波形。
这些 Tool 只在郊狼模式激活且设备已绑定时才能正常工作。
"""

import asyncio
from typing import Optional
from pydantic import Field
from pydantic.dataclasses import dataclass
from astrbot.api import logger

from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool, ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext

from .dg_waves import (
    get_wave_data,
    get_wave_names,
    get_wave_descriptions,
    get_wave_model_reference_examples,
    WAVE_NAME_MAP,
)


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
    """将单帧 4 组频率/强度数据编码为 8 字节 HEX。"""
    freq_hex = "".join(f"{value:02X}" for value in freqs)
    strength_hex = "".join(f"{value:02X}" for value in strengths)
    return f"{freq_hex}{strength_hex}"


def _build_custom_wave_data(frames: list[dict]) -> tuple[list[str], Optional[str]]:
    """把自定义帧数据转换为协议波形数组，返回 (wave_data, error)。"""
    if not isinstance(frames, list):
        return [], "错误：frames 必须是数组。"
    if not frames:
        return [], "错误：frames 不能为空。"
    if len(frames) > 100:
        # 超过 100 帧时自动截断到 99 帧，而不是报错。
        frames = frames[:99]

    wave_data: list[str] = []

    for index, frame in enumerate(frames, start=1):
        if not isinstance(frame, dict):
            return [], f"错误：frames[{index}] 必须是对象。"

        freqs = frame.get("freqs")
        strengths = frame.get("strengths")

        if not isinstance(freqs, list) or len(freqs) != 4:
            return [], f"错误：frames[{index}].freqs 必须是长度为 4 的数组。"
        if not isinstance(strengths, list) or len(strengths) != 4:
            return [], f"错误：frames[{index}].strengths 必须是长度为 4 的数组。"

        converted_freqs: list[int] = []
        converted_strengths: list[int] = []

        for group_index in range(4):
            raw_freq = freqs[group_index]
            raw_strength = strengths[group_index]

            if not isinstance(raw_freq, (int, float)):
                return [], f"错误：frames[{index}].freqs[{group_index + 1}] 必须是数字。"
            if not isinstance(raw_strength, (int, float)):
                return [], f"错误：frames[{index}].strengths[{group_index + 1}] 必须是数字。"

            input_freq = int(raw_freq)
            input_strength = int(raw_strength)

            if input_freq < 10 or input_freq > 1000:
                return [], (
                    f"错误：frames[{index}].freqs[{group_index + 1}]={input_freq} 超出范围，"
                    "允许范围为 10-1000。"
                )
            if input_strength < 0 or input_strength > 100:
                return [], (
                    f"错误：frames[{index}].strengths[{group_index + 1}]={input_strength} 超出范围，"
                    "允许范围为 0-100。"
                )

            converted_freq = _convert_wave_frequency(input_freq)
            if converted_freq < 10 or converted_freq > 240:
                return [], (
                    f"错误：frames[{index}].freqs[{group_index + 1}] 换算后为 {converted_freq}，"
                    "不在协议字节范围 10-240 内。"
                )

            converted_freqs.append(converted_freq)
            converted_strengths.append(input_strength)

        wave_data.append(_frame_to_hex(converted_freqs, converted_strengths))

    return wave_data, None


def _get_wave_task_attr(channel: str) -> str:
    """返回通道对应的后台波形任务属性名。"""
    return "_wave_task_a" if channel == "A" else "_wave_task_b"


async def _cancel_session_wave_task(session, channel: Optional[str] = None) -> None:
    """取消并回收会话中的后台波形任务。

    channel 为 A/B 时仅取消对应通道任务；为空时取消全部通道任务。
    """
    task_attrs = []

    if channel in ("A", "B"):
        task_attrs.append(_get_wave_task_attr(channel))
    else:
        task_attrs.extend(["_wave_task_a", "_wave_task_b"])

    for task_attr in task_attrs:
        wave_task = getattr(session, task_attr, None)
        if not wave_task:
            continue

        if not wave_task.done():
            wave_task.cancel()
            try:
                await wave_task
            except asyncio.CancelledError:
                pass
            except Exception as ex:
                logger.warning(f"取消旧波形任务时出现异常: {ex}")

        setattr(session, task_attr, None)


def create_dglab_tools(plugin) -> list:
    """创建 DG-Lab 相关的 LLM Tools"""
    tools = [
        DGLabSetStrengthTool(),
        DGLabSendWaveTool(),
        DGLabSendCustomWaveTool(),
        DGLabQuickFireTool(),
        DGLabGetStatusTool(),
        DGLabClearWaveTool(),
        DGLabStopOutputTool(),
    ]
    for tool in tools:
        tool._plugin = plugin
    return tools


def _get_plugin_from_tool(tool) -> Optional[object]:
    return getattr(tool, "_plugin", None)


async def _get_tool_session(plugin, context):
    """根据 tool 执行上下文定位当前会话，避免跨会话误控。"""
    session = await plugin.get_tool_session(context)
    if session:
        return session, None
    return None, "错误：未找到当前会话对应的郊狼模式，或会话未激活。请在当前会话先执行 /dglab start 并完成绑定。"


def _get_channel_max_strength(plugin, session, channel: str) -> int:
    """通道最大强度 = min(设备上限, 配置最大值)。"""
    configured_max = plugin._max_strength_a if channel == "A" else plugin._max_strength_b
    ctrl = getattr(session, "controller", None)
    if not ctrl:
        return configured_max

    device_limit = ctrl.strength_a_limit if channel == "A" else ctrl.strength_b_limit
    if isinstance(device_limit, (int, float)) and device_limit > 0:
        return min(configured_max, int(device_limit))
    return configured_max


@dataclass
class DGLabSetStrengthTool(FunctionTool[AstrAgentContext]):
    """设置郊狼设备的通道强度"""

    name: str = "dglab_set_strength"
    description: str = (
        "设置 DG-Lab 郊狼设备的通道强度。"
        "可以调整 A 通道或 B 通道的强度。"
        "强度值范围 0-200，但实际会受到配置的最大强度限制。"
        "mode: 'set' 设置为指定值, 'increase' 增加, 'decrease' 减少。"
        "注意：只有在郊狼模式开启且设备已绑定时才能使用。"
    )
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "channel": {
                    "type": "string",
                    "description": "通道，A 或 B",
                    "enum": ["A", "B"],
                },
                "mode": {
                    "type": "string",
                    "description": "强度变化模式：set(设置为指定值), increase(增加), decrease(减少)",
                    "enum": ["set", "increase", "decrease"],
                },
                "value": {
                    "type": "number",
                    "description": "强度值 (0-200)",
                },
            },
            "required": ["channel", "mode", "value"],
        }
    )

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        plugin = _get_plugin_from_tool(self)
        if not plugin:
            return "错误：插件未初始化。"

        channel = str(kwargs.get("channel", "A")).upper()
        mode = kwargs.get("mode", "set")

        try:
            value = int(kwargs.get("value", 0))
        except (TypeError, ValueError):
            return "错误：value 必须是整数。"

        session, session_err = await _get_tool_session(plugin, context)
        if not session:
            return session_err

        if not session.controller or not session.controller.is_bound:
            return "错误：设备未绑定，请先让用户扫码绑定 APP。"

        # 检查通道配置
        if channel == "A" and "A" not in session.channel_config:
            return f"错误：用户未启用 A 通道，当前配置为 {session.channel_config} 通道。"
        if channel == "B" and "B" not in session.channel_config:
            return f"错误：用户未启用 B 通道，当前配置为 {session.channel_config} 通道。"

        # 限制最大强度
        max_strength = _get_channel_max_strength(plugin, session, channel)
        value = max(0, min(value, max_strength))

        ch_num = 1 if channel == "A" else 2
        mode_map = {"decrease": 0, "increase": 1, "set": 2}
        mode_num = mode_map.get(mode, 2)

        try:
            await session.controller.send_strength(ch_num, mode_num, value)
            ctrl = session.controller
            part_info = ""
            if channel == "A" and session.channel_a_part:
                part_info = f"（部位: {session.channel_a_part}）"
            elif channel == "B" and session.channel_b_part:
                part_info = f"（部位: {session.channel_b_part}）"

            return f"已成功对 {channel} 通道{part_info}执行强度操作: {mode} {value}。当前 A 通道强度: {ctrl.strength_a}, B 通道强度: {ctrl.strength_b}"
        except Exception as e:
            return f"设置强度失败: {str(e)}"


@dataclass
class DGLabSendWaveTool(FunctionTool[AstrAgentContext]):
    """发送波形到郊狼设备"""

    name: str = "dglab_send_wave"
    description: str = (
        "向 DG-Lab 郊狼设备的指定通道发送预设波形数据。"
        f"可用波形列表:\n{get_wave_descriptions()}\n"
        "wave_name 使用英文名。波形会持续循环发送，直到调用停止输出/清空波形，或发送新的波形进行覆盖。"
        "注意：只有在郊狼模式开启且设备已绑定时才能使用。"
    )
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "channel": {
                    "type": "string",
                    "description": "通道，A 或 B",
                    "enum": ["A", "B"],
                },
                "wave_name": {
                    "type": "string",
                    "description": f"波形名称（英文），可选: {', '.join(get_wave_names())}",
                },
            },
            "required": ["channel", "wave_name"],
        }
    )

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        plugin = _get_plugin_from_tool(self)
        if not plugin:
            return "错误：插件未初始化。"

        channel = str(kwargs.get("channel", "A")).upper()
        if channel not in ("A", "B"):
            return "错误：channel 仅支持 A 或 B。"

        wave_name = kwargs.get("wave_name", "breathe")

        session, session_err = await _get_tool_session(plugin, context)
        if not session:
            return session_err

        if not session.controller or not session.controller.is_bound:
            return "错误：设备未绑定，请先让用户扫码绑定 APP。"

        if channel == "A" and "A" not in session.channel_config:
            return f"错误：用户未启用 A 通道，当前配置为 {session.channel_config} 通道。"
        if channel == "B" and "B" not in session.channel_config:
            return f"错误：用户未启用 B 通道，当前配置为 {session.channel_config} 通道。"

        wave_data = get_wave_data(wave_name)
        if not wave_data:
            available = ", ".join(get_wave_names())
            return f"错误：未找到波形 '{wave_name}'。可用波形: {available}"

        try:
            # 发送新波形前先停止同通道旧任务，避免新旧指令交错。
            await _cancel_session_wave_task(session, channel)

            # 先清空队列
            ch_num = 1 if channel == "A" else 2
            await session.controller.clear_wave_queue(ch_num)
            await asyncio.sleep(0.15)

            # 计算单轮波形时长
            wave_duration_ms = len(wave_data) * 100
            task_attr = _get_wave_task_attr(channel)

            # 在后台任务中持续循环发送（避免阻塞 tool call 返回）
            async def _send_waves():
                try:
                    while True:
                        if not session.controller.is_bound:
                            break
                        await session.controller.send_wave(channel, wave_data)
                        await asyncio.sleep(wave_duration_ms / 1000 * 0.9)
                except asyncio.CancelledError:
                    logger.info("波形发送后台任务已取消")
                    raise
                except Exception as ex:
                    logger.error(f"波形发送后台任务异常: {ex}")
                finally:
                    if getattr(session, task_attr, None) is asyncio.current_task():
                        setattr(session, task_attr, None)

            setattr(session, task_attr, asyncio.create_task(_send_waves()))

            cn_name = WAVE_NAME_MAP.get(wave_name, wave_name)
            part_info = ""
            if channel == "A" and session.channel_a_part:
                part_info = f"（部位: {session.channel_a_part}）"
            elif channel == "B" and session.channel_b_part:
                part_info = f"（部位: {session.channel_b_part}）"

            return f"已向 {channel} 通道{part_info}发送波形 '{cn_name}'，将持续循环发送，直到停止或被新波形覆盖。"
        except Exception as e:
            return f"发送波形失败: {str(e)}"


@dataclass
class DGLabQuickFireTool(FunctionTool[AstrAgentContext]):
    """一键开火：按配置临时提高当前强度并自动恢复"""

    name: str = "dglab_quick_fire"
    description: str = (
        "一键开火：在当前强度基础上临时增加指定通道强度，并在 duration_seconds 后恢复到原强度。"
        "增量由 /dglab fire 命令配置，默认 A=1、B=1，最大 30。"
        "channel 支持 A/B/AB。"
    )
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "channel": {
                    "type": "string",
                    "description": "目标通道，A/B/AB，默认 AB",
                    "enum": ["A", "B", "AB"],
                },
                "duration_seconds": {
                    "type": "number",
                    "description": "持续时间（秒），默认 2 秒，最大 30 秒",
                },
            },
            "required": [],
        }
    )

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        plugin = _get_plugin_from_tool(self)
        if not plugin:
            return "错误：插件未初始化。"

        channel = str(kwargs.get("channel", "AB")).upper()
        if channel not in ("A", "B", "AB"):
            return "错误：channel 仅支持 A、B 或 AB。"

        try:
            duration = float(kwargs.get("duration_seconds", 2))
        except (TypeError, ValueError):
            return "错误：duration_seconds 必须是数字。"

        if duration <= 0:
            return "错误：duration_seconds 必须大于 0。"
        duration = min(duration, 30)

        session, session_err = await _get_tool_session(plugin, context)
        if not session:
            return session_err

        if not session.controller or not session.controller.is_bound:
            return "错误：设备未绑定，请先让用户扫码绑定 APP。"

        ctrl = session.controller

        # 若存在旧恢复任务，取消它，避免恢复顺序冲突。
        old_restore_task = getattr(session, "_quick_fire_restore_task", None)
        if old_restore_task and not old_restore_task.done():
            old_restore_task.cancel()
            try:
                await old_restore_task
            except asyncio.CancelledError:
                pass
            except Exception as ex:
                logger.warning(f"取消旧一键开火恢复任务异常: {ex}")

        original_a = int(ctrl.strength_a)
        original_b = int(ctrl.strength_b)

        boost_a = max(0, min(30, int(getattr(session, "quick_fire_boost_a", 1))))
        boost_b = max(0, min(30, int(getattr(session, "quick_fire_boost_b", 1))))

        targets = []
        if channel in ("A", "AB"):
            if "A" not in session.channel_config:
                return f"错误：用户未启用 A 通道，当前配置为 {session.channel_config} 通道。"
            max_a = _get_channel_max_strength(plugin, session, "A")
            new_a = min(max_a, original_a + boost_a)
            targets.append((1, new_a))
        if channel in ("B", "AB"):
            if "B" not in session.channel_config:
                return f"错误：用户未启用 B 通道，当前配置为 {session.channel_config} 通道。"
            max_b = _get_channel_max_strength(plugin, session, "B")
            new_b = min(max_b, original_b + boost_b)
            targets.append((2, new_b))

        try:
            for ch, val in targets:
                await ctrl.send_strength(ch, 2, val)

            async def _restore_after_delay():
                try:
                    await asyncio.sleep(duration)
                    if not ctrl.is_bound:
                        return
                    if channel in ("A", "AB"):
                        await ctrl.send_strength(1, 2, original_a)
                    if channel in ("B", "AB"):
                        await ctrl.send_strength(2, 2, original_b)
                except asyncio.CancelledError:
                    raise
                except Exception as ex:
                    logger.warning(f"一键开火恢复失败: {ex}")
                finally:
                    if getattr(session, "_quick_fire_restore_task", None) is asyncio.current_task():
                        session._quick_fire_restore_task = None

            session._quick_fire_restore_task = asyncio.create_task(_restore_after_delay())

            return (
                f"一键开火已触发：通道 {channel}，持续 {duration} 秒。"
                f"当前配置增量 A={boost_a}, B={boost_b}，结束后将恢复触发前强度。"
            )
        except Exception as e:
            return f"一键开火执行失败: {str(e)}"


@dataclass
class DGLabSendCustomWaveTool(FunctionTool[AstrAgentContext]):
    """发送自定义波形到郊狼设备"""

    name: str = "dglab_send_custom_wave"
    description: str = (
        "发送自定义波形到 DG-Lab 郊狼设备。"
        "每条 frame 代表 100ms，必须包含 4 组频率(freqs)与 4 组强度(strengths)，每组对应 25ms。"
        "freqs 输入范围 10-1000，会自动换算为协议频率字节(10-240)；strengths 输入范围 0-100。"
        "frames 建议长度不超过 100，超过 100 会自动截断到 99 帧。"
        "duration_seconds 默认 30 秒，最小 30 秒，最大 180 秒，期间会循环发送该自定义波形。"
        f"\n随机抽取1个预设波形的前4帧参考（已转换为 frames 约定格式），可作为设计参考，但是不要直接使用，也不要再这基础上补充，而是学习其节奏与强度控制:\n{get_wave_model_reference_examples()}"
    )
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "channel": {
                    "type": "string",
                    "description": "通道，A 或 B",
                    "enum": ["A", "B"],
                },
                "frames": {
                    "type": "array",
                    "description": "自定义波形帧数组（每帧=100ms，超过100帧会自动截断到99帧）",
                    "items": {
                        "type": "object",
                        "properties": {
                            "freqs": {
                                "type": "array",
                                "description": "4组频率输入值（每组25ms），每项范围10-1000",
                                "items": {"type": "number"},
                                "minItems": 4,
                                "maxItems": 4,
                            },
                            "strengths": {
                                "type": "array",
                                "description": "4组强度输入值（每组25ms），每项范围0-100",
                                "items": {"type": "number"},
                                "minItems": 4,
                                "maxItems": 4,
                            },
                        },
                        "required": ["freqs", "strengths"],
                    },
                    "minItems": 10,
                    "maxItems": 100,
                },
                "duration_seconds": {
                    "type": "number",
                    "description": "波形持续发送总时长（秒），默认 30 秒，最小 30 秒，最大 180 秒",
                },
            },
            "required": ["channel", "frames"],
        }
    )

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        plugin = _get_plugin_from_tool(self)
        if not plugin:
            return "错误：插件未初始化。"

        channel = str(kwargs.get("channel", "A")).upper()
        if channel not in ("A", "B"):
            return "错误：channel 仅支持 A 或 B。"

        frames = kwargs.get("frames", [])

        try:
            duration = float(kwargs.get("duration_seconds", 30))
        except (TypeError, ValueError):
            return "错误：duration_seconds 必须是数字。"

        if duration < 30:
            return "错误：duration_seconds 最小为 30 秒。"
        duration = min(duration, 180)

        wave_data, wave_err = _build_custom_wave_data(frames)
        if wave_err:
            return wave_err

        session, session_err = await _get_tool_session(plugin, context)
        if not session:
            return session_err

        if not session.controller or not session.controller.is_bound:
            return "错误：设备未绑定，请先让用户扫码绑定 APP。"

        if channel == "A" and "A" not in session.channel_config:
            return f"错误：用户未启用 A 通道，当前配置为 {session.channel_config} 通道。"
        if channel == "B" and "B" not in session.channel_config:
            return f"错误：用户未启用 B 通道，当前配置为 {session.channel_config} 通道。"

        try:
            # 发送新波形前先停止同通道旧任务，避免新旧指令交错。
            await _cancel_session_wave_task(session, channel)

            ch_num = 1 if channel == "A" else 2
            await session.controller.clear_wave_queue(ch_num)
            await asyncio.sleep(0.15)

            wave_duration_ms = len(wave_data) * 100
            total_ms = duration * 1000
            total_sends = max(1, int(total_ms / wave_duration_ms))
            task_attr = _get_wave_task_attr(channel)

            async def _send_custom_waves():
                try:
                    for i in range(total_sends):
                        if not session.controller.is_bound:
                            break
                        await session.controller.send_wave(channel, wave_data)
                        if i < total_sends - 1:
                            await asyncio.sleep(wave_duration_ms / 1000 * 0.9)
                except asyncio.CancelledError:
                    logger.info("自定义波形发送后台任务已取消")
                    raise
                except Exception as ex:
                    logger.error(f"自定义波形发送后台任务异常: {ex}")
                finally:
                    if getattr(session, task_attr, None) is asyncio.current_task():
                        setattr(session, task_attr, None)

            setattr(session, task_attr, asyncio.create_task(_send_custom_waves()))

            part_info = ""
            if channel == "A" and session.channel_a_part:
                part_info = f"（部位: {session.channel_a_part}）"
            elif channel == "B" and session.channel_b_part:
                part_info = f"（部位: {session.channel_b_part}）"

            return (
                f"已向 {channel} 通道{part_info}发送自定义波形，"
                f"共 {len(wave_data)} 帧（每帧100ms），持续约 {duration} 秒。"
            )
        except Exception as e:
            return f"发送自定义波形失败: {str(e)}"


@dataclass
class DGLabGetStatusTool(FunctionTool[AstrAgentContext]):
    """获取郊狼设备当前状态"""

    name: str = "dglab_get_status"
    description: str = (
        "获取 DG-Lab 郊狼设备的当前状态，包括通道强度、强度上限、通道配置和部位信息。"
        "注意：只有在郊狼模式开启时才能使用。"
    )
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {},
            "required": [],
        }
    )

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        plugin = _get_plugin_from_tool(self)
        if not plugin:
            return "错误：插件未初始化。"

        session, session_err = await _get_tool_session(plugin, context)
        if not session:
            return session_err

        ctrl = session.controller
        info = []
        info.append("郊狼模式: 已开启")
        info.append(f"设备绑定: {'是' if ctrl.is_bound else '否'}")
        info.append(f"使用通道: {session.channel_config}")
        if session.channel_a_part:
            info.append(f"A通道部位: {session.channel_a_part}")
        if session.channel_b_part:
            info.append(f"B通道部位: {session.channel_b_part}")
        if ctrl.is_bound:
            info.append(f"A通道强度: {ctrl.strength_a} (上限: {ctrl.strength_a_limit}, 配置最大: {plugin._max_strength_a})")
            info.append(f"B通道强度: {ctrl.strength_b} (上限: {ctrl.strength_b_limit}, 配置最大: {plugin._max_strength_b})")
        info.append(f"可用波形: {', '.join(get_wave_names())}")
        return "\n".join(info)


@dataclass
class DGLabClearWaveTool(FunctionTool[AstrAgentContext]):
    """清空郊狼设备的波形队列"""

    name: str = "dglab_clear_wave"
    description: str = (
        "清空 DG-Lab 郊狼设备指定通道的波形队列。"
        "当需要立即停止当前波形并切换到新波形时使用。"
        "注意：只有在郊狼模式开启且设备已绑定时才能使用。"
    )
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "channel": {
                    "type": "string",
                    "description": "通道，A 或 B",
                    "enum": ["A", "B"],
                },
            },
            "required": ["channel"],
        }
    )

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        plugin = _get_plugin_from_tool(self)
        if not plugin:
            return "错误：插件未初始化。"

        channel = str(kwargs.get("channel", "A")).upper()
        if channel not in ("A", "B"):
            return "错误：channel 仅支持 A 或 B。"

        session, session_err = await _get_tool_session(plugin, context)
        if not session:
            return session_err

        if not session.controller or not session.controller.is_bound:
            return "错误：设备未绑定。"

        try:
            # 清空前先停止后台发送，避免旧任务继续写入。
            await _cancel_session_wave_task(session, channel)
            ch_num = 1 if channel == "A" else 2
            await session.controller.clear_wave_queue(ch_num)
            return f"已清空 {channel} 通道的波形队列。"
        except Exception as e:
            return f"清空波形队列失败: {str(e)}"


@dataclass
class DGLabStopOutputTool(FunctionTool[AstrAgentContext]):
    """停止郊狼设备输出"""

    name: str = "dglab_stop_output"
    description: str = (
        "立即停止 DG-Lab 郊狼设备的输出，将所有通道强度归零并清空波形队列。"
        "当用户表示不适、疼痛或要求停止时应立即调用此工具。"
        "注意：只有在郊狼模式开启且设备已绑定时才能使用。"
    )
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {},
            "required": [],
        }
    )

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        plugin = _get_plugin_from_tool(self)
        if not plugin:
            return "错误：插件未初始化。"

        session, session_err = await _get_tool_session(plugin, context)
        if not session:
            return session_err

        if not session.controller or not session.controller.is_bound:
            return "错误：设备未绑定。"

        try:
            ctrl = session.controller
            # 先停止后台波形任务，避免 stop 后仍有发送。
            await _cancel_session_wave_task(session)
            # 清空波形队列
            await ctrl.clear_wave_queue(1)
            await ctrl.clear_wave_queue(2)
            await asyncio.sleep(0.15)
            # 强度归零
            await ctrl.send_strength(1, 2, 0)
            await ctrl.send_strength(2, 2, 0)
            return "已停止所有输出：AB 通道波形队列已清空，强度已归零。"
        except Exception as e:
            return f"停止输出失败: {str(e)}"
