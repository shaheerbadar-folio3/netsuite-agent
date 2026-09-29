"""Metadata-bounded intent extraction and deterministic draft compilation."""
import re
import math

BLOCKED = re.compile(r'^(id|internalid|externalid|customform|type|recordtype|baserecordtype|rectype|_eml_nkey_|nsapi.*|nluser|nlrole|nlsub|nldept|nlloc|entryformquerystring|sys_.*|.*password.*|giveaccess|accessrole|roles|emailpassword|requirepwdchange|isinactive|lastmodified.*|createddate|datecreated|balance|subsidiaryedition|nexus|total|subtotal|taxtotal|tax2total|shippingcost|discounttotal)$', re.I)
NUMERIC = {'integer', 'float', 'currency', 'percent', 'posinteger', 'nonneginteger', 'rate', 'ratehighprecision'}
_ID_ANNOTATION = re.compile(r'\(\s*internal\s+id\s*[:=#]?\s*(\d+)\s*\)', re.I)
# Keep Ollama structured-output grammars small: only fields the question cues.
FIELD_CUES = {
    'body.entity': r'\b(customer|vendor)\b',
    'body.subsidiary': r'\bsubsidiary\b',
    'body.currency': r'\bcurrency\b',
    'body.memo': r'\bmemo\b',
    'body.trandate': r'\b(date|trandate)\b',
    'body.companyname': r'\b(company|named|name|customer|vendor)\b',
    'body.firstname': r'\b(named|first\s*name|firstname)\b',
    'body.lastname': r'\b(named|last\s*name|lastname)\b',
    'body.entityid': r'\b(named|name|employee)\b',
    'body.email': r'\bemail\b',
    'body.phone': r'\bphone\b',
    'item.item': r'\bitem\b',
    'item.quantity': r'\b(quantity|qty)\b',
    'item.rate': r'\b(rate|price|amount)\b',
}

# Soft create phrasing ("want to create a new …") must skip the SuiteQL planner.
_CREATE = r'(?:(?:want|need|like)\s+to\s+|please\s+)?(?:create|add)\s+(?:a\s+|an\s+)?(?:new\s+)?'
RECORD_TYPE_CUES = (
    (rf'\b{_CREATE}sales\s*orders?\b', 'salesorder'),
    (rf'\b{_CREATE}purchase\s*orders?\b', 'purchaseorder'),
    (rf'\b{_CREATE}estimates?\b', 'estimate'),
    (rf'\b{_CREATE}invoices?\b', 'invoice'),
    (rf'\b{_CREATE}credit\s*memos?\b', 'creditmemo'),
    (rf'\b{_CREATE}vendors?\b', 'vendor'),
    (rf'\b{_CREATE}customers?\b', 'customer'),
    (rf'\b{_CREATE}contacts?\b', 'contact'),
    (rf'\b{_CREATE}employees?\b', 'employee'),
)


def _name_variants(name):
    """Tolerate trailing punctuation differences between extraction and the question."""
    text = str(name or '').strip()
    if not text:
        return []
    variants = [text]
    stripped = text.rstrip('.,;:')
    if stripped and stripped not in variants:
        variants.append(stripped)
    if stripped and (stripped + '.') not in variants:
        variants.append(stripped + '.')
    return variants


def parse_embedded_internal_id(value):
    """Return (bare_name_or_value, id_or_None) for 'Name (internal ID 12)' forms."""
    text = str(value or '').strip()
    match = _ID_ANNOTATION.search(text)
    if not match:
        return text, None
    name = _ID_ANNOTATION.sub('', text).strip(' ,;—-')
    return name or text, match.group(1)


def explicit_reference_id(question, field, name):
    """Bind an ID only when the current request explicitly associates it with
    this named reference. Do not borrow another field's ID or stale history.
    """
    labels = {'subsidiary': 'subsidiary', 'entity': '(?:customer|vendor)',
              'item': 'item', 'currency': 'currency'}
    label = labels.get(field)
    if not label:
        return None
    matches = set()
    for variant in _name_variants(name):
        pattern = (r'\b' + label + r'\s+' + re.escape(variant) +
                   r'\s*' + _ID_ANNOTATION.pattern)
        matches.update(re.findall(pattern, question, re.I))
    if len(matches) > 1:
        raise ValueError('Conflicting explicit internal IDs for ' + field)
    return next(iter(matches), None)


