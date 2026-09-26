"""Bind explicit request spans and public catalog identifiers for Slack.

Ambiguous targets and content remain masked for the HOW model.
"""

from __future__ import annotations

import copy
import re
from typing import Any

from recommit.components.argument_provenance import (
    attach_ledger_meta,
    bind_seed_state,
    bind_task_span,
    track_provenance,
)

MASK = "[M]"
LAST_CHANNEL = "$last_channel"


def missing(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value in (MASK, ""))


def literal_mentions(task, rows, id_key, name_keys):
    """Return distinct catalog IDs in request order, with token boundaries."""
    hits = []
    for row in rows:
        identifier = row.get(id_key)
        if not identifier:
            continue
        for key in (id_key, *name_keys):
            name = str(row.get(key) or "").strip()
            if not name:
                continue
            match = re.search(r"(?<![\w.-])" + re.escape(name) + r"(?![\w-]|\.\w)", task, re.I)
            if match:
                hits.append((match.start(), -len(name), str(identifier)))
    ordered = []
    for _, _, identifier in sorted(hits):
        if identifier not in ordered:
            ordered.append(identifier)
    return ordered


def is_dm_task(task: str) -> bool:
    return bool(re.search(r"\bdm\b|direct message", task, re.I))


def channel_mentions(task: str) -> list[str]:
    return list(dict.fromkeys(m.group(1).lower() for m in re.finditer(r"#([A-Za-z0-9_-]+)", task)))


def channel_id_by_name(seed):
    return {
        str(row["channel_name"]).lower(): str(row["channel_id"])
        for row in seed.get("channels", [])
        if row.get("channel_name") and row.get("channel_id")
    }


def channel_ids_from_task(task, seed):
    return literal_mentions(task, seed.get("channels", []), "channel_id", ("channel_name",))


def user_ids_from_task(task, seed):
    return literal_mentions(
        task, seed.get("users", []), "user_id", ("username", "display_name", "real_name")
    )


def recipient_ids(task, seed):
    # Prefer the explicit recipient clause over people mentioned in message text.
    clause = re.search(
        r"(?:\bto\s+|\bdm\s+)(.+?)(?=\s+saying\b|\s+with\s+(?:the\s+)?(?:text|message)\b|[.;\n]|$)",
        task,
        re.I,
    )
    users = user_ids_from_task(clause.group(1), seed) if clause else []
    if not users:
        users = user_ids_from_task(task, seed)
    if len(users) == 1:
        return users
    if users and re.search(r"group (?:conversation|message)|\bto\b[^.;\n]+\band\b", task, re.I):
        return users
    return []


def explicit_text(task, kind):
    if re.search(r"block kit|rich_text", task, re.I):
        return None
    cue = (
        r"(?:topic\s+(?:to|as)|set\s+(?:the\s+)?topic\s+to)"
        if kind == "topic"
        else r"(?:saying|post|message(?:\s+text)?|text)"
    )
    match = re.search(cue + r"\s*[:=]?\s*(['\"])(.+?)\1", task, re.I | re.S)
    if not match or re.search(r"\[[A-Z][A-Z0-9_]*\]", match.group(2)):
        return None
    return match.group(2)


def explicit_message(task, seed):
    """Compose a literal body with explicitly named mentions, or defer to HOW.

    Only the simple ``mentioning NAME with text 'BODY'`` construction is
    completed here. Unknown names, negation, formatting, and other mention
    instructions must not turn a partial literal body into a locked value.
    """
    text = explicit_text(task, "text")
    if text is None:
        return None, []
    # Ignore mention words inside the literal body itself.
    outer = re.sub(r"(['\"])(.*?)\1", " ", task, flags=re.S)
    if not re.search(r"\bmention(?:ing|s)?\b|<@|(?<!\w)@", outer, re.I):
        return text, []
    match = re.fullmatch(
        r"(?:please\s+)?post\s+to\s+#[\w-]+\s+mentioning\s+(.+?)"
        r"\s+with\s+(?:the\s+)?text\s*(['\"])(.+?)\2\s*[.!]?",
        task.strip(),
        re.I | re.S,
    )
    if not match or match.group(3) != text:
        return None, []
    names = re.split(r"\s*,\s*(?:and\s+)?|\s+and\s+", match.group(1))
    identifiers = []
    for name in names:
        name = name.strip().lstrip("@").casefold()
        hits = {
            str(row["user_id"])
            for row in seed.get("users", [])
            if row.get("user_id")
            if name
            in {
                str(row.get(k) or "").casefold()
                for k in ("user_id", "username", "display_name", "real_name")
            }
        }
        if not name or len(hits) != 1:
            return None, []
        identifier = next(iter(hits))
        if identifier not in identifiers:
            identifiers.append(identifier)
    prefix = " ".join(f"<@{uid}>" for uid in identifiers if f"<@{uid}>" not in text)
    return ((prefix + " " + text) if prefix else text), identifiers


def explicit_channel_name(task, rename=False):
    patterns = (
        [
            r"rename\s+#?[A-Za-z0-9_-]+\s+to\s+['\"]?#?([A-Za-z0-9_-]+)",
            r"rename\s+it\s+to\s+['\"]?#?([A-Za-z0-9_-]+)",
        ]
        if rename
        else [
            r"(?:called|named)\s+['\"]#?([A-Za-z0-9_-]+)['\"]",
            r"create\s+(?:(?:a|new|private|public)\s+)*channel\s+['\"]#?([A-Za-z0-9_-]+)['\"]",
        ]
    )
    for pattern in patterns:
        match = re.search(pattern, task, re.I)
        if match:
            return match.group(1)
    return None


