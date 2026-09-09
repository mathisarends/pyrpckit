from pyrpckit import RpcApp, RpcModel, RpcRouter


class StatusResult(RpcModel):
    status: str


class ProfileResult(RpcModel):
    name: str


system = RpcRouter(namespace="system", tags=("system",))
account = RpcRouter(namespace="account", tags=("account",))


class SystemRpc:
    @system.method
    async def status(self) -> StatusResult:
        return StatusResult(status="ready")


class AccountRpc:
    @account.method(summary="Return the current profile.")
    async def profile(self) -> ProfileResult:
        return ProfileResult(name="Mathis")


APP = RpcApp(version=2)
APP.include_router(system)
APP.include_router(account)


def main() -> None:
    print(f"Protocol version: {APP.protocol.version}")
    for method in APP.protocol.methods:
        print(f"{method.tags[0]}: {method.name}")


if __name__ == "__main__":
    main()
