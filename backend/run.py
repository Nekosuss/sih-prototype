"""
Dev launcher — use this instead of `python -m uvicorn app.main:app` on
Windows.

Why this file exists: on Windows, asyncio's default ProactorEventLoop logs a
harmless but noisy "ConnectionResetError" traceback from
_ProactorBasePipeTransport whenever a browser closes a keep-alive HTTP
connection (which it does routinely). Switching to SelectorEventLoop avoids
it. That switch has to happen *before* uvicorn creates its event loop —
`uvicorn.run()` calls `asyncio.run()`, which creates the loop using
whatever policy is active at that exact call, then imports the app
afterwards. So setting the policy inside app/main.py is too late when
launched via the `uvicorn` CLI; it has to happen here, first.

Note: this uvicorn version (0.40.0) hard-codes ProactorEventLoop on Windows
inside uvicorn.run() itself (uvicorn/loops/asyncio.py:asyncio_loop_factory),
passed to asyncio.run() as an explicit loop_factory — which overrides
whatever event loop policy is set beforehand. So setting the policy alone
(as this file used to do) is not enough; we also have to call
Server.serve() directly inside our own asyncio.run(), instead of going
through uvicorn.run()/Server.run(), so nothing overrides our policy's loop.

Usage:
    python run.py
"""
import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import uvicorn


async def main():
    config = uvicorn.Config("app.main:app", host="127.0.0.1", port=8000, reload=False)
    server = uvicorn.Server(config)
    await server.serve()


if __name__ == "__main__":
    asyncio.run(main())
