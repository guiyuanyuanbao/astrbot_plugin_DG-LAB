"""DG-Lab WebSocket 服务端 (APP 收信协议)

仅实现 APP 收信协议，不包含前端协议。
作为第三方终端，与 APP 通过 WebSocket 服务进行通信。
"""

import asyncio
import json
import uuid
import logging
from typing import Optional, Callable, Awaitable, Union

import websockets

try:
    from websockets.asyncio.server import ServerConnection
except ImportError:
    from websockets.legacy.server import WebSocketServerProtocol as ServerConnection

logger = logging.getLogger("astrbot")

# 标记内部虚拟客户端的哨兵对象
_VIRTUAL_CLIENT = object()


class DGLabWSServer:
    """DG-Lab WebSocket 中继服务端

    负责：
    1. 为连接的第三方终端（本插件）和 APP 分配 ID
    2. 处理 ID 关系绑定
    3. 在绑定关系后转发消息

    内部控制器作为"虚拟客户端"注册在 clients 中，
    bind 检查时视为有效客户端，但不参与心跳和消息发送。
    """

    def __init__(self, host: str = "0.0.0.0", port: int = 5555):
        self.host = host
        self.port = port
        # clientId -> websocket 或 _VIRTUAL_CLIENT
        self.clients: dict[str, Union[ServerConnection, object]] = {}
        # clientId(第三方终端) -> targetId(APP)
        self.relations: dict[str, str] = {}
        self._server = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        # 回调: 当收到强度数据时通知插件
        self.on_strength_update: Optional[Callable[[str, str, str], Awaitable[None]]] = None
        # 回调: 当收到反馈数据时通知插件
        self.on_feedback: Optional[Callable[[str, str, str], Awaitable[None]]] = None
        # 回调: 当绑定成功时通知插件
        self.on_bindback: Optional[Callable[[str, str], Awaitable[None]]] = None
        # 回调: 当连接断开时通知插件
        self.on_disconnect: Optional[Callable[[str], Awaitable[None]]] = None

    async def start(self):
        self._server = await websockets.serve(
            self._handler,
            self.host,
            self.port,
            ping_interval=30,
            ping_timeout=10,
        )
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        logger.info(f"DG-Lab WS 服务启动于 ws://{self.host}:{self.port}")

    async def stop(self):
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        # 关闭所有真实客户端连接
        for ws in list(self.clients.values()):
            if ws is not _VIRTUAL_CLIENT:
                try:
                    await ws.close()
                except Exception:
                    pass
        self.clients.clear()
        self.relations.clear()
        logger.info("DG-Lab WS 服务已停止")

    def get_client_id_for_ws(self, ws: ServerConnection) -> Optional[str]:
        for cid, w in self.clients.items():
            if w is ws:
                return cid
        return None

    def get_target_id_for_client(self, client_id: str) -> Optional[str]:
        return self.relations.get(client_id)

    def get_client_id_for_target(self, target_id: str) -> Optional[str]:
        for cid, tid in self.relations.items():
            if tid == target_id:
                return cid
        return None

    async def _handler(self, ws: ServerConnection):
        client_id = str(uuid.uuid4())
        self.clients[client_id] = ws
        logger.info(f"DG-Lab WS: 新连接 {client_id}")

        # 返回 ID 给客户端
        await ws.send(json.dumps({
            "type": "bind",
            "clientId": client_id,
            "targetId": "",
            "message": "targetId"
        }))

        try:
            async for raw_message in ws:
                await self._process_message(ws, client_id, raw_message)
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            await self._handle_disconnect(client_id)

    async def _process_message(self, ws: ServerConnection, sender_id: str, raw_message: str):
        try:
            data = json.loads(raw_message)
        except json.JSONDecodeError:
            await ws.send(json.dumps({
                "type": "msg", "clientId": "", "targetId": "", "message": "403"
            }))
            return

        msg_type = data.get("type")
        client_id = data.get("clientId", "")
        target_id = data.get("targetId", "")
        message = data.get("message", "")

        if not msg_type or not client_id or not message:
            return

        if msg_type == "bind" and message == "DGLAB":
            # APP 发起绑定请求
            await self._handle_bind(ws, client_id, target_id)
        elif msg_type == "msg":
            # 消息转发
            await self._forward_message(ws, client_id, target_id, message)

    async def _handle_bind(self, ws: ServerConnection, client_id: str, target_id: str):
        if client_id not in self.clients or target_id not in self.clients:
            await ws.send(json.dumps({
                "type": "bind", "clientId": client_id,
                "targetId": target_id, "message": "401"
            }))
            return

        # 检查是否已被绑定
        all_bound_ids = set(self.relations.keys()) | set(self.relations.values())
        if client_id in all_bound_ids or target_id in all_bound_ids:
            await ws.send(json.dumps({
                "type": "bind", "clientId": client_id,
                "targetId": target_id, "message": "400"
            }))
            return

        # 绑定
        self.relations[client_id] = target_id
        bind_result = {
            "type": "bind", "clientId": client_id,
            "targetId": target_id, "message": "200"
        }
        result_json = json.dumps(bind_result)
        await ws.send(result_json)
        # 如果另一端是真实 ws 连接，也通知它
        client_ws = self.clients.get(client_id)
        if client_ws and client_ws is not ws and client_ws is not _VIRTUAL_CLIENT:
            await client_ws.send(result_json)

        logger.info(f"DG-Lab WS: 绑定成功 {client_id} <-> {target_id}")

        if self.on_bindback:
            await self.on_bindback(client_id, target_id)

    async def _send_to_client(self, client_id: str, data: dict):
        """向客户端发送数据，虚拟客户端跳过实际发送"""
        ws = self.clients.get(client_id)
        if ws and ws is not _VIRTUAL_CLIENT:
            await ws.send(json.dumps(data))

    async def _forward_message(self, ws: ServerConnection, client_id: str, target_id: str, message: str):
        # 检查绑定关系
        if self.relations.get(client_id) != target_id:
            # 反向检查: APP 发给第三方终端
            reverse_client = self.get_client_id_for_target(target_id)
            if not reverse_client or self.relations.get(reverse_client) != client_id:
                # 再检查 targetId -> clientId（APP 作为 targetId）
                found = False
                for cid, tid in self.relations.items():
                    if (cid == client_id and tid == target_id) or \
                       (cid == target_id and tid == client_id):
                        found = True
                        break
                if not found:
                    await ws.send(json.dumps({
                        "type": "msg", "clientId": client_id,
                        "targetId": target_id, "message": "402"
                    }))
                    return

        # 判断消息类型并转发
        data = {"type": "msg", "clientId": client_id, "targetId": target_id, "message": message}
        if message.startswith("strength-"):
            parts = message.replace("strength-", "").split("+")
            if len(parts) == 4:
                # APP 上报强度数据，转发给第三方终端
                recv_id = self._find_receiver(client_id, target_id)
                if recv_id:
                    await self._send_to_client(recv_id, data)
                if self.on_strength_update:
                    await self.on_strength_update(client_id, target_id, message)
            elif len(parts) == 3:
                # 第三方终端下发强度指令，转发给 APP
                recv_id = self._find_receiver(client_id, target_id)
                if recv_id:
                    await self._send_to_client(recv_id, data)
        elif message.startswith("pulse-") or message.startswith("clear-"):
            recv_id = self._find_receiver(client_id, target_id)
            if recv_id:
                await self._send_to_client(recv_id, data)
        elif message.startswith("feedback-"):
            recv_id = self._find_receiver(client_id, target_id)
            if recv_id:
                await self._send_to_client(recv_id, data)
            if self.on_feedback:
                await self.on_feedback(client_id, target_id, message)
        else:
            recv_id = self._find_receiver(client_id, target_id)
            if recv_id:
                await self._send_to_client(recv_id, data)

    def _find_receiver(self, client_id: str, target_id: str) -> Optional[str]:
        """找到消息的接收方 ID"""
        # 如果发送者是第三方终端 (client_id 在 relations 的 key 中)，接收者是 APP (target_id)
        if client_id in self.relations:
            return target_id
        # 如果发送者是 APP (client_id 在 relations 的 value 中)，接收者是第三方终端
        for cid, tid in self.relations.items():
            if tid == client_id:
                return cid
        # fallback: 直接用 target_id
        return target_id

    async def _handle_disconnect(self, disconnected_id: str):
        logger.info(f"DG-Lab WS: 断开连接 {disconnected_id}")

        # 通知对方
        to_remove = []
        for cid, tid in list(self.relations.items()):
            if cid == disconnected_id or tid == disconnected_id:
                other_id = tid if cid == disconnected_id else cid
                other_ws = self.clients.get(other_id)
                if other_ws and other_ws is not _VIRTUAL_CLIENT:
                    try:
                        await other_ws.send(json.dumps({
                            "type": "break",
                            "clientId": cid,
                            "targetId": tid,
                            "message": "209"
                        }))
                        await other_ws.close()
                    except Exception:
                        pass
                self.clients.pop(other_id, None)
                to_remove.append(cid)

        for cid in to_remove:
            self.relations.pop(cid, None)

        self.clients.pop(disconnected_id, None)

        if self.on_disconnect:
            await self.on_disconnect(disconnected_id)

    async def _heartbeat_loop(self):
        while True:
            await asyncio.sleep(60)
            for cid, ws in list(self.clients.items()):
                if ws is _VIRTUAL_CLIENT:
                    continue
                try:
                    heartbeat = {
                        "type": "heartbeat",
                        "clientId": cid,
                        "targetId": self.relations.get(cid, ""),
                        "message": "200"
                    }
                    await ws.send(json.dumps(heartbeat))
                except Exception:
                    pass


