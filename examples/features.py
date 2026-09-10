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


APP = RpcChannel(version=2, modules=(system, account))


def main() -> None:
    protocol = APP.freeze()
    print(f"Protocol version: {protocol.version}")
    for method in protocol.methods:
        print(f"{method.tags[0]}: {method.name}")


if __name__ == "__main__":
    main()
