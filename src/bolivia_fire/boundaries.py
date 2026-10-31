from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urljoin

import httpx
import yaml
from filelock import FileLock

from bolivia_fire.config import Paths
from bolivia_fire.firms.storage import append_manifest, durable_write, read_manifest, sha256_file
from bolivia_fire.redact import Redactor

PUBLIC_HOSTS = {"github.com", "raw.githubusercontent.com", "media.githubusercontent.com"}
MAX_BOUNDARY_BYTES = 128 * 1024 * 1024


def _public_download(client: httpx.Client, url: str) -> bytes:
    for _ in range(6):
        parsed = httpx.URL(url)
        if (
            parsed.scheme != "https"
            or parsed.host not in PUBLIC_HOSTS
            or parsed.username
            or parsed.password
            or parsed.port not in (None, 443)
        ):
            raise RuntimeError("Boundary download URL is outside the public GitHub allowlist")
        with client.stream("GET", url, follow_redirects=False) as response:
            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    raise RuntimeError("Boundary redirect is missing Location")
                url = urljoin(url, location)
                continue
            if response.status_code != 200:
                raise RuntimeError(f"Boundary download HTTP {response.status_code}")
            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > MAX_BOUNDARY_BYTES:
                    raise RuntimeError("Boundary download exceeds size limit")
            return bytes(body)
    raise RuntimeError("Too many boundary download redirects")


def _validate_geojson(body: bytes, level: str) -> tuple[int, bool]:
    try:
        data = json.loads(body)
        features = data["features"]
        valid = (
            data["type"] == "FeatureCollection"
            and bool(features)
            and all(
                feature["type"] == "Feature"
                and feature["geometry"]["type"] in {"Polygon", "MultiPolygon"}
                and bool(feature["geometry"]["coordinates"])
                for feature in features
            )
        )
        santa_cruz = any(
            feature.get("properties", {}).get("shapeName", "").casefold() == "santa cruz"
            for feature in features
        )
    except (ValueError, KeyError, TypeError, AttributeError):
        valid, features, santa_cruz = False, [], False
    if not valid or (level == "ADM1" and not santa_cruz):
        raise RuntimeError(f"Invalid {level} GeoJSON or missing Santa Cruz department")
    return len(features), santa_cruz


def acquire_boundaries(
    paths: Paths | None = None,
    *,
    transport: httpx.BaseTransport | None = None,
) -> list[dict[str, Any]]:
    paths = paths or Paths()
    paths.temporary.mkdir(parents=True, exist_ok=True)
    sources_path = paths.datasets / "sources.yaml"
    manifest = paths.manifests / "boundaries.jsonl"
    records = []
    with (
        FileLock(str(paths.temporary / "boundaries.lock"), timeout=0),
        httpx.Client(
            verify=True,
            follow_redirects=False,
            transport=transport,
            timeout=httpx.Timeout(connect=20, read=120, write=30, pool=20),
        ) as client,
    ):
        sources = (
            yaml.safe_load(sources_path.read_text(encoding="utf-8"))
            if sources_path.exists()
            else {}
        )
        sources = sources or {}
        pins = sources.setdefault("boundaries", {})
        existing = read_manifest(manifest)
        for level in ("ADM0", "ADM1"):
            pinned = pins.get(level)
            metadata_url = f"https://www.geoboundaries.org/api/current/gbOpen/BOL/{level}/"
            if pinned:
                record = dict(pinned)
                path = (paths.root / record["path"]).resolve()
                raw_directory = (paths.datasets / "raw" / "boundaries").resolve()
                if not path.is_relative_to(raw_directory):
                    raise RuntimeError("Pinned boundary path is outside its raw directory")
                if path.exists():
                    if sha256_file(path) != record["sha256"]:
                        raise RuntimeError(f"Pinned {level} boundary hash mismatch")
                    _validate_geojson(path.read_bytes(), level)
                    if not any(item.get("sha256") == record["sha256"] for item in existing):
                        append_manifest(manifest, record, Redactor())
                    records.append(record)
                    continue
                body = _public_download(client, record["url"])
                if hashlib.sha256(body).hexdigest() != record["sha256"]:
                    raise RuntimeError(f"Pinned {level} download hash mismatch")
            else:
                response = client.get(metadata_url, follow_redirects=False)
                if response.status_code != 200:
                    raise RuntimeError(f"Boundary metadata HTTP {response.status_code}")
                metadata = response.json()
                if metadata.get("boundaryISO") != "BOL" or metadata.get("boundaryType") != level:
                    raise RuntimeError("Boundary metadata country or level mismatch")
                url = metadata.get("gjDownloadURL") or metadata.get("simplifiedGeometryGeoJSON")
                if not isinstance(url, str):
                    raise RuntimeError("Boundary metadata has no GeoJSON download URL")
                if not metadata.get("boundaryLicense") or not metadata.get("boundaryID"):
                    raise RuntimeError("Boundary metadata has no license or boundaryID")
                body = _public_download(client, url)
                digest = hashlib.sha256(body).hexdigest()
                path = (
                    paths.datasets
                    / "raw"
                    / "boundaries"
                    / f"geoBoundaries-BOL-{level}_{digest}.geojson"
                )
                record = {
                    "provider": "geoBoundaries",
                    "release": "gbOpen",
                    "country": "BOL",
                    "level": level,
                    "metadata_url": metadata_url,
                    "url": url,
                    "boundaryID": metadata["boundaryID"],
                    "version": httpx.URL(url).path.split("/raw/")[-1].split("/")[0],
                    "boundaryYearRepresented": metadata.get("boundaryYearRepresented"),
                    "sourceDataUpdateDate": metadata.get("sourceDataUpdateDate"),
                    "buildDate": metadata.get("buildDate"),
                    "license": metadata["boundaryLicense"],
                    "licenseDetail": metadata.get("licenseDetail"),
                    "licenseSource": metadata.get("licenseSource"),
                    "sha256": digest,
                    "bytes": len(body),
                    "path": path.relative_to(paths.root).as_posix(),
                    "retrieval_utc": datetime.now(UTC).isoformat(),
                }
            count, santa_cruz = _validate_geojson(body, level)
            record.update(features=count, contains_santa_cruz=santa_cruz)
            temporary = paths.temporary / f"boundary_{level}.geojson.part"
            durable_write(temporary, body)
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                if sha256_file(path) != record["sha256"]:
                    raise RuntimeError("Existing boundary snapshot has different bytes")
            else:
                os.replace(temporary, path)
            if not any(item.get("sha256") == record["sha256"] for item in existing):
                append_manifest(manifest, record, Redactor())
                existing.append(record)
            pins[level] = record
            pending = paths.temporary / "sources.yaml.part"
            durable_write(pending, yaml.safe_dump(sources, sort_keys=False).encode())
            os.replace(pending, sources_path)
            records.append(record)
    return records
