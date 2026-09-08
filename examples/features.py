import pyrpckit as rpc


class StatusResult(rpc.RpcModel):
    status: str


class ProfileResult(rpc.RpcModel):
    name: str


system = rpc.RpcRouter(prefix="system", tags=("system",))
account = rpc.RpcRouter(prefix="account", tags=("account",))


class SystemRpc:
    @system.method("status")
    async def status(self) -> StatusResult:
        return StatusResult(status="ready")


class AccountRpc:
    @account.method("profile", summary="Return the current profile.")
    async def profile(self) -> ProfileResult:
        return ProfileResult(name="Mathis")


APP = rpc.RpcApp(version=2)
APP.include_router(system)
APP.include_router(account)


def main() -> None:
    print(f"Protocol version: {APP.protocol.version}")
    for method in APP.protocol.methods:
        print(f"{method.tags[0]}: {method.name}")


if __name__ == "__main__":
    main()
