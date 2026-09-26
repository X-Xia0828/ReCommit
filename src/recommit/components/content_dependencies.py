"""Read prerequisites for content that requires public observations."""

import re

CONTENT_READ_TOOLS = frozenset(
    {"users.list", "conversations.members", "search.messages", "search.all"}
)


def content_reads_for_task(task, obligations):
    writes = {str(item.get("effect") or "") for item in obligations}
    if not writes & {"chat.postMessage", "chat.update", "conversations.setTopic"}:
        return []
    reads = []
    if re.search(r"\bmention\b|user element|\badmins?\b", task, re.I):
        reads.append("users.list")
    if re.search(r"\bmembers?\b|\broster\b", task, re.I):
        reads.append("conversations.members")
    if re.search(r"\bsearch\b|\bgather\b|\bfind all\b|\bcombine\b", task, re.I):
        reads.append("search.messages")
    return reads
