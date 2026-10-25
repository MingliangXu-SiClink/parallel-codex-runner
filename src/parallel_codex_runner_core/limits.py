from __future__ import annotations


# Agent totals are intentionally independent from execution concurrency. This
# guard protects request/state sizes without turning a typical workstation's
# parallel capacity into a product limit.
MAX_AGENT_COUNT = 4_096


def validate_agent_count(value: int, name: str, *, allow_zero: bool = False) -> int:
    minimum = 0 if allow_zero else 1
    if value < minimum:
        qualifier = "non-negative" if allow_zero else "greater than zero"
        raise ValueError(f"{name} must be {qualifier}")
    if value > MAX_AGENT_COUNT:
        raise ValueError(
            f"{name} must not exceed {MAX_AGENT_COUNT}; reduce the total Agent "
            "count while controlling concurrency separately with max_parallel"
        )
    return value
