"""汇总报告生成。"""

from __future__ import annotations

from collections import Counter
from datetime import datetime

from models.result import (
    STATUS_ICONS,
    STATUS_LABELS,
    CheckinResult,
    CheckinStatus,
)


def build_report(results: list[CheckinResult]) -> str:
    """根据所有站点的结果生成汇总报告文本。"""
    counts: Counter[CheckinStatus] = Counter(r.status for r in results)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    success_n = counts[CheckinStatus.SUCCESS]
    already_n = counts[CheckinStatus.ALREADY_CHECKED]
    failed_n = (
        counts[CheckinStatus.FAILED]
        + counts[CheckinStatus.LOGIN_FAILED]
        + counts[CheckinStatus.TIMEOUT]
    )
    manual_n = counts[CheckinStatus.CAPTCHA] + counts[CheckinStatus.UNKNOWN]

    lines: list[str] = [
        "📅 今日自动签到汇总",
        f"时间: {now}",
        "━━━━━━━━━━━━━━━━",
        f"🟢 签到成功: {success_n}",
        f"🟡 今日已签到: {already_n}",
        f"🔴 签到失败: {failed_n}",
        f"⚠️ 需要人工处理: {manual_n}",
        "━━━━━━━━━━━━━━━━",
        "详细结果：",
        "",
    ]

    for r in results:
        lines.append(f"{STATUS_ICONS[r.status]} {r.site}")
        lines.append(f"状态: {STATUS_LABELS[r.status]}")
        if r.reward:
            lines.append(f"🎁 本次奖励: {r.reward}")
        if r.balance:
            lines.append(f"💰 余额: {r.balance}")
        # 非"已签到"状态时展示更多细节（详情与原因重复时只显示一次）
        if r.status not in (CheckinStatus.SUCCESS, CheckinStatus.ALREADY_CHECKED):
            if r.message:
                lines.append(f"详情: {r.message}")
            if r.error and r.error != r.message:
                lines.append(f"原因: {r.error}")
            if r.screenshot:
                lines.append(f"截图: {r.screenshot}")
        lines.append("")

    return "\n".join(lines)
