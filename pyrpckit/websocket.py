from types import MappingProxyType

from pyrpckit.connection import RpcConnectionClose, RpcRejection

CLOSE_CODES = MappingProxyType(
    {
        RpcConnectionClose.NORMAL: 1000,
        RpcConnectionClose.SHUTDOWN: 1001,
        RpcConnectionClose.PROTOCOL_ERROR: 1002,
        RpcConnectionClose.POLICY_VIOLATION: 1008,
        RpcConnectionClose.MESSAGE_TOO_BIG: 1009,
        RpcConnectionClose.INTERNAL_ERROR: 1011,
    }
)
REJECTION_CLOSE_CODES = MappingProxyType(
    {
        RpcRejection.NOT_FOUND: 1008,
        RpcRejection.PROTOCOL_ERROR: 1002,
        RpcRejection.UNAVAILABLE: 1013,
        RpcRejection.INTERNAL_ERROR: 1011,
    }
)
MAX_CLOSE_REASON_BYTES = 123


def close_reason(reason: str) -> str:
    return reason.encode("utf-8")[:MAX_CLOSE_REASON_BYTES].decode(
        "utf-8", errors="ignore"
    )
