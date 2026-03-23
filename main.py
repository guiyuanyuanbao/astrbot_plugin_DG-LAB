import asyncio
import io
import os
import tempfile
from typing import Optional

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger, AstrBotConfig
from astrbot.api.event import MessageChain
import astrbot.api.message_components as Comp

from .dg_server import DGLabWSServer, DGLabController


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


@register("astrbot_plugin_DG-LAB", "桂鸢", "DG-Lab 郊狼控制器插件：通过大模型对话控制郊狼脉冲主机", "1.0.6")
class DGLabPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self._ws_host: str = config.get("ws_host", "0.0.0.0")
        self._ws_port: int = config.get("ws_port", 5555)
        self._ws_external_host: str = config.get("ws_external_host", "127.0.0.1")
        self._send_qr_raw_url: bool = bool(config.get("send_qr_raw_url", True))
        self._max_strength_a: int = config.get("max_strength_a", 100)
        self._max_strength_b: int = config.get("max_strength_b", 100)
        # 郊狼人格配置
        self._dglab_persona_id: str = config.get("dglab_persona_id", "dglab_persona_shared")
        self._dglab_persona_system_prompt: str = config.get("dglab_persona_system_prompt", "")
        self._dglab_persona_begin_dialogs = config.get("dglab_persona_begin_dialogs", [])
        self._dglab_persona_error_reply: str = config.get(
            "dglab_persona_error_reply",
            "郊狼模式开启失败：无法创建并切换郊狼人格，请检查人格配置。",
        )
        self._dglab_default_persona_id: str = config.get("dglab_default_persona_id", "")
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

    async def initialize(self):
        """插件初始化（WS 服务按郊狼模式启停）"""
        return

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
                    session.controller._bound = False
                    session.controller.target_id = None
                    try:
                        chain = MessageChain().message("⚠️ DG-Lab APP 已断开连接。")
                        await self.context.send_message(session.umo, chain)
                    except Exception as e:
                        logger.error(f"发送断开通知失败: {e}")
                    break

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

    @filter.command_group("dglab")
    def dglab_group(self):
        """郊狼指令组"""
        pass

    @dglab_group.command("help", alias={"帮助"})
    async def dglab_help(self, event: AstrMessageEvent):
        """查看郊狼指令帮助"""
        help_text = (
            "郊狼指令组用法:\n"
            "/dglab start - 开启郊狼模式并生成绑定二维码\n"
            "/dglab stop - 退出郊狼模式\n"
            "/dglab status - 查看当前郊狼状态\n"
            "/dglab channel A|B|AB - 设置使用通道\n"
            "/dglab part A:部位 B:部位 - 设置通道部位\n"
            "/dglab fire [强度] 或 /dglab fire A:强度 B:强度 - 设置一键开火临时增量(默认1, 最大30)\n"
            "/dglab persona - 查看当前郊狼人格配置与状态\n"
            "/dglab help - 查看本帮助"
        )
        yield event.plain_result(help_text)

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

        # 断开控制器连接
        if session.controller:
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

            if session.controller.is_bound:
                try:
                    # 将强度归零
                    await session.controller.send_strength(1, 2, 0)
                    await session.controller.send_strength(2, 2, 0)
                except Exception:
                    pass
            # 无论是否绑定都移除虚拟客户端和关系，避免泄漏
            if self._ws_server and session.controller.client_id:
                self._ws_server.clients.pop(session.controller.client_id, None)
                self._ws_server.relations.pop(session.controller.client_id, None)

        # 恢复原人格（即使原人格为默认人格 None 也要支持）
        try:
            conv_mgr = self.context.conversation_manager
            curr_cid = await conv_mgr.get_curr_conversation_id(umo)
            if curr_cid:
                restore_persona_id = await self._resolve_restore_persona_id(session)
                await conv_mgr.update_conversation(
                    unified_msg_origin=umo,
                    conversation_id=curr_cid,
                    persona_id=restore_persona_id,
                )
        except Exception as e:
            logger.error(f"恢复人格失败: {e}")

        session.active = False
        await self._pop_session(umo)

        # 仅当没有活跃会话时卸载工具、停服并删除共享人格
        if not await self._has_active_sessions():
            self._unregister_tools()
            await self._stop_server_if_idle()
            await self._delete_shared_dglab_persona_if_idle()

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
            if session._quick_fire_restore_task and not session._quick_fire_restore_task.done():
                session._quick_fire_restore_task.cancel()
                try:
                    await session._quick_fire_restore_task
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    logger.warning(f"terminate 时停止一键开火恢复任务异常: {e}")
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
                        logger.warning(f"terminate 时停止波形任务异常: {e}")
                setattr(session, task_attr, None)

            if session.active and session.controller and session.controller.is_bound:
                try:
                    await session.controller.send_strength(1, 2, 0)
                    await session.controller.send_strength(2, 2, 0)
                except Exception:
                    pass
            session.active = False
        async with self._sessions_lock:
            self._sessions.clear()

        # 取消注册 tools
        self._unregister_tools()

        # 关闭 WS 服务
        await self._stop_server_if_idle()
        await self._delete_shared_dglab_persona_if_idle()
