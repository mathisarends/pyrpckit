import shutil
import subprocess
from copy import deepcopy
from pathlib import Path
from textwrap import dedent
from typing import Any

import pytest

from pyrpckit.codegen import generate_typescript_client
from pyrpckit.codegen.typescript import TypeScriptClientOptions


def test_generated_client_connect_behavior_in_node(
    document: dict[str, Any],
    tmp_path: Path,
) -> None:
    npx = shutil.which("npx.cmd") or shutil.which("npx")
    node = shutil.which("node.exe") or shutil.which("node")
    if npx is None or node is None:
        pytest.skip("Node.js and npx are required")

    deployed = deepcopy(document)
    deployed["methods"] = [deployed["methods"][0]]
    deployed["methods"][0]["servers"] = [{"name": "primary"}]
    deployed["x-rpc-notifications"] = []
    deployed["servers"] = [
        {
            "name": "primary",
            "url": "wss://{host}/rpc",
            "variables": {"host": {"default": "api.example.com"}},
            "x-rpckit-transport": {
                "type": "websocket",
                "messageEncoding": "json",
            },
        },
        {
            "name": "secondary",
            "url": "wss://{host}/second",
            "variables": {"host": {"default": "api.example.com"}},
            "x-rpckit-transport": {
                "type": "websocket",
                "messageEncoding": "json",
            },
        },
    ]
    deployed["x-rpckit-binary-streams"] = [
        {
            "name": "greeting.frames",
            "url": "wss://{host}/frames",
            "direction": "server-to-client",
            "contentType": "image/jpeg",
            "frameType": "binary",
            "variables": {"host": {"default": "api.example.com"}},
        }
    ]

    output = tmp_path / "generated"
    generate_typescript_client(
        deployed,
        output,
        TypeScriptClientOptions(
            client_name="GreetingClient",
            source="greeting.openrpc.json",
            with_transport="websocket",
        ),
    )
    runner = tmp_path / "connect.test.ts"
    runner.write_text(
        dedent(
            """
            import { GreetingClient } from "./generated";

            type Listener = (...args: any[]) => void;

            class FakeSocket {
              readyState = 1;
              binaryType: BinaryType = "blob";
              readonly #listeners = new Map<string, Listener[]>();

              constructor(
                readonly url: string,
                private readonly result: unknown = undefined,
              ) {}

              addEventListener(
                type: "open" | "close" | "error",
                listener: () => void,
              ): void;
              addEventListener(
                type: "message",
                listener: (event: { readonly data: unknown }) => void,
              ): void;
              addEventListener(type: string, listener: Listener): void {
                const listeners = this.#listeners.get(type) ?? [];
                listeners.push(listener);
                this.#listeners.set(type, listeners);
              }

              send(data: string): void {
                const request = JSON.parse(data) as { readonly id: number };
                this.#emit("message", {
                  data: JSON.stringify({
                    jsonrpc: "2.0",
                    id: request.id,
                    result: this.result,
                  }),
                });
              }

              close(): void {
                this.readyState = 3;
              }

              #emit(type: string, event: { readonly data: unknown }): void {
                for (const listener of this.#listeners.get(type) ?? []) {
                  listener(event);
                }
              }
            }

            function assert(condition: unknown, message: string): asserts condition {
              if (!condition) throw new Error(message);
            }

            async function main(): Promise<void> {
              const lazyUrls: string[] = [];
              const lazy = await GreetingClient.connect({
                host: "stage.example.com",
                socketFactory: (url) => {
                  lazyUrls.push(String(url));
                  return new FakeSocket(String(url), { text: "Hello, Mathis!" });
                },
              });
              assert(lazyUrls.length === 0, "connect() opened an RPC socket eagerly");
              await lazy.greeting.say({ name: "Mathis" });
              assert(
                lazyUrls.join() === "wss://stage.example.com/rpc",
                `unexpected lazy URLs: ${lazyUrls}`,
              );
              await lazy.close();

              const eagerUrls: string[] = [];
              const eager = await GreetingClient.connect({
                host: "stage.example.com",
                eager: true,
                socketFactory: (url) => {
                  eagerUrls.push(String(url));
                  return new FakeSocket(String(url));
                },
              });
              assert(
                eagerUrls.sort().join() ===
                  "wss://stage.example.com/rpc,wss://stage.example.com/second",
                `unexpected eager URLs: ${eagerUrls}`,
              );
              await eager.close();

              const overrideUrls: string[] = [];
              const overridden = await GreetingClient.connect({
                host: "stage.example.com",
                servers: { primary: "wss://localhost:8000/rpc" },
                socketFactory: (url) => {
                  overrideUrls.push(String(url));
                  return new FakeSocket(String(url), { text: "Hello, Mathis!" });
                },
              });
              await overridden.greeting.say({ name: "Mathis" });
              assert(
                overrideUrls.join() === "wss://localhost:8000/rpc",
                `unexpected override URLs: ${overrideUrls}`,
              );
              await overridden.close();

              const streamUrls: string[] = [];
              const streams = await GreetingClient.connect({
                host: "stage.example.com",
                socketFactory: (url) => new FakeSocket(String(url)),
                streamSocketFactory: (url) => {
                  streamUrls.push(String(url));
                  return new FakeSocket(String(url));
                },
              });
              const inherited = await streams.greeting.frames();
              await inherited.close();
              const explicit = await streams.greeting.frames({
                host: "other.example.com",
              });
              await explicit.close();
              assert(
                streamUrls.join() ===
                  "wss://stage.example.com/frames,wss://other.example.com/frames",
                `unexpected stream URLs: ${streamUrls}`,
              );
              await streams.close();
            }

            void main();
            """
        ),
        encoding="utf-8",
        newline="\n",
    )

    compiled = tmp_path / "compiled"
    type_check = subprocess.run(
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
    assert type_check.returncode == 0, type_check.stdout + type_check.stderr

    behavior = subprocess.run(
        [node, str(compiled / "connect.test.js")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert behavior.returncode == 0, behavior.stdout + behavior.stderr
