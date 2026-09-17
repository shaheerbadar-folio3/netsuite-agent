import json
import httpx
import pytest
from pydantic import ValidationError
from agent.llm import Ollama, OllamaError, generation_schema
from agent.models import Plan


def test_generation_grammar_is_small_but_validation_limits_remain():
    assert 'maxLength' not in json.dumps(generation_schema())
    assert generation_schema()['properties']['kind']['enum'] == ['query', 'clarify', 'unsupported']
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
