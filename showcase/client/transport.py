from collections.abc import AsyncIterator
from typing import Any

import httpx
from pyrpckit.client import RpcRemoteError


class HttpJsonRpcTransport:
    def __init__(
        self,
        endpoint: str,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._endpoint = endpoint
        self._client = client or httpx.AsyncClient()
        self._request_id = 0

    async def request(self, method: str, params: dict[str, Any]) -> Any:
        self._request_id += 1
        response = await self._client.post(
            self._endpoint,
            json={
                "jsonrpc": "2.0",
                "id": self._request_id,
                "method": method,
                "params": params,
            },
        )
        response.raise_for_status()
        envelope = response.json()
        if error := envelope.get("error"):
            raise RpcRemoteError(error["code"], error["message"], error.get("data"))
        return envelope["result"]

    async def notifications(self) -> AsyncIterator[dict[str, Any]]:
        for message in ():
            yield message

    async def close(self) -> None:
        await self._client.aclose()
