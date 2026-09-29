"""Read-only connectivity/metadata checks; never prints credentials or business rows."""
import asyncio
import json
import time

from agent.config import Settings
from agent.netsuite import NetSuite


async def main():
    ns = NetSuite(Settings())
    try:
        for action, payload in [
            ("schema_inventory", {}),
            ("schema_probe", {"tables": ["customer"]}),
            ("schema_probe", {"tables": ["transaction"]}),
            ("schema_probe", {"tables": ["transactionline"]}),
        ]:
            started = time.monotonic()
            result = {"action": action, **payload}
            try:
                response = await ns.call(action, **payload)
                result["ok"] = not response.get("failures")
                if action == "schema_inventory":
                    result["table_count"] = len(response.get("tables", []))
                else:
                    result["discovered"] = [
                        {"name": t["name"], "field_count": len(t["fields"])}
                        for t in response.get("tables", [])
                    ]
                    result["failures"] = response.get("failures", [])
            except Exception as exc:
                # Exception representations can contain sensitive URLs or headers.
                result.update(ok=False, error=type(exc).__name__)
            result["seconds"] = round(time.monotonic() - started, 2)
            print(json.dumps(result, indent=2), flush=True)
    finally:
        await ns.close()


if __name__ == "__main__":
    asyncio.run(main())
