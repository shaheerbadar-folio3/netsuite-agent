import json
from agent.cli import benchmark
from agent.models import Plan


async def test_benchmark_repairs_invalid_sql_before_reporting_success(settings, monkeypatch, capsys):
    contexts = []
    class Model:
        def __init__(self, settings):
            pass
        async def plan(self, context):
            contexts.append(dict(context))
            sql = 'SELECT missing_field FROM customer ORDER BY missing_field'
            if len(contexts) > 1:
                sql = 'SELECT COUNT(*) AS total FROM customer ORDER BY COUNT(*)'
            return Plan(kind='query', explanation='Count customers', sql=sql)
        async def close(self):
            pass
    monkeypatch.setattr('agent.cli.Ollama', Model)
    await benchmark(settings)
    output = json.loads(capsys.readouterr().out)
    assert output['validated'] is True
    assert output['attempts'] == 2
    assert 'Column validation failed' in contexts[1]['previous_error']
