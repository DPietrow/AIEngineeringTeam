# AIEngineeringTeam

Case study on MCP, loop/harness engineering, and LLMOps: a multi-agent coding team
(Architect, Implementation, Testing, Review, Delivery) with typed handoffs, sandboxed
execution, a human review gate, an eval harness, and live tracing.

## Layout

- `backend/` Python 3.12 (uv), Flask API, worker, tracing core, agents
- `frontend/` React + Vite + TypeScript observability dashboard

## Backend quickstart

```powershell
cd backend
uv sync
uv run pytest
uv run ruff check .
uv run flask --app agentteam.app:create_app run --debug
```

Health check: `http://127.0.0.1:5000/health`

## Frontend quickstart

```powershell
cd frontend
npm install
npm run dev      # http://localhost:5173, proxies /api to the Flask backend on :5000
npm run lint
npm run build
```

Run the backend first so the dashboard's "Backend" status turns green.

## Status

Day 1 in progress: scaffold, schemas, tracing core.
