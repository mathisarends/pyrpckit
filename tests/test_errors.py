from pyrpckit import RpcErrorCode, error_message


def test_error_message_names_a_reserved_code() -> None:
    assert error_message(RpcErrorCode.METHOD_NOT_FOUND) == "Method not found"


def test_error_message_names_a_reserved_code_given_as_a_plain_int() -> None:
    assert error_message(int(RpcErrorCode.INVALID_PARAMS)) == "Invalid params"


def test_error_message_falls_back_for_an_application_defined_code() -> None:
    assert error_message(-32004) == "Error -32004"