def catalog(metadata):
    result = {}
    for scope, fields in [('body', metadata.get('fields', [])), *metadata.get('sublists', {}).items()]:
        for field in fields:
            key = field.get('id', '')
            if key and not BLOCKED.match(key) and field.get('type') not in {'inlinehtml', 'password', 'summary', 'image'}:
                result[scope + '.' + key] = field
    return result


def relevant_extraction_keys(metadata, context=None):
    """Only cue-matched fields enter the Ollama grammar; huge catalogs make CPU drafts take many minutes."""
    allowed = catalog(metadata)
    question = (context or {}).get('question', '')
    lower = question.lower()
    record_type = (context or {}).get('prepare_record_type', '')
    keys = [key for key, pattern in FIELD_CUES.items() if key in allowed and re.search(pattern, lower)]
    # Customer/vendor prompts say "named X"; always expose companyname when creating those types.
    if record_type in {'customer', 'vendor'} and 'body.companyname' in allowed and 'body.companyname' not in keys:
        keys.append('body.companyname')
    if record_type == 'employee':
        for key in ('body.firstname', 'body.lastname', 'body.entityid', 'body.subsidiary'):
            if key in allowed and key not in keys:
                keys.append(key)
    # Transaction prompts should not treat "customer/vendor" as companyname.
    if record_type in {'salesorder', 'purchaseorder', 'estimate', 'invoice', 'creditmemo'}:
        keys = [key for key in keys if key != 'body.companyname']
    if not keys:
        # Fall back to a tiny mandatory/core set so extraction still works for terse follow-ups.
        core = ['body.entity', 'body.subsidiary', 'body.companyname', 'body.memo', 'item.item', 'item.quantity']
        keys = [key for key in core if key in allowed][:8]
    return keys


def infer_creation_record_type(question, known_types):
    """Map an explicit create request to a catalog type without calling the model."""
    allowed = set(known_types or [])
    text = question or ''
    entry = re.search(
        r'\b(?:create|add)\s+(?:an?\s+)?(?:entry|record)\s+in\s+(customrecord_[a-z][a-z0-9_]{0,26})\b',
        text, re.I)
    if entry:
        candidate = entry.group(1).lower()
        # Explicit custom-entry targets can skip waiting on the full capability list;
        # NetSuite inspect still rejects unknown or reserved types.
        if not allowed or candidate in allowed:
            return candidate
    for pattern, record_type in RECORD_TYPE_CUES:
        if re.search(pattern, text, re.I):
            # Standard cues resolve with an empty catalog so create can skip
            # creation_capabilities and inspect immediately.
            if not allowed or record_type in allowed:
                return record_type
    return None


def extraction_schema(metadata, context=None):
    keys = relevant_extraction_keys(metadata, context)
    if not keys:
        raise ValueError('No supported creation fields were discovered')
    value_schema = {'type': 'array', 'minItems': 1, 'items': {
        'type': 'object', 'additionalProperties': False,
        'required': ['line', 'value', 'reference', 'evidence'], 'properties': {
            'line': {'type': 'integer', 'minimum': 0}, 'value': {'type': 'string'},
            'reference': {'type': 'string', 'enum': ['name', 'internal_id', 'none']},
            'evidence': {'type': 'string'}}}}
    return {'type': 'object', 'additionalProperties': False, 'required': ['values'], 'properties': {
        'values': {'type': 'object', 'additionalProperties': False, 'required': keys,
                   'properties': {key: value_schema for key in keys}}}}


def _coerce_select_reference(value, reference):
    """Models often emit reference=none for selects; recover from the literal value."""
    if reference in {'name', 'internal_id'}:
        return value, reference
    bare, embedded = parse_embedded_internal_id(value)
    if embedded:
        return embedded, 'internal_id'
    if str(value).strip().isdecimal():
        return str(value).strip(), 'internal_id'
    if bare:
        return bare, 'name'
    raise ValueError('Select fields require name or internal_id intent')


