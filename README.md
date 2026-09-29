# CarbonMesh

Lightweight hackathon foundation for the CarbonMesh React frontend and FastAPI backend.

## Prerequisites

- Node.js 22.12 or newer
- npm 10 or newer
- Python 3.12 or newer

## Install the frontend

From the repository root:

```bash
npm --prefix apps/web install
```

## Install and run the backend

```powershell
cd apps/api
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m uvicorn app.main:app --reload --reload-dir app --port 8000
```

On macOS or Linux, activate the environment with `source .venv/bin/activate`.

The API health endpoint is available at `http://localhost:8000/api/health`, and interactive API documentation is available at `http://localhost:8000/docs`.

Keep the backend terminal open while the API is running. The base URL at
`http://localhost:8000/` returns `404` because the API does not define a root route.
Limiting reloads to the `app` directory prevents virtual-environment or OneDrive changes
from repeatedly restarting the server.

## Run the frontend

In a second terminal, from the repository root:

```bash
npm run dev
```

Open `http://localhost:3000`. Vite proxies `/api` requests to the FastAPI server on port `8000`.

## Repository layout

```text
apps/
  web/        React, TypeScript, Vite, Tailwind CSS v4, shadcn/ui
  api/        FastAPI application and backend domain placeholders
data/demo/    Synthetic hackathon fixtures
docs/         Architecture and API documentation
infra/        Deployment and infrastructure files
scripts/      Project automation
tests/e2e/    Cross-application end-to-end tests
```

Feature and domain folders intentionally contain only `.gitkeep` files until the team starts implementation.
