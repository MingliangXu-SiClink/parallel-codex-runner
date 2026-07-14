from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


AGENT_ROLE_CANDIDATE = "candidate"
AGENT_ROLE_SYNTHESIS = "synthesis"


def normalize_agent_role(value: object) -> str:
    return (
        AGENT_ROLE_SYNTHESIS
        if str(value or "").strip().lower() == AGENT_ROLE_SYNTHESIS
        else AGENT_ROLE_CANDIDATE
    )


@dataclass
class AgentResult:
    idx: int
    workspace_dir: str
    meta_dir: str
    codex_home: str
    stdout_log: str
    stderr_log: str
    final_message: str
    command: List[str]
    returncode: Optional[int]
    status: str
    seconds: float
    codex_thread_id: Optional[str] = None
    reasoning_tokens: Optional[int] = None
    reasoning_token_values: List[int] = field(default_factory=list)
    reasoning_token_counts: Dict[int, int] = field(default_factory=dict)
    error: Optional[str] = None
    stdout_tail: str = ""
    stderr_tail: str = ""
    role: str = AGENT_ROLE_CANDIDATE

    def __post_init__(self) -> None:
        self.role = normalize_agent_role(self.role)
        normalized: Dict[int, int] = {}
        source = self.reasoning_token_counts if isinstance(self.reasoning_token_counts, dict) else {}
        for raw_delta, raw_count in source.items():
            try:
                delta = int(raw_delta)
                count = int(raw_count)
            except (TypeError, ValueError):
                continue
            if delta > 0 and count > 0:
                normalized[delta] = normalized.get(delta, 0) + count
        self.reasoning_token_counts = normalized


@dataclass
class AgentState:
    idx: int
    codex_thread_id: Optional[str] = None
    reasoning_values: List[int] = field(default_factory=list)
    reasoning_token_counts: Dict[int, int] = field(default_factory=dict)
    reasoning_last_total: Optional[int] = None
    json_events: int = 0
    stdout_lines: int = 0
    stderr_lines: int = 0

    def seed_reasoning_total(self, total: int) -> None:
        if isinstance(total, bool) or total < 0:
            return
        self.reasoning_last_total = total

    def record_reasoning_total(self, total: int) -> None:
        if isinstance(total, bool) or total < 0:
            return
        if not self.reasoning_values or self.reasoning_values[-1] != total:
            self.reasoning_values.append(total)

    def observe_reasoning_total(self, total: int) -> bool:
        if isinstance(total, bool) or total < 0:
            return False
        previous = self.reasoning_last_total
        self.reasoning_last_total = total
        self.record_reasoning_total(total)
        delta = total - (previous if previous is not None else 0)
        if delta <= 0:
            return False
        self.reasoning_token_counts[delta] = self.reasoning_token_counts.get(delta, 0) + 1
        return True

    @property
    def reasoning_tokens(self) -> Optional[int]:
        candidates = list(self.reasoning_values)
        if self.reasoning_last_total is not None:
            candidates.append(self.reasoning_last_total)
        if not candidates:
            return None
        return max(candidates)


@dataclass
class ResumeSession:
    session_id: str
    title: str
    cwd: str
    updated_at: Optional[int]
    created_at: Optional[int] = None
    source: str = ""
    model: str = ""
    rollout_path: str = ""
    preview: str = ""
    tokens_used: Optional[int] = None


@dataclass(frozen=True)
class CodexHistoryEntry:
    category: str
    text: str


@dataclass
class CodexSessionPromotion:
    session_id: str
    workspace: str
    source_codex_home: str = ""
    state_path: str = ""
    state_found: bool = False
    state_updated: bool = False
    old_cwd: str = ""
    rollout_path: str = ""
    rollout_found: bool = False
    rollout_updated: bool = False
    source_promoted: bool = False
    error: Optional[str] = None
