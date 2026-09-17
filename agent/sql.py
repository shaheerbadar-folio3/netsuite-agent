import re
import sqlglot
from sqlglot import exp
from sqlglot.optimizer.qualify import qualify


class QueryRejected(ValueError):
    pass


def validate_sql(sql, schema):
    if not sql.strip() or len(sql) > 16000:
        raise QueryRejected("Query is empty or too long")
    try:
        statements = sqlglot.parse(sql, read="oracle")
    except sqlglot.errors.ParseError as exc:
        raise QueryRejected("Invalid Oracle SQL syntax") from exc
    if len(statements) != 1 or not isinstance(statements[0], (exp.Select, exp.Union, exp.Intersect, exp.Except)):
        raise QueryRejected("Exactly one read-only SELECT query is required")
    tree = statements[0]
    blocked = {"Insert", "Update", "Delete", "Create", "Drop", "Alter", "Command", "Merge", "Into",
               "Lock", "Transaction", "Grant", "Revoke", "Copy", "Execute", "Use"}
    if any(type(node).__name__ in blocked for node in tree.walk()):
        raise QueryRejected("Only read queries are allowed")
    if any(node.comments for node in tree.walk()):
        raise QueryRejected("SQL comments are not allowed")
    if any(t.db or t.catalog for t in tree.find_all(exp.Table)):
        raise QueryRejected("Cross-schema references are not allowed")
    if tree.find(exp.Offset) or tree.find(exp.Limit) or tree.find(exp.Fetch):
        raise QueryRejected("Use the application's pagination rather than SQL LIMIT/OFFSET/FETCH")
    scalar_order_added = False
    if isinstance(tree, exp.Select) and not tree.args.get("order") and not tree.args.get("group"):
        projections = [item.this if isinstance(item, exp.Alias) else item for item in tree.expressions]
        if projections and all(isinstance(item, exp.AggFunc) for item in projections):
            tree.set("order", exp.Order(expressions=[exp.Ordered(this=projections[0].copy(), nulls_first=False)]))
            scalar_order_added = True
    if not tree.args.get("order"):
        raise QueryRejected("Include ORDER BY with a unique tie-breaker for stable pagination")
    for join in tree.find_all(exp.Join):
        if str(join.args.get("kind", "")).upper() == "CROSS" or not (join.args.get("on") or join.args.get("using")):
            raise QueryRejected("Every join needs an explicit join condition")
    # Reject arbitrary function calls; BUILTIN.DF is the one permitted NetSuite extension.
    functions = {"COUNT", "SUM", "AVG", "MIN", "MAX", "COALESCE", "NVL", "NVL2", "NULLIF", "ABS", "ROUND",
                 "TRUNC", "TRIM", "LTRIM", "RTRIM", "LOWER", "UPPER", "LENGTH", "SUBSTR", "SUBSTRING",
                 "TO_DATE", "TO_CHAR", "TO_NUMBER", "ADD_MONTHS", "MONTHS_BETWEEN", "LAST_DAY", "EXTRACT",
                 "CAST", "CONCAT", "REPLACE", "ROW_NUMBER", "RANK", "DENSE_RANK", "DF"}
    for function in tree.find_all(exp.Anonymous):
        if function.name.upper() not in functions:
            raise QueryRejected(f"Unsupported function: {function.name}")
    for dot in tree.find_all(exp.Dot):
        if not (isinstance(dot.this, exp.Identifier) and dot.this.name.upper() == "BUILTIN"
                and isinstance(dot.expression, exp.Anonymous) and dot.expression.name.upper() == "DF"):
            raise QueryRejected("Unsupported qualified function")
    mapping = {t.name.lower(): {f.name.lower(): "UNKNOWN" for f in t.fields} for t in schema.tables}
    if any(t.name.lower().startswith("customrecord_nsa_") for t in tree.find_all(exp.Table)):
        raise QueryRejected("Agent job storage is not a business query source")
    ctes = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    for table in tree.find_all(exp.Table):
        if table.name.lower() not in mapping and table.name.lower() not in ctes:
            raise QueryRejected(f"Unknown table: {table.name}; refresh the schema")
    for star in tree.find_all(exp.Star):
        if not isinstance(star.parent, exp.Count):
            raise QueryRejected("Select explicit columns instead of SELECT *")
    for select in tree.find_all(exp.Select):
        outputs = [e.alias_or_name.lower() for e in select.expressions]
        if any(not name for name in outputs):
            raise QueryRejected("Every selected expression must have an explicit output alias")
        if len(set(outputs)) != len(outputs):
            raise QueryRejected("Selected columns must have unique names; alias repeated field names")
    try:
        qualify(tree.copy(), dialect="oracle", schema=mapping, validate_qualify_columns=True,
                identify=False, quote_identifiers=False)
    except (sqlglot.errors.OptimizeError, sqlglot.errors.SchemaError) as exc:
        raise QueryRejected("Column validation failed: " + str(exc)[:400]) from exc
    # NetSuite can reject ORDER BY aliases of aggregate expressions even though
    # the equivalent ORDER BY aggregate expression succeeds. Normalize only that
    # case, within each SELECT scope; never substitute across subqueries/unions.
    changed = scalar_order_added
    for select in tree.find_all(exp.Select):
        aliases = {
            item.alias.lower(): item.this
            for item in select.expressions
            if isinstance(item, exp.Alias)
            and item.this.find(exp.AggFunc)
            and not item.this.find(exp.Subquery)
            and not item.this.find(exp.Window)
        }
        order = select.args.get("order")
        if not order:
            continue
        for ordered in order.expressions:
            column = ordered.this
            if isinstance(column, exp.Column) and not column.table and column.name.lower() in aliases:
                ordered.set("this", aliases[column.name.lower()].copy())
                changed = True
    if changed:
        return tree.sql(dialect="oracle")
    # Preserve the original SuiteQL text when no compatibility rewrite is needed.
    return sql.strip().rstrip(";")


def record_link_columns(sql):
    """Only link directly selected primary IDs from unambiguous base tables."""
    tree = sqlglot.parse_one(sql, read="oracle")
    if not isinstance(tree, exp.Select) or tree.find(exp.CTE) or tree.find(exp.Subquery):
        return []
    tables = {t.alias_or_name.lower(): t.name.lower() for t in tree.find_all(exp.Table)}
    allowed = {"customer", "vendor", "employee", "contact", "subsidiary", "department", "location"}
    result = []
    for expression in tree.expressions:
        column = expression.this if isinstance(expression, exp.Alias) else expression
        if not isinstance(column, exp.Column) or column.name.lower() != "id":
            continue
        table = tables.get(column.table.lower()) if column.table else next(iter(tables.values())) if len(tables) == 1 else None
        if table == "transaction":
            for other in tree.expressions:
                target = other.this if isinstance(other, exp.Alias) else other
                if isinstance(target, exp.Column) and target.name.lower() == "type" and target.table.lower() == column.table.lower():
                    result.append({"column": expression.alias_or_name.lower(), "record_type": "transaction",
                                   "type_column": other.alias_or_name.lower()})
                    break
        elif table in allowed:
            result.append({"column": expression.alias_or_name.lower(), "record_type": table})
    return result
