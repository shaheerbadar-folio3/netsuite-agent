import pytest
from agent.schema import refresh_schema


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
    before = await refresh_schema(ns, store)
    ns.extra = True
    after = await refresh_schema(ns, store)
    assert before.version != after.version
    assert after.tables[0].fields[-1].name == "custentity_new"


async def test_total_failure_preserves_last_snapshot(store):
    class Broken:
        async def call(self, action, **kwargs):
            return {"tables": ["customer"]} if action == "schema_inventory" else {"tables": [], "failures": []}
    previous = store.get("schema")
    with pytest.raises(ValueError):
        await refresh_schema(Broken(), store)
    assert store.get("schema") == previous


async def test_line_table_is_probed_when_missing_from_inventory(store):
    class LineNS:
        async def call(self, action, **kwargs):
            if action == 'schema_inventory': return {'tables':['customer']}
            assert 'transactionline' in kwargs['tables']
            return {'tables':[{'name':'transactionline','fields':[{'name':'transaction'},{'name':'item'}]}],'failures':[]}
    schema=await refresh_schema(LineNS(),store)
    assert schema.tables[0].name=='transactionline'
