from __future__ import annotations

import json
import re

_FENCED_JSON_BLOCK = re.compile(r"```(?:json)?\s*\n?(.*?)```", re.DOTALL | re.IGNORECASE)


def extract_json_text(raw: str) -> str:
    """Normalize model output to a JSON object string when possible."""
    text = raw.strip()
    if not text:
        return text

    fenced = _extract_fenced_json(text)
    if fenced is not None:
        return fenced

    object_text = _extract_first_json_object(text)
    if object_text is not None:
        return object_text

    return text


def _extract_fenced_json(text: str) -> str | None:
    match = _FENCED_JSON_BLOCK.search(text)
    if match is None:
        return None
    candidate = match.group(1).strip()
    return candidate or None


def _extract_first_json_object(text: str) -> str | None:
    start = 0
    while True:
        brace = text.find("{", start)
        if brace < 0:
            return None
        candidate = _balanced_brace_slice(text, brace)
        if candidate is None:
            start = brace + 1
            continue
        try:
            json.loads(candidate)
        except json.JSONDecodeError:
            start = brace + 1
            continue
        return candidate


def _balanced_brace_slice(text: str, start: int) -> str | None:
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None
