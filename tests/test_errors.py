import pytest
from pydantic import BaseModel, ValidationError

from pyrpckit import (
    ProtocolDefinitionError,
    RpcError,
    RpcInvalidParamsError,
    RpcModel,
)
from pyrpckit.envelopes import RpcFailure


class MissingDetails(RpcModel):
    project_id: str


class ProjectNotFoundError(RpcError):
    details: MissingDetails


class HTTPTimeoutError(RpcError):
    rpc_code = -32010


class VoiceTurnAlreadyActiveRpcError(RpcError):
    pass


def test_error_metadata_is_derived() -> None:
    assert ProjectNotFoundError.code == "project_not_found"
    assert ProjectNotFoundError.message == "Project not found"
    assert HTTPTimeoutError.code == "http_timeout"
    assert HTTPTimeoutError.rpc_code == -32010
    assert VoiceTurnAlreadyActiveRpcError.code == "voice_turn_already_active"


def test_details_accept_fields_or_model() -> None:
    expected = MissingDetails(project_id="p-1")
    assert ProjectNotFoundError(project_id="p-1").details == expected
    assert ProjectNotFoundError(expected).details == expected
    with pytest.raises(TypeError):
        ProjectNotFoundError()


def test_wire_error_has_string_code_and_details() -> None:
    failure = RpcFailure.from_error(7, ProjectNotFoundError(project_id="p-1"))
    assert failure.model_dump(mode="json", exclude_none=True) == {
        "jsonrpc": "2.0",
        "id": 7,
        "error": {
            "code": -32000,
            "message": "Project not found",
            "data": {
                "code": "project_not_found",
                "details": {"projectId": "p-1"},
            },
        },
    }


def test_direct_error_and_invalid_declarations_are_rejected() -> None:
    with pytest.raises(TypeError):
        RpcError()
    with pytest.raises(ProtocolDefinitionError):

        class BadCode(RpcError):
            code = "Bad-Code"

    with pytest.raises(ProtocolDefinitionError):

        class BadDetails(RpcError):
            details: str


def test_invalid_params_has_structured_issues() -> None:
    class Input(BaseModel):
        name: str

    with pytest.raises(ValidationError) as caught:
        Input.model_validate({})
    error = RpcInvalidParamsError.from_validation_error(caught.value)
    assert error.code == "invalid_params"
    assert error.details.issues[0].loc == ["name"]

    manual = RpcInvalidParamsError(message="Invalid domain value")
    assert manual.details.issues == []
    assert manual.message == "Invalid domain value"


def test_positional_message_mistake_has_a_targeted_error() -> None:
    class ResourceNotFoundError(RpcError):
        pass

    with pytest.raises(TypeError, match=r"did you mean.*message="):
        ResourceNotFoundError("Not found: x")  # type: ignore[arg-type]
