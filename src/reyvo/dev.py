from __future__ import annotations

import asyncio


def selector_loop_factory(use_subprocess: bool = False) -> asyncio.AbstractEventLoop:
    """Uvicorn loop factory for Windows.

    psycopg's async driver cannot run on the ProactorEventLoop, which uvicorn
    selects by default on Windows. Use with:
        uvicorn reyvo.main:app --loop reyvo.dev:selector_loop_factory
    """

    return asyncio.SelectorEventLoop()


def main() -> None:
    import uvicorn

    uvicorn.run(
        "reyvo.main:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
        loop="reyvo.dev:selector_loop_factory",
    )


if __name__ == "__main__":
    main()
