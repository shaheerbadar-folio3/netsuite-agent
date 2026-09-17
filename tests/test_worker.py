import asyncio
import pytest
from agent.worker import Worker


async def test_completion_failure_keeps_durable_result_for_replay(settings, store):
    from agent.engine import Engine
    from test_engine import NS, LLM, GOOD, job
    engine = Engine(settings, store, NS(), LLM([GOOD]))
    class BrokenPublish:
        async def call(self, action, **kwargs):
            raise OSError('network unavailable')
    worker = Worker(settings, store, BrokenPublish(), engine)
    with pytest.raises(OSError):
        await worker.process_job(job())
    assert store.cached_result('1')["kind"] == "result"


async def test_cancellation_stops_inference_and_never_publishes(settings, store):
    finished = asyncio.Event()
    class Slow:
        async def process(self, job):
            try:
                await asyncio.sleep(3600)
            finally:
                finished.set()
    class NS:
        async def call(self, *args, **kwargs):
            raise AssertionError('must not complete cancelled job')
    worker = Worker(settings, store, NS(), Slow())
    async def cancelled(job):
        await asyncio.sleep(0)
        return 'cancelled'
    worker.heartbeat = cancelled
    await worker.process_job({'id':'1'})
    assert finished.is_set()
