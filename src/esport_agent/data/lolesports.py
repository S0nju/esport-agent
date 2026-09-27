"""Fetching the schedule from the unofficial lolesports API (`getSchedule`).

This API is undocumented and may change without notice: everything that depends on it
stays isolated in this module. It is only called by `sync.py`, never by the tools.
"""

import httpx


def fetch_schedule(client: httpx.Client) -> dict[str, object]:
    """Fetch the raw JSON response of `getSchedule` (exact format to be checked)."""
    raise NotImplementedError
