import asyncio
import contextlib
import logging
import time

from .schema import refresh_schema

log = logging.getLogger(__name__)


class Worker:
    def __init__(self, settings, store, ns, engine):
        self.settings, self.store, self.ns, self.engine = settings, store, ns, engine
        self.stop = asyncio.Event()
        self.refresh_requested = True
        self.status = {"state": "starting", "last_poll": None, "error": None}

    async def heartbeat(self, job):
        while True:
            await asyncio.sleep(25)
            response = await self.ns.call("heartbeat", job=job["id"], lease=job["lease"])
            if response.get("cancelled"):
                return "cancelled"

    async def process_job(self, job):
        task = asyncio.create_task(self.engine.process(job))
        heartbeat = asyncio.create_task(self.heartbeat(job))
        try:
            done, _ = await asyncio.wait({task, heartbeat}, return_when=asyncio.FIRST_COMPLETED)
            if heartbeat in done:
                # Lease loss or cancellation stops inference; don't publish a stale completion.
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
                heartbeat.result()
                self.store.audit(job["id"], "cancelled_or_lease_lost")
                return
            result = task.result()
            await self.ns.call("complete", job=job["id"], lease=job["lease"], result=result)
        finally:
            for pending in (task, heartbeat):
                if not pending.done():
                    pending.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await pending

    async def run(self):
        last_refresh_attempt = 0
        while not self.stop.is_set():
            try:
                self.store.purge(self.settings.retention_days)
                schema = self.store.get("schema", {})
                stale = time.time() - schema.get("refreshed_at", 0) > self.settings.schema_refresh_seconds
                if (self.refresh_requested or stale) and time.time() - last_refresh_attempt >= 60:
                    last_refresh_attempt = time.time()
                    self.status.update(state="refreshing_schema", error=None)
                    try:
                        await refresh_schema(self.ns, self.store)
                        self.refresh_requested = False
                    except Exception as exc:
                        self.store.put("schema_status", {"ok": False, "error": type(exc).__name__, "last_attempt": time.time()})
                        raise
                self.status.update(state="polling", error=None)
                response = await self.ns.call("claim")
                self.status["last_poll"] = time.time()
                if response.get("job"):
                    self.status["state"] = "answering"
                    await self.process_job(response["job"])
                self.status["state"] = "idle"
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self.status.update(state="error", error=type(exc).__name__)
                log.warning("Worker operation failed: %s", type(exc).__name__)
            try:
                await asyncio.wait_for(self.stop.wait(), timeout=self.settings.poll_seconds)
            except TimeoutError:
                pass
        self.status["state"] = "stopped"
