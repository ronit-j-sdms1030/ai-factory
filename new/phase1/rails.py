"""Intake turn: NeMo / Presidio / Guardrails stand-ins on the same seams.

Production swaps these for the named products. The conversation must not reach
the model with raw personal data, and must not accept a shapeless reply.
"""

from __future__ import annotations

import json
import re
from typing import Any

EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
PHONE = re.compile(r"\b(?:\+?\d[\s-]?){8,}\b")
OFF_RAILS = re.compile(
    r"ignore (all|previous) instructions|you are now|jailbreak",
    re.I,
)


class InputRefused(Exception):
    """NeMo-shaped: the turn never reached the model."""


class OutputRefused(Exception):
    """Guardrails-shaped: the model replied, but the shape is unusable."""


def mask_personal_data(text: str) -> str:
    """Presidio stand-in — mask before anything leaves the tenant."""
    text = EMAIL.sub("<EMAIL>", text)
    return PHONE.sub("<PHONE>", text)


def screen_input(text: str) -> str:
    if not (text or "").strip():
        raise InputRefused("empty turn — the model is not asked to invent a question")
    if OFF_RAILS.search(text):
        raise InputRefused("input rails refused this turn as off-scope")
    return mask_personal_data(text.strip())


def looks_like_aborted_scope(text: str) -> bool:
    """True when the model tried to emit a scope_report (often truncated) as prose/JSON junk.

    Free models cut mid-object; without this check the truncated blob was shown as the
    next chat question and the Review modal never opened.
    """
    raw = (text or "").strip()
    if not raw:
        return False
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, count=1, flags=re.I)
    if not raw.lstrip().startswith("{"):
        return False
    lower = raw.lower()
    return (
        '"scope_report"' in lower
        or '"in_scope"' in lower
        or '"inscope"' in lower.replace("_", "")
        or ('"title"' in lower and '"users"' in lower)
        or ('"title"' in lower and '"current_state"' in lower)
        or ('"title"' in lower and '"in_scope"' in lower)
    )


def parse_agent_output(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if not text:
        raise OutputRefused("agent output is empty")
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, re.I)
    if fenced:
        text = fenced.group(1).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        if start < 0:
            return _question_from_prose(text)
        try:
            data, _ = json.JSONDecoder().raw_decode(text[start:])
        except json.JSONDecodeError as exc:
            if looks_like_aborted_scope(text):
                raise OutputRefused("truncated or invalid scope_report JSON") from exc
            return _question_from_prose(text)
    data = _normalise_agent_json(data)
    kind = data.get("type")
    if kind == "question":
        if not str(data.get("text") or "").strip():
            raise OutputRefused("question has no text")
        if looks_like_aborted_scope(str(data.get("text") or "")):
            raise OutputRefused("question text is a dumped scope_report, not a question")
        return data
    if kind == "scope_report":
        data["in_scope"] = as_string_list(data.get("in_scope"))
        data["out_of_scope"] = as_string_list(data.get("out_of_scope"))
        data["assumptions"] = as_string_list(data.get("assumptions"))
        data["open_questions"] = as_string_list(data.get("open_questions"))
        data["success"] = str(data.get("success") or "").strip()
        data["users"] = str(data.get("users") or "").strip()
        data["current_state"] = str(data.get("current_state") or "").strip()
        data["non_functional"] = as_string_list(data.get("non_functional"))
        for key in ("in_scope", "out_of_scope", "success", "open_questions"):
            if key not in data:
                raise OutputRefused(f"scope report missing {key}")
        if not data["out_of_scope"]:
            raise OutputRefused("out of scope must not be empty on a real requirement")
        if not data["in_scope"]:
            raise OutputRefused("in scope is empty")
        return data
    if kind:
        raise OutputRefused(f"unknown agent output type {kind!r}")
    if looks_like_aborted_scope(text):
        raise OutputRefused("scope-shaped JSON without a usable type")
    return _question_from_prose(text)


def _question_from_prose(text: str) -> dict[str, str]:
    clipped = " ".join((text or "").split())
    if not clipped:
        raise OutputRefused("agent output is not JSON")
    if looks_like_aborted_scope(clipped):
        raise OutputRefused("refusing to surface raw scope JSON as a question")
    if len(clipped) > 400:
        clipped = clipped[:397].rsplit(" ", 1)[0] + "…"
    return {"type": "question", "text": clipped}


