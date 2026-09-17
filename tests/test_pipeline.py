"""Protocol-level integration: real adapters + worker, synthetic external services."""
import json
import sqlite3

import httpx
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization

from agent.config import Settings
from agent.engine import Engine
from agent.llm import Ollama
from agent.netsuite import NetSuite
from agent.schema import refresh_schema
from agent.worker import Worker


async def test_oauth_schema_model_query_and_completion_pipeline(settings, store, tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    path = tmp_path / 'private.pem'
    path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    config = Settings(_env_file=None, account='123_SB1', client_id='client', certificate_id='certificate',
                      private_key_path=path, restlet_url='https://123-sb1.restlets.api.netsuite.com/app/site/hosting/restlet.nl?script=1&deploy=1',
                      document_path=settings.document_path, data_dir=settings.data_dir, management_token='x'*40)
    db = sqlite3.connect(':memory:')
    db.execute('CREATE TABLE customer (id INTEGER PRIMARY KEY, companyname TEXT)')
    db.executemany('INSERT INTO customer VALUES (?, ?)', [(1, 'Acme'), (2, 'Northwind')])
    completed, calls = [], []
    job = {'id': 'pipeline-1', 'lease': 'test-lease', 'request': {'kind': 'ask', 'question': 'List customers'}}
    def route(request):
        if request.url.path.endswith('/token'):
            return httpx.Response(200, json={'access_token': 'synthetic-token', 'expires_in': 3600})
        body = json.loads(request.content)
        if request.url.path == '/api/chat':
            assert body['model'] == 'qwen3.5:4b'
            assert body['stream'] is False
            assert 'Customers are customer records' in body['messages'][1]['content']
            assert 'Northwind' not in request.content.decode()  # Results never go to the model.
            return httpx.Response(200, json={'message': {'content': json.dumps({
                'kind': 'query', 'explanation': 'All customer records',
                'sql': 'SELECT id, companyname FROM customer ORDER BY id'})}})
        assert request.headers['Authorization'] == 'Bearer synthetic-token'
        calls.append(body['action'])
        if body['action'] == 'schema_inventory':
            data = {'tables': ['customer']}
        elif body['action'] == 'schema_probe':
            data = {'tables': [{'name': 'customer', 'fields': [{'name': 'id'}, {'name': 'companyname'}]}]}
        elif body['action'] == 'query':
            assert body['job'] == job['id'] and body['lease'] == job['lease']
            cursor = db.execute(body['sql'])
            rows = [dict(zip([d[0] for d in cursor.description], row)) for row in cursor.fetchall()]
            data = {'result': {'rows': rows, 'total': len(rows), 'page': 0, 'page_size': 100, 'has_more': False}}
        elif body['action'] == 'complete':
            completed.append(body['result'])
            data = {'completed': True}
        else:
            raise AssertionError(body['action'])
        return httpx.Response(200, json={'ok': True, **data})
    ns = NetSuite(config, httpx.AsyncClient(transport=httpx.MockTransport(route)))
    llm = Ollama(config, httpx.AsyncClient(transport=httpx.MockTransport(route)))
    await refresh_schema(ns, store)
    worker = Worker(config, store, ns, Engine(config, store, ns, llm))
    await worker.process_job(job)
    assert completed[0]['rows'] == [{'id': 1, 'companyname': 'Acme'}, {'id': 2, 'companyname': 'Northwind'}]
    assert calls == ['schema_inventory', 'schema_probe', 'query', 'complete']
    # Simulate worker restart and ambiguous prior publication: only completion is replayed.
    await worker.process_job(job)
    assert calls[-1] == 'complete' and calls.count('query') == 1
    await ns.close()
    await llm.close()
    db.close()
