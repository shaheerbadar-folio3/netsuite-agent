import httpx
from agent.app import create_app


async def test_management_routes_require_auth(settings):
    app = create_app(settings)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        assert (await client.get('/health')).status_code == 200
        assert (await client.get('/status')).status_code == 401
        assert (await client.post('/schema/refresh')).status_code == 401
        assert (await client.get('/docs')).status_code == 404
