from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any

import aiohttp

from astrbot.api import logger


@dataclass(slots=True)
class AfdianConfig:
    base_url: str
    user_id: str
    token: str

    @property
    def is_configured(self) -> bool:
        return bool(self.base_url.strip() and self.user_id.strip() and self.token.strip())


class AfdianAPIClient:
    def __init__(self, config: AfdianConfig):
        self._config = config
        self._session: aiohttp.ClientSession | None = None

    @property
    def is_configured(self) -> bool:
        return self._config.is_configured

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    async def query_order(
        self, out_trade_no: str, page: int = 1, per_page: int = 50
    ) -> list[dict[str, Any]]:
        if not self.is_configured:
            raise RuntimeError("爱发电 API 未配置完整。")

        params: dict[str, Any] = {
            "page": page,
            "per_page": per_page,
        }
        if out_trade_no:
            params["out_trade_no"] = out_trade_no

        response = await self._post("/query-order", params)
        data = response.get("data", {})
        order_list = data.get("list", [])
        if isinstance(order_list, list):
            return [order for order in order_list if isinstance(order, dict)]
        return []

    async def _post(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        session = await self._ensure_session()
        ts = int(time.time())
        sign = self._generate_sign(params, ts)
        payload = {
            "user_id": self._config.user_id,
            "params": json.dumps(params, separators=(",", ":"), ensure_ascii=False),
            "ts": ts,
            "sign": sign,
        }
        url = f"{self._config.base_url.rstrip('/')}{endpoint}"

        try:
            async with session.post(url, json=payload, timeout=10) as response:  # type: ignore[arg-type]
                response.raise_for_status()
                data = await response.json()
        except aiohttp.ClientError as exc:
            logger.error(f"[DG-LAB][Afdian] request failed: {exc}")
            raise RuntimeError("请求爱发电接口失败。") from exc

        if not isinstance(data, dict):
            raise RuntimeError("爱发电接口返回格式异常。")

        if data.get("ec") not in (0, None):
            raise RuntimeError(str(data.get("em") or "爱发电接口返回错误。"))
        return data

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    def _generate_sign(self, params: dict[str, Any], ts: int) -> str:
        params_text = json.dumps(params, separators=(",", ":"), ensure_ascii=False)
        source = f"{self._config.token}params{params_text}ts{ts}user_id{self._config.user_id}"
        return hashlib.md5(source.encode("utf-8")).hexdigest()
