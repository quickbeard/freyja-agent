"""Tools available to the orchestrator.

The only tool of Freyja's own with an effect outside the workspace is ``send_notification``,
a placeholder that writes a log line and nothing else. It exists so the approval gate
(constitution principle I) can be built and tested before any real external tool exists. Its
classification, and the gate's configuration for it, are in ``agent/catalog.py``.
"""

import logging

from langchain_core.tools import tool

logger = logging.getLogger("freyja_agent.actions")


@tool
def send_notification(channel: str, message: str) -> str:
    """Send a notification to a channel.

    Placeholder: nothing is sent. The call is recorded as a log line only. Use it when the
    user asks you to notify a channel or a team about something.
    """
    logger.warning("send_notification channel=%s message=%s", channel, message)
    return f"Notification to {channel} recorded (placeholder: logged only)."
