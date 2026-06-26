# contrib/ — agent integrations

## mcp_gs.py — MCP server over the GS web API

Exposes the Ground Station web API as MCP tools so an agent can drive fleet ops
directly (list devices/apps/runtime/deployments, deploy an app version, watch a
rollout). It calls the GS API — never Mender or MinIO directly — so the backend's
credentials never leave it; mutating tools present `X-GS-Key`.

```sh
pip install fastmcp
GS_API=http://10.0.0.99:8090 GS_API_KEY=<key> python contrib/mcp_gs.py
```

Register it with an MCP client (Claude Code, etc.) as a stdio server. Tools:
`list_devices`, `list_apps`, `list_runtime`, `list_deployments`,
`deployment_status`, `deploy_app(fleet, app, version, deploy=True)`.

It's a thin 1:1 mapping to `/api`, so it tracks the backend without drift — the
same surface as `tools/gs.py` and the web UI, for the agent.
