from __future__ import annotations

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "local_data: requires the local frozen SP data and artifacts"
    )
