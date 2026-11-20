"""
SQLite-compatible column types.

PostgreSQL's ARRAY type is not supported on SQLite, so this module provides
a StringList type that stores a Python list of strings as JSON text. It works
on both PostgreSQL and SQLite, keeping the rest of the codebase unchanged.
"""
import enum
import json
from typing import Any, Type

from sqlalchemy.types import TEXT, String, TypeDecorator


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


class EnumString(TypeDecorator):
    """A Python Enum stored as a plain VARCHAR, on every backend.

    New in 2.0.0 (2026-11-20). The models previously used
    `sa.Enum(X, name="playerauctionstatus", create_type=False)`. `create_type=False`
    tells SQLAlchemy that some migration has already created the native
    PostgreSQL ENUM type — but the `add_enum_types` migration is a no-op whose
    own comment claims it creates them. The types therefore never existed, while
    SQLAlchemy still emitted `$1::playerauctionstatus` casts, so any query
    filtering on status failed outright against PostgreSQL.

    Storing the value as VARCHAR matches what the columns actually are, works
    the same on SQLite and PostgreSQL, and avoids the ALTER TYPE dance every
    time a status is added. Validity is enforced in Python, as before.
    """

    impl = String
    cache_ok = True

    def __init__(self, enum_class: Type[enum.Enum], length: int = 32, **kwargs):
        self.enum_class = enum_class
        super().__init__(length=length, **kwargs)

    def process_bind_param(self, value: Any | None, dialect) -> str | None:
        if value is None:
            return None
        if isinstance(value, self.enum_class):
            return value.value
        # Accept the raw string too, so filters can be written either way.
        return self.enum_class(value).value

    def process_result_value(self, value: Any | None, dialect):
        if value is None:
            return None
        try:
            return self.enum_class(value)
        except ValueError:
            # Unknown value in the database: hand back the raw string rather
            # than crashing a whole page load.
            return value
