"""Pipeline settings preserve operational and shared-database invariants."""

from job_discovery import settings as discovery_settings
from job_pipeline import settings


def test_retry_policy_is_positive() -> None:
    assert settings.RETRY_LIMIT > 0
    assert settings.RETRY_BACKOFF.total_seconds() > 0


def test_database_path_is_the_shared_object() -> None:
    assert settings.DATABASE_PATH is discovery_settings.DATABASE_PATH
