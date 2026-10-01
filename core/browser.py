"""浏览器生命周期管理器。

全局共享一个浏览器实例；每个站点使用完全隔离的 ``BrowserContext``，
站点处理完成后关闭 Context，保证 Cookie/localStorage 互不影响。
"""

from __future__ import annotations

import logging
from typing import Any

from playwright.async_api import Browser, BrowserContext, Playwright, async_playwright

from core.session import SessionManager
from models.config import BrowserSettings, SiteConfig

logger = logging.getLogger(__name__)


class BrowserManager:
    """Playwright 浏览器与 Context 管理。"""

    def __init__(self, settings: BrowserSettings, sessions: SessionManager) -> None:
        self._settings = settings
        self._sessions = sessions
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None

    @property
    def is_running(self) -> bool:
        return self._browser is not None and self._browser.is_connected()

    async def start(self) -> None:
        """启动 Playwright 与 Chromium（未指定时默认 Chromium）。"""
        if self.is_running:
            return
        logger.debug(
            "启动 Chromium (headless=%s, slow_mo=%d)",
            self._settings.headless,
            self._settings.slow_mo,
        )
        self._playwright = await async_playwright().start()
        launch_args: dict[str, Any] = dict(
            headless=self._settings.headless,
            slow_mo=self._settings.slow_mo,
            args=[
                "--disable-blink-features=AutomationControlled",  # 降低自动化特征
                "--no-sandbox",
            ],
        )
        self._browser = await self._playwright.chromium.launch(**launch_args)

    async def new_context(self, site: SiteConfig) -> BrowserContext:
        """为指定站点创建完全隔离的上下文，并载入已保存的登录态（若有）。"""
        if not self.is_running:
            raise RuntimeError("浏览器未启动，请先调用 start()")

        ctx_opts: dict[str, Any] = {
            "viewport": {
                "width": self._settings.viewport_width,
                "height": self._settings.viewport_height,
            },
        }
        if self._settings.user_agent:
            ctx_opts["user_agent"] = self._settings.user_agent

        if site.save_session:
            saved = self._sessions.load(site.session_key)
            if saved is not None:
                # storage_state 内容禁止入日志
                ctx_opts["storage_state"] = saved
                logger.info("[%s] 已载入保存的登录态", site.name)
            else:
                logger.info("[%s] 未找到登录态，将执行登录", site.name)

        context = await self._browser.new_context(**ctx_opts)  # type: ignore[union-attr]

        timeout = site.browser_timeout or self._settings.timeout
        context.set_default_timeout(timeout)
        context.set_default_navigation_timeout(timeout)
        return context

    async def save_context_state(self, site: SiteConfig, context: BrowserContext) -> None:
        """持久化当前 Context 的登录态（内容禁止入日志）。"""
        if not site.save_session:
            return
        try:
            state = await context.storage_state()
            self._sessions.save(site.session_key, state)
            logger.info("[%s] 登录态已保存", site.name)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[%s] 登录态保存失败: %s", site.name, exc)

    @staticmethod
    async def close_context(context: BrowserContext | None) -> None:
        """关闭单个 Context。"""
        if context is None:
            return
        try:
            await context.close()
        except Exception as exc:  # noqa: BLE001
            logger.debug("关闭 Context 时出错: %s", exc)

    async def close(self) -> None:
        """关闭浏览器与 Playwright。"""
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception as exc:  # noqa: BLE001
                logger.debug("关闭浏览器时出错: %s", exc)
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception as exc:  # noqa: BLE001
                logger.debug("停止 Playwright 时出错: %s", exc)
        self._browser = None
        self._playwright = None
