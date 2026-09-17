import json
from agent.config import Settings
from agent.store import Store

s = Settings()
store = Store(s.data_dir)
schema = store.get('schema')
if not schema:
    print('No schema snapshot. Start the configured worker and inspect /status.')
else:
    print(json.dumps({'version': schema['version'], 'refreshed_at': schema['refreshed_at'],
                      'tables': [{'name': t['name'], 'field_count': len(t['fields'])} for t in schema['tables']],
                      'failures': schema.get('failures', [])}, indent=2))
store.close()
