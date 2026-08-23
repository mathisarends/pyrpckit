from typing import Annotated

import pyrpckit as rpc
from fastapi import Body, FastAPI, Response
from fastapi.responses import JSONResponse

from showcase.app.api import PROTOCOL, CalculatorRpc

app = FastAPI(title="pyrpckit FastAPI showcase")
server = rpc.RpcServer(CalculatorRpc(), protocol=PROTOCOL)


@app.post("/rpc")
async def handle_rpc(payload: Annotated[object, Body()]) -> Response:
    response = await server.handle(payload)
    if response is None:
        return Response(status_code=204)
    return JSONResponse(response.model_dump(mode="json"))
