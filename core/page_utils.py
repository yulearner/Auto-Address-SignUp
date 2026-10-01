"""通用页面操作工具（各 SiteAdapter 共用）。

包含：验证码检测、稳定等待、多选择器查找、截图等。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Sequence

from playwright.async_api import Locator, Page, TimeoutError as PWTimeoutError

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# 验证码 / 人机验证检测特征
# 说明：程序检测到验证码后只会暂停并请求人工处理，
#       绝不做任何绕过、破解或规避。
# ------------------------------------------------------------------
CAPTCHA_HINTS: tuple[str, ...] = (
    'iframe[src*="captcha"]',
    'iframe[src*="recaptcha"]',
    'iframe[src*="hcaptcha"]',
    'iframe[src*="turnstile"]',
    ".geetest_panel",
    "#nc_1_wrapper",
    ".sliderCaptcha",
    '[class*="captcha"]',
    '[id*="captcha"]',
    "text=/拖动|滑动验证|请完成验证|安全验证|human verification|I am not a robot|not a robot/i",
    "text=/Checking your browser|Just a moment|Verify you are human/i",  # Cloudflare
)


async def detect_captcha(page: Page) -> str | None:
    """检测当前页面是否出现验证码 / 人机验证，返回命中的特征（未命中返回 None）。"""
    for hint in CAPTCHA_HINTS:
        try:
            loc = page.locator(hint).first
            if await loc.count() > 0 and await loc.is_visible():
                return hint
        except Exception:  # noqa: BLE001 - 特征探测失败不应中断流程
            continue
    return None


async def wait_page_ready(page: Page, nav_timeout_ms: int = 30_000) -> None:
    """等待页面基本可用：先 domcontentloaded，再尽力等待 networkidle。

    SPA 页面 networkidle 常年不达成，因此 networkidle 仅尽力而为，超时不算错误。
    """
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=nav_timeout_ms)
    except PWTimeoutError:
        logger.debug("domcontentloaded 等待超时，继续后续流程")
    try:
        await page.wait_for_load_state("networkidle", timeout=8_000)
    except PWTimeoutError:
        pass  # SPA 站点 networkidle 不达成属正常现象


async def find_first_visible(
    page: Page,
    selectors: Sequence[str],
    timeout: float = 5.0,
    poll_interval: float = 0.25,
) -> Locator | None:
    """在多个候选 selector 中查找第一个可见元素，未找到返回 None。

    采用轮询而非固定 sleep，元素出现即返回。
    """
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        for sel in selectors:
            try:
                loc = page.locator(sel)
                count = min(await loc.count(), 10)
                for i in range(count):
                    el = loc.nth(i)
                    if await el.is_visible():
                        return el
            except Exception:  # noqa: BLE001 - 单个 selector 非法时跳过
                continue
        await asyncio.sleep(poll_interval)
    return None


async def any_visible(page: Page, selectors: Sequence[str]) -> bool:
    """判断一组 selector 中是否存在可见元素。"""
    return await find_first_visible(page, selectors, timeout=1.0) is not None


async def grab_body_text(page: Page, max_chars: int = 2000) -> str:
    """抓取页面可见纯文本（截断），用于辅助状态判断与调试。"""
    try:
        text = await page.evaluate(
            """() => document.body ? document.body.innerText : ''"""
        )
        return (text or "")[:max_chars]
    except Exception:  # noqa: BLE001
        return ""


def screenshot_path(shot_dir: Path, site_key: str, state: str) -> Path:
    """生成规范截图路径：站点_时间_状态.png"""
    shot_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return shot_dir / f"{site_key}_{ts}_{state}.png"


async def take_screenshot(page: Page, path: Path) -> str | None:
    """安全截图，失败返回 None（截图失败不应影响主流程）。"""
    try:
        await page.screenshot(path=str(path), full_page=True)
        logger.info("已保存截图: %s", path)
        return str(path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("截图失败: %s", exc)
        return None
