"""统一签到结果数据结构。

所有站点最终必须返回 ``CheckinResult``，供存储、汇总报告与通知模块使用。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class CheckinStatus(str, Enum):
    """签到状态枚举，取值固定。"""

    SUCCESS = "success"                  # 签到成功
    ALREADY_CHECKED = "already_checked"  # 今日已签到
    FAILED = "failed"                    # 签到失败
    LOGIN_FAILED = "login_failed"        # 登录失败
    TIMEOUT = "timeout"                  # 执行超时
    CAPTCHA = "captcha"                  # 需要人工处理验证码
    UNKNOWN = "unknown"                  # 状态未知


# 汇总报告中的中文描述
STATUS_LABELS: dict[CheckinStatus, str] = {
    CheckinStatus.SUCCESS: "签到成功 🎉",
    CheckinStatus.ALREADY_CHECKED: "今日已签到",
    CheckinStatus.FAILED: "签到失败",
    CheckinStatus.LOGIN_FAILED: "登录失败",
    CheckinStatus.TIMEOUT: "处理超时",
    CheckinStatus.CAPTCHA: "需要人工处理验证码",
    CheckinStatus.UNKNOWN: "签到可能失败，请手动检查",
}

# 汇总报告中的图标
STATUS_ICONS: dict[CheckinStatus, str] = {
    CheckinStatus.SUCCESS: "🟢",
    CheckinStatus.ALREADY_CHECKED: "🟡",
    CheckinStatus.FAILED: "🔴",
    CheckinStatus.LOGIN_FAILED: "🔴",
    CheckinStatus.TIMEOUT: "🔴",
    CheckinStatus.CAPTCHA: "⚠️",
    CheckinStatus.UNKNOWN: "⚠️",
}


@dataclass
class CheckinResult:
    """单个站点的最终统一返回结构。

    示例::

        {
            "site": "WindHub",
            "status": "success",
            "message": "签到成功",
            "reward": "$0.955988",
            "balance": null,
            "duration": 3.21,
            "timestamp": "2026-09-28 08:20:06"
        }
    """

    site: str
    status: CheckinStatus
    message: str = ""
    reward: str | None = None
    balance: str | None = None
    duration: float = 0.0
    timestamp: str = field(
        default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )
    # 额外信息：错误原因、人工提示、原始消息等（不含敏感信息）
    error: str | None = None
    # 失败/验证码时的截图文件名（相对路径）
    screenshot: str | None = None
    # 本次是否复用了已保存的登录态
    used_saved_session: bool = False

    def to_dict(self) -> dict[str, Any]:
        """转换为可 JSON 序列化的字典。"""
        data = asdict(self)
        data["status"] = self.status.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CheckinResult:
        """从字典（如数据库记录）重建结果对象。"""
        status_raw = data.get("status", CheckinStatus.UNKNOWN)
        status = (
            status_raw
            if isinstance(status_raw, CheckinStatus)
            else CheckinStatus(str(status_raw))
        )
        return cls(
            site=data["site"],
            status=status,
            message=data.get("message", ""),
            reward=data.get("reward"),
            balance=data.get("balance"),
            duration=float(data.get("duration", 0.0)),
            timestamp=data.get("timestamp", ""),
            error=data.get("error"),
            screenshot=data.get("screenshot"),
            used_saved_session=bool(data.get("used_saved_session", False)),
        )
