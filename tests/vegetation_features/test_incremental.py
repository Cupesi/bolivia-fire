from pathlib import Path

import pandas as pd
import pytest

from bolivia_fire.vegetation_features.pipeline import cached_table


def test_synthetic_incremental_extension_idempotence_and_corruption(tmp_path: Path):
    first = pd.DataFrame({"cell_id": ["a"], "ndvi": [0.5]})
    path = tmp_path / "2013.parquet"
    assert cached_table(path, "source-one", lambda: first) is True
    original = path.read_bytes(), path.stat().st_mtime_ns

    def should_not_run():
        raise AssertionError("Finished composite was recomputed")

    assert cached_table(path, "source-one", should_not_run) is False
    assert (path.read_bytes(), path.stat().st_mtime_ns) == original
    second = tmp_path / "2014.parquet"
    assert cached_table(second, "source-two", lambda: first) is True
    assert cached_table(path, "source-one", should_not_run) is False
    assert (path.read_bytes(), path.stat().st_mtime_ns) == original
    path.write_bytes(b"synthetic corruption")
    with pytest.raises(ValueError, match="checksum"):
        cached_table(path, "source-one", should_not_run)
