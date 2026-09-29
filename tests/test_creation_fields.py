import pytest
from agent.creation_fields import extraction_schema, compile_values

META = {'fields': [{'id': 'id', 'type': 'text'}, {'id': 'entity', 'type': 'select'},
                  {'id': 'currency', 'type': 'select'}, {'id': 'memo', 'type': 'text'}],
        'sublists': {'item': [{'id': 'item', 'type': 'select'}, {'id': 'quantity', 'type': 'float'}]}}

def entry(field, value, reference='none', line=0, evidence=None):
    return {'field': field, 'value': value, 'reference': reference, 'line': line,
            'evidence': value if evidence is None else evidence}

def test_metadata_limits_fields_and_builds_references():
    keys = extraction_schema(META)['properties']['values']['properties']
    assert 'body.id' not in keys
    values = [entry('body.entity','Acme','name'),entry('item.item','Hardware','name'),entry('item.quantity','2')]
    result = compile_values({'values':values}, META, {'question':'Acme Hardware quantity 2'})
    assert result == {'fields':{'entity':{'text':'Acme'}},'sublists':{'item':[{'item':{'text':'Hardware'},'quantity':2}]}}
    assert 'currency' not in result['fields']

@pytest.mark.parametrize('value', [entry('body.id','1'), entry('body.currency','USD','name'), entry('body.giveaccess','true')])
def test_unknown_fields_and_unsupplied_defaults_rejected(value):
    with pytest.raises(ValueError):
        compile_values({'values':[value]},META,{'question':'Create an order for Acme'})

def test_assistant_history_is_not_evidence():
    with pytest.raises(ValueError):
        compile_values({'values':[entry('body.currency','USD','name')]},META,
                       {'question':'Create order','history':[{'question':'Acme','interpretation':'Use USD'}]})


def test_explicit_item_is_not_silently_dropped():
    with pytest.raises(ValueError, match='item.item'):
        compile_values({'values':[entry('body.entity','Acme','name')]}, META,
                       {'question':'Create sales order for customer Acme item Hardware quantity 2',
                        'prepare_record_type':'salesorder'})


def test_schema_requires_explicit_transaction_inputs_and_compiles_field_groups():
    context = {'question':'customer Acme item Hardware quantity 2', 'prepare_record_type':'salesorder'}
    schema = extraction_schema(META, context)
    assert set(schema['properties']['values']['required']) == {'body.entity','item.item','item.quantity'}
    assert set(schema['properties']['values']['properties']) == {'body.entity','item.item','item.quantity'}
    values = {}
    for e in [entry('body.entity','Acme','name'),entry('item.item','Hardware','name'),entry('item.quantity','2')]:
        key = e.pop('field')
        values[key] = [e]
    payload = compile_values({'values':values}, META, context)
    assert payload['sublists']['item'][0]['quantity'] == 2
    assert payload['fields']['entity'] == {'text':'Acme'}


def test_select_reference_none_is_coerced_from_value():
    metadata = {'fields': [{'id': 'entity', 'type': 'select'}],
                'sublists': {'item': [{'id': 'item', 'type': 'select'}]}}
    payload = compile_values({'values': [
        entry('body.entity', 'TEST US Customer', 'none'),
        entry('item.item', '1334', 'none'),
    ]}, metadata, {'question': 'customer TEST US Customer item 1334'})
    assert payload['fields']['entity'] == {'text': 'TEST US Customer'}
    assert payload['sublists']['item'][0]['item'] == {'id': '1334'}


def test_companyname_reference_name_is_coerced_to_text():
    metadata = {'fields': [{'id': 'companyname', 'type': 'text'}, {'id': 'subsidiary', 'type': 'select'}]}
    payload = compile_values({'values': [
        entry('body.companyname', 'Agent Vendor Test Co', 'name'),
        entry('body.subsidiary', 'Honeycomb Mfg.', 'name'),
    ]}, metadata, {'question': 'vendor named Agent Vendor Test Co for subsidiary Honeycomb Mfg. (internal ID 1)',
                   'prepare_record_type': 'vendor'})
    assert payload['fields']['companyname'] == 'Agent Vendor Test Co'
    assert payload['fields']['subsidiary'] == {'id': '1'}


