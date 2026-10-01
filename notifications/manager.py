"""通知管理器：向所有已注册渠道发送通知。"""

from __future__ import annotations

import logging

from notifications.base import BaseNotifier

logger = logging.getLogger(__name__)


class NotificationManager:
    """聚合多个通知渠道，单个渠道失败不影响其他渠道。"""

    def __init__(self, notifiers: list[BaseNotifier] | None = None) -> None:
        self._notifiers: list[BaseNotifier] = notifiers or []

    def register(self, notifier: BaseNotifier) -> None:
        """注册新的通知渠道。"""
        self._notifiers.append(notifier)

    async def notify(self, title: str, text: str) -> None:
        """向所有渠道发送通知（渠道异常仅记录日志）。"""
        for notifier in self._notifiers:
            try:
                await notifier.send(title, text)
            except Exception as exc:  # noqa: BLE001
                logger.warning("通知渠道 %s 发送失败: %s", notifier.name, exc)
