import pytest

from pyrpckit import ProtocolDefinitionError, RpcChannel, RpcService


def test_endpoint_matching_and_static_priority() -> None:
    dynamic = RpcChannel("dynamic")
    static = RpcChannel("static")
    service = RpcService()
    service.socket("/items/{item_id}", dynamic, name="item")
    expected = service.socket("/items/special", static, name="special")
    endpoint, values = service.match("/items/special")
    assert endpoint is expected
    assert values == {}
    assert service.match("/items/a%20b")[1] == {"item_id": "a b"}


@pytest.mark.parametrize("path", ["rpc", "/rpc/", "/a//b", "/rpc?q=1", "/rpc#x"])
def test_invalid_paths(path: str) -> None:
    with pytest.raises(ProtocolDefinitionError):
        RpcService().socket(path, RpcChannel("channel"))


def test_channel_cannot_be_mounted_twice() -> None:
    channel = RpcChannel("control")
    service = RpcService()
    service.socket("/one", channel)
    with pytest.raises(ProtocolDefinitionError, match="only be mounted once"):
        service.socket("/two", channel)


def test_service_freezes_channels() -> None:
    channel = RpcChannel("control")
    service = RpcService()
    service.socket("/rpc", channel)
    service.freeze()
    with pytest.raises(ProtocolDefinitionError, match="frozen"):
        service.socket("/other", RpcChannel("other"))
