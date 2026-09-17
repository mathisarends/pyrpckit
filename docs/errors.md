# Typed errors

Expected application failures should be part of the contract. An `RpcError`
subclass defines a stable application code, a JSON-RPC integer code, a default
message, and optional typed details.

```python
from pyrpckit import RpcError, RpcModel


class MissingTaskDetails(RpcModel):
    task_id: int


class MissingTaskError(RpcError):
    code = "task_missing"
    rpc_code = -32004
    message = "Task not found"
    details: MissingTaskDetails
```

Declare expected errors on a method and raise them with a details model or its
fields:

```python
@tasks.method(raises=(MissingTaskError,))
async def get(params: GetTask) -> Task:
    task = await find_task(params.task_id)
    if task is None:
        raise MissingTaskError(task_id=params.task_id)
    return task
```

On the wire, JSON-RPC's numeric code remains in `error.code`. The stable
application code and validated details are placed in `error.data`. Generated
clients turn declared failures back into concrete exception classes.

## Defaults and rules

When omitted, `code` is derived from the class name: `MissingTaskError` becomes
`missing_task`. The message is derived from that code, and `rpc_code` defaults
to `-32000`.

Application codes must be lowercase identifiers using letters, digits, and
underscores. JSON-RPC's standard and reserved numeric codes cannot be claimed
by application errors. Details, when declared, must be a Pydantic model and
are required when constructing the error.

Errors may be declared for every method in a channel:

```python
tasks = RpcChannel("tasks", raises=(PermissionDeniedError,))
```

Method-level declarations are added to the channel-level set.

## Unexpected exceptions

Undeclared implementation failures are returned as an internal JSON-RPC error
without exposing their exception text. A transport may supply an
`error_mapper` to map selected exceptions to application errors, while logs
retain the original failure for operators.

Validation, parse, invalid-request, and unknown-method failures use pyrpckit's
built-in JSON-RPC errors and do not need to be declared.

[Back to documentation](README.md)