def compile_values(extracted, metadata, context):
    """No model-selected payload structure, unknown fields, or unsourced defaults."""
    allowed = catalog(metadata)
    sources = [context['question']] + [h.get('question', '') for h in context.get('history', [])]
    payload = {'fields': {}, 'sublists': {}}
    lines = {}
    seen = set()
    if isinstance(extracted, dict) and isinstance(extracted.get('values'), dict):
        flattened = []
        for key, entries in extracted['values'].items():
            if not isinstance(entries, list) or not entries:
                raise ValueError('Each supplied field needs a nonempty value list')
            for entry in entries:
                if not isinstance(entry, dict) or set(entry) != {'line', 'value', 'reference', 'evidence'}:
                    raise ValueError('Invalid field value structure')
                flattened.append({'field': key, **entry})
        extracted = {**extracted, 'values': flattened}
    if not isinstance(extracted, dict) or set(extracted) != {'values'} or not isinstance(extracted['values'], list):
        raise ValueError('Expected extracted values array')
    if len(extracted['values']) > 200:
        raise ValueError('Too many creation values')
    for entry in extracted['values']:
        if not isinstance(entry, dict) or set(entry) != {'field', 'line', 'value', 'reference', 'evidence'}:
            raise ValueError('Invalid extracted value structure')
        key, index, value, evidence = entry['field'], entry['line'], entry['value'], entry['evidence']
        if key not in allowed or type(index) is not int or not 0 <= index < 50:
            raise ValueError('Unknown creation field or invalid line index')
        if not isinstance(value, str) or not isinstance(evidence, str) or not evidence.strip():
            raise ValueError('Each value requires quoted user evidence')
        if not any(evidence.casefold() in source.casefold() for source in sources) or value.casefold() not in evidence.casefold():
            raise ValueError('Extract only literal supplied values; omit inferred defaults')
        scope, field = key.split('.', 1)
        if (key, index) in seen or (scope == 'body' and index != 0):
            raise ValueError('Duplicate field or invalid body line')
        seen.add((key, index))
        kind = allowed[key].get('type')
        reference = entry['reference']
        if kind in {'select', 'radio'}:
            value, reference = _coerce_select_reference(value, reference)
            # Models often put "Name (internal ID N)" in value, or tag a name as
            # internal_id. Recover the explicit ID or coerce to a name reference.
            bare_value, embedded_id = parse_embedded_internal_id(value)
            if reference == 'internal_id' and not value.isdecimal():
                supplied_id = embedded_id or explicit_reference_id(
                    context['question'], field, bare_value)
                if supplied_id is not None:
                    value, reference = supplied_id, 'internal_id'
                elif bare_value and not bare_value.isdecimal():
                    value, reference = bare_value, 'name'
                else:
                    raise ValueError(f'{key}: use reference=name for a name, not internal_id')
            elif reference == 'name' and embedded_id and bare_value:
                value = bare_value
            if reference == 'internal_id' and field in {'entity', 'subsidiary', 'item', 'currency'} and not value.isdecimal():
                raise ValueError(f'{key}: use reference=name for a name, not internal_id')
            converted = {'text' if reference == 'name' else 'id': value}
            if reference == 'name':
                supplied_id = explicit_reference_id(context['question'], field, value)
                if supplied_id is not None:
                    converted = {'id': supplied_id}
                elif embedded_id is not None:
                    converted = {'id': embedded_id}
        else:
            # "named Acme" often makes the model set reference=name on companyname/memo.
            if reference != 'none':
                reference = 'none'
            if kind in NUMERIC:
                converted = float(value)
                if not math.isfinite(converted):
                    raise ValueError('Non-finite numeric value')
                if 'integer' in kind and not converted.is_integer():
                    raise ValueError('Expected integer')
            elif kind == 'checkbox':
                if value.lower() not in {'true', 'false', 'yes', 'no'}:
                    raise ValueError('Expected explicit checkbox value')
                converted = value.lower() in {'true', 'yes'}
            elif kind == 'multiselect':
                raise ValueError('Multiselect creation requires a dedicated extractor')
            else:
                converted = value
        if scope == 'body':
            payload['fields'][field] = converted
        else:
            lines.setdefault(scope, {}).setdefault(index, {})[field] = converted
    for scope, rows in lines.items():
        if sorted(rows) != list(range(len(rows))):
            raise ValueError('Line indexes must be consecutive from zero')
        payload['sublists'][scope] = [rows[i] for i in sorted(rows)]
    # Do not silently drop common explicit transaction inputs. This checks
    # extraction completeness, not whether NetSuite considers a field required.
    if context.get('prepare_record_type') in {'salesorder', 'purchaseorder', 'estimate', 'invoice', 'creditmemo'}:
        question = context['question'].lower()
        expected = {key: pattern for key, pattern in FIELD_CUES.items()
                    if key in {'body.entity', 'body.subsidiary', 'body.memo', 'item.item', 'item.quantity'}}
        omitted = [key for key, pattern in expected.items() if key in allowed and re.search(pattern, question)
                   and not any(k == key for k, _ in seen)]
        if omitted:
            raise ValueError('Explicit user inputs were omitted. Extract these supplied fields: ' + ', '.join(omitted))
    if context.get('prepare_record_type') in {'customer', 'vendor'}:
        question = context['question'].lower()
        if 'body.companyname' in allowed and re.search(r'\b(named|company)\b', question) \
                and not any(k == 'body.companyname' for k, _ in seen):
            raise ValueError('Explicit user inputs were omitted. Extract these supplied fields: body.companyname')
        if 'body.subsidiary' in allowed and re.search(r'\bsubsidiary\b', question) \
                and not any(k == 'body.subsidiary' for k, _ in seen):
            raise ValueError('Explicit user inputs were omitted. Extract these supplied fields: body.subsidiary')
    if context.get('prepare_record_type') == 'employee':
        question = context['question'].lower()
        if re.search(r'\bnamed\b', question):
            name_keys = [k for k in ('body.firstname', 'body.lastname', 'body.entityid') if k in allowed]
            if name_keys and not any(k in {s[0] for s in seen} for k in name_keys):
                raise ValueError('Explicit user inputs were omitted. Extract these supplied fields: '
                                 + ', '.join(name_keys[:2]))
        if 'body.subsidiary' in allowed and re.search(r'\bsubsidiary\b', question) \
                and not any(k == 'body.subsidiary' for k, _ in seen):
            raise ValueError('Explicit user inputs were omitted. Extract these supplied fields: body.subsidiary')
    return payload


