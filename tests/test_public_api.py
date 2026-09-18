import pytest

import pyrpckit as rpc


@pytest.mark.parametrize(
    "name",
    (
        "RpcFeatureDefinition",
        "RpcHandler",
        "RpcNotificationDefinition",
        "RpcProtocol",
        "RpcApp",
        "RpcRouter",
        "OpenRpcContract",
        "OpenRpcServer",
        "ConnectionRejected",
        "feature",
        "event",
        "method",
        "notification",
    ),
)
def test_removed_pre_v1_composition_symbols_are_not_public(name: str) -> None:
    assert not hasattr(rpc, name)


def test_servers_are_created_by_an_app() -> None:
    with pytest.raises(TypeError, match="RpcChannel.server"):
        rpc.RpcServer()


def test_authentication_rejections_are_not_part_of_the_rpc_api() -> None:
    assert not hasattr(rpc.RpcRejection, "UNAUTHORIZED")
    assert not hasattr(rpc.RpcRejection, "FORBIDDEN")
