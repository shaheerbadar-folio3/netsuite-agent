"""End-to-end synthetic planning check using local Ollama; never connects to NetSuite."""
import asyncio
import json
import tempfile
import time
from pathlib import Path
from agent.config import Settings
from agent.creation import CreationEngine
from agent.llm import Ollama
from agent.store import Store


class SyntheticNetSuite:
    def __init__(self):
        self.calls = []

    async def call(self, action, **payload):
        self.calls.append(action)
        if action == 'creation_capabilities':
            return {'capabilities': {'standard': ['customer'], 'custom': []}}
        if action == 'creation_inspect':
            return {'record_type': 'customer', 'fields': [
                {'id': 'companyname', 'label': 'Company Name', 'type': 'text', 'mandatory': True},
                {'id': 'subsidiary', 'label': 'Subsidiary', 'type': 'select', 'mandatory': True},
            ], 'sublists': {}}
        if action == 'creation_prepare':
            p = payload.get('payload', {})
            fields = p.get('fields', {})
            if set(fields) != {'companyname', 'subsidiary'} or fields['companyname'] != 'Synthetic Test Customer':
                raise ValueError('Use companyname for Synthetic Test Customer and subsidiary for ID 1; these are the only writable fields.')
            if fields['subsidiary'] not in ({'id': '1'}, {'text': 'Test Subsidiary'}):
                raise ValueError('Subsidiary must be an explicit reference to Test Subsidiary (ID 1).')
            if not isinstance(p.get('sublists', {}), dict):
                raise ValueError('sublists must be a JSON object, not an array')
            return {'result': {'kind': 'draft', 'preview': p, 'synthetic': True}}
        raise ValueError('Synthetic benchmark does not allow this operation: '+action)


async def main():
    settings = Settings()
    settings.creation_enabled = True
    settings.sdf_enabled = False
    llm = Ollama(settings)
    start = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix='nsa-model-check-') as folder:
            settings.document_path = Path(folder)/'business.md'
            settings.document_path.write_text('# Synthetic benchmark\nCustomer means a customer record. No additional business rules.\n')
            store = Store(Path(folder)/'store')
            ns = SyntheticNetSuite()
            try:
                result = await CreationEngine(settings, store, ns, llm).process({
                    'id': '1', 'lease': 'synthetic', 'request': {'kind': 'ask', 'mode': 'create',
                    'question': 'Create a customer named Synthetic Test Customer for Test Subsidiary (internal ID 1). Ask me to review before saving.'}})
                passed = result['kind'] == 'draft' and 'creation_inspect' in ns.calls
                print(json.dumps({'seconds': round(time.monotonic()-start, 2), 'calls': ns.calls,
                                  'result': result, 'passed': passed}, indent=2))
                if not passed:
                    raise SystemExit(1)
            finally:
                store.close()
    finally:
        await llm.close()


if __name__ == '__main__':
    asyncio.run(main())
