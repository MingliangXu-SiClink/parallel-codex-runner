from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


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
    error: Optional[str] = None
    stdout_tail: str = ""
    stderr_tail: str = ""


@dataclass
class AgentState:
    idx: int
    codex_thread_id: Optional[str] = None
    reasoning_values: List[int] = field(default_factory=list)
    json_events: int = 0
    stdout_lines: int = 0
    stderr_lines: int = 0

    @property
    def reasoning_tokens(self) -> Optional[int]:
        if not self.reasoning_values:
            return None
        return max(self.reasoning_values)


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
