import time
import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from agent.config import Settings
from agent.netsuite import NetSuite, NetSuiteError


async def test_oauth_assertion_signature_scope_and_token_cache(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    path = tmp_path / "private.pem"
    path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    s = Settings(_env_file=None, account="123_SB1", client_id="client", certificate_id="cert", private_key_path=path,
                 restlet_url="https://123-sb1.restlets.api.netsuite.com/app/site/hosting/restlet.nl?script=1&deploy=1")
    calls = []
    def handler(request):
        calls.append(request)
        if request.url.path.endswith("/token"):
            from urllib.parse import parse_qs
            form = parse_qs(request.content.decode())
            assertion = form["client_assertion"][0]
            claims = jwt.decode(assertion, key.public_key(), algorithms=["PS256"], audience=s.token_url)
            assert claims["scope"] == ["restlets"]
            assert claims["iss"] == "client"
            assert jwt.get_unverified_header(assertion)["kid"] == "cert"
            return httpx.Response(200, json={"access_token": "test-token", "expires_in": 3600})
        assert request.headers["Authorization"] == "Bearer test-token"
        return httpx.Response(200, json={"ok": True})
    ns = NetSuite(s, httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    await ns.call("ping")
    await ns.call("ping")
    assert len(calls) == 3
    await ns.close()


@pytest.mark.parametrize("url", ["http://127.0.0.1/api", "https://evil.com/api", "https://123-sb1.restlets.api.netsuite.com.evil.com/app/site/hosting/restlet.nl"])
def test_credentials_cannot_be_sent_to_other_hosts(url):
    with pytest.raises(ValueError):
        Settings(_env_file=None, account="123_SB1", restlet_url=url)


def test_external_ai_endpoint_rejected():
    with pytest.raises(ValueError):
        Settings(_env_file=None, ollama_url="https://api.external-ai.com")


def test_cloud_model_tag_rejected():
    with pytest.raises(ValueError):
        Settings(_env_file=None, model="qwen3.5:cloud")
