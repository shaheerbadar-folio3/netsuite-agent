import time
import pytest
from agent.engine import Engine
from agent.models import Plan
from agent.netsuite import NetSuiteError


class LLM:
    def __init__(self, plans):
        self.plans = iter(plans)
        self.contexts = []

    async def plan(self, context):
        self.contexts.append(dict(context))
        return Plan.model_validate(next(self.plans))


class NS:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    async def call(self, action, **payload):
        self.calls.append((action, payload))
        if self.fail:
            self.fail = False
            raise NetSuiteError("Invalid SuiteQL field")
        return {"ok": True, "result": {"rows": [{"id": 7, "companyname": "Acme"}], "total": 1,
                                        "page": payload.get("page", 0), "has_more": False}}


def job(id="1", **request):
    return {"id": id, "lease": "abc", "request": {"kind": "ask", "question": "Show customers", **request}}


GOOD = {"kind": "query", "explanation": "All customers", "sql": "SELECT id, companyname FROM customer ORDER BY id"}


async def test_question_to_executed_results_and_durable_completion(settings, store):
    ns, llm = NS(), LLM([GOOD])
    engine = Engine(settings, store, ns, llm)
    result = await engine.process(job())
    assert result["kind"] == "result"
    assert result["rows"][0]["id"] == 7
    assert result["document_version"]
    assert "Customers are customer records" in llm.contexts[0]["business_document"]
    # A replay after restart returns the persisted completion; it does not re-query or re-infer.
    assert await Engine(settings, store, ns, LLM([])).process(job()) == result
    assert len(ns.calls) == 1


async def test_validation_repair_never_executes_bad_sql(settings, store):
    ns = NS()
    llm = LLM([{**GOOD, "sql": "DELETE FROM customer"}, GOOD])
    result = await Engine(settings, store, ns, llm).process(job())
    assert result["kind"] == "result"
    assert len(ns.calls) == 1
    assert llm.contexts[1]["previous_error"]


async def test_execution_error_is_repaired(settings, store):
    result = await Engine(settings, store, NS(fail=True), LLM([GOOD, GOOD])).process(job())
    assert result["attempts"] == 2


async def test_ambiguity_does_not_query(settings, store):
    ns = NS()
    result = await Engine(settings, store, ns, LLM([{"kind": "clarify", "explanation": "Which return date?"}])).process(job())
    assert result["kind"] == "clarify"
    assert ns.calls == []


async def test_stale_schema_fails_closed(settings, store):
    schema = store.get("schema")
    schema["refreshed_at"] = time.time() - 100000
    store.put("schema", schema)
    result = await Engine(settings, store, NS(), LLM([])).process(job())
    assert result["kind"] == "error"
    assert "stale" in result["message"]


async def test_next_page_uses_original_sql_without_model(settings, store):
    ns = NS()
    j = job(kind="page", page=1)
    j["source_result"] = {"kind": "result", "sql": GOOD["sql"], "schema_version": "test-schema", "document_version": "old-version"}
    result = await Engine(settings, store, ns, LLM([])).process(j)
    assert result["page"] == 1
    assert result["document_version"] == "old-version"


async def test_retries_are_bounded(settings, store):
    ns = NS()
    bad = {**GOOD, "sql": "DROP TABLE customer"}
    result = await Engine(settings, store, ns, LLM([bad, bad, bad])).process(job())
    assert result["kind"] == "error"
    assert not ns.calls


async def test_plan_reuse_still_queries_live_and_document_change_invalidates(settings, store):
    llm, ns = LLM([GOOD, GOOD]), NS()
    engine = Engine(settings, store, ns, llm)
    first = await engine.process(job("cache1"))
    second = await engine.process(job("cache2"))
    assert first["kind"] == second["kind"] == "result"
    assert second["plan_cache_hit"] is True
    assert len(llm.contexts) == 1 and len(ns.calls) == 2
    settings.document_path.write_text("# Changed\nCustomers include inactive records.")
    third = await engine.process(job("cache3"))
    assert third["plan_cache_hit"] is False
    assert len(llm.contexts) == 2


async def test_history_and_relative_time_bypass_cache(settings, store):
    llm = LLM([GOOD] * 4)
    engine = Engine(settings, store, NS(), llm)
    for i in range(2):
        result = await engine.process(job("time"+str(i), question="Show customers created today"))
        assert result["plan_cache_hit"] is False
    for i in range(2):
        j = job("history"+str(i));j["history"] = [{"question":"Active only", "interpretation":"Active customers"}]
        result = await engine.process(j)
        assert result["plan_cache_hit"] is False
    assert len(llm.contexts) == 4


async def test_schema_refresh_invalidates_plan_cache(settings, store):
    llm = LLM([GOOD, GOOD]);engine = Engine(settings, store, NS(), llm)
    await engine.process(job("s1"))
    schema = store.get("schema");schema["refreshed_at"] += 1;store.put("schema",schema)
    result = await engine.process(job("s2"))
    assert result["plan_cache_hit"] is False


async def test_cached_query_failure_replans(settings, store):
    llm,ns = LLM([GOOD,GOOD]),NS();engine=Engine(settings,store,ns,llm)
    await engine.process(job("f1"));ns.fail=True
    result=await engine.process(job("f2"))
    assert result["kind"] == "result"
    assert len(llm.contexts)==2 and len(ns.calls)==3


async def test_expired_cache_replans(settings, store):
    llm=LLM([GOOD,GOOD]);engine=Engine(settings,store,NS(),llm)
    await engine.process(job("ttl1"))
    key=next(iter(engine.plan_cache));_,plan=engine.plan_cache[key]
    engine.plan_cache[key]=(time.monotonic()-301,plan)
    result=await engine.process(job("ttl2"))
    assert result["plan_cache_hit"] is False
    assert len(llm.contexts)==2


async def test_cache_revalidates_sql_before_execution(settings, store):
    llm=LLM([GOOD,GOOD]);ns=NS();engine=Engine(settings,store,ns,llm)
    await engine.process(job("v1"))
    key=next(iter(engine.plan_cache));stamp,plan=engine.plan_cache[key]
    engine.plan_cache[key]=(stamp,{**plan,"sql":"DELETE FROM customer"})
    result=await engine.process(job("v2"))
    assert result["kind"]=="result"
    assert len(ns.calls)==2
    assert all(call[1]["sql"].startswith("SELECT") for call in ns.calls)
