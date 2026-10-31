from __future__ import annotations

import logging
import re
import traceback
from collections.abc import Iterable
from typing import Any
from urllib.parse import quote, quote_plus

REDACTED = "<redacted>"
_PATH_KEY = re.compile(r"(/api/(?:area|country|data_availability)/(?:csv|json)/)[^/\s?]+", re.I)
_QUERY_KEY = re.compile(r"([?&](?:MAP_KEY|FIRMS_MAP_KEY)=)[^&\s\"'<>]+", re.I)


class Redactor:
    def __init__(self, secrets: Iterable[str] = ()) -> None:
        self._secrets = sorted(
            {
                variant
                for key in secrets
                if key
                for variant in (key, quote(key, safe=""), quote_plus(key))
            },
            key=len,
            reverse=True,
        )

    def text(self, value: object) -> str:
        result = str(value)
        for secret in self._secrets:
            result = result.replace(secret, REDACTED)
        result = _PATH_KEY.sub(r"\1" + REDACTED, result)
        return _QUERY_KEY.sub(r"\1" + REDACTED, result)

    def sanitize(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {self.text(key): self.sanitize(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.sanitize(item) for item in value]
        if isinstance(value, (str, BaseException)):
            return self.text(value)
        return value


class RedactionFilter(logging.Filter):
    def __init__(self, redactor: Redactor) -> None:
        super().__init__()
        self.redactor = redactor

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self.redactor.text(record.getMessage())
        record.args = ()
        if record.exc_info:
            record.exc_text = self.redactor.text(
                "".join(traceback.format_exception(*record.exc_info))
            )
            record.exc_info = None
        if record.exc_text:
            record.exc_text = self.redactor.text(record.exc_text)
        if record.stack_info:
            record.stack_info = self.redactor.text(record.stack_info)
        return True