def _normalise_agent_json(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise OutputRefused("agent output is not a JSON object")
    kind = data.get("type")
    if kind:
        return data
    question = data.get("question") or data.get("text")
    if isinstance(question, str) and question.strip() and "in_scope" not in data:
        return {"type": "question", "text": question.strip()}
    if "in_scope" in data:
        data = dict(data)
        data["type"] = "scope_report"
        data.setdefault("out_of_scope", ["Anything outside the stated in-scope items"])
        data.setdefault("success", "")
        data.setdefault("assumptions", [])
        data.setdefault("open_questions", [])
        return data
    return data


BOOTSTRAP = re.compile(r"guided requirement intake conversation", re.I)
OUT_LEAD = re.compile(r"^(no |not |never |without )", re.I)
CHANNEL = re.compile(r"browser on a phone|app from a store|app-store", re.I)


def is_bootstrap(text: str) -> bool:
    return bool(BOOTSTRAP.search(text or ""))


def _clip(text: str, limit: int = 220) -> str:
    text = " ".join(text.strip().split())
    text = text.rstrip(".")
    if len(text) <= limit:
        return text
    return text[: limit - 1].rsplit(" ", 1)[0] + "…"


def as_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return split_into_bullets(value)
    if isinstance(value, list):
        items: list[str] = []
        for entry in value:
            items.extend(as_string_list(entry) if not isinstance(entry, str) else split_into_bullets(entry))
        return items
    return split_into_bullets(str(value))


def split_into_bullets(text: str) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    lines = []
    for line in text.splitlines():
        line = line.strip().lstrip("-*• ").strip()
        if line:
            lines.append(line)
    if len(lines) > 1:
        return [_clip(line) for line in lines]
    blob = lines[0] if lines else text
    if len(blob) <= 220:
        return [_clip(blob)]
    parts = re.split(r"(?<=[.!?])\s+", blob)
    return [_clip(part, 220) for part in parts if len(part.strip()) >= 12]


def user_answers(messages: list[dict[str, Any]]) -> list[str]:
    return [
        str(m.get("content") or "").strip()
        for m in messages
        if m.get("role") == "user" and str(m.get("content") or "").strip() and not is_bootstrap(str(m.get("content") or ""))
    ]


def _used_by(who: str, request: str) -> str:
    text = (who or "").strip()
    if text and len(text) <= 140 and not re.match(r"^(we need|i need|a website)", text, re.I):
        return _clip(f"Used by {text.rstrip('.')}")
    blob = f"{who} {request}".lower()
    roles = []
    if "contractor" in blob:
        roles.append("contractors on site")
    if "supervisor" in blob or "manager" in blob:
        roles.append("supervisors who need the same-day picture")
    if "staff" in blob or "employee" in blob:
        roles.append("named staff")
    if roles:
        return "Used by " + " and ".join(roles) + "."
    return "Used by the people named in the request, in a browser."


def _split_request(text: str) -> tuple[list[str], list[str], list[str]]:
    in_scope: list[str] = []
    out_scope: list[str] = []
    nfr: list[str] = []
    for item in split_into_bullets(text):
        low = item.lower()
        if is_bootstrap(item):
            continue
        if re.search(r"iphone|android|native app|app store|app-store install", low):
            out_scope.append(_clip(item))
            continue
        if CHANNEL.search(item) and OUT_LEAD.search(item):
            nfr.append(_clip(item))
            continue
        if CHANNEL.search(low) and "enough" in low:
            nfr.append(_clip(item))
            continue
        if OUT_LEAD.search(item):
            out_scope.append(_clip(item))
            continue
        in_scope.append(_clip(item))
    return in_scope, out_scope, nfr


def scope_from_conversation(
    messages: list[dict[str, Any]],
    *,
    request_text: str = "",
) -> dict[str, Any]:
    """Build a reviewer-readable scope report from intake answers."""
    answers = user_answers(messages)
    if len(answers) >= 5:
        request, who, today, success, out = answers[0], answers[1], answers[2], answers[3], answers[4]
    elif len(answers) >= 4:
        who, today, success, out = answers[0], answers[1], answers[2], answers[3]
        request = request_text if request_text and not is_bootstrap(request_text) else answers[0]
    else:
        request = next((a for a in answers if len(a) > 40), request_text)
        who = answers[0] if answers else ""
        today = answers[1] if len(answers) > 1 else ""
        success = answers[2] if len(answers) > 2 else ""
        out = answers[3] if len(answers) > 3 else ""
    if is_bootstrap(request):
        request = answers[0] if answers else request
    in_scope, from_request_out, nfr = _split_request(request)
    users = _used_by(who, request)
    if users and not any(item.lower().startswith("used by") for item in in_scope):
        in_scope.insert(0, users)
    for extra in _split_request(success)[0]:
        if extra.lower() not in {item.lower() for item in in_scope}:
            in_scope.append(extra)
    out_scope, _, more_nfr = _split_request(out)
    out_scope = from_request_out + out_scope
    nfr = nfr + more_nfr
    if not any("native" in item.lower() or "app store" in item.lower() or "iphone" in item.lower() for item in out_scope):
        out_scope.append("A separate iPhone or Android app from an app store")
    if not nfr:
        nfr = ["Phone and laptop browser only. No app-store install."]
    if not in_scope:
        in_scope = split_into_bullets(request) or ["Deliver the described website in a browser"]
    seen: set[str] = set()
    unique_in = []
    for item in in_scope:
        key = item.lower()
        if key in seen or is_bootstrap(item):
            continue
        seen.add(key)
        unique_in.append(item)
    unique_out = []
    seen_out: set[str] = set()
    for item in out_scope:
        key = item.lower()
        if key in seen_out or (CHANNEL.search(item) and "enough" in key):
            continue
        seen_out.add(key)
        unique_out.append(item)
    today = " ".join((today or "").split())
    success = " ".join((success or "").split())
    questions = []
    if not re.search(r"paper|whatsapp|spreadsheet|cabin|chat|register", today, re.I):
        questions.append("Which existing system holds the source records today?")
    else:
        questions.append("Who can correct a missed check-in, and how soon must that show on the live list?")
    if not re.search(r"export|payroll|csv|spreadsheet|month", f"{request} {success} {out}", re.I):
        questions.append("Does anyone need a file at week or month end, and who receives it?")
    return {
        "type": "scope_report",
        "title": title_from_request(request),
        "in_scope": unique_in[:10],
        "out_of_scope": unique_out or ["Anything not listed as in scope"],
        "success": success or "The work is visible the same day without a side channel.",
        "users": users,
        "current_state": today or "Today the work is done by hand.",
        "non_functional": nfr[:6],
        "assumptions": [
            f"Today: {today}" if today else "Today the work is done by hand.",
            "People use a web browser on a phone or laptop, not an app-store install.",
        ],
        "open_questions": questions[:4],
    }


def title_from_request(request: str) -> str:
    first = (request or "").strip().split(".")[0].strip()
    first = re.sub(
        r"^(i need |we need |i'd like |i would like |please (build|make) )",
        "",
        first,
        flags=re.I,
    ).strip()
    if not first:
        return "Requirement"
    if len(first) > 72:
        first = first[:71].rsplit(" ", 1)[0]
    return first[0].upper() + first[1:]


def card_title(display: str, in_scope: list[str], success: str) -> str:
    """Short name for lists — not the afterwards paragraph."""
    display = (display or "").strip()
    if display and not is_bootstrap(display):
        words = display.split()
        if 2 <= len(words) <= 10 and len(display) <= 72:
            return display[0].upper() + display[1:] if display[0].islower() else display
    candidates = []
    for item in in_scope or []:
        if str(item).lower().startswith("used by"):
            continue
        candidates.append(str(item))
    for text in candidates:
        if not text.strip() or is_bootstrap(text):
            continue
        short = title_from_request(text)
        words = short.split()
        if 3 <= len(words) <= 10 and len(short) <= 72:
            return short
    fallback = title_from_request(success or display or "Requirement")
    words = fallback.split()
    if len(words) > 10:
        fallback = " ".join(words[:8]).rstrip(",.") + "…"
    return fallback or "Requirement"


def shape_scope_report(data: dict[str, Any], *, request_text: str = "") -> dict[str, Any]:
    """Turn a model dump into a reviewer-readable scope report."""
    report = dict(data)
    in_scope: list[str] = []
    for item in as_string_list(report.get("in_scope")):
        low = item.lower()
        if item.lower().startswith("built from the request:"):
            in_scope.extend(split_into_bullets(item.split(":", 1)[-1]))
            continue
        if item.lower().startswith("a web application for ") and "replacing:" in low:
            left, right = re.split(r"replacing:\s*", item, maxsplit=1, flags=re.I)
            who = left[len("A web application for ") :].strip(" ,")
            if who and len(who) <= 80:
                in_scope.append(_clip(f"Used by {who}"))
            in_scope.extend(split_into_bullets(right))
            continue
        if is_bootstrap(item):
            continue
        in_scope.append(item)
    in_scope = [item for item in in_scope if not is_bootstrap(item)]
    if request_text and not is_bootstrap(request_text) and (
        not in_scope or (len(in_scope) <= 2 and any(len(x) > 160 for x in in_scope))
    ):
        in_scope = [item for item in split_into_bullets(request_text) if not is_bootstrap(item)]
    out_scope = [item for item in as_string_list(report.get("out_of_scope")) if not is_bootstrap(item)]
    if not out_scope:
        out_scope = ["Anything not listed as in scope"]
    success = str(report.get("success") or "").strip()
    if len(success) > 800:
        success = " ".join(split_into_bullets(success)[:4])
    assumptions = as_string_list(report.get("assumptions"))
    questions = as_string_list(report.get("open_questions"))
    title = str(report.get("title") or "").strip()
    if not title or is_bootstrap(title):
        title = title_from_request(request_text)
    report.update(
        {
            "type": "scope_report",
            "title": title,
            "in_scope": in_scope,
            "out_of_scope": out_scope,
            "success": success,
            "users": str(report.get("users") or "").strip(),
            "current_state": str(report.get("current_state") or "").strip(),
            "non_functional": as_string_list(report.get("non_functional")),
            "assumptions": assumptions,
            "open_questions": questions,
        }
    )
    return report
