"""Per-HTTP accounting in a dedicated AKShare subprocess (never patch the API process)."""
from contextlib import contextmanager
from unittest.mock import patch

from scripts.market_probe import ProbeFailure
from .market_usage import MarketBlocked


class _StopSDK(BaseException):
    """Exit an SDK internal retry loop immediately when its request budget is closed."""



@contextmanager
def metered_sdk(meter):
    import requests
    from urllib3.connectionpool import HTTPConnectionPool

    original_request = HTTPConnectionPool._make_request
    original_send = requests.adapters.HTTPAdapter.send

    def request(pool, connection, method, url, *args, **kwargs):
        def invoke():
            response = original_request(pool, connection, method, url, *args, **kwargs)
            if response.status >= 400:
                response.close()
                raise ProbeFailure(f"HTTP_ERROR: status={response.status}", http_status=response.status)
            return response
        try:
            return meter.request(invoke)
        except MarketBlocked as exc:
            raise _StopSDK(str(exc)) from None
        except ProbeFailure as exc:
            if exc.metadata.get("http_status") in (401, 403, 429):
                raise _StopSDK(str(exc)) from None
            raise

    def send(adapter, prepared, **kwargs):
        kwargs["timeout"] = (4, 8)
        return original_send(adapter, prepared, **kwargs)

    with patch.object(HTTPConnectionPool, "_make_request", request), patch.object(requests.adapters.HTTPAdapter, "send", send):
        try:
            yield
        except _StopSDK as exc:
            raise MarketBlocked(str(exc)) from None