def _clip_phrase(value):
    return re.split(
        r'\s*,\s*|\s+with\s+|\s+and\s+(?=memo\b)|\s+for\s+(?=subsidiary\b)'
        r'|\s+under\s+(?=subsidiary\b)|\.\s+Ask\b',
        value, maxsplit=1, flags=re.I
    )[0].strip(' .,;')


def _named_person(question):
    """Return (full_name, evidence) for 'named First Last' phrases, or None."""
    match = re.search(
        r'\bnamed\s+(.+?)(?=\s+under\s+|\s+for\s+|\s*,\s*|\s*\.\s*Ask\b|\s*$)',
        question, re.I)
    if not match:
        return None
    raw = _clip_phrase(match.group(1))
    evidence = match.group(0).strip().rstrip('.,;')
    if not raw or raw.casefold() not in evidence.casefold():
        return None
    return raw, evidence


def _subsidiary_from_question(question):
    """Return (value, reference, evidence) for subsidiary name/ID, or None."""
    if not re.search(r'\bsubsidiary\b', question, re.I):
        return None
    match = re.search(
        r'\b(?:under\s+|for\s+)?subsidiary\s+(.+?)(?=\s*,\s*|\s*\.\s*Ask\b|\s+with\s+|\s*$)',
        question, re.I)
    if not match:
        return None
    raw = _clip_phrase(match.group(1))
    evidence = match.group(0).strip().rstrip('.,;')
    bare, embedded = parse_embedded_internal_id(raw)
    if bare.isdecimal() and not embedded:
        value, reference = bare, 'internal_id'
    else:
        value, reference = (bare or raw), 'name'
    if not value or value.casefold() not in evidence.casefold():
        return None
    return value, reference, evidence


