"""控制台通知渠道（第一阶段默认实现）。"""

from __future__ import annotations

import logging

from notifications.base import BaseNotifier

logger = logging.getLogger(__name__)


class ConsoleNotifier(BaseNotifier):
    """将通知打印到控制台。"""

    name = "console"

    async def send(self, title: str, text: str) -> None:
        print(f"\n{'=' * 30} {title} {'=' * 30}")
        print(text)
