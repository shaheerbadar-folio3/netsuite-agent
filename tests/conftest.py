import time
import pytest
from agent.config import Settings
from agent.models import Schema
from agent.store import Store


@pytest.fixture
def schema():
    return Schema(version="test-schema", refreshed_at=time.time(), tables=[
        {"name": "customer", "fields": [{"name": n} for n in ["id", "companyname", "datecreated", "status"]]},
        {"name": "transaction", "fields": [{"name": n} for n in ["id", "entity", "trandate", "type", "total"]]},
        {"name": "customrecord_membership", "fields": [{"name": "id"}, {"name": "custrecord_member"}]},
    ])


@pytest.fixture
def settings(tmp_path):
    doc = tmp_path / "business.md"
    doc.write_text("# Acme\n## Core rules\nCustomers are customer records.\n## Signup\nSignup means datecreated.\n")
    return Settings(_env_file=None, document_path=doc, data_dir=tmp_path / "data", management_token="x" * 40)


@pytest.fixture
def store(settings, schema):
    value = Store(settings.data_dir)
    value.put("schema", schema.model_dump())
    yield value
    value.close()
