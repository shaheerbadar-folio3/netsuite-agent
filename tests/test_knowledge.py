import pytest
from agent.knowledge import load_knowledge, KnowledgeError


def test_changes_are_used_without_restart(settings):
    first = load_knowledge(settings.document_path, "signup")
    settings.document_path.write_text("# Acme\nSignup now means custentity_registration_date")
    second = load_knowledge(settings.document_path, "signup")
    assert first["version"] != second["version"]
    assert "custentity_registration_date" in second["text"]


def test_unconfigured_business_rules_fail_closed(settings):
    settings.document_path.write_text("BUSINESS_DOCUMENT_NOT_CONFIGURED")
    with pytest.raises(KnowledgeError):
        load_knowledge(settings.document_path, "customers")


def test_mandatory_rules_never_silently_truncated(settings):
    settings.document_path.write_text("# Acme\n## Core rules\n" + "x" * 10000)
    with pytest.raises(KnowledgeError):
        load_knowledge(settings.document_path, "customers", budget=1000)


def test_retrieves_relevant_sections(settings):
    settings.document_path.write_text("# Acme\n## Core rules\nNever guess.\n## Customers\nActive customers use status A.\n## Inventory\n" + "sku " * 1000)
    result = load_knowledge(settings.document_path, "active customers", budget=500)
    assert "Active customers" in result["text"]
    assert "Never guess" in result["text"]
    assert result["partial"]


def test_small_document_excludes_unrelated_sections(settings):
    settings.document_path.write_text("# Acme\n## Core rules\nNever guess.\n## Customers\nCount customer records.\n## Returns\nReturns mean physical receipt.")
    result=load_knowledge(settings.document_path,"Count customers")
    assert "Count customer records" in result["text"]
    assert "physical receipt" not in result["text"]


def test_schema_focus_uses_plural_table_names(schema):
    from agent.knowledge import select_schema
    result=select_schema(schema,"Show customers","",max_chars=5000)
    assert "customer(" in result
    assert "transaction(" not in result
