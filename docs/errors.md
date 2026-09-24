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
@tasks.server.method(raises=(MissingTaskError,))
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

Both `Error` and `RpcError` suffixes are removed during code derivation, so
`VoiceTurnAlreadyActiveRpcError` becomes `voice_turn_already_active`.

Application codes must be lowercase identifiers using letters, digits, and
underscores. JSON-RPC's standard and reserved numeric codes cannot be claimed
by application errors. Details, when declared, must be a Pydantic model and
are required when constructing the error.

Errors may be declared for every method in a channel:

```python
tasks = RpcChannel("tasks", raises=(PermissionDeniedError,))
```

Method-level declarations are added to the channel-level set.

Map domain exceptions at service construction when an RPC error needs no
details model:

```python
service = RpcService(
    errors={TaskNotFound: TaskNotFoundRpcError},
    strict_errors=True,
)
```

The original exception text becomes the RPC error message. `error_mapper`
remains a fallback for mappings that need custom details. With
`strict_errors=True`, an application error absent from the method's `raises=`
declaration becomes an internal error and is logged.

Clients should identify application errors by `error.data.code`. The numeric
`error.code` is optional for application-specific identity; distinct errors may
share it. The service warns when explicitly assigned numeric codes collide.

## Unexpected exceptions

Undeclared implementation failures are returned as an internal JSON-RPC error
without exposing their exception text. A transport may supply an
`error_mapper` to map selected exceptions to application errors, while logs
retain the original failure for operators.

Validation, parse, invalid-request, and unknown-method failures use pyrpckit's
built-in JSON-RPC errors and do not need to be declared.

## Generated client behavior

Declared error classes are exported from generated Python and TypeScript
packages. Catching the concrete class preserves the stable application code and
gives typed access to declared details:

```python
from tasks_client import MissingTaskError

try:
    await client.tasks.get(task_id=42)
except MissingTaskError as error:
    print(error.details.task_id)
```

```ts
import { MissingTaskError } from "./tasks-client";

try {
  await client.tasks.get({ taskId: 42 });
} catch (error) {
  if (error instanceof MissingTaskError) {
    console.log(error.details.taskId);
  }
}
```

An undeclared application code becomes `RpcRemoteError`, retaining its numeric
`rpc_code` / `rpcCode`, string `code`, message, and raw details. Python also
validates declared error details, responses, and notifications against their
generated Pydantic models. Invalid payloads fall back to `RpcRemoteError` for
error details or raise `RpcResponseValidationError` /
`RpcNotificationValidationError` for successful data. TypeScript types these
payloads at compile time but does not perform runtime schema validation.

Connection and stream lifecycle failures are separate from remote application
errors. TypeScript exports `RpcConnectionClosed`; Python transport failures are
available through `RpcClientError` and its exported subclasses. Direct binary
stream reads use `RpcStreamClosed` for a regular remote close, while async
iteration treats that condition as normal completion.

[Back to documentation](README.md)
