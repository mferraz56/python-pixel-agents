# pixel-agents-python


forked from - https://github.com/pablodelucca/pixel-agents


Real-time, gamified telemetry viewer for AI-agent workflows.
Copilot-first, agent-agnostic by design, fully containerized, **no VS Code
extension required**. Reuses the wire contract of
[pixel-agents](https://github.com/) (`ViewerMessage` + SSE bootstrap/message)
so the existing browser viewer can be pointed at this Python backend.

> Phase 1 status: ingestion + projection + SSE end-to-end with a placeholder
> viewer. The gamified UI from `pixel-agents/webview-ui` will be ported in a
> follow-up phase. See [project_rules/000-rules.md](project_rules/000-rules.md)
> for the v1 scope and explicit deviations from the generic MVP stack.

## Architecture (v1)

```
 ┌──────────────┐    POST /api/hooks/{provider}         ┌────────────────────┐
 │ adapter(s)   │ ────────────────────────────────────► │  FastAPI app       │
 │  - OTel*     │                                       │  ├─ EventEnvelope  │
 │  - debug-log*│                                       │  ├─ Aggregator     │
 │  - manual    │                                       │  ├─ ReplayStore    │
 └──────────────┘                                       │  └─ EventBus (SSE) │
                                                        └─────────┬──────────┘
                                                                  │
                                                  GET /api/viewer/events (SSE)
                                                                  │
                                                          ┌───────▼────────┐
                                                          │ browser viewer │
                                                          └────────────────┘
 *adapters land in Phase 2; v1 accepts hand-crafted envelopes via the hook.
```

## Endpoints

| Method | Path                          | Auth          | Purpose                          |
|--------|-------------------------------|---------------|----------------------------------|
| GET    | `/api/health`                 | none          | liveness                         |
| POST   | `/api/hooks/{provider_id}`    | bearer token  | normalized event ingest          |
| GET    | `/api/viewer/events?token=…`  | token (query) | SSE: `bootstrap` → `message`     |
| GET    | `/viewer/`                    | none (asset)  | static viewer SPA (placeholder)  |

## Run locally (no Docker)

```powershell
cd C:\Projetos-Git\pixel-agents-python
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .[dev]
$env:PIXEL_AGENTS_TOKEN = "dev-token-change-me"
uvicorn app.main:app --host 0.0.0.0 --port 8765
```

In another terminal:

```powershell
.\scripts\send-sample-event.ps1
```

Then open `http://localhost:8765/viewer/?token=dev-token-change-me`.

## Run via Docker Compose

```powershell
cd C:\Projetos-Git\pixel-agents-python
Copy-Item .env.example .env
docker compose up --build
```

## Tests

```powershell
pip install -e .[dev]
pytest -q
```

## Event envelope shape

```json
{
  "provider_id": "copilot",
  "session_id": "abc123",
  "agent_id": null,
  "parent_agent_id": null,
  "event": { "kind": "toolStart", "tool_id": "t1", "tool_name": "Read" }
}
```

Supported `event.kind` values mirror the Pixel Agents `AgentEvent` union plus
`tokenUsage`: `sessionStart`, `sessionEnd`, `userTurn`, `turnEnd`, `toolStart`,
`toolEnd`, `subagentStart`, `subagentEnd`, `subagentTurnEnd`, `progress`,
`permissionRequest`, `tokenUsage`.
