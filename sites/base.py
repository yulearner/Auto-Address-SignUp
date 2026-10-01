"""站点适配器基类（模板方法模式）。

``run()`` 统一编排：打开 → 检测登录 → 登录 → 签到 → 结果，
并对各类异常做统一捕获与状态映射，保证：
- 单个站点失败不影响其他站点；
- 验证码只暂停并请求人工处理，绝不绕过；
- 敏感信息（密码 / Cookie / Token）永不进入日志。

新增站点只需：
1. 继承 ``BaseSite``，设置 ``ADAPTER_NAME``；
2. 提供该站点的选择器默认值（或全部写入 sites.yaml 的 selectors 字段）；
3. 仅在默认逻辑不适用时覆盖 ``is_logged_in`` / ``login`` / ``checkin``。
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path

from playwright.async_api import BrowserContext, Page, TimeoutError as PWTimeoutError

from core.checkin import (
    CheckinOutcome,
    CheckinSelectors,
    checkin_button_candidates,
    click_and_await,
    resolve_checkin_button,
)
from core.exceptions import (
    CaptchaDetectedError,
    CheckinError,
    LoginError,
    SiteError,
)
from core.login import FormLoginSelectors, form_login
from core.page_utils import (
    any_visible,
    detect_captcha,
    find_first_visible,
    screenshot_path,
    take_screenshot,
    wait_page_ready,
)
from core.session import SessionManager
from models.config import RunSettings, SiteConfig
from models.result import CheckinResult, CheckinStatus
from utils.parser import extract_first_amount


@dataclass
class SiteRuntime:
    """站点运行时依赖（由调度器注入，与配置解耦）。"""

    page: Page
    context: BrowserContext
    sessions: SessionManager
    run: RunSettings
    screenshots_dir: Path
    headless: bool = True
    default_timeout_ms: int = 30_000


class BaseSite:
    """所有站点适配器的基类。"""

    ADAPTER_NAME = "generic"

    # ---- 子类可覆盖的默认选择器（均可被 sites.yaml 的 selectors 覆盖）----
    LOGGED_IN_SELECTORS: list[str] = []          # 已登录特征元素候选
    LOCAL_STORAGE_KEYS: list[str] = []          # localStorage 用户信息键候选
    LOGIN_TAB: list[str] = []                    # 登录方式切换 Tab 候选（如 text=邮箱登录）
    LOGIN_USERNAME: list[str] = []              # 用户名输入框候选
    LOGIN_PASSWORD: list[str] = []              # 密码输入框候选
    LOGIN_SUBMIT: list[str] = []                # 登录按钮候选
    LOGIN_ERROR_TOAST: list[str] = []           # 登录失败提示候选
    CHECKIN_KEYWORDS: list[str] = [             # 签到按钮文本关键词
        "签到", "立即签到", "今日签到", "每日签到",
        "Check in", "Daily Check-in", "Check-in",
    ]
    CHECKIN_CSS: list[str] = []                 # 签到按钮 CSS 候选
    CHECKIN_RESPONSE_PATTERNS: list[str] = [    # 签到接口 URL 关键词
        "check_in", "check-in", "checkin", "sign_in", "sign-in", "qiandao",
    ]
    CHECKIN_TOAST: list[str] = []               # 签到结果提示候选
    ALREADY_HINTS: list[str] = ["text=/已签到|明日再来|已领取|今日已签/"]
    BALANCE_SELECTORS: list[str] = []            # 余额元素候选

    def __init__(self, config: SiteConfig, runtime: SiteRuntime) -> None:
        self.config = config
        self.runtime = runtime
        self.page = runtime.page
        self._logger = logging.getLogger(f"site.{config.name}")
        self._used_saved_session = runtime.sessions.has_session(config.session_key)
        # 本次运行中登录态是否已正向确认有效（is_logged_in 通过 / 登录成功）。
        # 只有为 True 时调度器才允许把当前浏览器状态写回 session 文件，
        # 防止登录中途超时/异常的「半登录」状态覆盖原本有效的登录态。
        self._login_verified: bool = False

    @property
    def login_verified(self) -> bool:
        """本次运行是否已确认登录有效（调度器据此决定是否持久化登录态）。"""
        return self._login_verified

    # ------------------------------------------------------------------
    # 日志辅助（自动加站点名前缀）
    # ------------------------------------------------------------------
    def _msg(self, message: str) -> str:
        return f"[{self.config.name}] {message}"

    def info(self, message: str) -> None:
        self._logger.info(self._msg(message))

    def success(self, message: str) -> None:
        self._logger.success(self._msg(message))  # type: ignore[attr-defined]

    def warning(self, message: str) -> None:
        self._logger.warning(self._msg(message))

    def error(self, message: str) -> None:
        self._logger.error(self._msg(message))

    # ------------------------------------------------------------------
    # 选择器合并（sites.yaml 的 selectors 优先于类默认值）
    # ------------------------------------------------------------------
    def _cfg(self, key: str, default: list[str]) -> list[str]:
        value = (self.config.selectors or {}).get(key)
        if isinstance(value, list):
            return [str(v) for v in value if v]
        if isinstance(value, str) and value:
            return [value]
        return list(default)

    def login_selectors(self) -> FormLoginSelectors:
        return FormLoginSelectors(
            tab=self._cfg("login_tab", self.LOGIN_TAB),
            username=self._cfg("username", self.LOGIN_USERNAME),
            password=self._cfg("password", self.LOGIN_PASSWORD),
            submit=self._cfg("submit", self.LOGIN_SUBMIT),
            error_toast=self._cfg("error_toast", self.LOGIN_ERROR_TOAST),
        )

    def checkin_selectors(self) -> CheckinSelectors:
        return CheckinSelectors(
            button_keywords=self._cfg("checkin_keywords", self.CHECKIN_KEYWORDS),
            button_css=self._cfg("checkin_css", self.CHECKIN_CSS),
            response_patterns=self._cfg("checkin_response", self.CHECKIN_RESPONSE_PATTERNS),
            toast=self._cfg("checkin_toast", self.CHECKIN_TOAST),
            already_hints=self._cfg("already_hints", self.ALREADY_HINTS),
        )

    @property
    def nav_timeout_ms(self) -> int:
        return self.runtime.default_timeout_ms

    # ------------------------------------------------------------------
    # 打开站点
    # ------------------------------------------------------------------
    async def open(self, url: str | None = None) -> None:
        """打开站点页面。"""
        target = url or self.config.url
        self.info(f"打开网站: {target}")
        try:
            await self.page.goto(
                target, wait_until="domcontentloaded", timeout=self.nav_timeout_ms
            )
        except PWTimeoutError:
            raise SiteError(f"页面加载超时: {target}")
        await wait_page_ready(self.page, self.nav_timeout_ms)

    # ------------------------------------------------------------------
    # 登录状态检测
    # ------------------------------------------------------------------
    async def is_logged_in(self) -> bool:
        """默认登录态检测策略（DOM + URL + localStorage，子类可覆盖）。

        1. URL 被重定向到登录类路径 → 未登录；
        2. 已登录特征元素可见 → 已登录；
        3. localStorage 中存在用户信息键 → 已登录。
        """
        # 1) URL 判断
        if re.search(r"/(?:login|signin|auth)\b", self.page.url, re.IGNORECASE):
            return False

        # 2) DOM 特征
        if await any_visible(self.page, self._cfg("logged_in", self.LOGGED_IN_SELECTORS)):
            return True

        # 3) localStorage 键
        for key in self._cfg("local_storage_keys", self.LOCAL_STORAGE_KEYS):
            try:
                exists = await self.page.evaluate(
                    f"() => !!localStorage.getItem({key!r})"
                )
            except Exception:  # noqa: BLE001
                exists = False
            if exists:
                return True
        return False

    # ------------------------------------------------------------------
    # 登录
    # ------------------------------------------------------------------
    async def login(self) -> None:
        """表单登录（模拟正常用户操作：输入账号密码并点击提交）。"""
        if not self.config.username or not self.config.password:
            raise LoginError(
                "未配置账号信息，请通过环境变量设置 USERNAME/PASSWORD（见 .env.example）"
            )
        await form_login(
            self.page,
            username=self.config.username,   # 值禁止进入日志
            password=self.config.password,   # 值禁止进入日志
            login_url=self.config.login_url,
            selectors=self.login_selectors(),
            nav_timeout_ms=self.nav_timeout_ms,
        )

    # ------------------------------------------------------------------
    # 手动登录模式
    # ------------------------------------------------------------------
    async def manual_login(self) -> None:
        """手动登录模式：打开登录页，轮询等待人工在浏览器窗口中完成登录。

        登录成功后自动检测登录态 → 回到目标页面 → 由调度器保存 storage_state。
        凭据全程不经过程序，密码无需写入任何文件。

        :raises LoginError: 等待超时仍未检测到登录状态
        """
        wait_s = self.runtime.run.manual_login_wait
        self.info(f"手动登录模式：请在打开的浏览器窗口中完成登录（最多等待 {wait_s} 秒）")

        # 若当前不在登录页则先打开（站点可能已重定向到登录页）
        if "/login" not in self.page.url:
            await self.open(self.config.login_url)

        deadline = time.monotonic() + wait_s
        next_hint = time.monotonic() + 30.0  # 每 30 秒提示一次剩余时间
        while time.monotonic() < deadline:
            if await self.is_logged_in():
                self.success("检测到手动登录成功")
                # 登录后可能停留在首页等页面，回到目标页面（个人中心）再签到
                await self.open()
                return
            if time.monotonic() >= next_hint:
                remain = int(deadline - time.monotonic())
                self.info(f"等待手动登录中…（剩余约 {remain} 秒）")
                next_hint = time.monotonic() + 30.0
            await asyncio.sleep(2.0)

        raise LoginError(f"等待手动登录超时（{wait_s} 秒），未检测到登录状态")

    # ------------------------------------------------------------------
    # 签到
    # ------------------------------------------------------------------
    async def checkin(self) -> CheckinResult:
        """执行签到并返回结果（具体流程由通用引擎完成，子类一般无需覆盖）。

        签到按钮候选按优先级依次尝试（CSS → 文本关键词），直到某一候选
        签到成功或检测到“今日已签到”为止；全部候选尝试完毕仍未成功时，
        才返回签到失败（附各候选的尝试明细）。
        """
        self.info("开始签到")

        selectors = self.checkin_selectors()

        # “今日已签到”页面特征优先判断
        if await any_visible(self.page, selectors.already_hints):
            self.info("检测到今日已签到标志")
            return self._make_result(
                CheckinStatus.ALREADY_CHECKED, message="今日已签到"
            )

        candidates = checkin_button_candidates(selectors)
        if not candidates:
            raise CheckinError(
                "未配置任何签到按钮候选（请在 selectors.checkin_css / checkin_keywords 中提供）"
            )

        total = len(candidates)
        attempts: list[str] = []
        last_outcome: CheckinOutcome | None = None

        for idx, selector in enumerate(candidates, start=1):
            # 每次尝试前重新解析候选（上一次点击可能已改变 DOM）
            button = await resolve_checkin_button(self.page, selector, timeout=1.5)
            if button is None:
                attempts.append(f"#{idx} {selector}（不可见或已失效）")
                continue

            try:
                outcome = await click_and_await(
                    self.page, button, selectors, response_timeout_s=15.0
                )
            except CaptchaDetectedError:
                # 验证码需人工处理，继续尝试其他按钮没有意义，直接上抛
                raise
            except CheckinError as exc:
                # 点击失败：常见原因是前一次误点打开了弹窗/日历浮层，遮罩
                # 挡住了当前按钮 —— 按 Esc 关闭浮层后重试一次同一候选。
                outcome = await self._retry_click_after_escape(selector, selectors)
                if outcome is None:
                    attempts.append(f"#{idx} {selector}（点击失败: {exc}）")
                    continue

            # 成功 / 已签到：终止尝试
            if outcome.status in ("success", "already"):
                self._log_outcome(outcome)
                return self._outcome_to_result(outcome)

            # failed / unknown：视为「未确认签到成功」，继续尝试下一候选。
            # 只有接口响应 / 新 toast / 按钮状态变化才可能给出 success/already，
            # 防止误点“签到详情/日历入口”等伪按钮后被面板常驻文案误导提前终止。
            self.warning(
                f"签到按钮候选 {idx}/{total}（{selector}）未确认签到成功"
                f"（{outcome.status}: {outcome.message or '无有效反馈'}），尝试下一候选"
            )
            attempts.append(f"#{idx} {selector}（{outcome.status}）")
            last_outcome = outcome

        # 全部候选尝试完毕仍未成功 → 签到失败
        self.error(f"共 {total} 个签到按钮候选均未成功")
        message = f"所有签到按钮候选均未成功（共 {total} 个）"
        if last_outcome is not None and last_outcome.message:
            message += f"，最后一次反馈: {last_outcome.message[:80]}"
        if attempts:
            message += f"。尝试明细: {'；'.join(attempts)[:300]}"
        return self._make_result(CheckinStatus.FAILED, message=message)

    async def _retry_click_after_escape(
        self, selector: str, selectors: CheckinSelectors
    ) -> CheckinOutcome | None:
        """按钮点击失败后，按 Esc 关闭可能被误点打开的浮层，再重试一次。

        场景：候选按钮被前一次误点打开的弹窗/日历遮罩遮挡而无法点击。
        重试仍失败、或浮层关闭后按钮已消失时返回 None，交由上层尝试下一候选。
        验证码异常照常上抛（继续尝试其他按钮无意义）。
        """
        try:
            await self.page.keyboard.press("Escape")
            await asyncio.sleep(0.5)
        except Exception:  # noqa: BLE001 - Esc 按键失败不应中断流程
            return None
        button = await resolve_checkin_button(self.page, selector, timeout=1.5)
        if button is None:
            return None
        try:
            return await click_and_await(
                self.page, button, selectors, response_timeout_s=15.0
            )
        except CheckinError as exc:
            self._logger.debug(self._msg(f"Esc 重试后仍点击失败: {str(exc)[:60]}"))
            return None

    async def get_balance(self) -> str | None:
        """尝试提取余额（不可解析时返回 None，绝不猜测）。

        New API 等站点的余额展示形如「当前余额 / $12.34」：标签与金额分处
        不同元素。本方法先在元素自身文本中找金额；找不到则向上遍历父容器
        （标签 + 金额通常位于同一卡片内）再提取，最多 3 层。
        """
        selectors = self._cfg("balance", self.BALANCE_SELECTORS)
        loc = await find_first_visible(self.page, selectors, timeout=3.0)
        if loc is None:
            return None
        try:
            # 1) 元素自身文本
            amount = extract_first_amount(await loc.inner_text())
            if amount:
                return amount
            # 2) 向上最多 3 层父容器（标签 + 金额在同一容器内）
            for _ in range(3):
                loc = loc.locator("xpath=..")
                amount = extract_first_amount(await loc.inner_text())
                if amount:
                    return amount
            # 3) 仍无法解析金额：返回 None（不返回「当前余额」这类标签文本，避免误导）
            return None
        except Exception:  # noqa: BLE001
            return None

    async def logout(self) -> None:
        """登出并清理本地 session（站点可覆盖，默认只删本地存储态）。"""
        # 默认策略：登出按钮未必存在时，直接使本地保存的登录态失效
        self.runtime.sessions.delete(self.config.session_key)

    # ------------------------------------------------------------------
    # 结果构造
    # ------------------------------------------------------------------
    def _make_result(
        self,
        status: CheckinStatus,
        *,
        message: str = "",
        reward: str | None = None,
        balance: str | None = None,
        error: str | None = None,
        screenshot: str | None = None,
    ) -> CheckinResult:
        return CheckinResult(
            site=self.config.name,
            status=status,
            message=message,
            reward=reward,
            balance=balance,
            used_saved_session=self._used_saved_session,
            screenshot=screenshot,
            error=error,
        )

    def _outcome_to_result(self, outcome: CheckinOutcome) -> CheckinResult:
        """将通用签到引擎的原始结果映射为最终 ``CheckinResult``。"""
        status_map = {
            "success": CheckinStatus.SUCCESS,
            "already": CheckinStatus.ALREADY_CHECKED,
            "failed": CheckinStatus.FAILED,
            "unknown": CheckinStatus.UNKNOWN,
        }
        return self._make_result(
            status_map.get(outcome.status, CheckinStatus.UNKNOWN),
            message=outcome.message or ("签到成功" if outcome.status == "success" else ""),
            reward=outcome.reward,
        )

    def _log_outcome(self, outcome) -> None:
        status = getattr(outcome, "status", "unknown")
        if status == "success":
            self.success(f"签到成功{f'，获得 {outcome.reward}' if outcome.reward else ''}")
        elif status == "already":
            self.info("今日已签到")
        elif status == "failed":
            self.error(f"签到失败{f'：{outcome.message}' if outcome.message else ''}")
        else:
            self.warning("签到状态未知，请人工确认")

    async def _screenshot(self, state: str) -> str | None:
        return await take_screenshot(
            self.page,
            screenshot_path(self.runtime.screenshots_dir, self.config.session_key, state),
        )

    # ------------------------------------------------------------------
    # 验证码处理（不绕过、不破解，只暂停并请求人工处理）
    # ------------------------------------------------------------------
    async def handle_captcha(self, hint: str) -> None:
        """检测到验证码时的处理流程：截图 → 记录 → 等待人工（可选）。

        :raises CaptchaDetectedError: 人工等待超时或配置为不等待时抛出
        """
        shot = await self._screenshot("captcha")
        self.error(f"[需要人工处理] 站点: {self.config.name} 原因: 检测到验证码（{hint}）")

        wait_seconds = self.runtime.run.captcha_wait
        if wait_seconds > 0 and not self.runtime.headless:
            self.info(f"请在打开的浏览器窗口中手动完成验证，最多等待 {wait_seconds} 秒…")
            deadline = time.monotonic() + wait_seconds
            while time.monotonic() < deadline:
                await asyncio.sleep(1.0)
                if await detect_captcha(self.page) is None:
                    self.success("人工验证已完成，继续执行")
                    return
            raise CaptchaDetectedError("人工验证等待超时")
        raise CaptchaDetectedError(f"检测到验证码: {hint}（截图: {shot or '未生成'}）")

    # ------------------------------------------------------------------
    # 统一主流程（模板方法）
    # ------------------------------------------------------------------
    async def run(self) -> CheckinResult:
        """统一执行流程：打开 → 检测登录 → 登录 → 签到 → 返回结果。

        所有站点级异常在此统一捕获并映射为 ``CheckinStatus``，
        保证单个站点失败不影响其他站点。
        """
        start = time.monotonic()
        result: CheckinResult | None = None
        try:
            await self.open()

            # 验证码检测（登录前）
            if await detect_captcha(self.page):
                await self.handle_captcha("页面出现验证码")

            # 登录状态检测
            self.info("检测登录状态")
            if await self.is_logged_in():
                self._login_verified = True
                self.info("已登录" + ("（复用保存的登录态）" if self._used_saved_session else ""))
            else:
                if self.runtime.run.manual_login:
                    # 手动登录模式：打开浏览器等待人工登录（凭据不经程序）
                    await self.manual_login()
                else:
                    self.info("未登录，执行登录流程")
                    await self.login()
                if not await self.is_logged_in():
                    raise LoginError("登录流程完成后仍未检测到登录状态（账号密码错误或页面结构变化）")
                self._login_verified = True
                self.success("登录成功")

            # 签到
            if not self.config.checkin_enabled:
                result = self._make_result(
                    CheckinStatus.SUCCESS, message="未启用签到（仅登录验证）"
                )
                self.info("未启用签到，仅完成登录验证")
            else:
                # 签到入口与当前页面不同时，先导航到签到页面
                target = self.config.checkin_url
                if target and target.rstrip("/") != self.page.url.rstrip("/"):
                    self.info(f"跳转签到页面: {target}")
                    await self.open(target)
                result = await self.checkin()

            # 补充余额信息（尽力而为，失败不影响结果）
            if result.balance is None:
                try:
                    result.balance = await self.get_balance()
                except Exception:  # noqa: BLE001
                    pass

            # 按配置决定是否登出
            if self.config.logout_after:
                # 登出后当前状态不再是有效登录态，禁止调度器再写回 session 文件
                # （置于 try 之前：即使登出失败也不回写，避免半登出状态污染）
                self._login_verified = False
                try:
                    await self.logout()
                except Exception as exc:  # noqa: BLE001
                    self.warning(f"登出失败: {exc}")

            return result

        # ---------------- 异常统一映射 ----------------
        except CaptchaDetectedError as exc:
            shot = None
            try:
                shot = await self._screenshot("captcha")
            except Exception:  # noqa: BLE001
                pass
            result = self._make_result(
                CheckinStatus.CAPTCHA, message=str(exc), error=str(exc), screenshot=shot
            )
            self.error(f"需要人工处理验证码: {exc}")
        except LoginError as exc:
            # 登录失败：本地保存的登录态很可能已失效，删除以便下次重新登录
            self.runtime.sessions.delete(self.config.session_key)
            shot = None
            try:
                shot = await self._screenshot("login_failed")
            except Exception:  # noqa: BLE001
                pass
            result = self._make_result(
                CheckinStatus.LOGIN_FAILED, message=str(exc), error=str(exc), screenshot=shot
            )
            self.error(f"登录失败\n原因: {exc}")
        except (PWTimeoutError, TimeoutError) as exc:
            shot = None
            try:
                shot = await self._screenshot("timeout")
            except Exception:  # noqa: BLE001
                pass
            result = self._make_result(
                CheckinStatus.TIMEOUT, message="页面操作超时", error=str(exc), screenshot=shot
            )
            self.error("处理超时")
        except (CheckinError, SiteError) as exc:
            shot = None
            try:
                shot = await self._screenshot("error")
            except Exception:  # noqa: BLE001
                pass
            result = self._make_result(
                CheckinStatus.FAILED, message=str(exc), error=str(exc), screenshot=shot
            )
            self.error(f"签到失败\n原因: {exc}")
        except Exception as exc:  # noqa: BLE001 - 兜底，任何未知异常都不能中断其他站点
            shot = None
            try:
                shot = await self._screenshot("error")
            except Exception:  # noqa: BLE001
                pass
            result = self._make_result(
                CheckinStatus.UNKNOWN, message="发生未预期异常", error=repr(exc), screenshot=shot
            )
            self.error(f"发生未预期异常: {exc!r}")

        finally:
            duration = time.monotonic() - start
            if result is not None:
                result.duration = round(duration, 2)
        return result



