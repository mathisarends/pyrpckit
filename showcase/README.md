# Showcase

The showcase demonstrates a complete integration rather than isolated API calls:

- [`app/`](app/) declares and serves the protocol and publishes
  its OpenRPC document.
- [`client/`](client/) contains the generated client and the small hand-written
  HTTP transport used to call the app.
- [`../scripts/`](../scripts/) contains commands for serving, regenerating, and
  calling the showcase.

Follow the [FastAPI walkthrough](app/README.md) to run the complete flow.

For small examples of the raw pyrpckit API, see [`examples/`](../examples/).
