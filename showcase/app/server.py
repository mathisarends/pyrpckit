from typing import Annotated

from fastapi import Body, FastAPI, Response
from fastapi.responses import JSONResponse

from showcase.app.api import CALCULATOR_RPC, CalculatorRpc

app = FastAPI(title="pyrpckit FastAPI showcase")
server = CALCULATOR_RPC.bind(CalculatorRpc())


@app.post("/rpc")
async def handle_rpc(payload: Annotated[object, Body()]) -> Response:
    response = await server.handle(payload)
    if response is None:
        return Response(status_code=204)
    return JSONResponse(response.model_dump(mode="json"))
