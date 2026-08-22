from pydantic import BaseModel

import pyrpckit as rpc


class EmptyParams(BaseModel):
    pass


class StatusResult(BaseModel):
    status: str


class ProfileResult(BaseModel):
    name: str


class SystemRpc(rpc.RpcHandler):
    @rpc.method("system.status")
    async def status(self, params: EmptyParams) -> StatusResult:
        return StatusResult(status="ready")


class AccountRpc(rpc.RpcHandler):
    @rpc.method("account.profile", summary="Return the current profile.")
    async def profile(self, params: EmptyParams) -> ProfileResult:
        return ProfileResult(name="Mathis")


SYSTEM = rpc.feature("system", handlers=(SystemRpc,))
ACCOUNT = rpc.feature("account", handlers=(AccountRpc,))
PROTOCOL = rpc.RpcProtocol(SYSTEM, ACCOUNT, version=2)


def main() -> None:
    print(f"Protocol version: {PROTOCOL.version}")
    for feature in PROTOCOL.features:
        print(f"{feature.name}: {[method.name for method in feature.methods]}")


if __name__ == "__main__":
    main()
