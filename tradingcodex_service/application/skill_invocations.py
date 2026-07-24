from __future__ import annotations

import posixpath
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any, Iterable


_NEWLINE_TRANSLATION = str.maketrans(
    {
        "\r": "\n",
        "\x85": "\n",
        "\u2028": "\n",
        "\u2029": "\n",
    }
)
_SKILL_ID = r"[a-z0-9]+(?:-[a-z0-9]+)*"
_SKILL_ID_INPUT = r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*"
_MARKDOWN_SKILL_LINK = re.compile(
    rf"\[(?P<label>\${_SKILL_ID_INPUT})\]\((?P<target><[^\r\n>]+>|[^\s)]+)\)"
)
_ASCII_UPPER_TO_LOWER = str.maketrans(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
    "abcdefghijklmnopqrstuvwxyz",
)


class SkillInvocationError(ValueError):
    """Raised when an explicit TradingCodex skill invocation is malformed."""


@dataclass(frozen=True)
class SkillInvocation:
    marker: str
    tail: str
    line_index: int
    linked: bool


def raw_prompt(prompt: Any) -> str:
    """Return the exact text used for prompt hashing by the owning gateway."""

    return str(prompt or "")


def normalize_prompt(prompt: Any) -> str:
    """Normalize presentation-only prompt details without changing skill tokens."""

    value = raw_prompt(prompt)
    if value.startswith("\ufeff"):
        value = value[1:]
    # Collapse CRLF before translating the remaining supported newline forms.
    return value.replace("\r\n", "\n").translate(_NEWLINE_TRANSLATION)


def meaningful_lines(prompt: Any) -> list[tuple[int, str]]:
    """Return non-empty normalized lines with horizontal edge space removed."""

    return [
        (index, _strip_horizontal_whitespace(line))
        for index, line in enumerate(normalize_prompt(prompt).split("\n"))
        if _strip_horizontal_whitespace(line)
    ]


def has_visible_content(value: Any) -> bool:
    """Return whether text contains a visible, non-control request character."""

    return any(
        not character.isspace()
        and unicodedata.category(character)[0] not in {"C", "M", "Z"}
        for character in str(value or "")
    )


def parse_first_meaningful_invocation(
    prompt: Any,
    markers: Iterable[str],
    *,
    workspace_root: Path | str | None = None,
) -> SkillInvocation | None:
    """Parse a reserved skill only when it starts the first meaningful line."""

    lines = meaningful_lines(prompt)
    if not lines:
        return None
    line_index, line = lines[0]
    invocation = parse_line_invocation(
        line,
        markers,
        line_index=line_index,
        workspace_root=workspace_root,
    )
    if invocation is not None:
        _validate_privileged_invocation_characters(normalize_prompt(prompt), invocation.marker)
    return invocation


def parse_line_invocation(
    line: str,
    markers: Iterable[str],
    *,
    line_index: int = 0,
    workspace_root: Path | str | None = None,
) -> SkillInvocation | None:
    """Parse one plain-token or workspace-bound Markdown skill invocation."""

    value = _strip_horizontal_whitespace(str(line))
    candidates = tuple(dict.fromkeys(str(marker) for marker in markers))
    for marker in candidates:
        token = value[: len(marker)]
        if _ascii_lower(token) == marker and len(value) == len(marker):
            return SkillInvocation(marker=marker, tail="", line_index=line_index, linked=False)
        if (
            _ascii_lower(token) == marker
            and len(value) > len(marker)
            and _is_horizontal_whitespace(value[len(marker)])
        ):
            return SkillInvocation(
                marker=marker,
                tail=_strip_horizontal_whitespace(value[len(marker) :]),
                line_index=line_index,
                linked=False,
            )

    link = _MARKDOWN_SKILL_LINK.match(value)
    linked_marker = _ascii_lower(link.group("label")) if link is not None else ""
    if link is not None and linked_marker in candidates:
        marker = linked_marker
        _validate_workspace_skill_link(marker, link.group("target"), workspace_root)
        suffix = value[link.end() :]
        if suffix and not _is_horizontal_whitespace(suffix[0]):
            raise SkillInvocationError(f"{marker} Markdown link must be followed by whitespace")
        return SkillInvocation(
            marker=marker,
            tail=_strip_horizontal_whitespace(suffix),
            line_index=line_index,
            linked=True,
        )

    for marker in candidates:
        if _ascii_lower(value[: len(marker) + 2]) == f"[{marker}]":
            raise SkillInvocationError(f"{marker} must use a valid workspace Markdown skill link")
    return None


