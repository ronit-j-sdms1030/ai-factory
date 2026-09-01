"""Safe application of model-proposed dot-path edits.

The FSD editor returns small path-based replacements rather than a rewritten
document, so a bad edit can only damage the field it names. This applies them.

The JavaScript guards against ``__proto__``/``constructor`` because prototype
pollution there can corrupt every object in the process. Python has no
prototype chain, but the equivalent risk is real: an unguarded path can reach
``__class__`` or ``__globals__`` and mutate interpreter state. The same
denylist is therefore kept, widened to any dunder.

The document is deep-copied before mutation so a rejected operation can never
leave a half-applied value behind — the JS does the same via JSON round-trip.
"""

from __future__ import annotations

import copy
from typing import Any

_UNSAFE = {"__proto__", "prototype", "constructor"}


class EditPathError(ValueError):
    """Raised when an edit path is malformed, unsafe, or does not exist."""


def _reject(part: str) -> bool:
    return not part or part in _UNSAFE or (part.startswith("__") and part.endswith("__"))


def apply_edit_operation(root: Any, path: str, value: Any) -> Any:
    """Return a copy of ``root`` with ``path`` set to ``value``.

    An empty path replaces the document wholesale. Intermediate keys must
    already exist — this edits, it does not create structure, so a typo in a
    path fails loudly instead of silently adding a field nobody asked for.
    """
    parts = [] if path == "" else path.split(".")
    if any(_reject(p) for p in parts):
        raise EditPathError(f'Invalid edit path: "{path}"')
    if not parts:
        return value

    clone = copy.deepcopy({} if root is None else root)
    cursor: Any = clone

    for part in parts[:-1]:
        if isinstance(cursor, list):
            if not part.lstrip("-").isdigit():
                raise EditPathError(f'Edit path does not exist: "{path}"')
            index = int(part)
            if not -len(cursor) <= index < len(cursor):
                raise EditPathError(f'Edit path does not exist: "{path}"')
            cursor = cursor[index]
        elif isinstance(cursor, dict):
            if part not in cursor or not isinstance(cursor[part], (dict, list)):
                raise EditPathError(f'Edit path does not exist: "{path}"')
            cursor = cursor[part]
        else:
            raise EditPathError(f'Edit path does not exist: "{path}"')

    last = parts[-1]
    if isinstance(cursor, list):
        if not last.lstrip("-").isdigit():
            raise EditPathError(f'Edit path does not exist: "{path}"')
        index = int(last)
        if not -len(cursor) <= index < len(cursor):
            raise EditPathError(f'Edit path does not exist: "{path}"')
        cursor[index] = value
    elif isinstance(cursor, dict):
        cursor[last] = value
    else:
        raise EditPathError(f'Edit path does not exist: "{path}"')

    return clone


def normalize_operation(raw: dict[str, Any]) -> dict[str, Any]:
    """Split a dotted target such as ``detailedReport.objective`` into target + path.

    The editor sometimes embeds the field into the target rather than using the
    separate path, so both forms are accepted.
    """
    op = dict(raw)
    target = op.get("target")
    if isinstance(target, str) and "." in target:
        prefix, _, rest = target.partition(".")
        if prefix in ("requirement", "detailedReport"):
            op["target"] = prefix
            existing = op.get("path") or ""
            op["path"] = f"{rest}.{existing}" if existing else rest
    op.setdefault("path", "")
    return op
