import pytest

from pyrpckit.client import RpcServerInfo, RpcServerVariable


def test_server_info_resolves_declared_variables() -> None:
    server = RpcServerInfo(
        name="control",
        url="wss://{host}/{sessionId}",
        variables={
            "host": RpcServerVariable("api.example.com"),
            "sessionId": RpcServerVariable("demo"),
        },
    )

    assert server.resolve({"sessionId": "s-123"}) == "wss://api.example.com/s-123"


def test_server_info_rejects_unknown_variables() -> None:
    server = RpcServerInfo(name="control", url="wss://example.com")

    with pytest.raises(ValueError, match="Unknown variables"):
        server.resolve({"token": "secret"})
