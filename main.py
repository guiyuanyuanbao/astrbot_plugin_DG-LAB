import asyncio
import io
import math
import os
import re
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.provider import LLMResponse, ProviderRequest
from astrbot.api.star import Context, Star, register
from astrbot.api import logger, AstrBotConfig
from astrbot.api.event import MessageChain
import astrbot.api.message_components as Comp
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .afdian_api import AfdianAPIClient, AfdianConfig
from .billing_db import BillingDB, UserQuota
from .dg_server import DGLabWSServer, DGLabController
from .dg_waves import get_wave_descriptions, get_wave_names, get_wave_data, reload_uploaded_waves


class DGLabSession:
    """单个聊天会话的郊狼模式状态"""

    def __init__(self, umo: str):
        self.umo = umo
        self.active = False
        self.controller: Optional[DGLabController] = None
        self.ws_server: Optional[DGLabWSServer] = None
        self.channel_config: str = "AB"  # 用户选择的通道: A, B, AB
        self.channel_a_part: str = ""  # A通道连接的部位
        self.channel_b_part: str = ""  # B通道连接的部位
        self.original_persona_id: Optional[str] = None  # 进入郊狼模式前的人格 ID
        self.dglab_persona_id: Optional[str] = None  # 当前使用的郊狼共享人格 ID
        self.bound_conversation_id: Optional[str] = None  # 绑定郊狼模式时的对话 ID
        self._wave_task_a: Optional[asyncio.Task] = None
        self._wave_task_b: Optional[asyncio.Task] = None
        self.quick_fire_boost_a: int = 1  # 一键开火 A 通道临时增量
        self.quick_fire_boost_b: int = 1  # 一键开火 B 通道临时增量
        self._quick_fire_restore_task: Optional[asyncio.Task] = None
        self._is_exiting: bool = False

    def get_status_desc(self) -> str:
        """获取当前会话状态描述"""
        if not self.active:
            return "郊狼模式未开启"
        parts = []
        parts.append(f"通道: {self.channel_config}")
        if self.channel_a_part:
            parts.append(f"A通道部位: {self.channel_a_part}")
        if self.channel_b_part:
            parts.append(f"B通道部位: {self.channel_b_part}")
        if self.controller and self.controller.is_bound:
            parts.append(f"设备已绑定")
            parts.append(f"A通道强度: {self.controller.strength_a}/{self.controller.strength_a_limit}")
            parts.append(f"B通道强度: {self.controller.strength_b}/{self.controller.strength_b_limit}")
        else:
            parts.append("等待APP扫码绑定")
        return " | ".join(parts)


