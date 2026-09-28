"""Shared helpers for the offline test suite (not collected as tests)."""

from pathlib import Path
from types import SimpleNamespace


def fake_settings(data_dir, **overrides):
    """A stand-in for ``annas_api.api.Settings`` covering every attribute."""
    values = dict(
        data_dir=Path(data_dir),
        workers=1,
        # Unset by default so a fresh fixture really is a fresh deployment;
        # tests that exercise the legacy token pass it explicitly.
        api_token=None,
        signing_key="secret",
        file_url_ttl=300,
        retention_seconds=24 * 3600,
        public_base_url="",
        admin_username="admin",
        admin_password=None,
        token_username="api-token",
        registration_open=False,
        session_ttl_seconds=3600,
        session_secure=False,
    )
    values.update(overrides)
    return SimpleNamespace(**values)
