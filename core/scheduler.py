"""调度器：顺序处理所有站点。

核心职责：
- 站点完全隔离：每个站点独立 BrowserContext，处理完即关闭；
- 单站点超时：超过 ``site_timeout`` 秒标记 TIMEOUT，继续下一个站点；
- 单站点失败不影响其他站点（所有异常在 ``BaseSite.run`` 与此处兜底）；
- 登录态白名单保存：仅当本次运行确认登录有效（login_verified）时才写回
  session 文件，防止超时/异常中断时用未确认的浏览器状态覆盖有效登录态；
  登录失败则清理登录态。
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from core.browser import BrowserManager
from core.page_utils import screenshot_path, take_screenshot
from core.session import SessionManager
from models.config import AppConfig, SiteConfig
from models.result import CheckinResult, CheckinStatus
from sites.base import SiteRuntime
from sites.manager import SiteManager
from storage.database import Database

logger = logging.getLogger(__name__)


class Scheduler:
    """站点任务调度器。"""

    def __init__(
        self,
        site_manager: SiteManager,
        config: AppConfig,
        database: Database,
        root_dir: Path,
    ) -> None:
        self._site_manager = site_manager
        self._config = config
        self._db = database
        self._root = root_dir
        self._sessions = SessionManager(root_dir / config.run.sessions_dir)
        self._shots_dir = root_dir / config.run.screenshots_dir
        self._browser = BrowserManager(config.browser, self._sessions)

    # ------------------------------------------------------------------
    # 公共入口
    # ------------------------------------------------------------------
    async def run(self, only_site: str | None = None) -> list[CheckinResult]:
        """依次处理所有（或指定的）站点，返回结果列表。"""
        sites = self._select_sites(only_site)
        results: list[CheckinResult] = []

        await self._browser.start()
        try:
            total = len(sites)
            for index, site_cfg in enumerate(sites, 1):
                print(f"\n[{index}/{total}] {site_cfg.name}")
                result = await self._run_one(site_cfg)
                results.append(result)
                self._db.save_result(result)
        finally:
            await self._browser.close()
        return results

    def _select_sites(self, only_site: str | None) -> list[SiteConfig]:
        """按名称过滤站点（支持模糊匹配）。"""
        if not only_site:
            return list(self._config.sites)
        keyword = only_site.strip().lower()
        matched = [
            s for s in self._config.sites
            if keyword in s.name.lower() or keyword == s.adapter.lower()
        ]
        if not matched:
            names = ", ".join(s.name for s in self._config.sites)
            raise ValueError(f"未找到站点: {only_site}（可用站点: {names}）")
        return matched

    # ------------------------------------------------------------------
    # 单站点执行
    # ------------------------------------------------------------------
    async def _run_one(self, cfg: SiteConfig) -> CheckinResult:
        """处理单个站点：创建隔离上下文 → 执行 → 保存/清理登录态 → 关闭上下文。"""
        start = time.monotonic()
        context = None
        try:
            context = await self._browser.new_context(cfg)
            page = await context.new_page()
            runtime = SiteRuntime(
                page=page,
                context=context,
                sessions=self._sessions,
                run=self._config.run,
                screenshots_dir=self._shots_dir,
                headless=self._config.browser.headless,
                default_timeout_ms=cfg.browser_timeout or self._config.browser.timeout,
            )
            site = self._site_manager.create(cfg, runtime)

            timeout_s = cfg.site_timeout or self._config.run.site_timeout
            try:
                result = await asyncio.wait_for(site.run(), timeout=timeout_s)
            except asyncio.TimeoutError:
                # 单站点超时：截图 → 标记 TIMEOUT → 继续下一个站点
                logger.error("[%s] 站点执行超过 %d 秒，标记为 TIMEOUT", cfg.name, timeout_s)
                shot = None
                try:
                    shot = await take_screenshot(
                        page, screenshot_path(self._shots_dir, cfg.session_key, "timeout")
                    )
                except Exception:  # noqa: BLE001
                    pass
                result = CheckinResult(
                    site=cfg.name,
                    status=CheckinStatus.TIMEOUT,
                    message=f"站点最大执行时间 {timeout_s}s 已超时",
                    error="site timeout",
                    screenshot=shot,
                )

            # 登录态保存/清理（白名单策略，防止有效登录态被脏状态覆盖）：
            # - LOGIN_FAILED：BaseSite 已删除失效登录态，不再保存；
            # - 其余状态：仅当本次运行中已正向确认登录有效（login_verified）
            #   才允许写回。超时/验证码/未知异常中断且未确认登录时绝不覆盖
            #   原有 session 文件，下次运行可继续复用上次的有效登录态。
            if result.status != CheckinStatus.LOGIN_FAILED and site.login_verified:
                await self._browser.save_context_state(cfg, context)
            elif cfg.save_session and self._sessions.has_session(cfg.session_key):
                logger.info(
                    "[%s] 本次运行未确认登录有效，跳过登录态保存（保留原有 session 文件）",
                    cfg.name,
                )

            return result

        except Exception as exc:  # noqa: BLE001 - 配置错误/适配器缺失等兜底
            # 不让任何单站点异常影响后续站点
            logger.error("[%s] 站点处理异常: %s", cfg.name, exc)
            return CheckinResult(
                site=cfg.name,
                status=CheckinStatus.UNKNOWN,
                message="站点处理异常",
                error=str(exc),
            )
        finally:
            await self._browser.close_context(context)
            logger.debug("[%s] 站点处理完成，耗时 %.2fs", cfg.name, time.monotonic() - start)
