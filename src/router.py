"""Message formatting and channel routing."""
import re
from typing import Optional

from .timezone import format_local_time
from .schema import Channel, NewMessage


def escape_xml(s: str) -> str:
    if not s:
        return ""
    return (
        s.replace("&", "&amp;")
         .replace("<", "&lt;")
         .replace(">", "&gt;")
         .replace('"', "&quot;")
    )


def format_messages(messages: list[NewMessage], timezone: str) -> str:
    lines = []
    for m in messages:
        display_time = format_local_time(m.timestamp, timezone)
        lines.append(
            f'<message sender="{escape_xml(m.sender_name)}" '
            f'time="{escape_xml(display_time)}">'
            f"{escape_xml(m.content)}</message>"
        )
    header = f'<context timezone="{escape_xml(timezone)}" />\n'
    return f"{header}<messages>\n" + "\n".join(lines) + "\n</messages>"


_INTERNAL_PATTERN = re.compile(r"<internal>[\s\S]*?</internal>", re.DOTALL)


def strip_internal_tags(text: str) -> str:
    return _INTERNAL_PATTERN.sub("", text).strip()


def format_outbound(raw_text: str) -> str:
    return strip_internal_tags(raw_text)


def find_channel(channels: list[Channel], jid: str) -> Optional[Channel]:
    for ch in channels:
        if ch.owns_jid(jid):
            return ch
    return None


async def route_outbound(channels: list[Channel], jid: str, text: str) -> None:
    ch = find_channel(channels, jid)
    if not ch:
        raise RuntimeError(f"No channel for JID: {jid}")
    await ch.send_message(jid, text)
