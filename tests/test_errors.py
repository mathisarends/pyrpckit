import pytest

from pyrpckit import ProtocolDefinitionError, RpcError, RpcErrorCode, error_message


class Busy(RpcError):
    code = -32010
    message = "Server is busy"


def test_error_message_names_a_reserved_code() -> None:
    assert error_message(RpcErrorCode.METHOD_NOT_FOUND) == "Method not found"


def test_error_message_names_a_reserved_code_given_as_a_plain_int() -> None:
    assert error_message(int(RpcErrorCode.INVALID_PARAMS)) == "Invalid params"


def test_error_message_falls_back_for_an_application_defined_code() -> None:
    assert error_message(-32004) == "Error -32004"


def test_a_declared_error_carries_its_code_and_default_message() -> None:
    error = Busy()

    assert error.code == -32010
    assert error.message == "Server is busy"
    assert str(error) == "Server is busy"


def test_a_declared_error_may_override_its_message_per_instance() -> None:
    error = Busy("Try again in 30s")

    assert error.code == -32010
    assert error.message == "Try again in 30s"


def test_a_one_off_error_takes_its_code_directly() -> None:
    error = RpcError("Not found", code=-32004)

    assert error.code == -32004
    assert error.message == "Not found"


def test_an_error_without_a_message_falls_back_to_the_name_of_its_code() -> None:
    assert RpcError(code=RpcErrorCode.INTERNAL_ERROR).message == "Internal error"


def test_an_error_without_a_code_cannot_be_raised() -> None:
    class Codeless(RpcError):
        pass

    with pytest.raises(ProtocolDefinitionError, match="declares no code"):
        Codeless()
