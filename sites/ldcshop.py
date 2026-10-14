"""LdcShop 站点适配器（ldc-shop 框架，示例站：https://store.7li7li.com）。

站点特征（已通过 GitHub 源码 chatgptuk/ldc-shop v1.4.1 逐文件确认，无猜测）：
- 框架：Next.js 16 + shadcn/ui + NextAuth（OAuth-only，仅 Linux DO / GitHub 登录）；
- 无账号密码表单 → 自动登录不可用，首次必须 ``--manual-login``
  （NextAuth 凭据保存在 Cookie，storage_state 保存登录态后即全自动）；
- 未登录访问 /profile 会被服务端 ``redirect("/")``；
- 登录态检测：NextAuth 标准会话端点 ``/api/auth/session``（通用接口，比 DOM 稳）；
- 签到按钮：文本「签到」（shadcn Button，已签到时不再渲染或显示 disabled「已签到」）；
- 签到触发：React Server Action（POST 当前页 URL），无独立签到 API
  → ``CHECKIN_RESPONSE_PATTERNS`` 置空，结果确认依赖「点击后新出现的 toast」；
- 结果 toast：sonner 库（``[data-sonner-toast]``），
  成功「签到成功！+X 积分」、重复「今天已签到」；
- 选择弹窗：点击签到后先弹出「选择签到模式」对话框（试试手气 / 积分x8 / 取消），
  引擎输出选项等待人工选择，超时自动选择第一个真选项（跳过取消/关闭类按钮）。

所有选择器均可在 config/sites.yaml 的 ``selectors`` 字段中覆盖。
"""

from __future__ import annotations

import logging
import re

from core.exceptions import LoginError
from sites.base import BaseSite

logger = logging.getLogger(__name__)


class LdcShopSite(BaseSite):
    """ldc-shop（Next.js + shadcn/ui + NextAuth OAuth）适配器。"""

    ADAPTER_NAME = "ldcshop"

    # ---- 登录态特征 ----
    # NextAuth 凭据保存在 Cookie（next-auth.session-token），不写 localStorage
    LOCAL_STORAGE_KEYS: list[str] = []
    # 登录后 profile 页渲染的界面特征：退出按钮（i18n 中/英）、签到卡片按钮
    LOGGED_IN_SELECTORS = [
        "text=/退出登录|登出/",
        "text=/Logout|Log\\s*Out/i",
        'button:has-text("签到")',
    ]

    # ---- 表单登录（本站无表单，login() 已覆盖为抛出指引，以下不使用）----

    # ---- 签到（源码确认：Server Action 无独立 API，toast 为结果信号）----
    CHECKIN_KEYWORDS = ["签到", "每日签到", "Check in", "Check-in"]
    # 精确候选：可点击（非 disabled）的签到按钮；「已签到」disabled 按钮被 :not([disabled]) 排除，
    # 引擎 _STALE_BUTTON_TEXTS 也会跳过含「已签到」文案的按钮
    CHECKIN_CSS = [
        'button:not([disabled]):has-text("签到")',
        'button:not([disabled]):has-text("Check in")',
    ]
    # 置空：ldc-shop 用 Server Action（POST 当前页 URL），无独立签到接口可监听；
    # 引擎 click_and_await 将自动跳过接口监听，走「点击后新 toast」确认路径
    CHECKIN_RESPONSE_PATTERNS: list[str] = []

    # ---- 选择弹窗（2026-10-13 无头实测 DOM 确认，非源码猜测）----
    # 点击「签到」后先弹出 Radix Dialog「选择签到模式」：
    #   ① 试试手气（1-20 积分，随机） ② 积分x8（固定积分） ③ 取消 ④ Close(右上角 X)
    # 引擎将输出选项等待人工选择（默认 5 秒），超时自动点第一个真选项（跳过取消/Close）
    CHECKIN_DIALOG = [
        "[role='dialog'][data-state='open']",
        "[data-slot='dialog-content']",
    ]
    # 真选项关键词：留空 = 除「取消/关闭」类按钮外全部视为真选项
    CHECKIN_DIALOG_OPTIONS: list[str] = []
    # 等待人工选择的秒数（sites.yaml 可用 selectors.checkin_dialog_wait 覆盖）
    CHECKIN_DIALOG_WAIT: float = 5.0

    # sonner 库的 toast 容器特征属性（比 class 名稳定）；[class*="toast"] 作通用兜底
    CHECKIN_TOAST = [
        "[data-sonner-toast]",
        "[data-sonner-toaster]",
        '[class*="toast"]',
    ]
    # 已签到页面特征：只配精确文案，不配宽泛「已签到」——
    # 防止「已连续签到 N 天」等常驻统计文案造成误判
    ALREADY_HINTS = [
        "text=今天已签到",
        'button[disabled]:has-text("已签到")',
    ]

    # ---- 积分余额 ----
    # profile 页积分卡片带 lucide Coins 图标（class="lucide lucide-coins"）；
    # 展示文案若为「我的积分 27」形式则无法解析金额 → 返回 None（不猜测）
    BALANCE_SELECTORS = [
        '[class*="lucide-coins"]',
        'text=/积分余额|我的积分/',
    ]

    async def is_logged_in(self) -> bool:
        """ldc-shop 登录态检测（NextAuth，凭据在 Cookie）。

        优先级：
        1. URL 处于登录页 / NextAuth 回调路径 → 未登录（OAuth 跳转中）；
        2. NextAuth 标准会话端点 ``/api/auth/session`` 返回用户信息 → 已登录
           （该接口是 NextAuth 框架通用端点，比 DOM 稳定，与 windhub 的
           ``/api/user/self`` 探测同一模式）；
        3. 默认 DOM 策略兜底（退出登录按钮等）。
        """
        url = self.page.url
        # 1) 登录页 / NextAuth 回调（connect.linux.do 等外部 OAuth 域 fetch 会打到
        #    外部域返回非 JSON → session 探测为 false，无需特判）
        if re.search(r"/(?:login|signin)\b", url) or "/api/auth/" in url:
            return False

        # 2) NextAuth 通用会话端点探测（携带 Cookie 发起页面自身域的请求）
        try:
            logged = await self.page.evaluate(
                """async () => {
                    try {
                        const resp = await fetch('/api/auth/session',
                            {credentials: 'include', headers: {'Accept': 'application/json'}});
                        if (!resp.ok) return false;
                        const data = await resp.json();
                        return !!(data && (data.user || data.expires));
                    } catch (e) { return false; }
                }"""
            )
            if logged:
                return True
        except Exception:  # noqa: BLE001 - evaluate 失败时降级 DOM 策略
            pass

        # 3) 默认 DOM 策略兜底
        return await super().is_logged_in()

    async def login(self) -> None:
        """ldc-shop 仅支持 Linux DO / GitHub OAuth 登录，无账号密码表单。

        自动填写表单不可用：给出明确指引而不是盲目寻找输入框。
        首次请用 ``--manual-login`` 在浏览器窗口中人工完成 OAuth，
        登录态保存（storage_state 含 Cookie）后即可全自动签到。
        """
        raise LoginError(
            "该站点仅支持 Linux DO / GitHub OAuth 登录（无账号密码表单），"
            "请先运行: python main.py --manual-login --site <站点名> 手动登录一次"
        )
