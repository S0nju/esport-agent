import httpx
import pytest

from esport_agent.data import lolesports


def test_fetch_schedule_not_implemented() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={}))
    with httpx.Client(transport=transport) as client, pytest.raises(NotImplementedError):
        lolesports.fetch_schedule(client)
