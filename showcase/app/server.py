from typing import Annotated

import pyrpckit as rpc
from fastapi import Body, FastAPI, Response
from fastapi.responses import JSONResponse
from pyrpckit.schema import render_openrpc

from showcase.app.api import PROTOCOL, CalculatorRpc

app = FastAPI(title="pyrpckit FastAPI showcase")
server = rpc.RpcServer(CalculatorRpc(), protocol=PROTOCOL)


@app.post("/rpc")
async def handle_rpc(payload: Annotated[object, Body()]) -> Response:
    response = await server.handle(payload)
    if response is None:
        return Response(status_code=204)
    return JSONResponse(response.model_dump(mode="json"))


@app.get("/openrpc.json", include_in_schema=False)
async def openrpc() -> dict[str, object]:
    return render_openrpc(
        PROTOCOL,
        title="Calculator API",
        description="A small pyrpckit API served through FastAPI.",
        servers=({"name": "local", "url": "http://127.0.0.1:8000/rpc"},),
    )
