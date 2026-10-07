"""Bounded background inference, independent of the live translation thread pool."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import functools
import threading


_client_creation_lock = threading.Lock()


class ThreadLocalAWSClient:
    """Reuse connections within a worker, never a mutable SSLContext across workers.

    The local OpenSSL crashed in certificate verification during concurrent first
    requests on one botocore client. Each worker owns its client/connection pool;
    boto3 Session.client construction is serialized because the session is shared.
    Resolve the client when called, not when the method is passed to an executor.
    """

    def __init__(self, session, service, **options):
        self.session, self.service, self.options = session, service, options
        self.local = threading.local()

    def __getattr__(self, operation):
        if operation.startswith("_"):
            raise AttributeError(operation)

        def call(*args, **kwargs):
            if not hasattr(self.local, "client"):
                with _client_creation_lock:
                    self.local.client = self.session.client(self.service, **self.options)
            return getattr(self.local.client, operation)(*args, **kwargs)
        return call


class BackgroundInference:
    def __init__(self, live=lambda: False):
        self.live = live
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="materials")
        self.active = 0
        self.waiting = 0
        self.closed = False

    async def invoke(self, function, kwargs):
        self.waiting += 1
        try:
            while self.active >= (1 if self.live() else 2):
                if self.closed:
                    raise asyncio.CancelledError()
                await asyncio.sleep(0.1)
            if self.closed:
                raise asyncio.CancelledError()
            self.active += 1
        finally:
            self.waiting -= 1
        future = asyncio.get_running_loop().run_in_executor(self.pool, functools.partial(function, **kwargs))
        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            # Cancelling a coroutine cannot stop an SDK request. Keep its slot occupied until the
            # bounded SDK timeout ends; replacing documents must not accumulate detached calls.
            try:
                await asyncio.shield(future)
            except Exception:
                pass
            raise
        finally:
            self.active -= 1

    def close(self):
        self.closed = True
        self.pool.shutdown(wait=False, cancel_futures=True)
