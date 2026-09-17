import asyncio
import json
import time
import jwt
import httpx


class NetSuiteError(RuntimeError):
    pass


class NetSuite:
    def __init__(self, settings, client=None):
        self.settings = settings
        self.client = client or httpx.AsyncClient(timeout=60, follow_redirects=False, trust_env=False)
        self._token = ""
        self._expires = 0
        self._lock = asyncio.Lock()

    async def token(self):
        async with self._lock:
            if self._expires > time.time() + 60:
                return self._token
            s = self.settings
            s.check_credentials()
            now = int(time.time())
            assertion = jwt.encode(
                {"iss": s.client_id, "scope": ["restlets"], "aud": s.token_url,
                 "iat": now, "exp": now + 300},
                s.private_key_path.read_bytes(), algorithm=s.jwt_algorithm,
                headers={"kid": s.certificate_id, "typ": "JWT"})
            response = await self.client.post(s.token_url, data={
                "grant_type": "client_credentials",
                "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
                "client_assertion": assertion})
            if response.status_code != 200:
                raise NetSuiteError(f"OAuth token exchange failed (HTTP {response.status_code}); check certificate mapping")
            body = response.json()
            self._token = body["access_token"]
            self._expires = time.time() + int(body.get("expires_in", 3600))
            return self._token

    async def call(self, action, **payload):
        # No automatic retries of mutations: claim and completion have explicit recovery semantics.
        response = await self.client.post(self.settings.restlet_url,
            headers={"Authorization": "Bearer " + await self.token()},
            json={"action": action, "worker": self.settings.worker_id, **payload})
        if response.status_code == 401:
            self._expires = 0
        if response.status_code >= 400:
            raise NetSuiteError(f"NetSuite HTTP {response.status_code} during {action}")
        try:
            body = response.json()
            if isinstance(body, str):
                body = json.loads(body)
        except ValueError as exc:
            raise NetSuiteError("NetSuite returned invalid JSON") from exc
        if not isinstance(body, dict) or not body.get("ok"):
            error = body.get("error", {}) if isinstance(body, dict) else {}
            # Only query errors are supplied to model repair, never HTTP bodies or credentials.
            raise NetSuiteError(str(error.get("message", "NetSuite operation failed"))[:1500])
        return body

    async def close(self):
        await self.client.aclose()
