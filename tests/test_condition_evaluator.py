import pytest
from condition_evaluator import SafeConditionEvaluator

def test_simple_compare():
    assert SafeConditionEvaluator.evaluate("state['x'] > 5", {"x": 10})

def test_state_get_allowed():
    assert SafeConditionEvaluator.evaluate("state.get('i', 0) < 5", {"i": 2})

def test_disallowed_call():
    with pytest.raises(ValueError):
        SafeConditionEvaluator.evaluate("__import__('os').system('echo hi')", {})
