"""通知模块。第一阶段仅实现 Console 输出，
后续可扩展 Telegram / 企业微信 / 邮件等，无需改动核心代码。"""

from notifications.base import BaseNotifier
from notifications.console import ConsoleNotifier
from notifications.manager import NotificationManager

__all__ = ["BaseNotifier", "ConsoleNotifier", "NotificationManager"]
