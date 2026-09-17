import asyncio
import json
import hashlib
import re
import time
from datetime import datetime, timezone

from .knowledge import load_knowledge, select_schema
from .models import Schema, Plan
from .netsuite import NetSuiteError
from .sql import validate_sql, record_link_columns, QueryRejected
from pydantic import ValidationError


class Engine:
    def __init__(self, settings, store, ns, llm):
        self.settings, self.store, self.ns, self.llm = settings, store, ns, llm
        self.plan_cache = {}

    async def answer(self, job):
        start = time.monotonic()
        raw = self.store.get("schema")
        if not raw:
            raise ValueError("Schema not ready; wait for discovery or inspect schema status")
        schema = Schema.model_validate(raw)
        if time.time() - schema.refreshed_at > self.settings.schema_refresh_seconds * 2:
            raise ValueError("Schema is stale; a successful refresh is required")
        request = job["request"]
        if request.get("kind") == "page":
            source = job.get("source_result") or {}
            if source.get("kind") != "result" or source.get("schema_version") != schema.version:
                raise ValueError("Original query expired or schema changed; ask the question again")
            sql = validate_sql(source["sql"], schema)
            result = await self.ns.call("query", job=job["id"], lease=job["lease"],
                                        sql=sql, page=request["page"], record_links=record_link_columns(sql))
            return {**source, **result["result"], "queried_at": datetime.now(timezone.utc).isoformat(),
                    "elapsed_seconds": round(time.monotonic() - start, 2)}

        # Fixed, conservative character budget also leaves room for system instructions and output.
        context_chars = (self.settings.context_tokens - 2500) * 2
        knowledge = load_knowledge(self.settings.document_path, request["question"], budget=context_chars // 2)
        relevant_schema = select_schema(schema, request["question"], knowledge["text"], max_chars=context_chars // 2)
        context = {"question": request["question"], "history": job.get("history", [])[-4:],
                   "business_document": knowledge["text"], "document_is_excerpt": knowledge["partial"],
                   "schema": relevant_schema, "current_time_utc": datetime.now(timezone.utc).isoformat(),
                   "account_timezone": job.get("timezone", "Unknown; ask for timezone if needed")}
        # Only reuse standalone, explicitly time-independent questions. Cache SQL,
        # never result rows. Revalidate and execute with current NetSuite permissions.
        temporal = r"\b(today|yesterday|tomorrow|now|current|recent|latest|last|next|this|ago|date|time|day|week|month|year|quarter|since|before|after|between|as of)\b|\d"
        cacheable = not context["history"] and not re.search(temporal, request["question"], re.I)
        cache_key = hashlib.sha256(json.dumps({
            "question": request["question"], "document": knowledge["version"],
            "schema": schema.version, "refresh": schema.refreshed_at,
            "timezone": context["account_timezone"], "model": self.settings.model,
            "account": self.settings.account, "endpoint": self.settings.restlet_url,
            "client": self.settings.client_id, "worker": self.settings.worker_id,
        }, sort_keys=True).encode()).hexdigest()
        cached = self.plan_cache.get(cache_key) if cacheable else None
        if cached and time.monotonic() - cached[0] > 300:
            cached = None
        cache_hit = cached is not None
        ollama_metrics = []
        last_error = None
        model_seconds = 0.0
        query_seconds = 0.0
        for attempt in range(self.settings.max_attempts):
            context["previous_error"] = last_error
            try:
                model_start = time.monotonic()
                try:
                    if cached is not None:
                        plan = Plan.model_validate(cached[1])
                        cached = None
                    else:
                        plan = await self.llm.plan(context)
                        ollama_metrics.append(dict(getattr(self.llm, "last_metrics", {})))
                finally:
                    model_seconds += time.monotonic() - model_start
            except (ValidationError, json.JSONDecodeError):
                last_error = "Previous model output was invalid. Return only JSON matching the requested schema."
                self.store.audit(job["id"], "model_output_retry")
                continue
            if plan.kind != "query":
                return {"kind": plan.kind, "message": plan.explanation, "document_version": knowledge["version"],
                        "schema_version": schema.version}
            try:
                sql = validate_sql(plan.sql, schema)
                query_start = time.monotonic()
                try:
                    response = await self.ns.call("query", job=job["id"], lease=job["lease"], sql=sql, page=0,
                                                  record_links=record_link_columns(sql))
                finally:
                    query_seconds += time.monotonic() - query_start
                result = response["result"]
                # Date functions/literals and dynamic business rules must be replanned.
                dynamic = re.search(r"date|time|sysdate|systimestamp|current|interval|'", sql, re.I)
                if cacheable and not dynamic:
                    if len(self.plan_cache) >= 128:
                        self.plan_cache.pop(next(iter(self.plan_cache)))
                    self.plan_cache[cache_key] = (time.monotonic(), plan.model_dump())
                return {"kind": "result", "interpretation": plan.explanation, "definitions": plan.definitions,
                        "sql": sql, **result, "document_version": knowledge["version"],
                        "schema_version": schema.version, "schema_partial": bool(schema.failures),
                        "queried_at": datetime.now(timezone.utc).isoformat(),
                        "elapsed_seconds": round(time.monotonic() - start, 2), "attempts": attempt + 1,
                        "plan_cache_hit": cache_hit, "ollama": ollama_metrics,
                        "context_chars": {"schema": len(relevant_schema), "business_document": len(knowledge["text"])},
                        "timings": {"model_seconds": round(model_seconds, 2), "netsuite_seconds": round(query_seconds, 2)}}
            except (QueryRejected, NetSuiteError) as exc:
                self.plan_cache.pop(cache_key, None)
                last_error = str(exc)[:1500]
                self.store.audit(job["id"], "query_retry")
        raise ValueError("Could not produce a valid query after bounded retries. " + (last_error or ""))

    async def process(self, job):
        cached = self.store.cached_result(job["id"])
        if cached is not None:
            return cached
        self.store.audit(job["id"], "started")
        try:
            # Bounded even when an inference server never finishes generation.
            async with asyncio.timeout(self.settings.inference_timeout * self.settings.max_attempts + 180):
                result = await self.answer(job)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Do not forward arbitrary HTTP exception representations (URLs/headers) to the UI.
            message = str(exc)[:1700] if isinstance(exc, (ValueError, NetSuiteError)) else "Processing failed; check local health and connectivity."
            result = {"kind": "error", "message": message}
        if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > 90000:
            result = {"kind": "error", "message": "The answer exceeds the result storage limit. Select fewer fields or narrow the question."}
        self.store.complete(job["id"], result)
        self.store.audit(job["id"], result["kind"])
        return result
