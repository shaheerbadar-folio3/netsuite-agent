import pytest
from agent.sql import validate_sql, QueryRejected, record_link_columns


@pytest.mark.parametrize("sql", [
    "SELECT c.id, c.companyname FROM customer c ORDER BY c.id",
    "SELECT COUNT(*) AS total FROM customer ORDER BY COUNT(*)",
    "SELECT c.id FROM customer c WHERE c.datecreated >= TO_DATE('2026-08-01','YYYY-MM-DD') ORDER BY c.id",
    "SELECT c.id, COUNT(t.id) AS orders FROM customer c LEFT JOIN transaction t ON t.entity=c.id GROUP BY c.id ORDER BY c.id",
    "WITH c AS (SELECT id FROM customer) SELECT id FROM c ORDER BY id",
    "SELECT BUILTIN.DF(c.status) AS status FROM customer c ORDER BY c.id",
    "SELECT id FROM customrecord_membership ORDER BY id",
    "SELECT id FROM customer WHERE companyname='DROP UPDATE; --' ORDER BY id",
])
def test_safe_queries(sql, schema):
    assert validate_sql(sql, schema) == sql


@pytest.mark.parametrize("sql", [
    "DELETE FROM customer",
    "SELECT id FROM customer ORDER BY id; DELETE FROM customer",
    "SELECT id INTO stolen FROM customer ORDER BY id",
    "SELECT * FROM customer ORDER BY id",
    "SELECT password FROM customer ORDER BY id",
    "SELECT id FROM unknown ORDER BY id",
    "SELECT c.id FROM customer c CROSS JOIN transaction t ORDER BY c.id",
    "SELECT id FROM customer",
    "SELECT id FROM customer ORDER BY id FOR UPDATE",
    "SELECT id FROM customer ORDER BY id -- hidden",
    "SELECT id FROM other.customer ORDER BY id",
    "SELECT evil(id) FROM customer ORDER BY id",
    "SELECT id FROM customer ORDER BY id FETCH FIRST 1 ROWS ONLY",
    "SELECT c.id, t.id FROM customer c JOIN transaction t ON t.entity = c.id ORDER BY c.id, t.id",
])
def test_rejects_unsafe_queries(sql, schema):
    with pytest.raises(QueryRejected):
        validate_sql(sql, schema)


def test_links_only_for_proven_identity_columns():
    assert record_link_columns("SELECT c.id AS customer_id FROM customer c ORDER BY c.id") == [
        {"column": "customer_id", "record_type": "customer"}]
    assert record_link_columns("SELECT COUNT(id) AS id FROM customer ORDER BY id") == []
    assert record_link_columns("SELECT id FROM transaction ORDER BY id") == []
    assert record_link_columns("SELECT t.id AS order_id, t.type AS order_type FROM transaction t ORDER BY t.id") == [
        {"column": "order_id", "record_type": "transaction", "type_column": "order_type"}]


def test_aggregate_order_alias_is_expanded(schema):
    assert validate_sql("SELECT COUNT(*) AS total FROM customer ORDER BY total", schema) == "SELECT COUNT(*) AS total FROM customer ORDER BY COUNT(*)"
    sql = "SELECT c.id, COUNT(t.id) AS orders FROM customer c LEFT JOIN transaction t ON t.entity=c.id GROUP BY c.id ORDER BY orders DESC, c.id"
    output = validate_sql(sql, schema)
    assert "ORDER BY COUNT(t.id) DESC" in output
    assert output.endswith(", c.id")
    assert validate_sql(output, schema) == output


def test_detail_alias_order_is_not_rewritten(schema):
    sql = "SELECT id AS customer_id FROM customer ORDER BY customer_id"
    assert validate_sql(sql, schema) == sql


def test_scalar_aggregate_without_order_is_normalized(schema):
    assert validate_sql("SELECT COUNT(*) AS n FROM customer",schema) == "SELECT COUNT(*) AS n FROM customer ORDER BY COUNT(*)"
    with pytest.raises(QueryRejected):
        validate_sql("SELECT id, COUNT(*) AS n FROM customer GROUP BY id",schema)
