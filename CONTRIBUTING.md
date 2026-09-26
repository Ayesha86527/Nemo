# Contributing to Nemo

Thanks for your interest in making Nemo better!

## Getting set up

```bash
# Backend (Python 3.12+)
cd backend
pip install -e ".[dev]"

# Frontend (Node 18+)
cd frontend
npm install
```

## Everyday commands

```bash
# Backend tests (from backend/)
python -m pytest -q

# Frontend typecheck + bundle (from frontend/)
npm run build

# Run the full app in dev mode (from frontend/)
npm run dev
```

## Before you open a PR

- `python -m pytest` passes in `backend/` (all tests, no skips).
- `npm run build` passes in `frontend/` (this also runs `tsc`).
- New backend behavior comes with tests. The suite uses the `isolated_engine`
  fixture — never touch `~/.nemo/nemo.db` from a test.
- Keep the local-first contract: no telemetry, no accounts, no network calls
  except the user-configured LLM endpoint.
- Keep the agent's mutation surface deliberate: the LLM proposes whitelisted
  JSON actions; deterministic code applies them. If you add an action type,
  gate it like the existing ones (see `app/agent/service.py`).

## Design principles

1. **Local-first.** All data stays on the user's machine (`~/.nemo/`). The only
   network traffic is the user's own LLM endpoint.
2. **The model proposes, the code disposes.** LLM output is treated as
   untrusted: it never writes to the database directly, and it can never
   change security-relevant settings (endpoints are allowlisted, API keys are
   Settings-only).
3. **No auto-apply.** Nemo helps you prepare better applications; it never
   submits anything on your behalf or scrapes job boards.

For significant changes, please open an issue first so we can align on scope.
