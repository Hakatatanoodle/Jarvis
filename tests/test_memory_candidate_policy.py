"""Pure unit tests — no DB, no LLM. candidate_policy.py is deterministic
Python by design (V1-M3 Decision 3), so it should be fully testable
without either.
"""
from contracts.enums import MemoryType
from contracts.memory_candidate import MemoryCandidate, MemoryScope, Sensitivity
from memory.candidate_policy import PolicyOutcome, assess_sensitivity, decide


def _candidate(**overrides) -> MemoryCandidate:
    defaults = dict(
        type=MemoryType.PREFERENCE,
        title="user's preferred code editor",
        value="VS Code",
        scope=MemoryScope.DURABLE,
        explicit=True,
        raw_user_text="I prefer VS Code",
    )
    defaults.update(overrides)
    return MemoryCandidate(**defaults)


def test_safe_durable_explicit_writes_immediately():
    d = decide(_candidate())
    assert d.outcome is PolicyOutcome.WRITE_DURABLE


def test_ambiguous_scope_asks_confirmation():
    d = decide(_candidate(scope=MemoryScope.AMBIGUOUS))
    assert d.outcome is PolicyOutcome.ASK_CONFIRMATION


def test_session_scope_is_no_op():
    d = decide(_candidate(scope=MemoryScope.SESSION))
    assert d.outcome is PolicyOutcome.NO_OP


def test_taxonomy_gap_when_type_is_none():
    d = decide(_candidate(type=None))
    assert d.outcome is PolicyOutcome.TAXONOMY_GAP


def test_non_explicit_is_no_op_backstop():
    d = decide(_candidate(explicit=False))
    assert d.outcome is PolicyOutcome.NO_OP


def test_password_blocked_even_when_durable_and_explicit():
    d = decide(_candidate(title="my password", value="hunter2 is my password", scope=MemoryScope.DURABLE))
    assert d.outcome is PolicyOutcome.BLOCK_SENSITIVE


def test_api_key_blocked():
    c = _candidate(value="my api key is sk-abc123xyz")
    assert assess_sensitivity(c) is Sensitivity.SENSITIVE
    assert decide(c).outcome is PolicyOutcome.BLOCK_SENSITIVE


def test_sensitivity_check_looks_at_raw_text_too():
    # title/value clean, but the raw utterance itself names a credential
    c = _candidate(title="a note", value="keep this safe", raw_user_text="my ssn is 123-45-6789")
    assert assess_sensitivity(c) is Sensitivity.SENSITIVE


def test_ordinary_preference_is_not_sensitive():
    c = _candidate(value="I like rock music while coding")
    assert assess_sensitivity(c) is Sensitivity.SAFE


def test_block_takes_priority_over_ambiguous_scope():
    # sensitivity must be checked before scope — a sensitive+ambiguous
    # candidate must still be hard-blocked, not merely asked about.
    d = decide(_candidate(value="my password is x", scope=MemoryScope.AMBIGUOUS))
    assert d.outcome is PolicyOutcome.BLOCK_SENSITIVE