def deterministic_entity_payload(metadata, context):
    """Customer/vendor/employee/custom-entry drafts from explicit name + fields, without the model."""
    record_type = context.get('prepare_record_type')
    question = context.get('question') or ''
    allowed = catalog(metadata)
    if record_type and str(record_type).startswith('customrecord_'):
        extracted = []
        field_names = '|'.join(re.escape(k.split('.', 1)[1]) for k in allowed if k.startswith('body.'))
        value_end = (r'(?=\s*,\s*(?:and\s+)?(?:' + field_names + r')\b|'
                     r'\s+and\s+(?:' + field_names + r')\b|'
                     r'\s*\.\s*(?:Ask|Show|This\s+is|Keep|Do\s+not)\b|\s*$)')
        if 'body.name' in allowed:
            match = re.search(
                r'\bnamed\s+(.+?)(?=\s*,\s*|\s+with\s+|\s*\.\s*Ask\b|\s*$)',
                question, re.I)
            if not match:
                return None
            raw = _clip_phrase(match.group(1))
            evidence = match.group(0).strip().rstrip('.,;')
            if not raw or raw.casefold() not in evidence.casefold():
                return None
            extracted.append({'field': 'body.name', 'line': 0, 'value': raw,
                              'reference': 'none', 'evidence': evidence})
        for key, field in allowed.items():
            if key == 'body.name' or not key.startswith('body.'):
                continue
            field_id = key.split('.', 1)[1]
            match = re.search(
                r'\b' + re.escape(field_id) +
                r'\s+(“[^”]*”|"[^"]*"|.+?)' + value_end,
                question, re.I)
            if not match:
                continue
            captured = match.group(1).strip()
            raw = captured[1:-1] if ((captured.startswith('“') and captured.endswith('”')) or
                                      (captured.startswith('"') and captured.endswith('"'))) else _clip_phrase(captured)
            evidence = match.group(0).strip().rstrip('.,;')
            if not raw or raw.casefold() not in evidence.casefold():
                continue
            reference = 'none'
            if field.get('type') in {'select', 'radio'}:
                bare, embedded = parse_embedded_internal_id(raw)
                if embedded or bare.isdecimal():
                    raw, reference = (embedded or bare), 'internal_id'
                else:
                    raw, reference = bare, 'name'
            extracted.append({'field': key, 'line': 0, 'value': raw,
                              'reference': reference, 'evidence': evidence})
        if not extracted:
            return None
        try:
            return compile_values({'values': extracted}, metadata, context)
        except ValueError:
            return None
    if record_type == 'employee':
        extracted = []
        named = _named_person(question)
        if not named:
            return None
        full_name, evidence = named
        parts = full_name.split()
        if 'body.firstname' in allowed and 'body.lastname' in allowed and len(parts) >= 2:
            extracted.append({'field': 'body.firstname', 'line': 0, 'value': parts[0],
                              'reference': 'none', 'evidence': evidence})
            extracted.append({'field': 'body.lastname', 'line': 0, 'value': ' '.join(parts[1:]),
                              'reference': 'none', 'evidence': evidence})
        elif 'body.entityid' in allowed:
            extracted.append({'field': 'body.entityid', 'line': 0, 'value': full_name,
                              'reference': 'none', 'evidence': evidence})
        elif 'body.firstname' in allowed:
            extracted.append({'field': 'body.firstname', 'line': 0, 'value': full_name,
                              'reference': 'none', 'evidence': evidence})
        else:
            return None
        if 'body.subsidiary' in allowed:
            sub = _subsidiary_from_question(question)
            if not sub:
                return None
            value, reference, sub_evidence = sub
            extracted.append({'field': 'body.subsidiary', 'line': 0, 'value': value,
                              'reference': reference, 'evidence': sub_evidence})
        try:
            return compile_values({'values': extracted}, metadata, context)
        except ValueError:
            return None
    if record_type not in {'customer', 'vendor'}:
        return None
    extracted = []
    if 'body.companyname' in allowed:
        match = re.search(
            r'\b(?:customer|vendor)\s+named\s+(.+?)(?=\s+for\s+|\s+under\s+|\s*,\s*|\s*\.\s*Ask\b|\s*$)'
            r'|\bnamed\s+(.+?)(?=\s+for\s+|\s+under\s+|\s*,\s*|\s*\.\s*Ask\b|\s*$)',
            question, re.I)
        if not match:
            return None
        raw = _clip_phrase(next(g for g in match.groups() if g))
        evidence = match.group(0).strip().rstrip('.,;')
        if not raw or raw.casefold() not in evidence.casefold():
            return None
        extracted.append({'field': 'body.companyname', 'line': 0, 'value': raw,
                          'reference': 'none', 'evidence': evidence})
    if 'body.subsidiary' in allowed and re.search(r'\bsubsidiary\b', question, re.I):
        sub = _subsidiary_from_question(question)
        if not sub:
            return None
        value, reference, evidence = sub
        extracted.append({'field': 'body.subsidiary', 'line': 0, 'value': value,
                          'reference': reference, 'evidence': evidence})
    if not extracted:
        return None
    try:
        return compile_values({'values': extracted}, metadata, context)
    except ValueError:
        return None


