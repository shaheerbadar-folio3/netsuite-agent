import json
import httpx
from agent.config import Settings

s = Settings()
response = httpx.get('http://127.0.0.1:8765/status', headers={'Authorization': 'Bearer ' + s.management_token}, timeout=10, trust_env=False)
response.raise_for_status()
print(json.dumps(response.json(), indent=2))
