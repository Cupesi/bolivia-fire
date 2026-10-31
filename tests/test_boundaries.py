from __future__ import annotations

import hashlib
import json
from pathlib import Path

import httpx
import pytest
import yaml

from bolivia_fire.boundaries import acquire_boundaries
from bolivia_fire.config import Paths
from bolivia_fire.firms.storage import read_manifest


def synthetic_geojson(level: str, name: str = "Santa Cruz") -> bytes:
    return json.dumps(
        {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"shapeName": name if level == "ADM1" else "Bolivia"},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]],
                    },
                }
            ],
        }
    ).encode()


def test_boundary_pin_redirect_and_idempotent_resume(tmp_path: Path) -> None:
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        level = "ADM1" if "ADM1" in request.url.path else "ADM0"
        if request.url.host == "www.geoboundaries.org":
            return httpx.Response(
                200,
                json={
                    "boundaryISO": "BOL",
                    "boundaryType": level,
                    "boundaryID": f"synthetic-{level}",
                    "boundaryLicense": "Public Domain",
                    "boundaryYearRepresented": "2015",
                    "gjDownloadURL": f"https://github.com/synthetic/raw/abc/{level}.geojson",
                },
            )
        if request.url.host == "github.com":
            return httpx.Response(
                302,
                headers={
                    "Location": f"https://raw.githubusercontent.com/synthetic/abc/{level}.geojson",
                },
            )
        return httpx.Response(200, content=synthetic_geojson(level))

    paths = Paths(tmp_path)
    records = acquire_boundaries(paths, transport=httpx.MockTransport(respond))
    assert len(calls) == 6
    assert len(records) == 2
    assert records[1]["contains_santa_cruz"] is True
    assert records[0]["sha256"] == hashlib.sha256(synthetic_geojson("ADM0")).hexdigest()
    assert records[0]["version"] == "abc"
    pins = yaml.safe_load((paths.datasets / "sources.yaml").read_text())["boundaries"]
    assert pins["ADM1"]["boundaryID"] == "synthetic-ADM1"
    acquire_boundaries(
        paths,
        transport=httpx.MockTransport(
            lambda request: pytest.fail("Pinned valid files must not request data"),
        ),
    )
    assert len(read_manifest(paths.manifests / "boundaries.jsonl")) == 2


@pytest.mark.parametrize(
    "redirect",
    [
        "https://example.org/leak",
        "http://raw.githubusercontent.com/file",
        "https://github.com.evil.org/file",
        "https://user:password@github.com/file",
    ],
)
def test_boundary_redirect_allowlist(tmp_path: Path, redirect: str) -> None:
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.host == "www.geoboundaries.org":
            return httpx.Response(
                200,
                json={
                    "boundaryISO": "BOL",
                    "boundaryType": "ADM0",
                    "boundaryID": "synthetic-id",
                    "boundaryLicense": "Public Domain",
                    "gjDownloadURL": "https://github.com/file",
                },
            )
        return httpx.Response(302, headers={"Location": redirect})

    with pytest.raises(RuntimeError, match="allowlist"):
        acquire_boundaries(Paths(tmp_path), transport=httpx.MockTransport(respond))
    assert len(calls) == 2


def test_missing_santa_cruz_rejects_adm1(tmp_path: Path) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        level = "ADM1" if "ADM1" in request.url.path else "ADM0"
        if request.url.host == "www.geoboundaries.org":
            return httpx.Response(
                200,
                json={
                    "boundaryISO": "BOL",
                    "boundaryType": level,
                    "boundaryID": "synthetic-id",
                    "boundaryLicense": "Public Domain",
                    "gjDownloadURL": f"https://github.com/{level}.geojson",
                },
            )
        return httpx.Response(200, content=synthetic_geojson(level, "Other"))

    with pytest.raises(RuntimeError, match="Santa Cruz"):
        acquire_boundaries(Paths(tmp_path), transport=httpx.MockTransport(respond))
    assert len(read_manifest(Paths(tmp_path).manifests / "boundaries.jsonl")) == 1
