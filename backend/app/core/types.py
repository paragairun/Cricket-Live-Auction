"""
SQLite-compatible column types.

PostgreSQL's ARRAY type is not supported on SQLite, so this module provides
a StringList type that stores a Python list of strings as JSON text. It works
on both PostgreSQL and SQLite, keeping the rest of the codebase unchanged.
"""
import json
from typing import Any

from sqlalchemy.types import TypeDecorator, TEXT


class StringList(TypeDecorator):
    """A list of strings stored as a JSON-encoded TEXT column."""

    impl = TEXT
    cache_ok = True

    def process_bind_param(self, value: Any | None, dialect) -> Any | None:
        if value is None:
            return None
        return json.dumps(list(value))

    def process_result_value(self, value: Any | None, dialect) -> list[str] | None:
        if value is None:
            return None
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return [value] if value else []
