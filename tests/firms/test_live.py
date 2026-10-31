from __future__ import annotations

import pytest

from bolivia_fire.config import Paths, load_key
from bolivia_fire.firms.client import FirmsClient
from bolivia_fire.firms.quota import Quota


@pytest.mark.live
def test_live_firms_status() -> None:
    paths = Paths()
    quota = Quota(paths.temporary)
    with FirmsClient(load_key(), paths.quarantine, quota=quota) as client:
        status = client.status(fresh=True)
    assert status["transaction_limit"] == 5000
    assert type(status["current_transactions"]) is int
    assert status["current_transactions"] >= 0
    assert status["transaction_interval"] == "10 minutes"
