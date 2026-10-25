from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


_MARKDOWN_DESTINATION = re.compile(
    r"(?P<prefix>!?\[[^\]\n]*\]\()"
    # Disjoint single-character/parenthesis tokens avoid exponential backtracking
    # when a streamed or malformed link has not received its closing parenthesis.
    r"(?P<body><[^>\n]+>|(?:[^()\n]|\([^()\n]*\))+?)"
    r"(?P<suffix>\))"
)
_INLINE_CODE = re.compile(r"(?P<fence>`+)(?P<body>[^`\n]+)(?P=fence)")
_FILE_URI = re.compile(r"file://(?:localhost)?/[^\s<>\"'`\]\[()]+")
_LINE_SUFFIX = re.compile(r"(?P<line>:\d+(?::\d+)?)$")
_CODEX_FILE_CITATION = re.compile(
    r":codex-file-citation\{"
    r"(?P<attributes>(?:[^\"{}\\]|\\.|\"(?:[^\"\\]|\\.)*\")*)"
    r"\}"
)
_CODEX_FILE_CITATION_PATH = re.compile(
    r"(?:^|\s)path\s*=\s*\"(?P<path>(?:[^\"\\]|\\.)*)\""
)
_TRAILING_PUNCTUATION = ".,;!"
_SESSION_MAPPING_VERSION = 1
_MAX_SESSION_MAPPINGS = 256


@dataclass(frozen=True)
class WorkspacePathMapping:
    source_root: Path
    target_root: Path

    @classmethod
    def create(
        cls,
        source_root: str | os.PathLike[str],
        target_root: str | os.PathLike[str],
    ) -> "WorkspacePathMapping":
        return cls(
            source_root=_lexical_absolute(source_root),
            target_root=_lexical_absolute(target_root),
        )


def load_session_workspace_mappings(
    codex_home: str | os.PathLike[str],
    session_id: str,
) -> tuple[WorkspacePathMapping, ...]:
    path = _session_mapping_path(codex_home, session_id)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ()
    if (
        not isinstance(value, dict)
        or value.get("version") != _SESSION_MAPPING_VERSION
        or value.get("session_id") != session_id
        or not isinstance(value.get("mappings"), list)
    ):
        return ()
    mappings: list[WorkspacePathMapping] = []
    seen: set[tuple[Path, Path]] = set()
    for item in value["mappings"][:_MAX_SESSION_MAPPINGS]:
        if not isinstance(item, dict):
            continue
        source = item.get("source_root")
        target = item.get("target_root")
        if not isinstance(source, str) or not isinstance(target, str):
            continue
        mapping = WorkspacePathMapping.create(source, target)
        key = (mapping.source_root, mapping.target_root)
        if mapping.source_root == mapping.target_root or key in seen:
            continue
        seen.add(key)
        mappings.append(mapping)
    return tuple(mappings)


def record_session_workspace_mapping(
    codex_home: str | os.PathLike[str],
    session_id: str,
    source_root: str | os.PathLike[str],
    target_root: str | os.PathLike[str],
) -> None:
    session_id = session_id.strip()
    if not session_id:
        return
    mapping = WorkspacePathMapping.create(source_root, target_root)
    if mapping.source_root == mapping.target_root:
        return
    existing = list(load_session_workspace_mappings(codex_home, session_id))
    existing = [
        item
        for item in existing
        if item.source_root != mapping.source_root
    ]
    existing.append(mapping)
    existing = existing[-_MAX_SESSION_MAPPINGS:]
    path = _session_mapping_path(codex_home, session_id)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        path.parent.chmod(0o700)
    payload = {
        "version": _SESSION_MAPPING_VERSION,
        "session_id": session_id,
        "mappings": [
            {
                "source_root": str(item.source_root),
                "target_root": str(item.target_root),
            }
            for item in existing
        ],
    }
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            os.chmod(temporary, 0o600)
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        with contextlib.suppress(OSError):
            temporary.unlink()


def rewrite_workspace_references(
    text: str,
    mappings: Iterable[WorkspacePathMapping],
) -> str:
    """Rewrite only verifiable project references from accepted Agent copies."""

    text = codex_file_citations_to_markdown(text)

    active = tuple(
        mapping
        for mapping in mappings
        if mapping.source_root != mapping.target_root
        and mapping.target_root.is_dir()
    )
    if not text or not active:
        return text

    def markdown(match: re.Match[str]) -> str:
        body = match.group("body")
        if body.startswith("<") and body.endswith(">"):
            destination = body[1:-1]
            rewritten = _rewrite_reference(destination, active)
            body = f"<{rewritten}>"
        else:
            destination, remainder = _split_markdown_destination(body)
            body = _rewrite_reference(destination, active) + remainder
        return match.group("prefix") + body + match.group("suffix")

    value = _MARKDOWN_DESTINATION.sub(markdown, text)

    def inline_code(match: re.Match[str]) -> str:
        body = match.group("body")
        leading = body[: len(body) - len(body.lstrip())]
        trailing = body[len(body.rstrip()) :]
        core = body.strip()
        rewritten = _rewrite_reference(core, active)
        return match.group("fence") + leading + rewritten + trailing + match.group("fence")

    value = _INLINE_CODE.sub(inline_code, value)
    value = _FILE_URI.sub(lambda match: _rewrite_reference(match.group(0), active), value)

    for mapping in active:
        source = re.escape(str(mapping.source_root))
        naked = re.compile(
            source
            + r"(?=$|[/\s<>\"'`\]\[():?#])"
            + r"(?:/[^\s<>\"'`\]\[()]*)?"
            + r"(?::\d+(?::\d+)?)?"
            + r"(?:[?#][^\s<>\"'`\]\[()]*)?"
        )
        value = naked.sub(
            lambda match: _rewrite_reference(match.group(0), (mapping,)),
            value,
        )
    return value


