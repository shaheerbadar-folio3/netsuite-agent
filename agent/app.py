import asyncio
import contextlib
import hmac
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Depends

from .config import Settings
from .engine import Engine
from .knowledge import load_knowledge
from .llm import Ollama
from .netsuite import NetSuite
from .store import Store
from .worker import Worker


def create_app(settings=None):
    s = settings or Settings()
    if len(s.management_token) < 32:
        raise ValueError("Set AGENT_MANAGEMENT_TOKEN to a random value of at least 32 characters")

    @asynccontextmanager
    async def lifespan(app):
        store, ns, llm = Store(s.data_dir), NetSuite(s), Ollama(s)
        # An OS lock also prevents two local processes from consuming the same job stream.
        import fcntl
        lock = (s.data_dir / "worker.lock").open("w")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            lock.close()
            store.close()
            await ns.close()
            await llm.close()
            raise RuntimeError("Another worker is already running in this data directory")
        worker = Worker(s, store, ns, Engine(s, store, ns, llm))
        app.state.worker, app.state.store, app.state.llm = worker, store, llm
        task = asyncio.create_task(worker.run())
        try:
            yield
        finally:
            worker.stop.set()
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            await ns.close()
            await llm.close()
            store.close()
            lock.close()

    app = FastAPI(title="NetSuite Local Agent", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    async def authorized(authorization: str = Header(default="")):
        if not hmac.compare_digest(authorization, "Bearer " + s.management_token):
            raise HTTPException(401, "Unauthorized")

    @app.get("/health")
    async def health():
        return {"service": "netsuite-agent", "running": True}

    @app.get("/status", dependencies=[Depends(authorized)])
    async def status():
        try:
            model = await app.state.llm.health()
        except Exception:
            model = {"reachable": False, "model": s.model}
        try:
            doc = load_knowledge(s.document_path, "", 12000)
            knowledge = {"ready": True, "version": doc["version"]}
        except ValueError as exc:
            knowledge = {"ready": False, "error": str(exc)}
        return {"worker": app.state.worker.status, "schema": app.state.store.get("schema_status"),
                "model": model, "knowledge": knowledge,
                "creation": {"enabled": s.creation_enabled, "sdf_enabled": s.sdf_enabled,
                             "sdf_auth_configured": bool(s.sdf_auth_id)}}

    @app.post("/schema/refresh", dependencies=[Depends(authorized)])
    async def schema_refresh(full: bool = False):
        app.state.worker.refresh_requested = True
        if full:
            app.state.worker.refresh_full = True
        return {"scheduled": True, "full": bool(full)}

    return app
