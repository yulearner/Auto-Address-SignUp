"""文本 / 奖励解析工具。

用于从签到提示、页面文本中提取奖励、余额等信息。
原则：解析不出来就返回 None，绝不猜测数据。
"""

from __future__ import annotations

import re

# 金额 / 积分类数字：$12.50 / ¥1.5 / 1.000000 / 1积分 / 10点 / LDC 1.0
# [^\s\w]{1,4} 分支用于站点自定义货币符号（含 emoji，如 New API 的「🍥27.05」）
_AMOUNT_RE = re.compile(
    r"(?:[$¥￥€£]\s*\d[\d,]*(?:\.\d+)?"
    r"|[^\s\w]{1,4}\s*\d[\d,]*(?:\.\d+)?"
    r"|\d[\d,]*(?:\.\d+)?\s*(?:积分|点|枚|个|天|credits?|points?|tokens?)"
    r"|[A-Za-z]{1,6}\s+\d[\d,]*(?:\.\d+)?)",
    re.IGNORECASE,
)

# “获得 / 奖励 / 本次奖励”之后的片段
_REWARD_AFTER_RE = re.compile(
    r"(?:本次奖励|签到奖励|获得|奖励|reward(?:\s+amount)?)\s*[::]?\s*"
    r"([^\s，,。;；！!]{1,24})",
    re.IGNORECASE,
)

# 已签到提示关键词
_ALREADY_RE = re.compile(
    r"(已签到|已经签到|重复签到|今日已签|不可重复|already\s*check|already\s*sign|duplicate)",
    re.IGNORECASE,
)
# 成功提示关键词
_SUCCESS_RE = re.compile(r"(签到成功|成功|success|check[- ]?in\s+ok)", re.IGNORECASE)
# 失败提示关键词
_FAIL_RE = re.compile(r"(失败|错误|fail|error)", re.IGNORECASE)


def strip_html(html: str) -> str:
    """去除 HTML 标签，返回纯文本（仅在需要解析 HTML 时使用）。"""
    try:
        from bs4 import BeautifulSoup  # 局部导入，未安装时可降级

        return BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    except ImportError:
        return re.sub(r"<[^>]+>", " ", html).strip()


def extract_first_amount(text: str | None) -> str | None:
    """提取文本中第一个金额 / 积分类数值，例如 "$0.955988"、"1积分"。"""
    if not text:
        return None
    m = _AMOUNT_RE.search(text)
    return m.group(0).strip() if m else None


def extract_reward(text: str | None) -> str | None:
    """提取"本次奖励 / 获得 X"形式的奖励描述，找不到则尝试退化为首个数值。"""
    if not text:
        return None
    m = _REWARD_AFTER_RE.search(text)
    if m:
        return m.group(1).strip()
    return extract_first_amount(text)


def classify_checkin_message(text: str | None) -> str | None:
    """根据提示文本判断签到结果类别。

    返回：'already'（已签到）/ 'success'（成功）/ 'failed'（失败）/ None（无法判断）
    判断顺序：已签到 > 失败 > 成功。
    """
    if not text:
        return None
    if _ALREADY_RE.search(text):
        return "already"
    if _FAIL_RE.search(text):
        return "failed"
    if _SUCCESS_RE.search(text):
        return "success"
    return None