def test_deterministic_vendor_skips_model():
    from agent.creation_fields import deterministic_creation_payload, infer_creation_record_type
    assert infer_creation_record_type(
        'Create a vendor named Agent Vendor Test Co for subsidiary Honeycomb Mfg. (internal ID 1).',
        ['vendor', 'customer']) == 'vendor'
    metadata = {'fields': [{'id': 'companyname', 'type': 'text'}, {'id': 'subsidiary', 'type': 'select'}]}
    question = ('Create a vendor named Agent Vendor Test Co for subsidiary Honeycomb Mfg. '
                '(internal ID 1). Ask me to review before saving')
    payload = deterministic_creation_payload(metadata, {
        'question': question, 'prepare_record_type': 'vendor', 'history': []})
    assert payload == {
        'fields': {'companyname': 'Agent Vendor Test Co', 'subsidiary': {'id': '1'}},
        'sublists': {},
    }


def test_custom_entry_is_inferred_and_extracted():
    from agent.creation_fields import infer_creation_record_type, deterministic_creation_payload
    question = ('Create an entry in customrecord_agent_inspection named Inspection Run AO-001, '
                'with custrecord_ai_date 2026-09-23 and custrecord_ai_notes First agent test entry.')
    assert infer_creation_record_type(question, ['customrecord_agent_inspection']) == 'customrecord_agent_inspection'
    assert infer_creation_record_type(question, []) == 'customrecord_agent_inspection'
    metadata = {'fields': [
        {'id': 'name', 'type': 'text'},
        {'id': 'custrecord_ai_date', 'type': 'date'},
        {'id': 'custrecord_ai_notes', 'type': 'text'},
    ]}
    payload = deterministic_creation_payload(metadata, {
        'question': question, 'prepare_record_type': 'customrecord_agent_inspection', 'history': []})
    assert payload == {
        'fields': {
            'name': 'Inspection Run AO-001',
            'custrecord_ai_date': '2026-09-23',
            'custrecord_ai_notes': 'First agent test entry',
        },
        'sublists': {},
    }


def test_custom_type_request_is_parsed_deterministically():
    from agent.creation_fields import is_custom_type_request, parse_custom_type_spec
    question = ('Create a new custom record type named Agent Equipment Inspection, '
                'script ID customrecord_agent_inspection, with a Date field custrecord_ai_date '
                'and an optional text field custrecord_ai_notes. Include the Name field.')
    assert is_custom_type_request(question)
    assert parse_custom_type_spec(question) == {
        'script_id': 'customrecord_agent_inspection',
        'name': 'Agent Equipment Inspection',
        'description': '',
        'include_name': True,
        'fields': [
            {'script_id': 'custrecord_ai_date', 'label': 'Ai Date', 'field_type': 'DATE', 'mandatory': True},
            {'script_id': 'custrecord_ai_notes', 'label': 'Ai Notes', 'field_type': 'TEXT', 'mandatory': False},
        ],
    }


def test_custom_type_field_ids_rewrite_reused_short_prefixes():
    from agent.creation_fields import parse_custom_type_spec
    question = ('Create a new custom record type named Agent Item Check, '
                'script ID customrecord_agent_item_check, with a Date field custrecord_aac_date '
                'and an optional text field custrecord_aac_notes. Include the Name field.')
    spec = parse_custom_type_spec(question)
    assert spec['fields'][0]['script_id'] == 'custrecord_aic_date'
    assert spec['fields'][1]['script_id'] == 'custrecord_aic_notes'


def test_create_a_new_employee_cues_skip_suiteql_planner():
    from agent.creation_fields import is_creation_request, infer_creation_record_type
    prompts = [
        'Create a new employee named Marry Jane under subsidiary HoneyComb MFG.',
        'Ok i want to create a new employee named Marry Jane under subsidiary HoneyComb MFG.',
        'I want to create an employee named Marry Jane for subsidiary Honeycomb Mfg.',
        'Please add a new employee named Marry Jane under subsidiary HoneyComb MFG.',
    ]
    for question in prompts:
        assert is_creation_request(question), question
        assert infer_creation_record_type(question, []) == 'employee', question
        assert infer_creation_record_type(question, ['employee', 'customer']) == 'employee', question


def test_deterministic_employee_named_and_subsidiary():
    from agent.creation_fields import deterministic_creation_payload
    metadata = {
        'fields': [
            {'id': 'firstname', 'type': 'text'},
            {'id': 'lastname', 'type': 'text'},
            {'id': 'subsidiary', 'type': 'select'},
        ],
        'sublists': {},
    }
    question = ('Ok i want to create a new employee named Marry Jane '
                'under subsidiary HoneyComb MFG.')
    payload = deterministic_creation_payload(metadata, {
        'question': question, 'prepare_record_type': 'employee', 'history': []})
    assert payload == {
        'fields': {
            'firstname': 'Marry',
            'lastname': 'Jane',
            'subsidiary': {'text': 'HoneyComb MFG'},
        },
        'sublists': {},
    }


