"""通用登录流程（模拟正常网页操作）。

所有站点共享，但选择器/文案由各 SiteAdapter 提供配置；
适配器可整体覆盖 ``BaseSite.login()``，也可以只提供选择器。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from playwright.async_api import Page, TimeoutError as PWTimeoutError

from core.exceptions import CaptchaDetectedError, LoginError
from core.page_utils import detect_captcha, find_first_visible, wait_page_ready

logger = logging.getLogger(__name__)


@dataclass
class FormLoginSelectors:
    """表单登录所需的选择器候选（全部支持多候选，按顺序匹配）。"""

    # 登录方式切换 Tab 候选（如「账号登录 / 邮箱登录」二选一页面）
    tab: list[str] = field(default_factory=list)
    username: list[str] = field(default_factory=list)
    password: list[str] = field(default_factory=list)
    submit: list[str] = field(default_factory=list)
    # 登录失败提示元素候选（错误提示文案会从这些元素提取）
    error_toast: list[str] = field(default_factory=list)


async def grab_error_message(
    page: Page, selectors: list[str], max_poll: float = 3.0
) -> str | None:
    """轮询抓取登录失败提示文本（多为异步 toast）。"""
    deadline = asyncio.get_event_loop().time() + max_poll
    while asyncio.get_event_loop().time() < deadline:
        loc = await find_first_visible(page, selectors, timeout=0.2)
        if loc is not None:
            try:
                text = (await loc.inner_text()).strip()
                if text:
                    return text
            except Exception:  # noqa: BLE001
                pass
        await asyncio.sleep(0.25)
    return None


async def wait_login_result(
    page: Page,
    login_url: str,
    error_selectors: list[str],
    timeout_s: float = 15.0,
) -> None:
    """点击登录后等待结果：跳转离开登录页则成功；出现错误提示则记录原因。

    :raises LoginError: 检测到错误提示，或超时后仍未离开登录页
    """
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        # 已经跳转离开登录页
        try:
            if login_url.rstrip("/") not in page.url.rstrip("/"):
                return
        except Exception:  # noqa: BLE001
            pass
        # 页面是否出现错误提示
        err = await grab_error_message(page, error_selectors, max_poll=0.6)
        if err:
            raise LoginError(f"登录失败：{err}")
        # 验证码出现
        if await detect_captcha(page):
            raise CaptchaDetectedError("登录过程中出现验证码，需要人工处理")
        await asyncio.sleep(0.25)

    # 超时仍未离开登录页：交给调用方再次确认登录态
    logger.debug("登录后未观察到页面跳转，将通过登录态检测确认结果")


async def form_login(
    page: Page,
    *,
    username: str,
    password: str,
    login_url: str,
    selectors: FormLoginSelectors,
    nav_timeout_ms: int = 30_000,
    pre_login: Callable[[Page], Awaitable[None]] | None = None,
) -> None:
    """执行标准表单登录。

    流程：打开登录页 → 检测验证码 → 填写账号密码 → 点击登录 → 等待结果。

    :param username: 用户名（禁止写入日志）
    :param password: 密码（禁止写入日志）
    :param login_url: 登录页 URL
    :param selectors: 登录元素选择器候选
    :param pre_login: 点击登录按钮前的额外操作钩子（如关闭弹窗）
    :raises CaptchaDetectedError: 出现验证码，需要人工处理
    :raises LoginError: 元素缺失或登录失败
    """
    logger.info("打开登录页面: %s", login_url)
    try:
        await page.goto(login_url, wait_until="domcontentloaded", timeout=nav_timeout_ms)
    except PWTimeoutError:
        raise LoginError(f"登录页加载超时: {login_url}")
    await wait_page_ready(page, nav_timeout_ms)

    if await detect_captcha(page):
        raise CaptchaDetectedError("登录页存在验证码，需要人工处理")

    # 登录方式切换 Tab（如「账号登录 / 邮箱登录」二选一页面）：
    # 找到就点击切换；找不到不视为错误（并非所有站点都有 Tab）
    if selectors.tab:
        tab = await find_first_visible(page, selectors.tab, timeout=3.0)
        if tab is not None:
            await tab.click()
            logger.info("已切换登录方式 Tab")
            # 等待新表单替换旧表单，避免匹配到切换前的输入框
            await asyncio.sleep(0.5)

    # 用户名输入框
    user_input = await find_first_visible(page, selectors.username, timeout=10.0)
    if user_input is None:
        raise LoginError(
            "未找到用户名输入框（selectors.username 需要根据实际页面核实）"
        )
    await user_input.fill(username)  # 值禁止进入日志

    # 密码输入框
    pwd_input = await find_first_visible(page, selectors.password, timeout=5.0)
    if pwd_input is None:
        raise LoginError("未找到密码输入框（selectors.password 需要根据实际页面核实）")
    await pwd_input.fill(password)

    if pre_login is not None:
        await pre_login(page)

    # 登录按钮：部分表单填完自动提交（无按钮），找不到时跳过点击直接等结果
    if selectors.submit:
        submit = await find_first_visible(page, selectors.submit, timeout=5.0)
        if submit is not None:
            await submit.click()
            logger.info("已点击登录按钮")
        else:
            logger.info("未找到登录按钮，假定表单将自动提交")

    logger.info("等待登录结果")
    await wait_login_result(page, login_url, selectors.error_toast)
