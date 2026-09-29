import json
import httpx
import pytest
from pydantic import ValidationError
from agent.llm import Ollama, OllamaError, generation_schema
from agent.models import Plan


def test_generation_grammar_is_small_but_validation_limits_remain():
    assert 'maxLength' not in json.dumps(generation_schema())
    assert generation_schema()['properties']['kind']['enum'] == ['query', 'clarify', 'unsupported', 'create']
    assert 'sql' in generation_schema()['required']
    with pytest.raises(ValidationError):
        Plan(kind='query', explanation='test')
    with pytest.raises(ValidationError):
        Plan(kind='query', explanation='test', sql='x' * 16001)


async def test_ollama_error_is_actionable(settings):
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request:
        httpx.Response(400, json={'error': 'Failed to initialize samplers: failed to parse grammar'})))
    llm = Ollama(settings, client)
    try:
        with pytest.raises(OllamaError, match='failed to parse grammar'):
            await llm.plan({'question': 'Count customers'})
    finally:
        await llm.close()


async def test_ollama_duration_metrics(settings):
    body={"message":{"content":json.dumps({"kind":"query","explanation":"count", "sql":"SELECT COUNT(*) AS n FROM customer ORDER BY COUNT(*)"})},
          "load_duration":2000000000,"prompt_eval_duration":3000000000,"eval_duration":1000000000,"prompt_eval_count":100,"eval_count":20}
    llm=Ollama(settings,httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,json=body))))
    try:
        await llm.plan({"question":"Count customers"})
        assert llm.last_metrics["prompt_eval_seconds"]==3
        assert llm.last_metrics["prompt_eval_count"]==100
    finally: await llm.close()


async def test_creation_has_separate_cpu_timeout(settings):
    settings.creation_inference_timeout = 600
    def respond(request):
        assert request.extensions['timeout']['read'] == 600
        assert request.extensions['timeout']['connect'] == 10
        return httpx.Response(200, json={'message': {'content': json.dumps({
            'action': 'inspect', 'record_type': 'salesorder', 'message': '', 'payload_json': '{}'})}})
    llm = Ollama(settings, httpx.AsyncClient(transport=httpx.MockTransport(respond)))
    try:
        assert (await llm.creation_plan({'question': 'Create a sales order'})).action == 'inspect'
    finally:
        await llm.close()


async def test_creation_timeout_is_actionable(settings):
    def respond(request):
        raise httpx.ReadTimeout('sensitive transport detail', request=request)
    llm = Ollama(settings, httpx.AsyncClient(transport=httpx.MockTransport(respond)))
    try:
        with pytest.raises(OllamaError, match='Local Ollama creation planning timed out') as error:
            await llm.creation_plan({})
        assert 'sensitive' not in str(error.value)
    finally:
        await llm.close()


async def test_deterministic_creation_skips_ollama(settings):
    calls = []
    def respond(request):
        calls.append(request)
        raise AssertionError('Ollama should not be called for deterministic extraction')
    llm = Ollama(settings, httpx.AsyncClient(transport=httpx.MockTransport(respond)))
    question = ('Create a sales order for customer TEST US Customer, subsidiary Honeycomb Mfg. '
                '(internal ID 1), item F3 PL Hardware Item (internal ID 1334), quantity 2, '
                'with memo AO-TEST-001. Ask me to review before saving')
    metadata = {
        'fields': [{'id': 'entity', 'type': 'select'}, {'id': 'subsidiary', 'type': 'select'},
                   {'id': 'memo', 'type': 'text'}],
        'sublists': {'item': [{'id': 'item', 'type': 'select'}, {'id': 'quantity', 'type': 'float'}]},
    }
    try:
        step = await llm.creation_plan({
            'prepare_record_type': 'salesorder', 'question': question, 'metadata': metadata})
        assert step.action == 'prepare'
        assert '"id": "1"' in step.payload_json
        assert '"id": "1334"' in step.payload_json
        assert calls == []
        assert llm.last_metrics.get('deterministic_extraction') is True
    finally:
        await llm.close()


async def test_deterministic_vendor_skips_ollama(settings):
    calls = []
    def respond(request):
        calls.append(request)
        raise AssertionError('Ollama should not be called for vendor extraction')
    llm = Ollama(settings, httpx.AsyncClient(transport=httpx.MockTransport(respond)))
    question = ('Create a vendor named Agent Vendor Test Co for subsidiary Honeycomb Mfg. '
                '(internal ID 1). Ask me to review before saving')
    metadata = {'fields': [{'id': 'companyname', 'type': 'text'}, {'id': 'subsidiary', 'type': 'select'}]}
    try:
        step = await llm.creation_plan({
            'prepare_record_type': 'vendor', 'question': question, 'metadata': metadata})
        assert step.action == 'prepare'
        assert 'Agent Vendor Test Co' in step.payload_json
        assert '"id": "1"' in step.payload_json
        assert calls == []
    finally:
        await llm.close()


async def test_inspected_creation_grammar_only_allows_draft_preparation(settings):
    def respond(request):
        data = json.loads(request.content)
        properties = data['format']['properties']
        assert list(properties['values']['properties']) == ['body.memo']
        assert 'payload_json' not in properties
        assert data['options']['num_predict'] == 900
        return httpx.Response(200, json={'message': {'content': json.dumps({
            'values': {'body.memo': [{'line': 0, 'value': 'AO-1', 'reference': 'none', 'evidence': 'memo AO-1'}]}})}})
    llm = Ollama(settings, httpx.AsyncClient(transport=httpx.MockTransport(respond)))
    try:
        step = await llm.creation_plan({
            'prepare_record_type': 'salesorder',
            'question': 'Create order with memo AO-1',
            'metadata': {'fields': [{'id': 'memo', 'type': 'text'}]}})
        assert step.action == 'prepare'
        assert 'AO-1' in step.payload_json
    finally:
        await llm.close()