def codex_file_citations_to_markdown(text: str) -> str:
    """Render Codex's internal local-file directive as a normal file link."""

    if ":codex-file-citation{" not in text:
        return text

    def replace(match: re.Match[str]) -> str:
        path_match = _CODEX_FILE_CITATION_PATH.search(match.group("attributes"))
        if path_match is None:
            return match.group(0)
        try:
            path = json.loads(f'"{path_match.group("path")}"')
        except json.JSONDecodeError:
            return match.group(0)
        if not isinstance(path, str) or not path:
            return match.group(0)

        parsed = urllib.parse.urlsplit(path)
        if parsed.scheme.casefold() == "file":
            if parsed.netloc not in {"", "localhost"} or not parsed.path:
                return match.group(0)
            destination = path
            label_path = urllib.parse.unquote(parsed.path)
        elif not parsed.scheme and path.startswith("/"):
            destination = urllib.parse.urlunsplit(
                ("file", "", urllib.parse.quote(path, safe="/:@"), "", "")
            )
            label_path = path
        else:
            return match.group(0)

        label = label_path.rstrip("/").rsplit("/", 1)[-1] or label_path
        label = label.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")
        return f"[{label}](<{destination}>)"

    return _CODEX_FILE_CITATION.sub(replace, text)


def _split_markdown_destination(value: str) -> tuple[str, str]:
    stripped = value.lstrip()
    leading = value[: len(value) - len(stripped)]
    match = re.match(r"\S+", stripped)
    if match is None:
        return value, ""
    destination = match.group(0)
    remainder = stripped[match.end() :]
    return leading + destination, remainder


def _rewrite_reference(
    value: str,
    mappings: tuple[WorkspacePathMapping, ...],
) -> str:
    leading = value[: len(value) - len(value.lstrip())]
    trailing_space = value[len(value.rstrip()) :]
    core = value.strip()
    if not core:
        return value

    punctuation = ""
    while core and core[-1] in _TRAILING_PUNCTUATION:
        punctuation = core[-1] + punctuation
        core = core[:-1]

    parsed = urllib.parse.urlsplit(core)
    if parsed.scheme and parsed.scheme.casefold() != "file":
        return value
    is_file_uri = parsed.scheme.casefold() == "file"
    if is_file_uri and parsed.netloc not in {"", "localhost"}:
        return value

    raw_path = urllib.parse.unquote(parsed.path if is_file_uri else core)
    query = parsed.query if is_file_uri else ""
    fragment = parsed.fragment if is_file_uri else ""
    if not is_file_uri:
        raw_path, query, fragment = _split_plain_suffix(raw_path)
    line_match = _LINE_SUFFIX.search(raw_path)
    line_suffix = line_match.group("line") if line_match else ""
    if line_match:
        raw_path = raw_path[: line_match.start()]

    for mapping in mappings:
        candidate = _lexical_absolute(raw_path)
        try:
            relative = candidate.relative_to(mapping.source_root)
        except ValueError:
            continue
        target = mapping.target_root / relative
        if not (target.exists() or target.is_symlink()):
            continue
        mapped_path = str(target) + line_suffix
        if is_file_uri:
            rewritten = urllib.parse.urlunsplit(
                (
                    "file",
                    parsed.netloc,
                    urllib.parse.quote(mapped_path, safe="/:@"),
                    query,
                    fragment,
                )
            )
        else:
            if "%" in core:
                mapped_path = urllib.parse.quote(mapped_path, safe="/:@")
            rewritten = mapped_path
            if query:
                rewritten += "?" + query
            if fragment:
                rewritten += "#" + fragment
        return leading + rewritten + punctuation + trailing_space
    return value


def _split_plain_suffix(value: str) -> tuple[str, str, str]:
    fragment = ""
    query = ""
    if "#" in value:
        value, fragment = value.split("#", 1)
    if "?" in value:
        value, query = value.split("?", 1)
    return value, query, fragment


def _lexical_absolute(value: str | os.PathLike[str]) -> Path:
    return Path(os.path.abspath(os.path.expanduser(os.fspath(value))))


def _session_mapping_path(
    codex_home: str | os.PathLike[str],
    session_id: str,
) -> Path:
    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    return Path(codex_home).expanduser() / "pcr" / "session_path_mappings" / f"{digest}.json"
