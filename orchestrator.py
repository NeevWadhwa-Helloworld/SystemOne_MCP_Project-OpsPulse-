"""Compatibility entrypoint for the OpsPulse orchestration pipeline."""

import asyncio

from backend.orchestrator import execute_pipeline


async def main() -> None:
    result = await execute_pipeline("Check health status for compute node primary")
    print(result)


if __name__ == "__main__":
    asyncio.run(main())
