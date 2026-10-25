from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
from typing import Any

from .events import EventLog
from .state import positive_ints, safe_int
from ..agent_activity import AgentActivityProjection


def result_fingerprint(result: dict[str, Any]) -> str:
    normalized = json.loads(json.dumps(result))
    return hashlib.sha256(json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass
class RunProgressProjection:
    """Rebuildable timing and usage for the latest attempt, including older workers."""

    cursor: int = 0
    started: dict[int, str] = field(default_factory=dict)
    finished: dict[int, str] = field(default_factory=dict)
    token_counts: dict[int, dict[str, int]] = field(default_factory=dict)
    phases: dict[int, str] = field(default_factory=dict)
    phase_timings: dict[int, dict[str, float]] = field(default_factory=dict)
    activities: dict[int, AgentActivityProjection] = field(default_factory=dict)
    run_phase: str = ""
    run_phase_timings: dict[str, float] = field(default_factory=dict)
    candidate_results: dict[int, str] = field(default_factory=dict)
    synthesis_sources: dict[int, dict[int, str]] = field(default_factory=dict)
    synthesis_results: dict[int, str] = field(default_factory=dict)
    candidate_generation: int = 0
    synthesis_generations: dict[int, int] = field(default_factory=dict)

    def refresh(self, events: EventLog, run_id: str, event_count: int) -> None:
        while self.cursor < event_count:
            page = events.read(run_id, cursor=self.cursor, limit=1000)
            for event in page["events"]:
                self.apply(event)
            next_cursor = page["next_cursor"]
            if next_cursor <= self.cursor:
                break
            self.cursor = next_cursor
            if not page["has_more"]:
                break

    def apply(self, event: dict[str, Any]) -> None:
        kind = event.get("type")
        idx = safe_int(event.get("idx"))
        timestamp = str(event.get("timestamp") or "")
        if kind == "run_phase":
            self.run_phase = str(event.get("phase") or "")
            timings = event.get("phase_timings")
            if isinstance(timings, dict):
                self.run_phase_timings.update({
                    str(name): seconds for name, seconds in timings.items()
                    if type(seconds) in {int, float} and math.isfinite(seconds) and seconds >= 0
                })
        elif kind in {"batch_created", "synthesis_started"}:
            if kind == "synthesis_started":
                sources = positive_ints(event.get("source_agents"))
                bindings = {agent: self.candidate_results[agent] for agent in sources
                    if agent in self.candidate_results}
                for agent in positive_ints(event.get("indices")):
                    self.synthesis_results.pop(agent, None)
                    self.synthesis_sources.pop(agent, None)
                    self.synthesis_generations.pop(agent, None)
                    if sources and len(bindings) == len(sources):
                        self.synthesis_sources[agent] = dict(bindings)
                        self.synthesis_generations[agent] = self.candidate_generation
            for agent in positive_ints(event.get("indices")):
                self.started.pop(agent, None)
                self.finished.pop(agent, None)
                self.token_counts.pop(agent, None)
                self.phases.pop(agent, None)
                self.phase_timings.pop(agent, None)
                self.activities.pop(agent, None)
        elif kind == "agent_line" and idx > 0:
            self.activities.setdefault(idx, AgentActivityProjection()).observe(event)
        elif kind == "agent_phase" and idx > 0:
            self.phases[idx] = str(event.get("phase") or "")
            timings = event.get("phase_timings")
            if isinstance(timings, dict):
                self.phase_timings.setdefault(idx, {}).update({
                    str(name): seconds for name, seconds in timings.items()
                    if type(seconds) in {int, float} and math.isfinite(seconds) and seconds >= 0
                })
        elif kind == "agent_started" and idx > 0:
            if self.started.get(idx) != timestamp:
                self.activities.pop(idx, None)
            self.started.pop(idx, None)
            self.finished.pop(idx, None)
            self.token_counts.pop(idx, None)
            if timestamp:
                self.started[idx] = timestamp
        elif kind == "agent_tokens" and idx > 0:
            counts = event.get("reasoning_token_counts")
            if isinstance(counts, dict):
                # These are cumulative snapshots, not deltas. Repeated delivery
                # must not count the same model step twice.
                self.token_counts[idx] = {
                    str(safe_int(tokens)): count
                    for tokens, count in counts.items()
                    if safe_int(tokens) > 0 and type(count) is int and count > 0
                }
        elif kind == "agent_finished" and idx > 0:
            result = event.get("result")
            if isinstance(result, dict) and safe_int(result.get("idx")) == idx:
                target = self.synthesis_results if result.get("role") == "synthesis" else self.candidate_results
                previous = target.get(idx)
                if result.get("status") == "success" or target is self.synthesis_results:
                    target[idx] = result_fingerprint(result)
                else:
                    target.pop(idx, None)
                if target is self.candidate_results and previous != target.get(idx):
                    self.candidate_generation += 1
            if timestamp:
                self.finished[idx] = timestamp
            if idx in self.activities:
                self.activities[idx].finish(timestamp)
        elif kind in {"run_finished", "run_failed", "run_discarded"} and timestamp:
            for agent in self.started:
                self.finished.setdefault(agent, timestamp)
            for activity in self.activities.values():
                activity.finish(timestamp)
