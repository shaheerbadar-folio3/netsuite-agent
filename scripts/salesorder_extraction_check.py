"""Local-model regression check with synthetic metadata; never calls NetSuite."""
import asyncio, json
from agent.config import Settings
from agent.llm import Ollama
async def main():
    llm = Ollama(Settings())
    context = {'prepare_record_type': 'salesorder', 'question': 'Create a sales order for customer TEST US Customer, subsidiary Honeycomb Mfg. (internal ID 1), item F3 PL Hardware, quantity 2, with memo AO-TEST-001. Ask me to review before saving.',
    'metadata': {'fields': [{'id': k, 'type': t, 'mandatory': True} for k,t in [('entity','select'),('subsidiary','select'),('currency','select'),('trandate','date'),('memo','text')]],
    'sublists': {'item': [{'id':'item','type':'select','mandatory':True},{'id':'quantity','type':'float','mandatory':True},{'id':'rate','type':'currency','mandatory':True}]}}}
    try:
        for attempt in range(3):
            try:
                plan = await llm.creation_plan(context)
                break
            except ValueError as exc:
                context['validation'] = str(exc)
                print('Extraction correction needed:', str(exc), flush=True)
        else:
            raise AssertionError('Extraction did not converge: ' + context['validation'])
        p = json.loads(plan.payload_json)
        assert plan.action == 'prepare'
        assert p['fields']['entity'] == {'text':'TEST US Customer'}
        assert str(p['fields']['subsidiary']['id']) == '1'
        assert p['fields']['memo'] == 'AO-TEST-001'
        assert not {'currency','trandate'} & p['fields'].keys()
        assert p['sublists']['item'][0]['item'] == {'text':'F3 PL Hardware'}
        assert p['sublists']['item'][0]['quantity'] == 2
        assert 'rate' not in p['sublists']['item'][0]
        print('PASS: exact sales order request generates prepare, correct references and quantity, no invented date/currency/rate. No NetSuite calls or saves.')
    finally:
        await llm.close()
asyncio.run(main())