def deterministic_transaction_payload(metadata, context):
    """Build a draft payload from explicit question text without calling the model.

    Only returns a payload when every cued field for this question is captured
    with high-confidence patterns. Otherwise returns None and the LLM extractor runs.
    """
    record_type = context.get('prepare_record_type')
    if record_type not in {'salesorder', 'purchaseorder', 'estimate', 'invoice', 'creditmemo'}:
        return None
    question = context.get('question') or ''
    allowed = catalog(metadata)
    needed = [key for key, pattern in FIELD_CUES.items()
              if key in {'body.entity', 'body.subsidiary', 'body.memo', 'item.item', 'item.quantity'}
              and key in allowed and re.search(pattern, question, re.I)]
    if not needed:
        return None

    patterns = {
        'body.entity': r'\b(?:customer|vendor)\s+(.+?)(?=\s*,\s*(?:subsidiary|item|quantity|memo)\b|\s+with\s+|\s*$)',
        'body.subsidiary': r'\bsubsidiary\s+(.+?)(?=\s*,\s*(?:item|quantity|memo|customer|vendor)\b|\s+with\s+|\s*$)',
        'item.item': r'\bitem\s+(.+?)(?=\s*,\s*(?:quantity|qty|memo|subsidiary)\b|\s+with\s+|\s*$)',
        'item.quantity': r'\b(?:quantity|qty)\s*[:=]?\s*(\d+(?:\.\d+)?)',
        'body.memo': r'\bmemo\s+(.+?)(?=\s*\.\s*Ask\b|\s*$)',
    }
    extracted = []
    for key in needed:
        match = re.search(patterns[key], question, re.I)
        if not match:
            return None
        raw = _clip_phrase(match.group(1))
        if not raw:
            return None
        evidence = match.group(0).strip().rstrip('.,;')
        field = key.split('.', 1)[1]
        if field in {'entity', 'subsidiary', 'item'}:
            bare, embedded = parse_embedded_internal_id(raw)
            if not bare and not embedded:
                return None
            if bare.isdecimal() and not embedded:
                value, reference = bare, 'internal_id'
            else:
                # Prefer the name form so evidence stays a literal question substring.
                value, reference = (bare or raw), 'name'
        else:
            value, reference = raw, 'none'
        if value.casefold() not in evidence.casefold():
            return None
        extracted.append({
            'field': key, 'line': 0, 'value': value, 'reference': reference, 'evidence': evidence
        })
    try:
        return compile_values({'values': extracted}, metadata, context)
    except ValueError:
        return None


def deterministic_creation_payload(metadata, context):
    """Prefer a model-free draft when the question is explicit enough."""
    return (deterministic_entity_payload(metadata, context)
            or deterministic_transaction_payload(metadata, context))


def is_creation_request(question):
    """True when the question is clearly a create/draft request (skip SuiteQL planner)."""
    text = question or ''
    if is_custom_type_request(text):
        return True
    if infer_creation_record_type(text, []):
        return True
    for pattern, _record_type in RECORD_TYPE_CUES:
        if re.search(pattern, text, re.I):
            return True
    return bool(re.search(
        r'\b(?:create|add)\s+(?:an?\s+)?(?:new\s+)?(?:entry|record)\s+in\s+customrecord_',
        text, re.I))


def is_custom_type_request(question):
    text = question or ''
    return bool(re.search(
        r'\bcreate\s+(?:a\s+|an\s+)?(?:new\s+)?custom\s+record\s+types?\b'
        r'|\bnew\s+custom\s+record\s+type\b',
        text, re.I))


def type_field_prefix(script_id):
    """Build a short unique custrecord_ prefix from the type script ID."""
    body = re.sub(r'^customrecord_', '', str(script_id or '').lower())
    parts = [p for p in body.split('_') if p]
    if len(parts) >= 2:
        abbrev = ''.join(p[0] for p in parts[-3:])
    elif parts:
        abbrev = parts[0][:6]
    else:
        abbrev = 'x'
    return f'custrecord_{abbrev}_', abbrev


