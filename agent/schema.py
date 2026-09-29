"""Account schema inventory and metadata probes.

Routine refreshes reuse the last good snapshot: they re-probe core tables, newly
appeared inventory names, and prior failures. A full rescan runs on a longer
interval or when explicitly requested. Probe batches may run with limited
concurrency; the previous snapshot survives a total failure.
"""
import asyncio
import hashlib
import json
import logging
import time

import httpx

from .models import Schema, TableInfo

log = logging.getLogger(__name__)

# Always ensure this SuiteQL line table is considered even when N/query.Type omits it.
ALWAYS_PROBE = ("transactionline",)

DEFAULT_CORE_TABLES = (
    "customer", "vendor", "transaction", "transactionline", "item", "employee",
    "account", "subsidiary", "contact", "department", "location", "classification",
    "currency", "entity", "partner", "job",
)

# Hard ceiling must stay aligned with the RESTlet probe guard.
MAX_PROBE_BATCH = 12


def _setting(settings, name, default):
    if settings is None:
        return default
    return getattr(settings, name, default)


def core_table_names(settings=None):
    raw = _setting(settings, "schema_core_tables", "")
    names = [part.strip().lower() for part in str(raw).split(",") if part.strip()]
    return tuple(names) if names else DEFAULT_CORE_TABLES


def inventory_names(inventory):
    names = list(dict.fromkeys([*(inventory.get("tables") or []), *ALWAYS_PROBE]))
    return [n.lower() for n in names if isinstance(n, str) and n]


def inventory_digest(names):
    return hashlib.sha256("\n".join(names).encode()).hexdigest()[:16]


def select_probe_targets(names, previous, settings=None, *, force_full=False, probe_only=None):
    """Return (tables_to_probe, full_scan) for this refresh."""
    if probe_only:
        wanted = [str(n).lower() for n in probe_only if str(n).strip()]
        return list(dict.fromkeys(wanted)), False
    inventory = list(dict.fromkeys(names))
    inventory_set = set(inventory)
    previous = previous or {}
    cached = {t["name"].lower() for t in previous.get("tables", []) if isinstance(t, dict) and t.get("name")}
    failed = {
        str(f.get("table", "")).lower()
        for f in previous.get("failures", [])
        if isinstance(f, dict) and f.get("table")
    }
    last_full = float(previous.get("full_refreshed_at") or 0)
    full_every = int(_setting(settings, "schema_full_refresh_seconds", 86400))
    need_full = force_full or not cached or (time.time() - last_full >= full_every)
    if need_full:
        return inventory, True

    core = {n for n in core_table_names(settings) if n in inventory_set}
    new = inventory_set - cached
    retry = failed & inventory_set
    return sorted(core | new | retry), False


async def _probe_one_batch(ns, tables):
    return await ns.call("schema_probe", tables=tables)


async def probe_tables(ns, store, names, settings=None, *, started=None):
    """Probe metadata for ``names`` in sized batches with optional concurrency."""
    started = started or time.time()
    batch_size = min(MAX_PROBE_BATCH, max(1, int(_setting(settings, "schema_probe_batch_size", 12))))
    concurrency = max(1, int(_setting(settings, "schema_probe_concurrency", 2)))
    tables, failures = [], []
    completed = 0
    total = len(names)
    batches = [names[offset:offset + batch_size] for offset in range(0, len(names), batch_size)]
    lock = asyncio.Lock()

    def status(**extra):
        store.put("schema_status", {
            "ok": False, "state": "probing", "last_attempt": started,
            "completed_tables": completed, "total_tables": total,
            "tables": len(tables), "failed_tables": len(failures), **extra,
        })

    async def run_batch(current):
        nonlocal completed
        async with lock:
            status(current_tables=current)
        try:
            batch = await _probe_one_batch(ns, current)
        except httpx.TimeoutException:
            log.warning("Schema batch timed out; probing individually: %s", current)
            for name in current:
                async with lock:
                    status(current_tables=[name])
                try:
                    piece = await _probe_one_batch(ns, [name])
                except httpx.TimeoutException as exc:
                    piece = {"tables": [], "failures": [{
                        "table": name, "code": type(exc).__name__,
                        "message": "Metadata probe timed out; retry on the next schema refresh.",
                    }]}
                async with lock:
                    tables.extend(TableInfo.model_validate(t) for t in piece.get("tables", []))
                    failures.extend(piece.get("failures", []))
                    completed += 1
                    status(current_tables=[])
            return
        async with lock:
            tables.extend(TableInfo.model_validate(t) for t in batch.get("tables", []))
            failures.extend(batch.get("failures", []))
            completed += len(current)
            status(current_tables=[])

    if not batches:
        return tables, failures

    if concurrency == 1 or len(batches) == 1:
        for current in batches:
            await run_batch(current)
        return tables, failures

    semaphore = asyncio.Semaphore(concurrency)

    async def guarded(current):
        async with semaphore:
            await run_batch(current)

    await asyncio.gather(*(guarded(batch) for batch in batches))
    return tables, failures