def explicit_skill_ids(
    prompt: Any,
    namespace: str,
    *,
    workspace_root: Path | str | None = None,
) -> list[str]:
    """Return deduplicated explicit plain or linked ids for one skill namespace."""

    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", namespace):
        raise ValueError("skill namespace is invalid")
    value = normalize_prompt(prompt)
    marker_pattern = rf"\${re.escape(namespace)}-{_SKILL_ID_INPUT}"
    _validate_selector_candidates(value, namespace)
    names: list[str] = []
    masked = list(value)

    valid_links = {
        match.start(): match
        for match in _MARKDOWN_SKILL_LINK.finditer(value)
        if re.fullmatch(marker_pattern, _ascii_lower(match.group("label")))
    }
    bracketed_selector = re.compile(
        rf"\[(?P<label>\$[A-Za-z0-9-]+)\]"
    )
    for bracket in bracketed_selector.finditer(value):
        label = bracket.group("label")
        canonical_label = _ascii_lower(label)
        if not canonical_label.startswith(f"${namespace}-"):
            continue
        match = valid_links.get(bracket.start())
        if match is None or match.group("label") != label:
            raise SkillInvocationError(
                f"{label or f'${namespace}-*'} must use a valid workspace Markdown skill link"
            )
        _validate_workspace_skill_link(canonical_label, match.group("target"), workspace_root)
        names.append(canonical_label.removeprefix("$"))
        masked[match.start() : match.end()] = " " * (match.end() - match.start())

    masked_value = "".join(masked)
    plain = re.compile(marker_pattern, re.ASCII | re.IGNORECASE)
    for match in plain.finditer(masked_value):
        before = masked_value[match.start() - 1] if match.start() else ""
        after = masked_value[match.end()] if match.end() < len(masked_value) else ""
        if (before and _is_selector_boundary_character(before)) or (
            after and _is_selector_boundary_character(after)
        ):
            continue
        names.append(_ascii_lower(match.group(0)).removeprefix("$"))
    return list(dict.fromkeys(names))


def starts_with_any_invocation(
    line: str,
    markers: Iterable[str],
    *,
    workspace_root: Path | str | None = None,
) -> SkillInvocation | None:
    """Alias used when scanning later meaningful lines for mixed authority."""

    return parse_line_invocation(line, markers, workspace_root=workspace_root)


def _is_horizontal_whitespace(character: str) -> bool:
    return character == "\t" or unicodedata.category(character) == "Zs"


def _strip_horizontal_whitespace(value: str) -> str:
    start = 0
    end = len(value)
    while start < end and _is_horizontal_whitespace(value[start]):
        start += 1
    while end > start and _is_horizontal_whitespace(value[end - 1]):
        end -= 1
    return value[start:end]


def _ascii_lower(value: str) -> str:
    """Case-fold ASCII presentation without normalizing Unicode lookalikes."""

    return value.translate(_ASCII_UPPER_TO_LOWER)


def _validate_privileged_invocation_characters(value: str, marker: str) -> None:
    if any(
        character not in {"\t", "\n"} and unicodedata.category(character).startswith("C")
        for character in value
    ):
        raise SkillInvocationError(
            f"{marker} invocation contains an unsupported control or zero-width character"
        )
    if any(
        line and unicodedata.category(line[0]).startswith("M")
        for _, line in meaningful_lines(value)
    ):
        raise SkillInvocationError(
            f"{marker} invocation contains an unsupported leading combining character"
        )


def _validate_selector_candidates(value: str, namespace: str) -> None:
    """Reject hidden or confusable continuations before they can bind a prefix id."""

    prefix = f"${namespace}-"
    start = 0
    while True:
        marker_index = next(
            (
                index
                for index in range(start, max(start, len(value) - len(prefix) + 1))
                if _ascii_lower(value[index : index + len(prefix)]) == prefix
            ),
            -1,
        )
        if marker_index < 0:
            return
        if marker_index:
            previous = value[marker_index - 1]
            if previous not in {"\t", "\n"} and unicodedata.category(previous).startswith("C"):
                raise SkillInvocationError(
                    f"{prefix}* selector contains an unsupported control or zero-width character"
                )
        cursor = marker_index + len(prefix)
        ascii_id: list[str] = []
        while cursor < len(value):
            character = value[cursor]
            if (
                "a" <= character <= "z"
                or "A" <= character <= "Z"
                or "0" <= character <= "9"
                or character == "-"
            ):
                ascii_id.append(_ascii_lower(character))
                cursor += 1
                continue
            if character in {"\t", "\n"}:
                break
            category = unicodedata.category(character)
            if (
                character == "_"
                or category[0] in {"C", "L", "M", "N"}
                or _is_selector_confusable_separator(character)
            ):
                raise SkillInvocationError(
                    f"{prefix}* selector must use an ASCII skill id"
                )
            break
        candidate_id = "".join(ascii_id)
        if candidate_id and re.fullmatch(_SKILL_ID, candidate_id) is None:
            raise SkillInvocationError(
                f"{prefix}* selector must use an ASCII skill id"
            )
        start = marker_index + len(prefix)