class DGLabController:
    """DG-Lab 控制器

    作为第三方终端连接到 WS 服务，提供控制 API
    """

    def __init__(self, ws_server: DGLabWSServer):
        self.ws_server = ws_server
        self.client_id: Optional[str] = None  # 第三方终端 ID
        self.target_id: Optional[str] = None  # APP ID
        self._ws: Optional[ServerConnection] = None
        self._bound = False
        # 当前设备强度信息
        self.strength_a: int = 0
        self.strength_b: int = 0
        self.strength_a_limit: int = 0
        self.strength_b_limit: int = 0

    @property
    def is_bound(self) -> bool:
        return self._bound and self.client_id is not None and self.target_id is not None

    async def connect_as_client(self):
        """作为内部虚拟第三方终端注册到服务端"""
        self.client_id = str(uuid.uuid4())
        # 注册为虚拟客户端，使得 bind 检查时能找到此 ID
        self.ws_server.clients[self.client_id] = _VIRTUAL_CLIENT
        logger.info(f"DG-Lab 控制器注册为虚拟客户端，clientId: {self.client_id}")

    def get_qrcode_url(self, ws_host: str, ws_port: int) -> str:
        """生成二维码内容

        格式: https://www.dungeon-lab.com/app-download.php#DGLAB-SOCKET#ws://host:port/clientId
        """
        return f"https://www.dungeon-lab.com/app-download.php#DGLAB-SOCKET#ws://{ws_host}:{ws_port}/{self.client_id}"

    def set_bound(self, target_id: str):
        """设置绑定关系"""
        self.target_id = target_id
        self._bound = True
        logger.info(f"DG-Lab 控制器绑定成功: {self.client_id} <-> {self.target_id}")

    def update_strength(self, message: str):
        """解析 APP 上报的强度数据"""
        # strength-A通道强度+B通道强度+A强度上限+B强度上限
        try:
            parts = message.replace("strength-", "").split("+")
            if len(parts) == 4:
                self.strength_a = int(parts[0])
                self.strength_b = int(parts[1])
                self.strength_a_limit = int(parts[2])
                self.strength_b_limit = int(parts[3])
        except (ValueError, IndexError):
            pass

    async def send_strength(self, channel: int, mode: int, value: int):
        """发送强度操作指令

        Args:
            channel: 1=A通道, 2=B通道
            mode: 0=减少, 1=增加, 2=指定值
            value: 强度值 (0-200)
        """
        if not self.is_bound:
            raise RuntimeError("设备未绑定")

        value = max(0, min(200, value))
        msg = f"strength-{channel}+{mode}+{value}"
        await self._send_to_app(msg)

    async def send_wave(self, channel: str, wave_data: list[str]):
        """发送波形数据

        Args:
            channel: "A" 或 "B"
            wave_data: 波形 HEX 数据列表，每条 8 字节 HEX
        """
        if not self.is_bound:
            raise RuntimeError("设备未绑定")

        if len(wave_data) > 100:
            wave_data = wave_data[:100]

        wave_json = json.dumps(wave_data)
        msg = f"pulse-{channel}:{wave_json}"
        await self._send_to_app(msg)

    async def clear_wave_queue(self, channel: int):
        """清空波形队列

        Args:
            channel: 1=A通道, 2=B通道
        """
        if not self.is_bound:
            raise RuntimeError("设备未绑定")

        msg = f"clear-{channel}"
        await self._send_to_app(msg)

    async def _send_to_app(self, message: str):
        """发送消息给 APP"""
        if not self.target_id:
            raise RuntimeError("没有绑定的 APP")

        app_ws = self.ws_server.clients.get(self.target_id)
        if not app_ws or app_ws is _VIRTUAL_CLIENT:
            raise RuntimeError("APP 未连接")

        data = {
            "type": "msg",
            "clientId": self.client_id,
            "targetId": self.target_id,
            "message": message
        }
        json_str = json.dumps(data)
        if len(json_str) > 1950:
            raise RuntimeError("消息长度超出限制 (1950)")
        await app_ws.send(json_str)
