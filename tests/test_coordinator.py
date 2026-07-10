"""Tests for the draft-quality benchmark ranking (coordinator.py).

The ranking must put draft quality ahead of speed — the whole point of
the 0.12.0 redesign. The old benchmark ranked a fast, eager model over a
disciplined one, which is how an 8B that needs the fixer safety net got
recommended over qwen2.5-coder.
"""

from custom_components.helmsman.coordinator import _benchmark_sort_key


def _result(model, clean, passed, repairs, avg, completed=3, error=None):
    return {
        "model": model,
        "clean": clean,
        "passed": passed,
        "repairs": repairs,
        "avg_seconds": avg,
        "samples": [{"seconds": 1.0}] * completed,
        "error": error,
    }


def test_clean_drafts_beat_speed():
    """A slower model with more clean drafts wins over a fast eager one."""
    fast_eager = _result("llama8b", clean=0, passed=3, repairs=6, avg=3.0)
    slow_clean = _result("qwen14b", clean=3, passed=3, repairs=0, avg=12.0)
    ranked = sorted([fast_eager, slow_clean], key=_benchmark_sort_key)
    assert [r["model"] for r in ranked] == ["qwen14b", "llama8b"]


def test_passed_beats_failed_regardless_of_speed():
    fast_fail = _result("fast", clean=0, passed=0, repairs=0, avg=1.0)
    slow_pass = _result("slow", clean=0, passed=2, repairs=4, avg=20.0)
    ranked = sorted([fast_fail, slow_pass], key=_benchmark_sort_key)
    assert ranked[0]["model"] == "slow"


def test_fewer_repairs_break_ties():
    """Equal clean + passed: the model needing fewer repairs ranks first."""
    messy = _result("messy", clean=0, passed=3, repairs=9, avg=5.0)
    tidy = _result("tidy", clean=0, passed=3, repairs=2, avg=5.0)
    ranked = sorted([messy, tidy], key=_benchmark_sort_key)
    assert ranked[0]["model"] == "tidy"


def test_speed_is_last_tiebreak():
    """Only when quality is identical does speed decide."""
    slow = _result("slow", clean=3, passed=3, repairs=0, avg=30.0)
    fast = _result("fast", clean=3, passed=3, repairs=0, avg=8.0)
    ranked = sorted([slow, fast], key=_benchmark_sort_key)
    assert ranked[0]["model"] == "fast"