def message_target(task, seed, channel):
    """Resolve an explicit message ID or quoted text; use recency only if requested."""
    rows = [
        r for r in seed.get("messages", []) if not channel or str(r.get("channel_id")) == channel
    ]
    direct = literal_mentions(task, rows, "message_id", ())
    if len(direct) == 1:
        return direct[0]
    quoted = [a or b for a, b in re.findall(r"'([^']+)'|\"([^\"]+)\"", task)]
    hits = [
        str(r["message_id"])
        for r in rows
        if r.get("message_id")
        and any(q and q.casefold() in str(r.get("message_text", "")).casefold() for q in quoted)
    ]
    if len(set(hits)) == 1:
        return hits[0]
    if re.search(r"most recent|latest", task, re.I):
        parents = [r for r in rows if not r.get("parent_id")]
        if parents:
            # Public fixture messages are stored in chronological order.
            return str(parents[-1]["message_id"])
    return None


def reaction_names(task):
    names = re.findall(r":([A-Za-z0-9_+-]+):", task)
    if not names:
        for phrase, name in (("thumbs up", "thumbsup"), ("thumbs down", "thumbsdown")):
            if phrase in task.lower():
                names.append(name)
    return names


def bind_plan(calls, task: str, seed: dict, *, ids_only=False, fail_closed=True):
    """Fill unresolved slots only; leave unsupported inferences to the HOW model."""
    with track_provenance() as ledger:
        bound = copy.deepcopy(calls)
        channels = channel_ids_from_task(task, seed)
        users = recipient_ids(task, seed)
        channel_map = channel_id_by_name(seed)
        reactions = reaction_names(task)
        created = opened = False
        channel_index = 0

        def assign(args, key, value, tool, *, catalog=True):
            if value is None or not missing(args.get(key)):
                return
            bind = bind_seed_state if catalog else bind_task_span
            args[key] = bind(
                value, tool=tool, key=key, note="catalog_literal" if catalog else "request_span"
            )

        for call in bound:
            tool = str(call.get("tool") or "")
            args = call.setdefault("arguments", {})
            if tool == "conversations.open":
                if users:
                    assign(args, "users", ",".join(users), tool)
                opened = True
                continue
            if "channel" in args and missing(args["channel"]):
                if created or (opened and tool == "chat.postMessage"):
                    assign(args, "channel", LAST_CHANNEL, tool, catalog=False)
                elif channels:
                    rank = (
                        -1
                        if tool == "chat.postMessage" and len(channels) > 1
                        else min(channel_index, len(channels) - 1)
                    )
                    assign(args, "channel", channels[rank], tool)
            channel_value = args.get("channel")
            if isinstance(channel_value, str) and channel_value.lstrip("#").lower() in channel_map:
                args["channel"] = bind_seed_state(
                    channel_map[channel_value.lstrip("#").lower()],
                    tool=tool,
                    key="channel",
                    note="catalog_alias",
                )
            channel = args.get("channel")
            if tool in {"conversations.invite", "conversations.kick"} and users:
                key = "users" if "users" in args else "user"
                if key == "users" or len(users) == 1:
                    assign(args, key, ",".join(users) if key == "users" else users[0], tool)
            if tool == "users.info" and len(users) == 1:
                assign(args, "user", users[0], tool)
            if tool in {"chat.update", "chat.delete", "reactions.add", "reactions.remove"}:
                key = "timestamp" if tool.startswith("reactions.") else "ts"
                assign(args, key, message_target(task, seed, channel), tool)
            if tool.startswith("reactions.") and len(reactions) == 1:
                assign(args, "name", reactions[0], tool, catalog=False)
            if tool == "chat.postMessage" and re.search(
                r"\breply\b.*\b(?:thread|message)\b", task, re.I
            ):
                assign(args, "thread_ts", message_target(task, seed, channel), tool)
            if not ids_only:
                if tool in {"chat.postMessage", "chat.update"}:
                    if missing(args.get("text")):
                        text, mentioned = explicit_message(task, seed)
                        if text is not None:
                            for user_id in mentioned:
                                bind_seed_state(
                                    user_id, tool=tool, key="text", note="explicit_mention"
                                )
                            assign(args, "text", text, tool, catalog=False)
                if tool == "conversations.setTopic":
                    assign(args, "topic", explicit_text(task, "topic"), tool, catalog=False)
                if tool in {"conversations.create", "conversations.rename"}:
                    assign(
                        args,
                        "name",
                        explicit_channel_name(task, rename=tool.endswith("rename")),
                        tool,
                        catalog=False,
                    )
                if tool == "search.messages":
                    query = re.search(
                        r"(?:search(?:\s+for)?|about|related to)\s+(['\"])(.+?)\1", task, re.I
                    )
                    if query:
                        assign(args, "query", query.group(2), tool, catalog=False)
            if tool == "conversations.create":
                created = True
            if tool in {
                "conversations.join",
                "conversations.leave",
                "conversations.archive",
                "conversations.unarchive",
            }:
                channel_index += 1
        if fail_closed:
            ledger.assert_no_constants()
        return attach_ledger_meta(bound, ledger)
