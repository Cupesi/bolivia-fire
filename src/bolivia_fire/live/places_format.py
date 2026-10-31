from __future__ import annotations

from typing import Any

STRING_COLUMNS = (
    "department",
    "province",
    "municipality",
    "nearest_place_name",
    "nearest_place_feature_code",
)
FORMAT = "dictionary-v1"


def encode_places(columns: dict[str, Any]) -> dict[str, Any]:
    strings = sorted(
        {value for key in STRING_COLUMNS for value in columns[key] if value is not None}
    )
    indices = {value: index for index, value in enumerate(strings)}
    return {
        **columns,
        "format": FORMAT,
        "strings": strings,
        **{
            key: [None if value is None else indices[value] for value in columns[key]]
            for key in STRING_COLUMNS
        },
    }


def decode_places(payload: dict[str, Any]) -> dict[str, Any]:
    if "format" not in payload:
        return payload
    if payload["format"] != FORMAT:
        raise ValueError("Unsupported place data format")
    strings = payload["strings"]
    return {
        **{key: value for key, value in payload.items() if key not in ("format", "strings")},
        **{
            key: [None if index is None else strings[index] for index in payload[key]]
            for key in STRING_COLUMNS
        },
    }
