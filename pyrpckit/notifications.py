from dataclasses import dataclass
from typing import Any

from pydantic import TypeAdapter

from pyrpckit.envelopes import RpcNotification

type RpcOutgoingMessage = RpcNotification


@dataclass(frozen=True, slots=True)
class RpcNotificationHandle[PayloadT]:
    name: str
    payload: Any

    def __call__(self, payload: PayloadT) -> RpcOutgoingMessage:
        validated = TypeAdapter(self.payload).validate_python(payload)
        return RpcNotification._with_payload_annotation(
            self.name,
            validated,
            self.payload,
        )
