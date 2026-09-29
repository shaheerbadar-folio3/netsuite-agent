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
        self.refresh_full = False
        self.refresh_probe_only = None
        self._refresh_task = None
        self.status = {"state": "starting", "last_poll": None, "error": None}

    async def heartbeat(self, job):
        while True:
            await asyncio.sleep(25)
            try:
                response = await self.ns.call("heartbeat", job=job["id"], lease=job["lease"])
            except Exception as exc:
                # Transient NetSuite errors must not cancel a long SDF validate/deploy.
                log.warning("Heartbeat failed for job %s: %s", job.get("id"), type(exc).__name__)
                continue
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

    def _schema_usable(self):
        schema = self.store.get("schema") or {}
        return bool(schema.get("tables")) and bool(schema.get("refreshed_at"))

    def _refresh_running(self):
        return self._refresh_task is not None and not self._refresh_task.done()

    async def _run_refresh(self, *, force_full=False, probe_only=None):
        self.status.update(state="refreshing_schema", error=None)
        try:
            await refresh_schema(
                self.ns, self.store, self.settings,
                force_full=force_full, probe_only=probe_only)
            self.refresh_requested = False
            self.refresh_full = False
            self.refresh_probe_only = None
        except Exception as exc:
            self.store.put("schema_status", {
                **self.store.get("schema_status", {}),
                "ok": False, "state": "failed", "error": type(exc).__name__,
                "last_attempt": time.time(),
            })
            # Keep serving the last good snapshot when one exists.
            if not self._schema_usable():
                raise
            log.warning("Schema refresh failed (%s); continuing with last snapshot", type(exc).__name__)
            self.refresh_requested = False
            self.refresh_full = False
            self.refresh_probe_only = None

    async def ensure_refresh(self, *, force_full=False, blocking=False, probe_only=None):
        """Refresh schema. Block only on cold start when no usable snapshot exists."""
        if force_full:
            self.refresh_full = True
            self.refresh_probe_only = None
        elif probe_only:
            self.refresh_probe_only = list(probe_only)
        if self._refresh_running():
            if blocking:
                await self._refresh_task
            return

        if blocking:
            await self._run_refresh(
                force_full=self.refresh_full or force_full,
                probe_only=None if (self.refresh_full or force_full) else self.refresh_probe_only)
            return

        async def runner():
            try:
                await self._run_refresh(
                    force_full=self.refresh_full,
                    probe_only=None if self.refresh_full else self.refresh_probe_only)
            except Exception as exc:
                self.status.update(state="error", error=type(exc).__name__)
                log.warning("Worker schema refresh failed: %s", type(exc).__name__)
            finally:
                if self.status.get("state") == "refreshing_schema":
                    self.status["state"] = "idle"

        self._refresh_task = asyncio.create_task(runner())

    async def run(self):
        last_refresh_attempt = 0
        while not self.stop.is_set():
            try:
                self.store.purge(self.settings.retention_days)
                if self.store.get("creation_schema_refresh", False):
                    self.refresh_requested = True
                    probe = self.store.get("creation_schema_probe")
                    if probe:
                        self.refresh_probe_only = list(probe)
                        self.refresh_full = False
                        self.store.put("creation_schema_probe", None)
                    else:
                        self.refresh_full = True
                    self.store.put("creation_schema_refresh", False)
                schema = self.store.get("schema", {})
                stale = time.time() - schema.get("refreshed_at", 0) > self.settings.schema_refresh_seconds
                if (self.refresh_requested or stale) and time.time() - last_refresh_attempt >= 60:
                    last_refresh_attempt = time.time()
                    if self._schema_usable():
                        await self.ensure_refresh(
                            force_full=self.refresh_full, blocking=False,
                            probe_only=self.refresh_probe_only)
                    else:
                        await self.ensure_refresh(force_full=True, blocking=True)
                self.status.update(state="polling", error=None)
                response = await self.ns.call("claim")
                self.status["last_poll"] = time.time()
                if response.get("job"):
                    self.status["state"] = "answering"
                    await self.process_job(response["job"])
                if not self._refresh_running():
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
        if self._refresh_task and not self._refresh_task.done():
            self._refresh_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._refresh_task
        self.status["state"] = "stopped"