def test_deterministic_sales_order_skips_model_for_explicit_requests():
    from agent.creation_fields import deterministic_transaction_payload
    metadata = {
        'fields': [{'id': 'entity', 'type': 'select'}, {'id': 'subsidiary', 'type': 'select'},
                   {'id': 'memo', 'type': 'text'}],
        'sublists': {'item': [{'id': 'item', 'type': 'select'}, {'id': 'quantity', 'type': 'float'}]},
    }
    question = ('Create a sales order for customer TEST US Customer, subsidiary Honeycomb Mfg. '
                '(internal ID 1), item F3 PL Hardware Item (internal ID 1334), quantity 2, '
                'with memo AO-TEST-001. Ask me to review before saving')
    payload = deterministic_transaction_payload(metadata, {
        'question': question, 'prepare_record_type': 'salesorder', 'history': []})
    assert payload == {
        'fields': {
            'entity': {'text': 'TEST US Customer'},
            'subsidiary': {'id': '1'},
            'memo': 'AO-TEST-001',
        },
        'sublists': {'item': [{'item': {'id': '1334'}, 'quantity': 2.0}]},
    }


def test_explicit_subsidiary_id_wins_over_extracted_name():
    metadata = {'fields':[{'id':'subsidiary','type':'select'}, {'id':'entity','type':'select'}]}
    context = {'question':'Customer TEST US Customer, subsidiary Honeycomb Mfg. (internal ID 1), item Hardware'}
    payload = compile_values({'values':[entry('body.subsidiary','Honeycomb Mfg.','name'),
                                       entry('body.entity','TEST US Customer','name')]},metadata,context)
    assert payload['fields']['subsidiary'] == {'id':'1'}
    assert payload['fields']['entity'] == {'text':'TEST US Customer'}


def test_misencoded_item_internal_id_recovers_from_question_annotation():
    metadata = {'fields': [{'id': 'entity', 'type': 'select'}],
                'sublists': {'item': [{'id': 'item', 'type': 'select'}, {'id': 'quantity', 'type': 'float'}]}}
    question = ('Create a sales order for customer TEST US Customer, item F3 PL Hardware Item '
                '(internal ID 1334), quantity 2')
    context = {'question': question, 'prepare_record_type': 'salesorder', 'history': []}
    payload = compile_values({'values': [
        entry('body.entity', 'TEST US Customer', 'name'),
        entry('item.item', 'F3 PL Hardware Item', 'internal_id'),
        entry('item.quantity', '2'),
    ]}, metadata, context)
    assert payload['sublists']['item'][0]['item'] == {'id': '1334'}


def test_embedded_internal_id_in_value_is_compiled_to_id():
    metadata = {'fields': [{'id': 'subsidiary', 'type': 'select'}]}
    question = 'subsidiary Honeycomb Mfg. (internal ID 1)'
    payload = compile_values({'values': [
        entry('body.subsidiary', 'Honeycomb Mfg. (internal ID 1)', 'name', evidence='Honeycomb Mfg. (internal ID 1)'),
    ]}, metadata, {'question': question})
    assert payload['fields']['subsidiary'] == {'id': '1'}


def test_explicit_reference_binding_does_not_borrow_stale_or_other_ids():
    from agent.creation_fields import explicit_reference_id
    assert explicit_reference_id('item Hardware (internal ID 55), subsidiary Honeycomb', 'subsidiary','Honeycomb') is None
    assert explicit_reference_id('subsidiary Another (internal ID 1)', 'subsidiary','Honeycomb') is None
    assert explicit_reference_id('subsidiary Honeycomb', 'subsidiary','Honeycomb') is None
    assert explicit_reference_id('subsidiary Honeycomb Mfg. (internal ID 1)', 'subsidiary', 'Honeycomb Mfg') == '1'


def test_custom_entry_owner_and_notes_end_at_field_and_sentence_boundaries():
    from agent.creation_fields import deterministic_entity_payload
    metadata = {'fields':[{'id':key,'type':kind} for key,kind in [
        ('name','text'),('custrecord_ai_date','date'),('custrecord_ai_notes','text'),('owner','select')]]}
    result = deterministic_entity_payload(metadata, {'prepare_record_type':'customrecord_agent_inspection',
        'question':'Create a new entry in customrecord_agent_inspection named Inspection Run AO-001, with custrecord_ai_date 2026-09-23, custrecord_ai_notes “First agent test entry”, and Owner Shaheer Badar. This is a new unsaved entry. Show me the draft for review before saving.'})
    assert result is not None
    assert result['fields']['owner'] == {'text':'Shaheer Badar'}
    assert result['fields']['custrecord_ai_notes'] == 'First agent test entry'
    assert result['fields']['custrecord_ai_date'] == '2026-09-23'
