"""Shared PG fixtures for the RLS leak suite.

Re-exports the session-scoped Postgres + Alembic fixtures from
``tests/repositories/conftest.py`` so security tests can opt in
without re-implementing the boilerplate.
"""
from tests.repositories.conftest import _migrated_pg, _pg_url

__all__ = ["_migrated_pg", "_pg_url"]
