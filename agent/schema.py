import hashlib
import json
import time

from .models import Schema, TableInfo


async def refresh_schema(ns, store):
    inventory = await ns.call("schema_inventory")
    # N/query.Type does not enumerate every SuiteQL line table. Probe this
    # documented table explicitly; only successful metadata enters the schema.
    names = list(dict.fromkeys([*inventory["tables"], "transactionline"]))
    if not names:
        raise ValueError("NetSuite returned no schema inventory")
    tables, failures = [], []
    for offset in range(0, len(names), 6):
        batch = await ns.call("schema_probe", tables=names[offset:offset + 6])
        tables.extend(TableInfo.model_validate(t) for t in batch["tables"])
        failures.extend(batch.get("failures", []))
    if not tables:
        raise ValueError("Schema discovery found no usable column metadata. Check the integration role and N/query metadata in sandbox.")
    serialized = [t.model_dump() for t in sorted(tables, key=lambda t: t.name)]
    version = hashlib.sha256(json.dumps(serialized, sort_keys=True).encode()).hexdigest()[:16]
    schema = Schema(version=version, refreshed_at=time.time(), tables=tables, failures=failures)
    store.put("schema", schema.model_dump())
    store.put("schema_status", {"ok": True, "version": version, "tables": len(tables),
                               "failed_tables": len(failures), "refreshed_at": schema.refreshed_at})
    return schema
