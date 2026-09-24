import shutil
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path
from textwrap import dedent

import pytest

from pyrpckit import RpcChannel, RpcModel, RpcService
from pyrpckit.codegen import generate_typescript_client
from pyrpckit.codegen.typescript import TypeScriptClientOptions


class SessionParams(RpcModel):
    session_id: str


class SessionEvent(RpcModel):
    value: str


def test_generated_typescript_subscription(tmp_path: Path) -> None:
    npx = shutil.which("npx.cmd") or shutil.which("npx")
    node = shutil.which("node.exe") or shutil.which("node")
    if npx is None or node is None:
        pytest.skip("Node.js and npx are required")
    channel = RpcChannel("session")

    @channel.server.subscription()
    async def events(params: SessionParams) -> AsyncIterator[SessionEvent]:
        yield SessionEvent(value=params.session_id)

    service = RpcService()
    service.socket("/rpc", channels=(channel,))
    generate_typescript_client(
        service.contract(title="Session", base_url="ws://localhost").to_openrpc(),
        tmp_path / "generated",
        TypeScriptClientOptions(
            client_name="SessionClient", with_transport="websocket"
        ),
    )
    formatted = subprocess.run(
        [
            npx,
            "prettier",
            "--check",
            str(tmp_path / "generated/namespaces/session.ts"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert formatted.returncode == 0, formatted.stdout + formatted.stderr
    runner = tmp_path / "subscription.test.ts"
    runner.write_text(
        dedent(
            """
            import { SessionClient } from "./generated";

            let notify: ((value: unknown) => void) | undefined;
            let unsubscribed = false;
            const transport = {
              async request(method: string, params?: object): Promise<unknown> {
                if (method === "session.events.subscribe") {
                  if (JSON.stringify(params) !== '{"sessionId":"abc"}')
                    throw new Error("wrong params");
                  setTimeout(() => notify?.({
                    method: "session.events",
                    params: { subscriptionId: "1", payload: { value: "abc" } },
                  }), 0);
                  return { subscriptionId: "1" };
                }
                if (method === "session.events.unsubscribe") {
                  unsubscribed = true;
                  return null;
                }
                throw new Error(method);
              },
              async *notifications(): AsyncIterable<unknown> {
                while (true) yield await new Promise<unknown>((resolve) => {
                  notify = resolve;
                });
              },
              async close(): Promise<void> {},
            };

            async function main(): Promise<void> {
              const client = SessionClient.withTransports(transport);
              for await (const event of client.session.events({ sessionId: "abc" })) {
                if (event.value !== "abc") throw new Error("wrong payload");
                break;
              }
              if (!unsubscribed) throw new Error("unsubscribe was not sent");
              await client.close();
            }
            void main();
            """
        ),
        encoding="utf-8",
        newline="\n",
    )
    compiled = tmp_path / "compiled"
    checked = subprocess.run(
        [
            npx,
            "--yes",
            "--package",
            "typescript",
            "tsc",
            "--strict",
            "--target",
            "ES2022",
            "--module",
            "Node16",
            "--moduleResolution",
            "Node16",
            "--lib",
            "ES2022,DOM,ESNext.Disposable",
            "--skipLibCheck",
            "--outDir",
            str(compiled),
            str(runner),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert checked.returncode == 0, checked.stdout + checked.stderr
    behavior = subprocess.run(
        [node, str(compiled / "subscription.test.js")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert behavior.returncode == 0, behavior.stdout + behavior.stderr
