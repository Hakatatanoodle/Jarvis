"""Tests for §6.13 UserModel — derived projection, override always wins."""
from contracts.user_model import UserModel


def test_effective_value_falls_back_to_computed_when_no_override():
    um = UserModel(attribute_category="preferences", attribute_key="preferred_ide", computed_value="VS Code")
    assert um.effective_value == "VS Code"


def test_override_always_wins_when_present():
    um = UserModel(
        attribute_category="preferences",
        attribute_key="preferred_ide",
        computed_value="VS Code",
        override_value="Vim",
    )
    assert um.effective_value == "Vim"


# NOTE: "UserModel has no independent write path — only derived and
# overridden" (decision #12, §4) is a process invariant enforced by
# user_model/api.py's write path (M8), not something a single-instance
# model test can meaningfully assert. Not faked here with a no-op test.
