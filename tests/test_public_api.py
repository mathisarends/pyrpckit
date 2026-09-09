import pytest

import pyrpckit as rpc


@pytest.mark.parametrize(
    "name",
    (
        "RpcFeatureDefinition",
        "RpcHandler",
        "RpcNotificationDefinition",
        "RpcProtocol",
        "feature",
        "event",
        "method",
        "notification",
    ),
)
def test_removed_pre_v1_composition_symbols_are_not_public(name: str) -> None:
    assert not hasattr(rpc, name)


def test_servers_are_created_by_binding_an_app() -> None:
    with pytest.raises(TypeError, match="RpcApp.bind"):
        rpc.RpcServer()
