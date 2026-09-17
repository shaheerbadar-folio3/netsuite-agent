"""Run fixed read-only NetSuite probes without invoking the LLM or creating a job."""
import asyncio
import json
from agent.config import Settings
from agent.netsuite import NetSuite

async def main():
    ns = NetSuite(Settings())
    try:
        print(json.dumps(await ns.call("query_diagnostics"), indent=2))
    finally:
        await ns.close()

if __name__ == "__main__":
    asyncio.run(main())
