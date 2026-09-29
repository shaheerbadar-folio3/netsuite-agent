import argparse
import asyncio
import json
import time

from .config import Settings
from .knowledge import load_knowledge
from .llm import Ollama
from .netsuite import NetSuite
from .store import Store
from .models import Schema
from .sql import validate_sql, QueryRejected
from pydantic import ValidationError


async def doctor(s):
    report = {}
    try:
        s.check_credentials()
        report["credentials"] = "configured"
    except ValueError as exc:
        report["credentials"] = str(exc)
    try:
        document = load_knowledge(s.document_path, "")
        report["document"] = {"ready": True, "version": document["version"]}
    except ValueError as exc:
        report["document"] = str(exc)
    llm = Ollama(s)
    try:
        report["ollama"] = await llm.health()
    except Exception as exc:
        report["ollama"] = {"ready": False, "error": type(exc).__name__}
    finally:
        await llm.close()
    report["creation"] = {
        "enabled": s.creation_enabled,
        "sdf_enabled": s.sdf_enabled,
        "sdf_auth_configured": bool(s.sdf_auth_id),
        "sdf_auth_id": s.sdf_auth_id or None,
        "suitecloud_bin": s.suitecloud_bin,
    }
    if s.creation_enabled and (not s.sdf_enabled or not s.sdf_auth_id):
        from .creation_fields import sdf_configuration_message
        report["creation"]["custom_types"] = "not ready"
        report["creation"]["custom_types_help"] = sdf_configuration_message(s)
    elif s.creation_enabled and s.sdf_enabled and s.sdf_auth_id:
        report["creation"]["custom_types"] = "configured"
    print(json.dumps(report, indent=2))


async def check_netsuite(s):
    ns = NetSuite(s)
    try:
        print(json.dumps(await ns.call("ping"), indent=2))
    finally:
        await ns.close()


async def benchmark(s):
    llm = Ollama(s)
    start = time.monotonic()
    try:
        context = {"question": "Count all customers", "schema": "customer(id, companyname)",
                   "business_document": "Customers means every customer record."}
        schema = Schema(version="synthetic-benchmark", refreshed_at=time.time(), tables=[
            {"name": "customer", "fields": [{"name": "id"}, {"name": "companyname"}]}])
        for attempt in range(s.max_attempts):
            try:
                plan = await llm.plan(context)
                if plan.kind != "query":
                    raise QueryRejected("This synthetic question is answerable; return a query plan")
                validate_sql(plan.sql, schema)
                break
            except (QueryRejected, ValidationError) as exc:
                context["previous_error"] = str(exc)[:1000]
        else:
            raise ValueError("Benchmark failed validation after bounded retries: " + context["previous_error"])
        print(json.dumps({"model": s.model, "seconds": round(time.monotonic() - start, 2),
                          "validated": True, "attempts": attempt + 1, "plan": plan.model_dump()}, indent=2))
    finally:
        await llm.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["serve", "doctor", "check-netsuite", "benchmark", "backup"])
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--output", default="agent-backup.sqlite3")
    args = parser.parse_args()
    s = Settings()
    if args.command == "serve":
        import uvicorn
        from .app import create_app
        uvicorn.run(create_app(s), host="127.0.0.1", port=args.port, access_log=False)
    elif args.command == "doctor":
        asyncio.run(doctor(s))
    elif args.command == "check-netsuite":
        asyncio.run(check_netsuite(s))
    elif args.command == "benchmark":
        asyncio.run(benchmark(s))
    elif args.command == "backup":
        import os
        import sqlite3
        store = Store(s.data_dir)
        fd = os.open(args.output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        with sqlite3.connect(args.output) as destination:
            store.db.backup(destination)
        store.close()
        print("Backup created. Protect it as business data.")


if __name__ == "__main__":
    main()
