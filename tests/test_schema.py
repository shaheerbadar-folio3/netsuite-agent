import time

import httpx
import pytest

from agent.schema import refresh_schema, select_probe_targets


class NS:
    def __init__(self):
        self.extra = False

    async def call(self, action, **kwargs):
        if action == "schema_inventory":
            return {"tables": ["customer"]}
        fields = [{"name": "id"}]
        if self.extra:
            fields.append({"name": "custentity_new"})
        return {"tables": [{"name": "customer", "fields": fields}], "failures": []}


async def test_new_field_changes_schema_version(store):
    ns = NS()
    before = await refresh_schema(ns, store, force_full=True)
    ns.extra = True
    after = await refresh_schema(ns, store, force_full=True)
    assert before.version != after.version
    assert after.tables[0].fields[-1].name == "custentity_new"


async def test_total_failure_preserves_last_snapshot(store):
    class Broken:
        async def call(self, action, **kwargs):
            return {"tables": ["customer"]} if action == "schema_inventory" else {"tables": [], "failures": []}

    previous = store.get("schema")
    with pytest.raises(ValueError):
        await refresh_schema(Broken(), store, force_full=True)
    assert store.get("schema") == previous


async def test_line_table_is_probed_when_missing_from_inventory(store):
    class LineNS:
        async def call(self, action, **kwargs):
            if action == "schema_inventory":
                return {"tables": ["customer"]}
            assert "transactionline" in kwargs["tables"]
            return {"tables": [{"name": "transactionline", "fields": [{"name": "transaction"}, {"name": "item"}]}],
                    "failures": []}

    schema = await refresh_schema(LineNS(), store, force_full=True)
    assert any(t.name == "transactionline" for t in schema.tables)


async def test_timeout_isolated_and_progress_reported(store, settings):
    calls = []

    class SlowNS:
        async def call(self, action, **kwargs):
            if action == "schema_inventory":
                return {"tables": ["customer", "slow"]}
            names = kwargs["tables"]
            calls.append(names)
            status = store.get("schema_status")
            assert status["current_tables"] == names
            assert status["total_tables"] == 3
            if len(names) > 1 or names == ["slow"]:
                raise httpx.ReadTimeout("timeout")
            return {"tables": [{"name": names[0], "fields": [{"name": "id"}]}]}

    settings.schema_probe_concurrency = 1
    settings.schema_probe_batch_size = 12
    schema = await refresh_schema(SlowNS(), store, settings, force_full=True)
    assert calls[0] == ["customer", "slow", "transactionline"]
    assert ["customer"] in calls and ["slow"] in calls and ["transactionline"] in calls
    assert {t.name for t in schema.tables} == {"customer", "transactionline"}
    assert schema.failures[0]["table"] == "slow"
    assert store.get("schema_status")["ok"] is True
    assert store.get("schema_status")["failed_tables"] == 1


async def test_non_timeout_errors_are_not_retried(store):
    calls = []

    class BrokenNS:
        async def call(self, action, **kwargs):
            calls.append(action)
            if action == "schema_inventory":
                return {"tables": ["customer"]}
            raise RuntimeError("authentication rejected")

    store.put("schema", {"version": "previous"})
    with pytest.raises(RuntimeError):
        await refresh_schema(BrokenNS(), store, force_full=True)
    assert calls == ["schema_inventory", "schema_probe"]
    assert store.get("schema") == {"version": "previous"}


async def test_incremental_refresh_reuses_cached_tables(store, settings):
    probed = []

    class Tracking:
        async def call(self, action, **kwargs):
            if action == "schema_inventory":
                return {"tables": ["customer", "vendor", "obscure_custom"]}
            probed.extend(kwargs["tables"])
            return {
                "tables": [{"name": n, "fields": [{"name": "id"}]} for n in kwargs["tables"]],
                "failures": [],
            }

    settings.schema_core_tables = "customer,transactionline"
    settings.schema_full_refresh_seconds = 86400
    settings.schema_probe_concurrency = 1
    first = await refresh_schema(Tracking(), store, settings, force_full=True)
    assert set(probed) == {"customer", "vendor", "obscure_custom", "transactionline"}
    probed.clear()
    payload = store.get("schema")
    payload["full_refreshed_at"] = time.time()
    store.put("schema", payload)

    second = await refresh_schema(Tracking(), store, settings, force_full=False)
    assert set(probed) == {"customer", "transactionline"}
    assert {t.name for t in second.tables} >= {"customer", "vendor", "obscure_custom", "transactionline"}
    assert store.get("schema_status")["mode"] == "incremental"
    assert store.get("schema_status")["reused_tables"] >= 2
    assert first.version


async def test_incremental_probes_new_and_failed_tables(store, settings):
    store.put("schema", {
        "version": "old", "refreshed_at": time.time(), "full_refreshed_at": time.time(),
        "tables": [{"name": "customer", "fields": [{"name": "id"}]}],
        "failures": [{"table": "slow", "code": "Timeout", "message": "x"}],
    })
    settings.schema_core_tables = "customer"
    settings.schema_full_refresh_seconds = 86400
    names = ["customer", "slow", "brand_new", "transactionline"]
    targets, full = select_probe_targets(names, store.get("schema"), settings, force_full=False)
    assert full is False
    assert set(targets) == {"customer", "slow", "brand_new", "transactionline"}


async def test_failed_core_probe_keeps_previous_columns(store, settings):
    store.put("schema", {
        "version": "old", "refreshed_at": time.time(), "full_refreshed_at": time.time(),
        "tables": [{"name": "customer", "fields": [{"name": "id"}, {"name": "companyname"}]}],
        "failures": [],
    })

    class FailCustomer:
        async def call(self, action, **kwargs):
            if action == "schema_inventory":
                return {"tables": ["customer"]}
            return {"tables": [], "failures": [{"table": "customer", "code": "BUSY", "message": "try later"}]}

    settings.schema_core_tables = "customer"
    settings.schema_full_refresh_seconds = 86400
    settings.schema_probe_concurrency = 1
    schema = await refresh_schema(FailCustomer(), store, settings, force_full=False)
    customer = next(t for t in schema.tables if t.name == "customer")
    assert [f.name for f in customer.fields] == ["id", "companyname"]