def parse_custom_type_spec(question):
    """Build a CustomType payload from an explicit create-type question, or None."""
    if not is_custom_type_request(question):
        return None
    name_match = re.search(
        r'\bnamed\s+(.+?)(?=\s*,\s*script\s+id\b|\s+script\s+id\b|\s*\.\s*Ask\b|\s*$)',
        question or '', re.I)
    script_match = re.search(
        r'\bscript\s+id\s+(customrecord_[a-z][a-z0-9_]{0,26})\b',
        question or '', re.I)
    if not name_match or not script_match:
        return None
    name = name_match.group(1).strip(' .,;')
    script_id = script_match.group(1).lower()
    include_name = bool(re.search(r'\binclude(?:s)?\s+the\s+name\s+field\b', question or '', re.I))
    prefix, abbrev = type_field_prefix(script_id)
    fields = []
    for match in re.finditer(
            r'(?P<optional>optional\s+)?'
            r'(?P<type>date|text|textarea|clobtext|integer|float|currency|checkbox)'
            r'\s+field\s+(?P<id>custrecord_[a-z][a-z0-9_]{0,28})\b',
            question or '', re.I):
        field_type = match.group('type').upper()
        script = match.group('id').lower()
        # Reused short prefixes from another type (custrecord_aac_*) collide account-wide.
        # Rewrite them onto this type's abbreviation while leaving already-aligned IDs alone.
        parts = script.split('_')
        if (len(parts) >= 3 and parts[0] == 'custrecord' and parts[1] != abbrev
                and len(parts[1]) <= 4):
            candidate = prefix + '_'.join(parts[2:])
            if re.fullmatch(r'custrecord_[a-z][a-z0-9_]{0,28}', candidate):
                script = candidate
        label = script.replace('custrecord_', '').replace('_', ' ').strip().title() or field_type.title()
        fields.append({
            'script_id': script,
            'label': label,
            'field_type': field_type,
            'mandatory': not bool(match.group('optional')),
        })
    if not fields and re.search(r'\bfield\b', question or '', re.I):
        return None
    return {
        'script_id': script_id,
        'name': name,
        'description': '',
        'include_name': True if include_name else not bool(
            re.search(r'\bwithout\s+(?:the\s+)?name\s+field\b', question or '', re.I)),
        'fields': fields,
    }


def sdf_configuration_message(settings):
    """Actionable setup text when custom-type deployment cannot run."""
    missing = []
    if not getattr(settings, 'sdf_enabled', False):
        missing.append('Set AGENT_SDF_ENABLED=true in .env')
    if not getattr(settings, 'sdf_auth_id', ''):
        missing.append('Set AGENT_SDF_AUTH_ID to a SuiteCloud CI auth ID (for example agent-sdf)')
    account = getattr(settings, 'account', '') or 'YOUR_ACCOUNT_ID'
    lines = [
        'Creating a new custom record type requires SuiteCloud SDF deployment authentication.',
        'This is separate from RESTlet OAuth used for SuiteQL and record create.',
    ]
    if missing:
        lines.append('Missing configuration: ' + '; '.join(missing) + '.')
    lines.extend([
        'Configure CI auth (no browser login) with SuiteCloud CLI 4.x, then restart the worker:',
        f'suitecloud account:setup:ci --account {account} --authid agent-sdf '
        '--certificateid <CERTIFICATE_ID> --privatekeypath <PRIVATE_KEY.pem>',
        'Then set AGENT_SDF_ENABLED=true and AGENT_SDF_AUTH_ID=agent-sdf in .env.',
        'The certificate role must allow SDF and custom record type customization.',
        'Until that is configured, create entries in an existing custom type or a standard record instead.',
    ])
    return ' '.join(lines)


EXTRACTION_SYSTEM = '''Extract user-supplied creation values only. Return the values OBJECT matching the schema. Each key is a catalog field and its value is a nonempty array of extracted entries.
Object keys are supplied catalog keys; body fields use line=0; sublist rows use consecutive zero-based lines.
value is the literal user value as a string. evidence is an exact quote from a USER question containing that value.
For select/reference fields ALWAYS use reference=name or reference=internal_id (never none).
Use reference=name for names and internal_id ONLY when the value itself is a bare numeric ID.
If the user wrote "Name (internal ID 12)", prefer value=Name with reference=name; never put the name in reference=internal_id.
For non-select fields use reference=none. Entity is the transaction customer/vendor. Item belongs to item.item.
Example: customer TEST US Customer -> body.entity, value TEST US Customer, reference name.
Subsidiary internal ID 1 -> body.subsidiary, value 1, reference internal_id.
Do not extract review instructions as fields. Never set the new record's internal ID.
Omit unspecified fields, even mandatory dates, currency, rate, status: NetSuite supplies defaults.
Use the current question and user history; current values override earlier values. Never treat assistant
clarifications, business defaults or metadata as evidence of a user-supplied value. No questions or saving.'''
