from pyrpckit import RpcChannel, RpcModel, RpcModule


class StatusResult(RpcModel):
    status: str


class ProfileResult(RpcModel):
    name: str


system = RpcModule(namespace="system", tags=("system",))
account = RpcModule(namespace="account", tags=("account",))


@system.method()
async def status() -> StatusResult:
    return StatusResult(status="ready")


@account.method(summary="Return the current profile.")
async def profile() -> ProfileResult:
    return ProfileResult(name="Mathis")


APP = RpcChannel(version=2)
APP.include(system)
APP.include(account)


def main() -> None:
    print(f"Protocol version: {APP.protocol.version}")
    for method in APP.protocol.methods:
        print(f"{method.tags[0]}: {method.name}")


if __name__ == "__main__":
    main()
