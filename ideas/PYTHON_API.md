Die Klasse ist die Router-Definition, ihre Instanz der gebundene Handler:

class AgentRpc(RpcController):
namespace = "agent"

    def __init__(self, service: AgentService) -> None:
        self._service = service

    @rpc.method
    async def start(self, params: StartRunParams) -> RunRef:
        ...

    @rpc.method
    async def steer(self, params: SteerRunParams) -> None:
        ...

Dann:

app = RpcApp(version=1)

app.include(AgentRpc)

server = app.bind(
AgentRpc(agent_service),
)

Das hat eine sehr nette Symmetrie:

AgentRpc -> Protokolldefinition
AgentRpc(...) -> Runtime-Handler

Und RpcApp kann zur Build-Time problemlos die Klasse introspektieren, ohne Constructor aufzurufen:

app.include(AgentRpc)
contract = OpenRpcContract(app=app, ...)

Das ist wichtig, weil dein OpenRPC-Contract laut README explizit ein Build-Time-Artefakt sein soll und keinen laufenden Server braucht.

Intern könnte RpcController praktisch sowas machen:

class RpcController:
namespace: str | None = None
tags: tuple[str, ...] = ()
server: str | None = None

und @rpc.method hängt nur Metadata an die Funktion:

@rpc.method(errors=(AutomationNotFound,))
async def get(...):
...

Bei:

app.include(AutomationRpc)

Ein besonders schöner Effekt ist, dass Notifications jetzt ebenfalls natürlich dazugehören:

class BrowserRpc(RpcController):
namespace = "browser"
server = "control"

    @rpc.method
    async def navigate(self, params: NavigateParams) -> None:
        ...

    @rpc.method
    async def click(self, params: ClickParams) -> None:
        ...

    @rpc.notification
    def state_changed(self) -> BrowserStateChanged:
        ...