def merge_schema(previous, inventory, probed_tables, probed_failures, *, full_scan, started_full_at=None):
    """Combine cached metadata with this refresh's probe results."""
    inventory_set = set(inventory)
    cached = {}
    if previous and not full_scan:
        for raw in previous.get("tables", []):
            try:
                info = TableInfo.model_validate(raw)
            except Exception:
                continue
            if info.name.lower() in inventory_set:
                cached[info.name.lower()] = info

    probed_ok = set()
    for info in probed_tables:
        key = info.name.lower()
        probed_ok.add(key)
        cached[key] = info

    failure_by_table = {}
    for item in probed_failures:
        table = str(item.get("table", "")).lower()
        if not table or table in probed_ok:
            continue
        # Incremental: keep last successful columns when a re-probe fails.
        if table in cached and not full_scan:
            continue
        failure_by_table[table] = item

    if not full_scan and previous:
        for item in previous.get("failures", []):
            table = str(item.get("table", "")).lower()
            if table and table in inventory_set and table not in cached and table not in failure_by_table:
                failure_by_table[table] = item

    tables = [cached[name] for name in sorted(cached)]
    failures = [failure_by_table[name] for name in sorted(failure_by_table)]
    full_at = time.time() if full_scan else float((previous or {}).get("full_refreshed_at") or started_full_at or 0)
    return tables, failures, full_at


async def refresh_schema(ns, store, settings=None, *, force_full=False, probe_only=None):
    started = time.time()
    previous = store.get("schema") or {}
    store.put("schema_status", {
        "ok": False, "state": "inventory", "last_attempt": started,
        "mode": "full" if force_full else ("targeted" if probe_only else "auto"),
    })
    inventory = await ns.call("schema_inventory")
    names = inventory_names(inventory)
    if probe_only:
        for name in probe_only:
            key = str(name).lower()
            if key not in names:
                names.append(key)
    if not names:
        raise ValueError("NetSuite returned no schema inventory")

    digest = inventory_digest(names)
    targets, full_scan = select_probe_targets(
        names, previous, settings, force_full=force_full, probe_only=probe_only)
    store.put("schema_status", {
        "ok": False, "state": "probing", "last_attempt": started,
        "mode": "full" if full_scan else ("targeted" if probe_only else "incremental"),
        "inventory_tables": len(names), "probe_targets": len(targets),
        "inventory_digest": digest,
    })

    if not targets and previous.get("tables"):
        # Inventory unchanged enough that nothing needs probing; keep columns, freshen timestamp.
        tables = [TableInfo.model_validate(t) for t in previous["tables"] if t.get("name", "").lower() in set(names)]
        failures = [
            f for f in previous.get("failures", [])
            if str(f.get("table", "")).lower() in set(names)
        ]
        full_at = float(previous.get("full_refreshed_at") or 0)
        probed_tables, probed_failures = [], []
        reused = len(tables)
    else:
        probed_tables, probed_failures = await probe_tables(
            ns, store, targets, settings, started=started)
        tables, failures, full_at = merge_schema(
            previous, names, probed_tables, probed_failures, full_scan=full_scan)
        reused = max(0, len(tables) - len(probed_tables))

    if not tables:
        raise ValueError(
            "Schema discovery found no usable column metadata. "
            "Check the integration role and N/query metadata in sandbox."
        )

    serialized = [t.model_dump() for t in sorted(tables, key=lambda t: t.name)]
    version = hashlib.sha256(json.dumps(serialized, sort_keys=True).encode()).hexdigest()[:16]
    schema = Schema(
        version=version, refreshed_at=time.time(), tables=tables, failures=failures,
    )
    payload = schema.model_dump()
    payload["full_refreshed_at"] = full_at
    payload["inventory_digest"] = digest
    payload["refresh_mode"] = "full" if full_scan else "incremental"
    store.put("schema", payload)
    store.put("schema_status", {
        "ok": True, "state": "ready", "version": version,
        "tables": len(tables), "failed_tables": len(failures),
        "refreshed_at": schema.refreshed_at, "full_refreshed_at": full_at,
        "mode": "full" if full_scan else "incremental",
        "probed_tables": len(probed_tables), "reused_tables": reused,
        "inventory_tables": len(names), "inventory_digest": digest,
    })
    return schema
