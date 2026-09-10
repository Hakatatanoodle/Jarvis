"""Light smoke tests for infra/ (not a §6 contract, but §9 M0 scope)."""
from infra.logging import get_logger
from config.loader import load_config


def test_get_logger_returns_namespaced_logger():
    log = get_logger("goals")
    assert log.name == "jarvis.goals"


def test_get_logger_is_idempotent_singleton_root():
    log1 = get_logger("goals")
    log2 = get_logger("planner")
    assert log1.parent is log2.parent  # same configured root


def test_default_config_loads_and_has_expected_version():
    cfg = load_config()
    assert cfg.version == 1


def test_dotted_get_with_missing_key_returns_default():
    cfg = load_config()
    assert cfg.get("nonexistent.path", "fallback") == "fallback"


def test_dotted_get_reaches_nested_values():
    cfg = load_config()
    assert cfg.get("storage.postgres.database") == "jarvis"
