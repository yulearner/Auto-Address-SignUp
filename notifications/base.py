"""通知器抽象基类。"""

from __future__ import annotations

from abc import ABC, abstractmethod


class BaseNotifier(ABC):
    """通知渠道接口：所有通知渠道实现此接口。"""

    name: str = "base"

    @abstractmethod
    async def send(self, title: str, text: str) -> None:
        """发送通知。

        :param title: 通知标题，例如「今日自动签到汇总」
        :param text: 通知正文（汇总报告）
        """
        raise NotImplementedError