def _is_selector_boundary_character(character: str) -> bool:
    if character in {"\t", "\n"}:
        return False
    return bool(
        character in {"$", "_", "-"}
        or unicodedata.category(character)[0] in {"C", "L", "M", "N"}
        or _is_selector_confusable_separator(character)
    )


def _is_selector_confusable_separator(character: str) -> bool:
    name = unicodedata.name(character, "")
    return unicodedata.category(character) in {"Pc", "Pd"} or any(
        token in name for token in ("DASH", "HYPHEN", "MINUS")
    )


def _validate_workspace_skill_link(
    marker: str,
    raw_target: str,
    workspace_root: Path | str | None,
) -> None:
    if workspace_root is None or not str(workspace_root).strip():
        raise SkillInvocationError(f"{marker} Markdown links require the current workspace root")
    target = raw_target[1:-1] if raw_target.startswith("<") and raw_target.endswith(">") else raw_target
    if not target or any(character in target for character in ("?", "#")):
        raise SkillInvocationError(f"{marker} Markdown link target is invalid")
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", target) and not re.match(r"^[A-Za-z]:[\\/]", target):
        raise SkillInvocationError(f"{marker} Markdown link must target the projected workspace skill")

    skill_id = marker.removeprefix("$")
    root = Path(workspace_root).expanduser().resolve()
    expected_path = root / ".agents" / "skills" / skill_id / "SKILL.md"
    current = root
    for part in expected_path.relative_to(root).parts:
        current /= part
        if current.is_symlink():
            raise SkillInvocationError(f"{marker} projected workspace skill must not use symlinks")
    expected = expected_path.resolve()
    try:
        expected.relative_to(root)
    except ValueError as exc:
        raise SkillInvocationError(f"{marker} projected workspace skill must stay in the workspace") from exc
    if not expected.is_file():
        raise SkillInvocationError(f"{marker} projected workspace skill is unavailable")

    normalized_target = target.replace("\\", "/")
    duplicate_separator_scan = (
        normalized_target[2:] if normalized_target.startswith("//") else normalized_target
    )
    if (
        "//" in duplicate_separator_scan
        or normalized_target.endswith("/")
        or any(part in {".", ".."} for part in normalized_target.split("/"))
    ):
        raise SkillInvocationError(f"{marker} Markdown link target is not canonical")
    if normalized_target.startswith("/"):
        candidate = Path(normalized_target).expanduser().absolute()
        if candidate != expected_path:
            raise SkillInvocationError(f"{marker} Markdown link must target {expected.as_posix()}")
        candidate_key = candidate.resolve().as_posix()
    elif re.match(r"^[A-Za-z]:/", normalized_target):
        if not _windows_drive_target_matches_expected(normalized_target, expected_path):
            raise SkillInvocationError(f"{marker} Markdown link must target {expected.as_posix()}")
        candidate = Path(normalized_target).expanduser()
        if not candidate.is_absolute():
            raise SkillInvocationError(f"{marker} Markdown link must target {expected.as_posix()}")
        candidate_key = candidate.resolve().as_posix()
    else:
        candidate = (root / normalized_target).absolute()
        if candidate != expected_path:
            raise SkillInvocationError(f"{marker} Markdown link must target {expected.as_posix()}")
        candidate_key = candidate.resolve().as_posix()
    expected_key = posixpath.normpath(expected.as_posix())
    if candidate_key != expected_key:
        raise SkillInvocationError(f"{marker} Markdown link must target {expected_key}")


def _windows_drive_target_matches_expected(
    normalized_target: str,
    expected_path: Path | PureWindowsPath,
) -> bool:
    """Compare a drive target lexically before any symlink or junction resolution."""

    return PureWindowsPath(normalized_target) == PureWindowsPath(str(expected_path))
