"""WindHub 站点适配器（https://windhub.cc）。

站点特征（已通过抓取确认）：
- 基于 New API 框架（React SPA，Semi UI 组件库）；
- 页面需执行 JavaScript 渲染，静态 HTML 中无法确认具体 DOM；
- 登录页：https://windhub.cc/login（存在「账号登录 / 邮箱登录」二选一 Tab）；
- 个人中心：https://windhub.cc/console/personal（含「每日签到」卡片与余额 badge）；
- 余额展示形如「🍥27.05 当前余额」（站点自定义 emoji 货币符号）。

✅ 已核实（2026-09-29 探针脚本 probe_dom.py + 实际运行验证）：
- 登录态检测：/api/user/self 接口（success=true）+ localStorage user 键；
- 签到卡片：位于个人中心页，已签到时按钮 disabled 文案「今日已签到」；
- 余额 badge：span.semi-badge 文本「🍥27.05\n当前余额」。

⚠️ 仍未验证（下次真实签到时确认）：
- 未签到状态下按钮的可点击文案（预计为「签到 / 立即签到」类）；
- 签到 XHR 接口 URL（现有候选：check_in / checkin 等关键词）。

所有选择器均可在 config/sites.yaml 的 ``selectors`` 字段中覆盖。
"""

from __future__ import annotations

import logging

from sites.base import BaseSite

logger = logging.getLogger(__name__)


class WindHubSite(BaseSite):
    """WindHub（New API 框架站点）适配器。"""

    ADAPTER_NAME = "windhub"

    # ---- 登录态特征 ----
    # New API 登录后会在 localStorage 写入用户信息（键名 user）
    LOCAL_STORAGE_KEYS = ["user"]
    # 已登录时可见的界面特征候选（可根据实际页面补充）
    LOGGED_IN_SELECTORS = [
        'a[href*="/console"]',
        'text=/退出登录|注销/',
        '[class*="avatar"]',
    ]

    # ---- 表单登录选择器（New API / Semi UI 风格，待实际核实）----
    # 登录方式切换 Tab：WindHub 登录页存在「账号登录 / 邮箱登录」二选一，需先切到邮箱登录
    LOGIN_TAB = [
        "text=邮箱登录",
        "text=/邮箱\\s*登录/",
        'button:has-text("邮箱登录")',
        '[role="tab"]:has-text("邮箱")',
    ]
    LOGIN_USERNAME = [
        'input[type="email"]',
        'input[placeholder*="邮箱"]',
        'input[placeholder*="email" i]',
        'input[placeholder*="用户名"]',
        'input[placeholder*="username" i]',
    ]
    LOGIN_PASSWORD = [
        'input[type="password"]',
        'input[placeholder*="密码"]',
        'input[placeholder*="password" i]',
    ]
    LOGIN_SUBMIT = [
        'button:has-text("登录")',
        'button:has-text("登 录")',
        'button:has-text("Login")',
        'button[type="submit"]',
        "button.semi-button-primary",
    ]
    # Semi UI toast / 通用错误提示候选
    LOGIN_ERROR_TOAST = [
        ".semi-toast-content",
        ".semi-toast",
        '[class*="toast"]',
        '[role="alert"]',
        ".el-message",
    ]

    # ---- 签到 ----
    # 已核实：签到功能位于个人中心页「每日签到」卡片内；
    # 已签到时按钮文案变为「今日已签到」且带 disabled 样式（按钮不可重复点击）
    CHECKIN_KEYWORDS = ["立即签到", "今日签到", "每日签到", "签到", "Check in", "Check-in"]
    # 精确候选：可点击（非 disabled）的签到按钮
    CHECKIN_CSS = [
        'button:not(.semi-button-disabled):has-text("立即签到")',
        'button:not(.semi-button-disabled):has-text("签到")',
    ]
    CHECKIN_RESPONSE_PATTERNS = [
        "check_in", "check-in", "checkin", "sign_in", "sign-in", "qiandao",
    ]
    CHECKIN_TOAST = [
        ".semi-toast-content",
        ".semi-toast",
        '[class*="toast"]',
        '[role="alert"]',
        ".el-message",
    ]
    ALREADY_HINTS = ["text=/已签到|明日再来|已领取|今日已签/"]

    # ---- 余额（已核实：个人中心「当前余额」badge 文本形如「🍥27.05\n当前余额」）----
    BALANCE_SELECTORS = [
        'span.semi-badge:has-text("余额")',
        'text=/当前余额|余额/ >> nth=0',
        '[class*="balance"]',
    ]

    async def is_logged_in(self) -> bool:
        """WindHub 登录态检测（在默认策略之上增加 New API 用户接口探测）。

        优先级：
        1. URL 位于登录页 → 未登录；
        2. New API 用户信息接口 ``/api/user/self`` 返回 success → 已登录
           （该接口是 New API 框架的通用接口，比 DOM 更稳定）；
        3. 默认策略（DOM 特征 / localStorage）。
        """
        # 1) URL 判断
        if "/login" in self.page.url:
            return False

        # 2) New API 通用用户接口探测（携带 Cookie 发起页面自身的请求）
        try:
            logged = await self.page.evaluate(
                """async () => {
                    try {
                        const resp = await fetch('/api/user/self',
                            {credentials: 'include', headers: {'Accept': 'application/json'}});
                        if (!resp.ok) return false;
                        const data = await resp.json();
                        return !!(data && data.success === true && data.data);
                    } catch (e) { return false; }
                }"""
            )
            if logged:
                return True
        except Exception:  # noqa: BLE001
            pass

        # 3) 默认策略
        return await super().is_logged_in()