@register("astrbot_plugin_DG-LAB", "桂鸢", "DG-Lab 郊狼控制器插件：通过大模型对话控制郊狼脉冲主机", "2.0.0")
class DGLabPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config or {}
        self._ws_host: str = self.config.get("ws_host", "0.0.0.0")
        self._ws_port: int = self.config.get("ws_port", 5555)
        self._ws_external_host: str = self.config.get("ws_external_host", "127.0.0.1")
        self._send_qr_raw_url: bool = bool(self.config.get("send_qr_raw_url", True))
        self._max_strength_a: int = self.config.get("max_strength_a", 100)
        self._max_strength_b: int = self.config.get("max_strength_b", 100)
        # 郊狼人格配置
        self._dglab_persona_id: str = self.config.get("dglab_persona_id", "dglab_persona_shared")
        self._dglab_persona_system_prompt: str = self.config.get("dglab_persona_system_prompt", "")
        self._dglab_persona_begin_dialogs = self.config.get("dglab_persona_begin_dialogs", [])
        self._dglab_persona_error_reply: str = self.config.get(
            "dglab_persona_error_reply",
            "郊狼模式开启失败：无法创建并切换郊狼人格，请检查人格配置。",
        )
        self._dglab_default_persona_id: str = self.config.get("dglab_default_persona_id", "")
        afdian_cfg = self.config.get("afdian", {}) or {}
        billing_cfg = self.config.get("billing", {}) or {}
        self._afdian_client = AfdianAPIClient(
            AfdianConfig(
                base_url=str(
                    afdian_cfg.get("base_url", "https://afdian.com/api/open")
                    or "https://afdian.com/api/open"
                ),
                user_id=str(afdian_cfg.get("user_id", "") or ""),
                token=str(afdian_cfg.get("token", "") or ""),
            )
        )
        self._billing_free_quota_amount = max(
            0, self._safe_int(billing_cfg.get("free_quota_amount", 0))
        )
        self._billing_free_refresh_hours = max(
            0, self._safe_int(billing_cfg.get("free_refresh_hours", 24))
        )
        self._billing_token_per_yuan = max(
            0, self._safe_int(billing_cfg.get("token_per_yuan", 0))
        )
        self._billing_enabled = bool(billing_cfg.get("enabled", False))
        self._charge_only_in_coyote_mode = bool(
            billing_cfg.get("charge_only_in_coyote_mode", True)
        )
        self._skip_group_chat_billing = bool(
            billing_cfg.get("skip_group_chat_billing", False)
        )
        self._insufficient_balance_reply = str(
            billing_cfg.get("insufficient_balance_reply", "当前额度不足，无法继续请求。")
            or "当前额度不足，无法继续请求。"
        )
        self._billing_provider_multipliers = self._normalize_provider_multipliers(
            billing_cfg.get("provider_multipliers", [])
        )
        data_root = Path(get_astrbot_data_path())
        self._plugin_data_path: Path = data_root / "plugin_data" / "astrbot_plugin_DG_LAB"
        self._uploaded_wave_files_dir: Path = self._plugin_data_path / "files" / "uploaded_wave_files"
        self._plugin_data_path.mkdir(parents=True, exist_ok=True)
        self._billing_db_path: Path = self._plugin_data_path / "billing.db"
        self._billing_db = BillingDB(self._billing_db_path)
        self._billing_lock = asyncio.Lock()
        self._uploaded_wave_count: int = 0
        # 全局 WS 服务实例
        self._ws_server: Optional[DGLabWSServer] = None
        self._server_started = False
        self._server_lock = asyncio.Lock()
        self._persona_lock = asyncio.Lock()
        self._dglab_tools_registered = False
        self._current_tools = []
        self._shared_dglab_persona_id: Optional[str] = None
        self._sessions: dict[str, DGLabSession] = {}
        self._sessions_lock = asyncio.Lock()
        self._idle_cleanup_task: Optional[asyncio.Task] = None

    async def initialize(self):
        """插件初始化（WS 服务按郊狼模式启停）"""
        self._plugin_data_path.mkdir(parents=True, exist_ok=True)
        logger.info(f"用户上传波形目录: {self._uploaded_wave_files_dir}")
        self._uploaded_wave_count = reload_uploaded_waves(
            config=self.config,
            uploaded_wave_files_dir=self._uploaded_wave_files_dir,
            logger=logger,
        )

    async def _has_active_sessions(self) -> bool:
        async with self._sessions_lock:
            return any(session.active for session in self._sessions.values())

    async def _get_sessions_snapshot(self) -> list[DGLabSession]:
        async with self._sessions_lock:
            return list(self._sessions.values())

    async def _get_session(self, umo: str) -> Optional[DGLabSession]:
        async with self._sessions_lock:
            return self._sessions.get(umo)

    async def _set_session(self, umo: str, session: DGLabSession):
        async with self._sessions_lock:
            self._sessions[umo] = session

    async def _pop_session(self, umo: str) -> Optional[DGLabSession]:
        async with self._sessions_lock:
            return self._sessions.pop(umo, None)

    @staticmethod
    def _extract_umo_from_tool_context(tool_context) -> Optional[str]:
        nested_context = getattr(tool_context, "context", None)
        if nested_context is not None:
            tool_context = nested_context

        candidates = [
            "unified_msg_origin",
            "event_unified_msg_origin",
            "umo",
        ]

        for name in candidates:
            value = getattr(tool_context, name, None)
            if isinstance(value, str) and value:
                return value

        for event_attr in ("event", "message_event"):
            event_obj = getattr(tool_context, event_attr, None)
            if event_obj is None:
                continue
            value = getattr(event_obj, "unified_msg_origin", None)
            if isinstance(value, str) and value:
                return value

        meta = getattr(tool_context, "metadata", None)
        if isinstance(meta, dict):
            value = meta.get("unified_msg_origin") or meta.get("umo")
            if isinstance(value, str) and value:
                return value
            event_meta = meta.get("event") or meta.get("message_event")
            if isinstance(event_meta, dict):
                value = event_meta.get("unified_msg_origin")
                if isinstance(value, str) and value:
                    return value

        if isinstance(tool_context, dict):
            value = tool_context.get("unified_msg_origin") or tool_context.get("umo")
            if isinstance(value, str) and value:
                return value

        return None

    async def get_tool_session(self, tool_context) -> Optional[DGLabSession]:
        umo = self._extract_umo_from_tool_context(tool_context)
        if not umo:
            return None
        session = await self._get_session(umo)
        if not session or not session.active:
            return None

        # 使用对话管理器校验：仅允许绑定时所在对话调用该会话设备。
        try:
            curr_cid = await self.context.conversation_manager.get_curr_conversation_id(umo)
        except Exception as e:
            logger.warning(f"获取当前对话 ID 失败，拒绝 tool 调用: {e}")
            return None

        if session.bound_conversation_id and curr_cid != session.bound_conversation_id:
            logger.warning(
                f"检测到跨对话 tool 调用，已拒绝。umo={umo}, current_cid={curr_cid}, bound_cid={session.bound_conversation_id}"
            )
            return None

        if not session.bound_conversation_id and curr_cid:
            session.bound_conversation_id = curr_cid

        return session

    async def _ensure_server(self):
        """确保 WS 服务已启动"""
        async with self._server_lock:
            if self._server_started and self._ws_server:
                return

            self._ws_server = DGLabWSServer(self._ws_host, self._ws_port)
            self._ws_server.on_strength_update = self._on_strength_update
            self._ws_server.on_bindback = self._on_bind
            self._ws_server.on_disconnect = self._on_disconnect
            await self._ws_server.start()
            self._server_started = True

    async def _stop_server_if_idle(self):
        """没有活跃会话时关闭 WS 服务"""
        async with self._server_lock:
            if await self._has_active_sessions():
                return
            if self._ws_server and self._server_started:
                await self._ws_server.stop()
                self._server_started = False
                self._ws_server = None

    async def _cleanup_idle_resources(self):
        """在无活跃会话时回收工具、WS 服务与共享人格。"""
        if await self._has_active_sessions():
            return
        self._unregister_tools()
        await self._stop_server_if_idle()
        await self._delete_shared_dglab_persona_if_idle()

    async def _cleanup_idle_resources_deferred(self):
        """延后执行空闲资源回收，避免在 WS 断连回调栈中停服导致等待环。"""
        await asyncio.sleep(0)
        await self._cleanup_idle_resources()

    def _schedule_idle_cleanup(self):
        """调度一次空闲资源回收任务（去重）。"""
        if self._idle_cleanup_task and not self._idle_cleanup_task.done():
            return
        self._idle_cleanup_task = asyncio.create_task(self._cleanup_idle_resources_deferred())

    async def _on_strength_update(self, client_id: str, target_id: str, message: str):
        """APP 上报强度数据的回调"""
        for session in await self._get_sessions_snapshot():
            if session.controller and session.active:
                ctrl = session.controller
                # 协议里上报强度时的 clientId/targetId 方向在不同实现里可能互换，
                # 只要这两个 ID 与当前会话绑定对一致就更新强度。
                if {ctrl.client_id, ctrl.target_id} == {client_id, target_id}:
                    ctrl.update_strength(message)

    async def _on_bind(self, client_id: str, target_id: str):
        """绑定成功回调"""
        for session in await self._get_sessions_snapshot():
            if session.controller and session.controller.client_id == client_id:
                session.controller.set_bound(target_id)
                # 通知用户
                try:
                    chain = MessageChain().message("✅ DG-Lab APP 绑定成功！设备已连接。")
                    await self.context.send_message(session.umo, chain)
                except Exception as e:
                    logger.error(f"发送绑定成功通知失败: {e}")
                break

    async def _on_disconnect(self, disconnected_id: str):
        """断开连接回调"""
        for session in await self._get_sessions_snapshot():
            if session.controller and session.active:
                ctrl = session.controller
                if ctrl.client_id == disconnected_id or ctrl.target_id == disconnected_id:
                    # 客户端主动断连时，执行与 /dglab stop 一致的完整退出流程。
                    await self._exit_session(
                        session,
                        reason="ws_disconnect",
                        proactive_notice=True,
                        notice_text="⚠️ DG-Lab APP 已断开连接，已自动退出郊狼模式。",
                    )
                    break

    async def _cancel_session_tasks(self, session: DGLabSession):
        """取消会话后台任务（波形任务 + 一键开火恢复任务）。"""
        if session._quick_fire_restore_task and not session._quick_fire_restore_task.done():
            session._quick_fire_restore_task.cancel()
            try:
                await session._quick_fire_restore_task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.warning(f"停止会话一键开火恢复任务时出现异常: {e}")
        session._quick_fire_restore_task = None

        for task_attr in ("_wave_task_a", "_wave_task_b"):
            wave_task = getattr(session, task_attr, None)
            if wave_task and not wave_task.done():
                wave_task.cancel()
                try:
                    await wave_task
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    logger.warning(f"停止会话波形任务时出现异常: {e}")
            setattr(session, task_attr, None)

    async def _exit_session(
        self,
        session: DGLabSession,
        reason: str = "manual",
        proactive_notice: bool = False,
        notice_text: Optional[str] = None,
    ):
        """统一会话退出流程，供 ws 断连、/dglab stop 与 terminate 复用。"""
        if not session or not session.active:
            return
        if session._is_exiting:
            return

        session._is_exiting = True
        try:
            await self._cancel_session_tasks(session)

            if session.controller:
                if session.controller.is_bound:
                    try:
                        # 将强度归零
                        await session.controller.send_strength(1, 2, 0)
                        await session.controller.send_strength(2, 2, 0)
                    except Exception:
                        pass

                # ws_disconnect 场景由服务端先触发断连回调，避免再次主动断连导致重复流程。
                if reason != "ws_disconnect" and self._ws_server and session.controller.client_id:
                    await self._ws_server.disconnect_client(session.controller.client_id)

                session.controller._bound = False
                session.controller.target_id = None

            # 恢复原人格（即使原人格为默认人格 None 也要支持）
            try:
                conv_mgr = self.context.conversation_manager
                curr_cid = await conv_mgr.get_curr_conversation_id(session.umo)
                if curr_cid:
                    restore_persona_id = await self._resolve_restore_persona_id(session)
                    await conv_mgr.update_conversation(
                        unified_msg_origin=session.umo,
                        conversation_id=curr_cid,
                        persona_id=restore_persona_id,
                    )
            except Exception as e:
                logger.error(f"恢复人格失败: {e}")

            session.active = False
            await self._pop_session(session.umo)

            # 仅当没有活跃会话时回收资源。
            # ws_disconnect 回调位于 WS handler 的断连调用链中，若同步停服会产生等待环。
            if reason == "ws_disconnect":
                self._schedule_idle_cleanup()
            else:
                await self._cleanup_idle_resources()

            if proactive_notice:
                try:
                    chain = MessageChain().message(
                        notice_text or "⚠️ DG-Lab APP 已断开连接，已自动退出郊狼模式。"
                    )
                    await self.context.send_message(session.umo, chain)
                except Exception as e:
                    logger.error(f"发送主动断开退出通知失败: {e}")
        finally:
            session._is_exiting = False

    async def _get_current_conversation(self, umo: str):
        """获取当前会话对话对象"""
        conv_mgr = self.context.conversation_manager
        curr_cid = await conv_mgr.get_curr_conversation_id(umo)
        if not curr_cid:
            return None, None
        conv = await conv_mgr.get_conversation(umo, curr_cid)
        return curr_cid, conv

    def _is_persona_enabled(self) -> bool:
        return bool(self._dglab_persona_system_prompt and self._dglab_persona_system_prompt.strip())

    def _normalized_dglab_begin_dialogs(self) -> list[str]:
        """规范化 begin_dialogs 配置"""
        raw = self._dglab_persona_begin_dialogs
        if raw is None:
            return []
        if not isinstance(raw, list):
            raise ValueError("dglab_persona_begin_dialogs 必须是字符串列表")
        dialogs = [str(item).strip() for item in raw if str(item).strip()]
        if len(dialogs) % 2 != 0:
            raise ValueError("dglab_persona_begin_dialogs 必须是偶数条（user/assistant 交替）")
        return dialogs

    async def _resolve_restore_persona_id(self, session: DGLabSession) -> Optional[str]:
        """确定退出郊狼模式后要恢复到的人格 ID"""
        if session.original_persona_id is not None:
            return session.original_persona_id

        fallback = (self._dglab_default_persona_id or "").strip()
        if not fallback:
            return None

        try:
            self.context.persona_manager.get_persona(fallback)
            return fallback
        except ValueError:
            logger.warning(f"配置的默认人格不存在，回退到 None: {fallback}")
            return None

    async def _ensure_shared_dglab_persona(self) -> str:
        """确保郊狼共享人格存在并返回人格 ID"""
        if not self._is_persona_enabled():
            raise ValueError("郊狼人格提示词为空，已按配置跳过人格创建")

        persona_id = (self._dglab_persona_id or "").strip()
        if not persona_id:
            raise ValueError("郊狼人格 ID 不能为空")

        prompt = self._dglab_persona_system_prompt.strip()
        begin_dialogs = self._normalized_dglab_begin_dialogs()

        async with self._persona_lock:
            # 若已存在则更新提示词，保证与当前配置一致
            try:
                self.context.persona_manager.get_persona(persona_id)
                await self.context.persona_manager.update_persona(
                    persona_id=persona_id,
                    system_prompt=prompt,
                    begin_dialogs=begin_dialogs,
                )
            except ValueError:
                await self.context.persona_manager.create_persona(
                    persona_id=persona_id,
                    system_prompt=prompt,
                    begin_dialogs=begin_dialogs,
                )

            self._shared_dglab_persona_id = persona_id
            return persona_id

    async def _delete_shared_dglab_persona_if_idle(self):
        """没有活跃会话时删除郊狼共享人格"""
        if await self._has_active_sessions():
            return

        async with self._persona_lock:
            if await self._has_active_sessions():
                return
            if self._shared_dglab_persona_id:
                await self._delete_persona_if_exists(self._shared_dglab_persona_id)
                self._shared_dglab_persona_id = None

    async def _delete_persona_if_exists(self, persona_id: Optional[str]):
        """删除人格（不存在则忽略）"""
        if not persona_id:
            return
        persona_mgr = self.context.persona_manager
        try:
            await persona_mgr.delete_persona(persona_id)
        except ValueError:
            pass
        except Exception as e:
            logger.error(f"删除人格失败({persona_id}): {e}")

    @staticmethod
    def _safe_int(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _format_time(timestamp: int | None) -> str:
        if not timestamp:
            return "-"
        return datetime.fromtimestamp(int(timestamp)).strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _normalize_provider_multipliers(raw_value: Any) -> dict[str, float]:
        if not isinstance(raw_value, list):
            return {}

        normalized: dict[str, float] = {}

        def add_multiplier(provider_id: Any, value: Any) -> None:
            key_text = str(provider_id).strip()
            if not key_text:
                logger.warning("[DG-LAB] 忽略空倍率配置键")
                return
            try:
                ratio = float(value)
            except (TypeError, ValueError):
                logger.warning(f"忽略无效倍率配置: {key_text}={value}")
                return
            if ratio < 0:
                logger.warning(f"忽略负数倍率配置: {key_text}={value}")
                return
            normalized[key_text] = ratio

        for item in raw_value:
            if not isinstance(item, dict):
                logger.warning(f"忽略无效倍率配置项: {item}")
                continue
            add_multiplier(item.get("provider_id", ""), item.get("multiplier"))

        return normalized

    @staticmethod
    def _build_table(headers: list[str], rows: list[list[Any]]) -> str:
        string_rows = [[str(cell) for cell in row] for row in rows]
        widths = [len(header) for header in headers]
        for row in string_rows:
            for index, cell in enumerate(row):
                widths[index] = max(widths[index], len(cell))

        def format_row(values: list[str]) -> str:
            return " | ".join(
                value.ljust(widths[index]) for index, value in enumerate(values)
            )

        header_line = format_row(headers)
        separator_line = "-+-".join("-" * width for width in widths)
        body_lines = [format_row(row) for row in string_rows]
        return "\n".join([header_line, separator_line, *body_lines])

    @staticmethod
    def _parse_key_value_args(text: str, prefixes: tuple[str, ...]) -> dict[str, str]:
        remainder = text.strip()
        for prefix in prefixes:
            if remainder.startswith(prefix):
                remainder = remainder[len(prefix) :].strip()
                break

        if not remainder:
            return {}

        parsed: dict[str, str] = {}
        for part in remainder.split():
            if "=" not in part:
                raise ValueError("参数格式必须为 key=value。")
            key, value = part.split("=", 1)
            key = key.strip().lower()
            value = value.strip()
            if not key or not value:
                raise ValueError("参数格式必须为 key=value。")
            parsed[key] = value
        return parsed

    @staticmethod
    def _extract_provider_id(provider: Any) -> str:
        provider_config = getattr(provider, "provider_config", None)
        if isinstance(provider_config, dict):
            return str(provider_config.get("id", "") or "").strip()
        return ""

    def _get_billing_user_id(self, event: AstrMessageEvent) -> Optional[str]:
        message_obj = getattr(event, "message_obj", None)
        sender = getattr(message_obj, "sender", None)
        sender_id = getattr(sender, "user_id", None)
        if sender_id:
            return str(sender_id)

        event_user_id = getattr(event, "user_id", None)
        if event_user_id:
            return str(event_user_id)

        get_sender_id = getattr(event, "get_sender_id", None)
        if callable(get_sender_id):
            sender_value = get_sender_id()
            if sender_value:
                return str(sender_value)
        return None

    def _get_group_id(self, event: AstrMessageEvent) -> str:
        message_obj = getattr(event, "message_obj", None)
        group_id = getattr(message_obj, "group_id", "") or ""
        return str(group_id)

    def _get_provider_multiplier(self, event: AstrMessageEvent) -> float:
        try:
            provider = self.context.get_using_provider(umo=event.unified_msg_origin)
        except Exception as exc:
            logger.debug(f"[DG-LAB] failed to get current provider: {exc}")
            return 1.0

        provider_id = self._extract_provider_id(provider)
        if provider_id and provider_id in self._billing_provider_multipliers:
            return self._billing_provider_multipliers[provider_id]
        return 1.0

    async def _should_charge_for_event(self, event: AstrMessageEvent) -> bool:
        if not self._billing_enabled:
            return False
        if self._skip_group_chat_billing and self._get_group_id(event):
            return False
        if not self._charge_only_in_coyote_mode:
            return True

        session = await self.get_session_for_event(event.unified_msg_origin)
        return bool(session and session.active)

    async def _get_effective_user_quota(self, user_id: str) -> UserQuota:
        async with self._billing_lock:
            return self._billing_db.get_effective_user_quota(
                user_id=user_id,
                free_quota_amount=self._billing_free_quota_amount,
                refresh_hours=self._billing_free_refresh_hours,
                now_ts=int(time.time()),
            )

    def _get_next_refresh_timestamp(self, quota: UserQuota) -> int | None:
        if self._billing_free_refresh_hours <= 0:
            return None
        return quota.last_refresh + self._billing_free_refresh_hours * 3600

    def _build_quota_message(
        self,
        user_id: str,
        quota: UserQuota,
        prefix: str | None = None,
    ) -> str:
        next_refresh = self._get_next_refresh_timestamp(quota)
        lines = []
        if prefix:
            lines.append(prefix)
        lines.extend(
            [
                f"用户 ID: {user_id}",
                f"免费额度: {quota.free_balance}",
                f"发电额度: {quota.paid_balance}",
                f"总额度: {quota.total_balance}",
                f"上次刷新: {self._format_time(quota.last_refresh)}",
                f"下次刷新: {self._format_time(next_refresh) if next_refresh else '未启用'}",
            ]
        )
        return "\n".join(lines)

    def _build_insufficient_balance_message(
        self, user_id: str, quota: UserQuota
    ) -> str:
        prefix = (self._insufficient_balance_reply or "").strip() or "当前额度不足，无法继续请求。"
        return self._build_quota_message(user_id=user_id, quota=quota, prefix=prefix)

    @staticmethod
    def _extract_total_tokens(resp: LLMResponse) -> int:
        raw_completion = getattr(resp, "raw_completion", None)
        usage = getattr(raw_completion, "usage", None)
        if usage is None:
            return 0
        total_tokens = getattr(usage, "total_tokens", None)
        if total_tokens is not None:
            return max(0, int(total_tokens or 0))
        prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        return max(0, prompt_tokens + completion_tokens)

    def _extract_order_amount(self, order: dict[str, Any]) -> float:
        if "show_amount" in order and order.get("show_amount") not in (None, ""):
            return self._safe_float(order.get("show_amount"))
        return self._safe_float(order.get("total_amount"))

    def _tokens_from_amount(self, amount: float) -> int:
        return max(0, math.floor(amount * self._billing_token_per_yuan))

    @staticmethod
    def _parse_manual_order_id(order_id: str) -> tuple[str, str, str] | None:
        matched = re.match(r"^manual:([^:]+):([^:]+):(\d+)$", order_id)
        if not matched:
            return None
        return matched.group(1), matched.group(2), matched.group(3)

    async def _render_text_image(self, event: AstrMessageEvent, text: str):
        image = await self.text_to_image(text)
        return event.image_result(image)

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        del req
        user_id = self._get_billing_user_id(event)
        if not user_id:
            return
        if not await self._should_charge_for_event(event):
            return

        quota = await self._get_effective_user_quota(user_id)
        if quota.total_balance > 0:
            return

        await event.send(event.plain_result(self._build_insufficient_balance_message(user_id, quota)))
        event.stop_event()

    @filter.on_llm_response()
    async def on_llm_response(self, event: AstrMessageEvent, resp: LLMResponse):
        user_id = self._get_billing_user_id(event)
        if not user_id:
            return
        if not await self._should_charge_for_event(event):
            return

        total_tokens = self._extract_total_tokens(resp)
        multiplier = self._get_provider_multiplier(event)
        charge_amount = max(0, math.floor(total_tokens * multiplier))
        if charge_amount <= 0:
            return

        async with self._billing_lock:
            charge_result = self._billing_db.apply_usage_charge(
                user_id=user_id,
                amount=charge_amount,
                free_quota_amount=self._billing_free_quota_amount,
                refresh_hours=self._billing_free_refresh_hours,
                now_ts=int(time.time()),
            )
        logger.debug(
            f"[DG-LAB] charged user={user_id} tokens={total_tokens} "
            f"multiplier={multiplier} free={charge_result.charged_free} "
            f"paid={charge_result.charged_paid}"
        )

    @filter.command_group("dglab")
    def dglab_group(self):
        """郊狼指令组"""
        pass

    @dglab_group.command("help", alias={"帮助"})
    async def dglab_help(self, event: AstrMessageEvent):
        """查看郊狼指令帮助"""
        help_text = (
            "DG-LAB 指令帮助\n"
            "\n"
            "普通用户命令:\n"
            "/dglab help - 查看本帮助\n"
            "/dglab start - 开启郊狼模式并生成绑定二维码\n"
            "/dglab stop - 退出郊狼模式\n"
            "/dglab status - 查看当前郊狼状态\n"
            "/dglab channel A|B|AB - 设置使用通道\n"
            "/dglab part A:部位 B:部位 - 设置通道部位\n"
            "/dglab fire <强度> 或 /dglab fire A:<强度> B:<强度> - 设置一键开火临时增量\n"
            "/dglab persona - 查看当前郊狼人格配置与状态\n"
            "/dglab quota - 查看当前额度\n"
            "/dglab redeem <订单号> - 兑换爱发电订单\n"
            "/dglab wavelist - 查看当前可用波形列表\n"
            "/dglab waveinfo <波形名> - 查看指定波形详细信息\n"
            "\n"
            "管理员命令:\n"
            "/dglab quota-list [user_id=xxx] [limit=50] - 查看额度记录\n"
            "/dglab redeem-list [user_id=xxx] [order_id=xxx] [limit=50] - 查看充值记录\n"
            "/dglab recharge user_id=123 amount=6.66 - 手动为指定用户充值\n"
            "/dglab refresh-free user_id=123 - 立即刷新指定用户免费额度\n"
            "/dglab refresh-free all=true - 立即刷新全体已有额度记录用户的免费额度"
        )
        yield event.plain_result(help_text)

    @dglab_group.command("quota", alias={"额度"})
    async def dglab_quota(self, event: AstrMessageEvent):
        """查看当前用户额度"""
        user_id = self._get_billing_user_id(event)
        if not user_id:
            yield event.plain_result("未能识别当前用户 ID。")
            return

        quota = await self._get_effective_user_quota(user_id)
        yield event.plain_result(self._build_quota_message(user_id, quota))

    @dglab_group.command("redeem", alias={"兑换"})
    async def dglab_redeem(self, event: AstrMessageEvent, order_id: str = ""):
        """兑换爱发电订单"""
        order_id = (order_id or "").strip()
        if not order_id:
            yield event.plain_result("请提供订单号，例如：/dglab redeem 订单号")
            return
        if not self._afdian_client.is_configured:
            yield event.plain_result("爱发电 API 未配置完整，暂时无法兑换订单。")
            return
        if self._billing_token_per_yuan <= 0:
            yield event.plain_result("当前每元兑换 TOKEN 配置无效，无法进行订单兑换。")
            return

        user_id = self._get_billing_user_id(event)
        if not user_id:
            yield event.plain_result("未能识别当前用户 ID。")
            return

        try:
            orders = await self._afdian_client.query_order(out_trade_no=order_id)
        except RuntimeError as exc:
            yield event.plain_result(f"查询订单失败：{exc}")
            return

        target_order = None
        for order in orders:
            if str(order.get("out_trade_no", "") or "") == order_id:
                target_order = order
                break
        if target_order is None and orders:
            target_order = orders[0]
        if target_order is None:
            yield event.plain_result("未找到该订单。")
            return

        if self._safe_int(target_order.get("status"), 0) != 2:
            yield event.plain_result("该订单未支付成功，暂时不能兑换。")
            return

        amount = self._extract_order_amount(target_order)
        paid_balance = self._tokens_from_amount(amount)
        if paid_balance <= 0:
            yield event.plain_result("当前兑换比例对应的 TOKEN 数为 0，无法兑换。")
            return

        try:
            async with self._billing_lock:
                quota = self._billing_db.record_redeem(
                    order_id=order_id,
                    user_id=user_id,
                    amount=amount,
                    paid_balance=paid_balance,
                    source="afdian",
                    free_quota_amount=self._billing_free_quota_amount,
                    refresh_hours=self._billing_free_refresh_hours,
                    now_ts=int(time.time()),
                )
        except ValueError as exc:
            yield event.plain_result(str(exc))
            return

        yield event.plain_result(
            "\n".join(
                [
                    "兑换成功。",
                    f"订单号: {order_id}",
                    f"兑换金额: {amount:.2f} 元",
                    f"到账 TOKEN: {paid_balance}",
                    f"当前发电额度: {quota.paid_balance}",
                    f"当前总额度: {quota.total_balance}",
                ]
            )
        )

    @filter.permission_type(filter.PermissionType.ADMIN)
    @dglab_group.command("quota-list", alias={"额度列表"})
    async def dglab_quota_list(self, event: AstrMessageEvent):
        """管理员查看额度列表"""
        try:
            args = self._parse_key_value_args(
                event.message_str,
                prefixes=(
                    "/dglab quota-list",
                    "dglab quota-list",
                    "/dglab 额度列表",
                    "dglab 额度列表",
                ),
            )
        except ValueError as exc:
            yield event.plain_result(f"参数错误：{exc}")
            return

        user_id = args.get("user_id")
        limit = None
        if "limit" in args:
            limit = self._safe_int(args.get("limit"), -1)
            if limit <= 0:
                yield event.plain_result("limit 必须是大于 0 的整数。")
                return

        async with self._billing_lock:
            quotas = self._billing_db.list_user_quotas(user_id=user_id, limit=limit)

        if not quotas:
            yield event.plain_result("没有找到额度记录。")
            return

        rows = [
            [
                quota.user_id,
                quota.free_balance,
                quota.paid_balance,
                quota.total_balance,
                self._format_time(quota.last_refresh),
                self._format_time(self._get_next_refresh_timestamp(quota))
                if self._get_next_refresh_timestamp(quota)
                else "未启用",
            ]
            for quota in quotas
        ]
        text = "额度记录\n\n" + self._build_table(
            ["用户ID", "免费额度", "发电额度", "总额度", "上次刷新", "下次刷新"],
            rows,
        )
        yield await self._render_text_image(event, text)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @dglab_group.command("redeem-list", alias={"兑换记录"})
    async def dglab_redeem_list(self, event: AstrMessageEvent):
        """管理员查看充值记录"""
        try:
            args = self._parse_key_value_args(
                event.message_str,
                prefixes=(
                    "/dglab redeem-list",
                    "dglab redeem-list",
                    "/dglab 兑换记录",
                    "dglab 兑换记录",
                ),
            )
        except ValueError as exc:
            yield event.plain_result(f"参数错误：{exc}")
            return

        user_id = args.get("user_id")
        order_id = args.get("order_id")
        limit = None
        if "limit" in args:
            limit = self._safe_int(args.get("limit"), -1)
            if limit <= 0:
                yield event.plain_result("limit 必须是大于 0 的整数。")
                return

        async with self._billing_lock:
            orders = self._billing_db.list_redeemed_orders(
                user_id=user_id,
                order_id=order_id,
                limit=limit,
            )

        if not orders:
            yield event.plain_result("没有找到充值记录。")
            return

        rows = []
        for order in orders:
            parsed = self._parse_manual_order_id(str(order["order_id"]))
            admin_id = parsed[0] if parsed else "-"
            rows.append(
                [
                    str(order["order_id"]),
                    str(order["user_id"]),
                    f'{float(order["amount"]):.2f}',
                    int(order["paid_balance"]),
                    str(order["source"]),
                    admin_id,
                    self._format_time(int(order["redeem_time"])),
                ]
            )
        text = "充值记录\n\n" + self._build_table(
            ["订单号", "用户ID", "金额(元)", "TOKEN", "来源", "管理员ID", "兑换时间"],
            rows,
        )
        yield await self._render_text_image(event, text)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @dglab_group.command("recharge", alias={"手动充值"})
    async def dglab_recharge(self, event: AstrMessageEvent):
        """管理员手动充值"""
        try:
            args = self._parse_key_value_args(
                event.message_str,
                prefixes=(
                    "/dglab recharge",
                    "dglab recharge",
                    "/dglab 手动充值",
                    "dglab 手动充值",
                ),
            )
        except ValueError as exc:
            yield event.plain_result(f"参数错误：{exc}")
            return

        target_user_id = (args.get("user_id") or "").strip()
        if not target_user_id:
            yield event.plain_result("请提供 user_id，例如：/dglab recharge user_id=123 amount=6.66")
            return

        amount_text = args.get("amount")
        if amount_text is None:
            yield event.plain_result("请提供 amount，例如：/dglab recharge user_id=123 amount=6.66")
            return

        amount = self._safe_float(amount_text, -1)
        if amount <= 0:
            yield event.plain_result("amount 必须是大于 0 的数字。")
            return

        if self._billing_token_per_yuan <= 0:
            yield event.plain_result("当前每元兑换 TOKEN 配置无效，无法手动充值。")
            return

        paid_balance = self._tokens_from_amount(amount)
        if paid_balance <= 0:
            yield event.plain_result("当前兑换比例对应的 TOKEN 数为 0，无法充值。")
            return

        admin_id = self._get_billing_user_id(event) or "unknown"
        manual_order_id = f"manual:{admin_id}:{target_user_id}:{int(time.time() * 1000)}"

        try:
            async with self._billing_lock:
                quota = self._billing_db.record_redeem(
                    order_id=manual_order_id,
                    user_id=target_user_id,
                    amount=amount,
                    paid_balance=paid_balance,
                    source="manual_admin",
                    free_quota_amount=self._billing_free_quota_amount,
                    refresh_hours=self._billing_free_refresh_hours,
                    now_ts=int(time.time()),
                )
        except ValueError as exc:
            yield event.plain_result(str(exc))
            return

        yield event.plain_result(
            "\n".join(
                [
                    "手动充值成功。",
                    f"目标用户: {target_user_id}",
                    f"充值金额: {amount:.2f} 元",
                    f"到账 TOKEN: {paid_balance}",
                    f"记录订单号: {manual_order_id}",
                    f"当前发电额度: {quota.paid_balance}",
                    f"当前总额度: {quota.total_balance}",
                ]
            )
        )

    @filter.permission_type(filter.PermissionType.ADMIN)
    @dglab_group.command("refresh-free", alias={"刷新免费额度"})
    async def dglab_refresh_free(self, event: AstrMessageEvent):
        """管理员立即刷新免费额度"""
        try:
            args = self._parse_key_value_args(
                event.message_str,
                prefixes=(
                    "/dglab refresh-free",
                    "dglab refresh-free",
                    "/dglab 刷新免费额度",
                    "dglab 刷新免费额度",
                ),
            )
        except ValueError as exc:
            yield event.plain_result(f"参数错误：{exc}")
            return

        user_id = (args.get("user_id") or "").strip()
        all_flag = (args.get("all") or "").strip().lower()

        if all_flag and all_flag != "true":
            yield event.plain_result("参数错误：`all` 只支持 `true`。")
            return

        if user_id and all_flag == "true":
            yield event.plain_result("参数错误：`user_id` 和 `all=true` 不能同时传入。")
            return

        if user_id:
            now_ts = int(time.time())
            try:
                async with self._billing_lock:
                    quota = self._billing_db.refresh_user_free_quota(
                        user_id=user_id,
                        free_quota_amount=self._billing_free_quota_amount,
                        now_ts=now_ts,
                    )
            except ValueError as exc:
                yield event.plain_result(str(exc))
                return

            yield event.plain_result(
                "\n".join(
                    [
                        "免费额度刷新成功。",
                        f"目标用户: {quota.user_id}",
                        f"免费额度: {quota.free_balance}",
                        f"发电额度: {quota.paid_balance}",
                        f"总额度: {quota.total_balance}",
                        f"刷新时间: {self._format_time(quota.last_refresh)}",
                    ]
                )
            )
            return

        if not all_flag:
            yield event.plain_result("参数错误：请提供 `user_id=xxx` 或 `all=true`。")
            return

        now_ts = int(time.time())
        async with self._billing_lock:
            refreshed_count = self._billing_db.refresh_all_users_free_quota(
                free_quota_amount=self._billing_free_quota_amount,
                now_ts=now_ts,
            )

        if refreshed_count <= 0:
            yield event.plain_result("没有可刷新的额度记录。")
            return

        yield event.plain_result(
            "\n".join(
                [
                    "全体免费额度刷新成功。",
                    f"刷新人数: {refreshed_count}",
                    f"免费额度: {self._billing_free_quota_amount}",
                    f"刷新时间: {self._format_time(now_ts)}",
                ]
            )
        )

    @dglab_group.command("wavelist", alias={"波形列表"})
    async def dglab_wavelist(self, event: AstrMessageEvent):
        """查看当前可用波形（内置 + 用户上传）。"""
        wave_names = get_wave_names()
        if not wave_names:
            yield event.plain_result("当前没有可用波形。")
            return

        msg = (
            f"当前可用波形共 {len(wave_names)} 个（其中用户上传 {self._uploaded_wave_count} 个）：\n"
            f"{get_wave_descriptions()}"
        )
        yield event.plain_result(msg)

    @dglab_group.command("waveinfo", alias={"波形信息"})
    async def dglab_waveinfo(self, event: AstrMessageEvent, wave_name: str = ""):
        """查看指定波形详情。用法: /dglab waveinfo 波形名"""
        target = (wave_name or "").strip()
        if not target:
            yield event.plain_result("请提供波形名。用法: /dglab waveinfo 波形名")
            return

        data = get_wave_data(target)
        if not data:
            yield event.plain_result(
                f"未找到波形: {target}\n可用波形:\n{', '.join(get_wave_names())}"
            )
            return

        first_frame = data[0] if data else ""
        last_frame = data[-1] if data else ""
        msg = (
            f"波形: {target}\n"
            f"帧数: {len(data)}\n"
            f"总时长: {len(data) * 100}ms\n"
            f"首帧: {first_frame}\n"
            f"末帧: {last_frame}"
        )
        yield event.plain_result(msg)

    @dglab_group.command("fire")
    async def dglab_set_quick_fire_boost(self, event: AstrMessageEvent):
        """设置一键开火的临时增量。用法: /dglab fire 10 或 /dglab fire A:8 B:12"""
        umo = event.unified_msg_origin
        session = await self._get_session(umo)

        if not session or not session.active:
            yield event.plain_result("请先使用 /dglab start 开启郊狼模式后再设置一键开火强度。")
            return

        text = event.message_str.strip()
        for prefix in ("/dglab fire", "dglab fire"):
            if text.startswith(prefix):
                text = text[len(prefix):].strip()
                break

        # 空参数时重置为默认值
        if not text:
            session.quick_fire_boost_a = 1
            session.quick_fire_boost_b = 1
            yield event.plain_result("一键开火增量已重置为默认值: A=1, B=1")
            return

        # 支持格式:
        # 1) /dglab fire 10               -> AB 都设为 10
        # 2) /dglab fire A:8 B:12         -> 分通道设置
        # 3) /dglab fire A 8 B 12         -> 分通道设置
        parts = text.replace("：", ":").split()

        def _clamp_boost(v: int) -> int:
            return max(1, min(30, v))

        try:
            if len(parts) == 1 and ":" not in parts[0]:
                value = _clamp_boost(int(parts[0]))
                session.quick_fire_boost_a = value
                session.quick_fire_boost_b = value
            else:
                idx = 0
                set_a = False
                set_b = False
                while idx < len(parts):
                    token = parts[idx]
                    if ":" in token:
                        ch, raw = token.split(":", 1)
                        ch = ch.strip().upper()
                        value = _clamp_boost(int(raw.strip()))
                        if ch == "A":
                            session.quick_fire_boost_a = value
                            set_a = True
                        elif ch == "B":
                            session.quick_fire_boost_b = value
                            set_b = True
                        else:
                            raise ValueError("通道只能是 A 或 B")
                        idx += 1
                        continue

                    ch = token.strip().upper()
                    if ch not in ("A", "B") or idx + 1 >= len(parts):
                        raise ValueError("参数格式错误")
                    value = _clamp_boost(int(parts[idx + 1]))
                    if ch == "A":
                        session.quick_fire_boost_a = value
                        set_a = True
                    else:
                        session.quick_fire_boost_b = value
                        set_b = True
                    idx += 2

                if not set_a and not set_b:
                    raise ValueError("未解析到有效通道参数")
        except ValueError:
            yield event.plain_result(
                "格式错误。请使用: /dglab fire 10 或 /dglab fire A:8 B:12 (范围 1-30)。"
            )
            return

        yield event.plain_result(
            f"一键开火增量已设置: A={session.quick_fire_boost_a}, B={session.quick_fire_boost_b}"
        )

    @dglab_group.command("persona", alias={"人格"})
    async def dglab_persona(self, event: AstrMessageEvent):
        """查看郊狼人格配置与当前状态"""
        umo = event.unified_msg_origin
        session = await self._get_session(umo)

        configured_id = (self._dglab_persona_id or "").strip() or "(未配置)"
        enabled = "是" if self._is_persona_enabled() else "否"
        begin_dialogs_count = len(self._normalized_dglab_begin_dialogs()) if self._is_persona_enabled() else 0
        default_restore = (self._dglab_default_persona_id or "").strip() or "(系统默认)"
        shared_id = self._shared_dglab_persona_id or "(未创建)"

        if session and session.active:
            current_session_persona = session.dglab_persona_id or "(未切换)"
            original_persona = session.original_persona_id if session.original_persona_id is not None else "(系统默认)"
        else:
            current_session_persona = "(当前会话未开启郊狼模式)"
            original_persona = "(未知)"

        msg = (
            "郊狼人格配置:\n"
            f"- 启用状态: {enabled}\n"
            f"- 人格ID: {configured_id}\n"
            f"- 共享人格当前ID: {shared_id}\n"
            f"- 预设对话条数: {begin_dialogs_count}\n"
            f"- 退出默认恢复人格: {default_restore}\n"
            f"- 当前会话郊狼人格: {current_session_persona}\n"
            f"- 进入前原人格: {original_persona}"
        )
        yield event.plain_result(msg)

    @dglab_group.command("start", alias={"开启"})
    async def dglab_start(self, event: AstrMessageEvent):
        """开启郊狼模式，生成二维码供 APP 扫描绑定"""
        umo = event.unified_msg_origin

        existing_session = await self._get_session(umo)
        if existing_session and existing_session.active:
            yield event.plain_result("郊狼模式已经开启，请先使用 /dglab stop 退出后再重新开启。")
            return

        try:
            await self._ensure_server()
        except OSError as e:
            logger.error(f"WS 服务启动失败，可能端口冲突: {e}")
            yield event.plain_result(f"郊狼模式开启失败：WS 服务启动失败，端口 {self._ws_port} 可能被占用。")
            return
        except Exception as e:
            logger.error(f"WS 服务启动失败: {e}")
            yield event.plain_result(f"郊狼模式开启失败：{str(e)}")
            return

        session = DGLabSession(umo)
        session.ws_server = self._ws_server
        session.active = True
        qr_path = None
        controller = None
        switched_persona = False
        curr_cid = None

        try:
            # 创建控制器
            controller = DGLabController(self._ws_server)
            await controller.connect_as_client()
            # 将控制器的 client_id 注册到 ws_server 的 clients 列表中（不通过真正的 ws 连接）
            # 这里我们不需要为控制器创建实际的 ws 连接，因为控制器直接通过 ws_server 发送
            session.controller = controller

            # 生成二维码
            qr_url = controller.get_qrcode_url(self._ws_external_host, self._ws_port)

            try:
                import qrcode
                qr = qrcode.QRCode(version=1, box_size=10, border=5)
                qr.add_data(qr_url)
                qr.make(fit=True)
                img = qr.make_image(fill_color="black", back_color="white")
                img_bytes = io.BytesIO()
                img.save(img_bytes, format="PNG")
                img_bytes.seek(0)

                # 保存为临时文件
                tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
                tmp.write(img_bytes.getvalue())
                tmp.close()
                qr_path = tmp.name
            except ImportError:
                qr_path = None
                logger.warning("qrcode 库未安装，无法生成二维码图片")

            # 保存原人格并按配置创建/切换郊狼人格。
            # 配置为空时按 _conf_schema 约定不切换人格。
            curr_cid, conv = await self._get_current_conversation(umo)
            if not curr_cid:
                conv_mgr = self.context.conversation_manager
                curr_cid = await conv_mgr.new_conversation(unified_msg_origin=umo)
                conv = await conv_mgr.get_conversation(umo, curr_cid)

            session.bound_conversation_id = curr_cid
            if conv:
                session.original_persona_id = conv.persona_id

            if self._is_persona_enabled():
                dglab_persona_id = await self._ensure_shared_dglab_persona()
                session.dglab_persona_id = dglab_persona_id

                if curr_cid:
                    conv_mgr = self.context.conversation_manager
                    await conv_mgr.update_conversation(
                        unified_msg_origin=umo,
                        conversation_id=curr_cid,
                        persona_id=dglab_persona_id,
                    )
                    switched_persona = True

            # 注册 LLM Tools
            self._register_tools(session)

            await self._set_session(umo, session)

        except Exception as e:
            logger.error(f"郊狼模式开启流程失败: {e}")

            # 若已切换到郊狼人格，失败时回滚到进入前人格，避免人格残留。
            if switched_persona and curr_cid:
                try:
                    conv_mgr = self.context.conversation_manager
                    await conv_mgr.update_conversation(
                        unified_msg_origin=umo,
                        conversation_id=curr_cid,
                        persona_id=session.original_persona_id,
                    )
                except Exception as restore_err:
                    logger.error(f"郊狼模式失败时回滚人格失败: {restore_err}")

            # 回滚控制器注册，避免残留虚拟客户端
            if self._ws_server and controller and controller.client_id:
                self._ws_server.clients.pop(controller.client_id, None)
                self._ws_server.relations.pop(controller.client_id, None)
            session.active = False

            # 若没有其他活跃会话，回收服务与共享人格
            if not await self._has_active_sessions():
                self._unregister_tools()
                await self._stop_server_if_idle()
                await self._delete_shared_dglab_persona_if_idle()

            if qr_path:
                try:
                    os.unlink(qr_path)
                except Exception:
                    pass

            custom_error = (self._dglab_persona_error_reply or "").strip()
            if custom_error:
                yield event.plain_result(f"{custom_error}\n错误详情：{str(e)}")
            else:
                yield event.plain_result(f"郊狼模式开启失败：{str(e)}")
            return

        # 发送二维码
        qr_link_text = f"\n🔗 绑定链接:\n{qr_url}" if self._send_qr_raw_url else ""
        tips_text = (
            "\n\n操作提示:\n"
            "1. 打开 DG-Lab APP，使用扫码功能绑定设备。\n"
            "2. 若扫码无反应，请检查 外部访问地址(IP) 与 WebSocket 服务端口 是否可被手机访问。\n"
            "3. 可使用 /dglab channel A|B|AB 设置通道，/dglab part A:部位 B:部位 设置部位。\n"
            "4. 可使用 /dglab fire 10 或 /dglab fire A:8 B:12 设置一键开火临时增量。\n"
            "5. 使用 /dglab status 查看状态，使用 /dglab stop 退出并归零强度。\n"
            "6. 不清楚如何操作可发送 /dglab help 查看完整指令说明。\n"
            "7. 绑定成功后，大模型可自主控制波形与强度。"
        )

        if qr_path:
            chain = [
                Comp.Plain("🐺 郊狼模式已开启！请使用 DG-Lab APP 扫描下方二维码完成绑定。\n"),
                Comp.Image.fromFileSystem(qr_path),
                Comp.Plain(f"{qr_link_text}{tips_text}"),
            ]
            yield event.chain_result(chain)
            # 清理临时文件
            try:
                os.unlink(qr_path)
            except Exception:
                pass
        else:
            fallback_header = (
                "二维码图片生成失败，请检查运行环境是否已安装 qrcode 依赖后重试或复制以下链接到任意二维码生成器生成二维码扫码完成绑定："
                if self._send_qr_raw_url
                else "二维码图片生成失败，请检查运行环境是否已安装 qrcode 依赖后重试。"
            )
            yield event.plain_result(
                "🐺 郊狼模式已开启！\n\n"
                f"{fallback_header}"
                f"{qr_link_text if self._send_qr_raw_url else ''}{tips_text}"
            )

    @dglab_group.command("stop", alias={"退出", "关闭"})
    async def dglab_stop(self, event: AstrMessageEvent):
        """退出郊狼模式"""
        umo = event.unified_msg_origin
        session = await self._get_session(umo)

        if not session or not session.active:
            yield event.plain_result("当前未开启郊狼模式。")
            return

        await self._exit_session(session, reason="manual", proactive_notice=False)

        yield event.plain_result("🐺 郊狼模式已关闭，设备强度已归零。")

    @dglab_group.command("channel")
    async def dglab_channel(self, event: AstrMessageEvent, channel: str = "AB"):
        """设置使用的通道。参数: A / B / AB"""
        umo = event.unified_msg_origin
        session = await self._get_session(umo)

        if not session or not session.active:
            yield event.plain_result("请先使用 /dglab 开启郊狼模式。")
            return

        channel = channel.upper().strip()
        if channel not in ("A", "B", "AB"):
            yield event.plain_result("通道参数无效，请输入 A、B 或 AB。")
            return

        session.channel_config = channel
        yield event.plain_result(f"已设置使用通道: {channel}")

    @dglab_group.command("part")
    async def dglab_part(self, event: AstrMessageEvent):
        """设置通道连接的部位。格式: /dglab part A:大腿 B:手臂"""
        umo = event.unified_msg_origin
        session = await self._get_session(umo)

        if not session or not session.active:
            yield event.plain_result("请先使用 /dglab 开启郊狼模式。")
            return

        # 从 message_str 中去掉命令部分
        text = event.message_str.strip()
        for prefix in ("/dglab part", "dglab part", "/dglab_part", "dglab_part"):
            if text.startswith(prefix):
                text = text[len(prefix):].strip()
                break
        # 解析 A:xxx B:xxx 格式
        parts = text.split()
        for part in parts:
            if ":" in part or "：" in part:
                sep = ":" if ":" in part else "："
                key, value = part.split(sep, 1)
                key = key.upper().strip()
                value = value.strip()
                if key == "A":
                    session.channel_a_part = value
                elif key == "B":
                    session.channel_b_part = value

        result = "部位设置已更新：\n"
        if session.channel_a_part:
            result += f"A通道: {session.channel_a_part}\n"
        if session.channel_b_part:
            result += f"B通道: {session.channel_b_part}\n"
        if not session.channel_a_part and not session.channel_b_part:
            result += "未设置任何部位。格式: /dglab part A:大腿 B:手臂"

        yield event.plain_result(result)

    @dglab_group.command("status", alias={"状态"})
    async def dglab_status(self, event: AstrMessageEvent):
        """查看郊狼模式状态"""
        umo = event.unified_msg_origin
        session = await self._get_session(umo)

        if not session or not session.active:
            yield event.plain_result("郊狼模式未开启。使用 /dglab 开启。")
            return

        yield event.plain_result(f"🐺 郊狼状态: {session.get_status_desc()}")

    def _register_tools(self, session: DGLabSession):
        """注册 LLM Tools"""
        if self._dglab_tools_registered:
            return

        from .dg_tools import create_dglab_tools
        tools = create_dglab_tools(self)
        self._current_tools = tools
        self.context.add_llm_tools(*tools)
        self._dglab_tools_registered = True

    def _unregister_tools(self):
        """取消注册 LLM Tools"""
        if not self._dglab_tools_registered:
            return
        if hasattr(self, "_current_tools"):
            for tool in self._current_tools:
                try:
                    self.context.provider_manager.llm_tools.func_list.remove(tool)
                except (ValueError, AttributeError):
                    pass
            self._current_tools = []
        self._dglab_tools_registered = False

    async def get_session_for_event(self, event_umo: str) -> Optional[DGLabSession]:
        """获取事件对应的郊狼会话"""
        session = await self._get_session(event_umo)
        if session and session.active:
            return session
        return None

    async def terminate(self):
        """插件销毁"""
        # 关闭所有 session
        for session in await self._get_sessions_snapshot():
            is_bound = bool(session.controller and session.controller.is_bound)
            notice_text = (
                "⚠️ 插件正在卸载，已自动退出郊狼模式并断开设备连接。"
                if is_bound
                else None
            )
            await self._exit_session(
                session,
                reason="terminate",
                proactive_notice=is_bound,
                notice_text=notice_text,
            )
        async with self._sessions_lock:
            self._sessions.clear()

        # 取消注册 tools
        self._unregister_tools()

        # 关闭 WS 服务
        await self._stop_server_if_idle()
        await self._delete_shared_dglab_persona_if_idle()
        await self._afdian_client.close()
