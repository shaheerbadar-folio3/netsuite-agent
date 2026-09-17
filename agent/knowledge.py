import hashlib
import re


class KnowledgeError(ValueError):
    pass


def terms(text):
    words = set(re.findall(r"[a-z][a-z0-9_]{2,}", text.lower()))
    return words | {w[:-1] for w in words if w.endswith("s") and len(w) > 3}


def load_knowledge(path, question, budget=12000):
    if not path.is_file():
        raise KnowledgeError("Business document is missing")
    raw = path.read_text(encoding="utf-8")
    if not raw.strip() or len(raw) > 2_000_000:
        raise KnowledgeError("Business document must contain 1–2,000,000 characters")
    if "BUSINESS_DOCUMENT_NOT_CONFIGURED" in raw:
        raise KnowledgeError("Complete knowledge/business.md and remove BUSINESS_DOCUMENT_NOT_CONFIGURED")
    version = hashlib.sha256(raw.encode()).hexdigest()[:16]
    if "## " not in raw and len(raw) <= budget:
        return {"version": version, "text": raw, "partial": False}
    sections = re.split(r"(?m)(?=^## )", raw)
    mandatory = [s for i, s in enumerate(sections) if i == 0 or s.startswith("## Core rules")]
    chosen = "\n".join(mandatory)
    if len(chosen) > budget // 2:
        raise KnowledgeError("Core business rules exceed the context budget; shorten the mandatory sections")
    ranked = sorted((s for s in sections if s not in mandatory),
                    key=lambda s: len(terms(s) & terms(question)), reverse=True)
    for section in ranked:
        if not (terms(section.split("\n", 1)[0]) & terms(question)) and not (terms(section) & (terms(question) - {"all", "the", "count", "show", "record", "records"})):
            continue
        if len(chosen) + len(section) + 2 <= budget:
            chosen += "\n\n" + section
    return {"version": version, "text": chosen, "partial": True}


def select_schema(schema, question, knowledge, max_chars=14000):
    question_terms = terms(question)
    scores = terms(question + " " + knowledge)
    ranked = sorted(schema.tables, key=lambda t: (
        100 * (t.name.lower() in question_terms) + 20 * (t.name.lower() in scores) + len(terms(t.name + " " + " ".join(
            f.name + " " + f.label for f in t.fields)) & scores)), reverse=True)
    selected = []
    size = 0
    # Prefer explicitly mentioned tables and those referenced by selected definitions.
    relevant = [t for t in ranked if t.name.lower() in scores]
    candidates = relevant if relevant else ranked[:3]
    for table in candidates[:6]:
        # Large transaction schemas must not crowd out the relevant table entirely.
        fields = sorted(table.fields, key=lambda f: (
            100 if f.name.lower() == "id" else 0) + len(terms(f.name + " " + f.label) & scores) * 10,
            reverse=True)
        per_table = min(3000, max_chars - size)
        included = []
        for field in fields:
            # Keep identity/relationship columns and question/definition matches.
            # If no semantic match exists, retain a bounded sample for clarification.
            important = field.name.lower() in {"id", "entityid", "companyname", "type", "entity", "subsidiary", "parent"} or bool(terms(field.name + " " + field.label) & scores)
            if not important and len(included) >= 12:
                continue
            item = field.name + (":" + field.type if field.type else "")
            if sum(len(x) + 2 for x in included) + len(item) + len(table.name) + 3 <= per_table:
                included.append(item)
        if not included:
            continue
        text = table.name + "(" + ", ".join(included) + ")"
        if size + len(text) <= max_chars:
            selected.append(text)
            size += len(text)
    if not selected:
        raise KnowledgeError("No table fits the schema context budget")
    return "\n".join(selected)
