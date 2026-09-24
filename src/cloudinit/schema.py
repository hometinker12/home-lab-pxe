"""Cloud-init node editor schema: one node owns each modeled top-level cloud-config key.

The node tree is generated from the cloud-init JSON schema by scripts/extract_cloudinit_examples.py
(see generated_nodes.py; do not hand-edit it). This module adds runtime metadata from
module_examples.json (field placeholders and each node's doc ``example``) and key lookups.

Node: ``id``, ``group``, ``label``, ``help``, ``module``, ``doc_url``, ``example``, ``fields``.
Field: ``key``, ``label``, ``type`` plus optional ``help``, ``deprecated``, ``required``, ``default``,
``placeholder``, ``secret``, ``credential`` and type-specific attributes (see editor.py).
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

from .generated_nodes import ADVANCED_KEYS, ALIASES, DOCS_VERSION, GENERATED_NODES, SCHEMA_KEYS

__all__ = [
    "ADVANCED_KEYS",
    "ALIASES",
    "DOCS_VERSION",
    "MODELED_KEYS",
    "NODES",
    "SCHEMA_KEYS",
    "node_for_key",
    "node_label_for_key",
]

_EXAMPLES_PATH = Path(__file__).with_name("module_examples.json")
_PLACEHOLDER_TYPES = frozenset({"string", "text", "flex", "yaml_value"})


def _load_examples() -> dict:
    if not _EXAMPLES_PATH.is_file():
        return {}
    payload = json.loads(_EXAMPLES_PATH.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _apply_placeholders(nodes: list[dict], placeholders: dict) -> None:
    def walk(fields: list[dict], prefix: str) -> None:
        for field in fields:
            key = field.get("key")
            dotted = f"{prefix}.{key}" if prefix else str(key)
            if field.get("credential"):
                field.setdefault(
                    "placeholder", "{{password_hash}}" if key in {"passwd", "hashed_passwd"} else "{{password}}"
                )
            elif "placeholder" not in field and dotted in placeholders and field.get("type") in _PLACEHOLDER_TYPES:
                field["placeholder"] = placeholders[dotted]
            walk(field.get("object_fields") or [], dotted)
            walk(field.get("item_fields") or [], dotted)

    for node in nodes:
        walk(node.get("fields") or [], "")


def _apply_examples(nodes: list[dict], payload: dict) -> None:
    examples = payload.get("examples") or {}
    chosen = payload.get("node_examples") or {}
    for node in nodes:
        ref = chosen.get(node["id"])
        if not ref:
            continue
        module, index = ref
        texts = examples.get(module) or []
        if isinstance(index, int) and 0 <= index < len(texts):
            node["example"] = texts[index]


def _compose_nodes() -> list[dict]:
    nodes = copy.deepcopy(GENERATED_NODES)
    payload = _load_examples()
    _apply_placeholders(nodes, payload.get("placeholders") or {})
    _apply_examples(nodes, payload)
    return nodes


NODES: list[dict] = _compose_nodes()

_KEY_OWNER: dict[str, dict] = {}
for _node in NODES:
    for _field in _node.get("fields", []):
        _KEY_OWNER.setdefault(_field["key"], _node)

MODELED_KEYS: frozenset[str] = frozenset(_KEY_OWNER) | frozenset(ALIASES)


def node_for_key(key: str) -> dict | None:
    """Node that owns a top-level key (aliases resolve to their canonical key)."""
    return _KEY_OWNER.get(ALIASES.get(key, key))


def node_label_for_key(key: str) -> str:
    node = node_for_key(key)
    return node["label"] if node else key
