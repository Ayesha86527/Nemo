import sys
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.db.engine import init_db
from app.routers import health, llm, settings, profile, cv, market, jobs, agent, roadmap, speech


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize SQLite tables on startup."""
    await asyncio.to_thread(init_db)
    yield


app = FastAPI(title="Nemo Backend", version="0.2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost", "http://127.0.0.1", "file://"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(llm.router)
app.include_router(settings.router)
app.include_router(profile.router)
app.include_router(cv.router)
app.include_router(market.router)
app.include_router(jobs.router)
app.include_router(agent.router)
app.include_router(roadmap.router)
app.include_router(speech.router)


def main():
    """Entry point when launched by Electron — prints the port to stdout for IPC negotiation."""
    import uvicorn
    import socket

    # Fully initialize the database before announcing readiness, so Electron
    # never connects to a server still running migrations (perceived hang).
    try:
        init_db()
    except Exception as exc:
        print(f"NEMO_DB_ERROR: {exc}", flush=True)
        sys.exit(1)

    # Find an available port to avoid conflicts (AD-1 / D-1 resolution)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    except OSError:
        sys.exit(1)
    finally:
        sock.close()

    # Signal the port to Electron via stdout before uvicorn captures it
    print(f"NEMO_PORT:{port}", flush=True)

    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
