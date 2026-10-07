"""Opt-in synchronous test client using HTTPX ASGI requests on the caller's loop.

The periodic callback lets worker-thread completions reach the event loop in
managed environments with restricted cross-thread wakeups. Application routes,
middleware, lifespan and background jobs still execute normally. These checks
exercise HTTP contracts in process; listening-server and desktop checks are separate.
"""

import asyncio
import threading

import httpx


class InProcessTransport(httpx.BaseTransport):
    def __init__(self, app, runner):
        self.runner = runner
        self.owner_thread = threading.get_ident()
        self.asgi = httpx.ASGITransport(app=app)

    def handle_request(self, request):
        outgoing = httpx.Request(
            request.method,
            request.url,
            headers=request.headers,
            content=request.read(),
            extensions=request.extensions,
        )

        async def send():
            response = await self.asgi.handle_async_request(outgoing)
            try:
                return httpx.Response(
                    response.status_code,
                    headers=response.headers,
                    content=await response.aread(),
                    extensions=response.extensions,
                )
            finally:
                await response.aclose()

        if threading.get_ident() == self.owner_thread:
            return self.runner.run(send())
        # An upload may be paused while another caller requests progress/cancellation.
        # Each caller thread needs its own loop; serializing requests would deadlock it.
        with asyncio.Runner() as caller:
            pulse = None

            def tick():
                nonlocal pulse
                pulse = caller.get_loop().call_later(0.02, tick)

            tick()
            try:
                return caller.run(send())
            finally:
                pulse.cancel()

    def close(self):
        self.runner.run(self.asgi.aclose())


class InProcessClient(httpx.Client):
    def __init__(self, app, **kwargs):
        self.app = app
        self.runner = asyncio.Runner()
        self.lifespan = app.router.lifespan_context(app)
        self.pulse = None
        kwargs.setdefault("follow_redirects", True)
        kwargs.setdefault("headers", {"user-agent": "testclient"})
        super().__init__(transport=InProcessTransport(app, self.runner), **kwargs)

    def tick(self):
        self.pulse = self.runner.get_loop().call_later(0.02, self.tick)

    def stop_loop(self):
        if self.pulse is not None:
            self.pulse.cancel()
        self.runner.close()

    def __enter__(self):
        self.runner.__enter__()
        self.tick()
        try:
            self.runner.run(self.lifespan.__aenter__())
            return super().__enter__()
        except BaseException:
            self.stop_loop()
            raise

    def __exit__(self, exc_type, exc, traceback):
        try:
            super().__exit__(exc_type, exc, traceback)
        finally:
            try:
                self.runner.run(self.lifespan.__aexit__(exc_type, exc, traceback))
            finally:
                self.stop_loop()
