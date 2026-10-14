"""通用签到流程。

设计原则：
- 不依赖单一按钮文案，综合使用 文本 / CSS / role / data-* 等多种定位方式；
- 点击签到后必须等待服务器响应（优先捕获签到接口响应，其次 toast/文本提示）；
- 结果分类：成功 / 今日已签到 / 失败 / 未知，由适配器映射为 ``CheckinStatus``。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from playwright.async_api import ElementHandle, Locator, Page, TimeoutError as PWTimeoutError

from core.exceptions import CaptchaDetectedError, CheckinError
from core.page_utils import detect_captcha, find_first_visible
from utils.parser import classify_checkin_message, extract_reward

logger = logging.getLogger(__name__)


@dataclass
class CheckinSelectors:
    """签到相关选择器/文案候选。"""

    # 签到按钮文本关键词（组装为 text=/关键词/ 形式选择器）
    button_keywords: list[str] = field(default_factory=list)
    # 签到按钮 CSS 候选（高优先级）
    button_css: list[str] = field(default_factory=list)
    # 签到接口响应 URL 关键词（用于捕获服务器响应，解析结果）
    response_patterns: list[str] = field(default_factory=list)
    # 结果提示元素候选（toast / notification）
    toast: list[str] = field(default_factory=list)
    # “今日已签到”页面特征候选
    already_hints: list[str] = field(default_factory=list)
    # 点击签到后可能弹出的「选择弹窗」候选（如 ldc-shop 选择签到奖励模式）。
    # 为空表示该站点无此环节，完全不检测（默认关闭，其他站点零影响）。
    dialog_selectors: list[str] = field(default_factory=list)
    # 选择弹窗内可视为“真选项”的按钮（未命中则视为取消/关闭，自动选择时跳过）
    dialog_option_filters: list[str] = field(default_factory=list)
    # 弹窗出现后等待人工选择的秒数，超时自动点第一个真选项
    dialog_wait_seconds: float = 5.0


@dataclass
class CheckinOutcome:
    """签到的原始结果，供适配器映射为最终状态。"""

    status: str  # success / already / failed / unknown
    message: str | None = None
    reward: str | None = None


def _is_checkin_response(response: object, patterns: list[str]) -> bool:
    """判断响应是否为签到接口（URL 命中关键词且为成功状态码）。"""
    try:
        url = response.url  # type: ignore[attr-defined]
        status = response.status  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        return False
    if not url or status >= 400:
        return False
    return any(p in url for p in patterns)


async def _parse_api_response(response: object) -> CheckinOutcome | None:
    """解析签到接口响应体（New API 等常见结构：{success, message, data}）。"""
    try:
        body = await response.json()  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - 非 JSON 响应交给文本兜底
        return None
    if not isinstance(body, dict):
        return None

    success_flag = body.get("success")
    # New API 等系统返回 {success: bool, message: str, data: {...}}
    if isinstance(success_flag, bool):
        message = body.get("message") or ""
        # data 内部可能包含奖励信息
        data = body.get("data")
        reward: str | None = None
        if isinstance(data, dict):
            reward = data.get("reward") or data.get("amount") or data.get("quota")
        if reward is None:
            reward = extract_reward(message)

        status = "success" if success_flag else "failed"
        if success_flag and classify_checkin_message(message) == "already":
            status = "already"
        return CheckinOutcome(
            status=status,
            message=message or None,
            reward=str(reward) if reward is not None else None,
        )

    # 其他结构：交给文本分类
    text = str(body)[:500]
    cls = classify_checkin_message(text)
    if cls:
        return CheckinOutcome(status=cls, message=text[:200], reward=extract_reward(text))
    return None


# 失效状态文案：按钮文本命中即视为不可用，避免误点击
_STALE_BUTTON_TEXTS: tuple[str, ...] = ("已签到", "明日再来", "已领取", "已完成")

# 导航类文案：按钮文本命中即视为「非签到动作按钮」——
# 它们只会打开详情 / 日历 / 规则 / 活动页，并不会真正签到，直接跳过
_NAV_BUTTON_TEXTS: tuple[str, ...] = (
    "详情", "日历", "规则", "说明", "攻略", "历史", "记录", "公告", "帮助",
)


def checkin_button_candidates(selectors: CheckinSelectors) -> list[str]:
    """按优先级生成签到按钮候选选择器列表。

    顺序：CSS 候选（精确、通常已经站点核实）→ 文本关键词（组装为 text=<kw>）。
    供上层逐个尝试，直到某一候选签到成功为止。
    """
    return [*selectors.button_css, *(f"text={kw}" for kw in selectors.button_keywords)]


async def resolve_checkin_button(
    page: Page,
    selector: str,
    timeout: float = 1.0,
) -> Locator | None:
    """把单个候选选择器解析为当前可见、可点击的签到按钮。

    每次尝试前重新解析（而非一次性缓存），避免上一次点击导致 DOM 变化后
    定位失效。同一选择器可能匹配多个元素（``text=签到`` 会子串命中
    “签到详情”“签到日历”等入口按钮），因此逐一检查并过滤：

    - 文本命中失效状态（已签到 / 明日再来 / 已领取 / 已完成）→ 跳过该元素；
    - 文本命中导航类文案（详情 / 日历 / 规则等）→ 跳过该元素（非签到按钮）；
    - 其余第一个可见元素即返回。

    全部元素不合格时返回 None，交由上层尝试下一候选。
    """
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        try:
            loc = page.locator(selector)
            count = min(await loc.count(), 10)
        except Exception:  # noqa: BLE001 - 非法选择器
            return None
        for i in range(count):
            el = loc.nth(i)
            try:
                if not await el.is_visible():
                    continue
                text = (await el.inner_text()) or ""
            except Exception:  # noqa: BLE001 - 元素可能已随 DOM 变化被移除
                continue
            if any(skip in text for skip in _STALE_BUTTON_TEXTS):
                logger.debug("跳过失效签到按钮: %s", text.strip()[:20])
                continue
            if any(nav in text for nav in _NAV_BUTTON_TEXTS):
                logger.debug("跳过非签到按钮（详情/日历等入口）: %s", text.strip()[:20])
                continue
            return el
        await asyncio.sleep(0.2)
    return None


async def _visible_toast_texts(page: Page, selectors: list[str]) -> set[str]:
    """抓取当前可见 toast 候选的文本集合，作为点击前基线。

    点击后只有「新出现」的 toast 文本才可作为签到结果依据，
    常驻的通知栏文案（点击前已存在）不可信。
    """
    texts: set[str] = set()
    for sel in selectors:
        try:
            loc = page.locator(sel)
            for i in range(min(await loc.count(), 5)):
                el = loc.nth(i)
                if await el.is_visible():
                    t = (await el.inner_text() or "").strip()
                    if t:
                        texts.add(t)
        except Exception:  # noqa: BLE001
            continue
    return texts


async def _wait_toast(page: Page, selectors: list[str], timeout_s: float = 8.0) -> str | None:
    """等待并抓取 toast 结果文本。"""
    loc = await find_first_visible(page, selectors, timeout=timeout_s)
    if loc is None:
        return None
    try:
        return (await loc.inner_text()).strip()
    except Exception:  # noqa: BLE001
        return None


# 弹窗「取消/关闭」类按钮文本：自动选择时跳过，只有用户手动点才生效
_DIALOG_CANCEL_TEXTS = ("取消", "关闭", "cancel", "close", "返回")


async def _find_checkin_dialog(
    page: Page, dialog_selectors: list[str], timeout_s: float = 5.0
) -> Locator | None:
    """点击签到按钮后，检测是否弹出「选择弹窗」（如 ldc-shop 的签到奖励模式选择）。

    配置了 dialog_selectors 才检测；返回第一个可见的弹窗 Locator，未出现返回 None。
    """
    if not dialog_selectors:
        return None
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        for sel in dialog_selectors:
            try:
                loc = page.locator(sel)
                if await loc.count() > 0 and await loc.first.is_visible():
                    return loc.first
            except Exception:  # noqa: BLE001 - 非法选择器
                continue
        await asyncio.sleep(0.2)
    return None


async def _parse_dialog_options(dialog: Locator) -> tuple[str, list[tuple[Locator, str]]]:
    """读取弹窗标题与全部可点击选项 [(按钮 Locator, 文本)]。"""
    title = ""
    try:
        title_loc = dialog.locator("[data-slot='dialog-title'], h2, [role='dialog-title']")
        if await title_loc.count() > 0:
            title = ((await title_loc.first.inner_text()) or "").strip()
    except Exception:  # noqa: BLE001
        title = ""
    options: list[tuple[Locator, str]] = []
    try:
        buttons = dialog.locator("button")
        for i in range(min(await buttons.count(), 12)):
            el = buttons.nth(i)
            try:
                text = ((await el.inner_text()) or "").strip().replace("\n", " / ")
            except Exception:  # noqa: BLE001
                continue
            if text:
                options.append((el, text))
    except Exception:  # noqa: BLE001
        pass
    return title, options


def _is_real_dialog_option(
    option_text: str, dialog_option_filters: list[str]
) -> bool:
    """判断弹窗按钮是否为“真选项”（而非取消/关闭/关闭图标）。

    配置了 dialog_option_filters 时：命中任一关键词才算真选项；
    未配置时：只要不是取消/关闭类文本即视为真选项。
    """
    low = option_text.lower()
    if any(c.lower() in low for c in _DIALOG_CANCEL_TEXTS):
        return False
    if dialog_option_filters:
        return any(f.lower() in low for f in dialog_option_filters)
    return True


async def handle_checkin_dialog(
    page: Page, selectors: CheckinSelectors, log=None
) -> str | None:
    """处理点击签到按钮后的「选择弹窗」（不绕过任何安全机制）。

    流程（对应需求：弹出选择时输出给用户，用户选择，5 秒内没选默认第一个）：
    1. 检测弹窗（未配置 dialog_selectors 的站点直接返回 None，无任何影响）；
    2. 日志输出弹窗标题 + 全部选项文本，提示用户可在浏览器中手动选择；
    3. 轮询等待用户选择（弹窗消失即视为已选，尊重用户的选择，不重复点击）；
    4. 超时未选择 → 自动点击第一个「真选项」（跳过取消/关闭类按钮）。

    :param log: 可选的日志回调（如 BaseSite.info），未提供时用模块 logger
    :return: 实际生效的选择描述（如「试试手气 (1-20 积分 随机)」），无弹窗返回 None
    """

    def _log(msg: str) -> None:
        if log is not None:
            log(msg)  # 日志回调（如 BaseSite.info，自带站点名前缀）
        else:
            logger.info(msg)

    dialog = await _find_checkin_dialog(page, selectors.dialog_selectors, timeout_s=5.0)
    if dialog is None:
        return None

    title, options = await _parse_dialog_options(dialog)
    wait_s = selectors.dialog_wait_seconds
    _log(
        f"签到需要选择：{title or '弹窗'} —— 选项: "
        + " | ".join(t for _, t in options[:6])
        + f"（请在浏览器中选择，{wait_s:g} 秒内未选择将自动选第一个）"
    )

    real_options = [
        (el, t) for el, t in options if _is_real_dialog_option(t, selectors.dialog_option_filters)
    ]

    # 等待用户手动选择：弹窗消失即视为用户已选择（无法得知具体点了哪个，
    # 不猜测，只确认“用户已手动完成选择”这一事实）
    deadline = asyncio.get_event_loop().time() + selectors.dialog_wait_seconds
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(0.5)
        try:
            if await dialog.count() == 0 or not await dialog.is_visible():
                _log("检测到弹窗已关闭，视为用户已手动完成选择，尊重用户的选择")
                return "用户已手动选择"
        except Exception:  # noqa: BLE001 - 弹窗节点被移除同样视为已选择
            _log("检测到弹窗已关闭，视为用户已手动完成选择，尊重用户的选择")
            return "用户已手动选择"

    # 超时：自动点击第一个真选项（不点取消/关闭）
    if not real_options:
        _log("弹窗内未找到可自动选择的真选项，请人工处理")
        return None
    btn, text = real_options[0]
    _log(f"未检测到手动选择，自动选择第一个选项: {text[:40]}")
    try:
        await btn.click(timeout=3000)
        return text
    except Exception as exc:  # noqa: BLE001
        logger.warning("自动点击弹窗选项失败: %s", str(exc)[:80])
        return None


async def click_and_await(
    page: Page,
    button: Locator,
    selectors: CheckinSelectors,
    response_timeout_s: float = 15.0,
    log=None,
) -> CheckinOutcome:
    """点击签到按钮并等待服务器响应/页面提示，返回原始结果。

    结果确认只信任三类「点击后产生」的信号，防止误点非签到按钮（如
    “签到详情 / 签到日历”入口、活动链接）后被页面常驻文案误导：

    1. 签到接口 HTTP 响应（配置 response_patterns 时，最强）；
    2. 点击后新出现的 toast（点击前已存在的常驻提示不算）；
    3. 按钮自身状态变化（如“立即签到”→“今日已签到”）。

    若站点配置了 dialog_selectors（如 ldc-shop 点击签到后先弹「选择签到模式」），
    点击后先处理选择弹窗（输出选项 → 等待人工选择 → 超时自动选第一个），
    再继续等待签到结果信号。

    页面常驻文本（日历面板里的“已签到 3 天”“签到成功可得 10 积分”等）
    一律不作为结果依据；点击导致页面跳转后，新页面的任何文本同样不可信
    （活动页常驻“签到成功送好礼”等宣传文案），只信接口响应。
    三条信号均未出现时返回 ``unknown``，由上层继续尝试下一个候选按钮。
    """
    if await detect_captcha(page):
        # 签到环节出现验证码：不绕过，交给上层暂停并请求人工处理
        raise CaptchaDetectedError("签到页面出现验证码，需要人工处理")

    button_text = ""
    try:
        button_text = ((await button.inner_text()) or "").strip()
    except Exception:  # noqa: BLE001
        pass
    logger.info("点击签到按钮: %s", button_text[:24] or button)

    # 点击前基线：页面 URL、当前可见 toast 文本、按钮节点句柄
    # （用于识别「点击后新增」的结果信号与按钮自身状态变化）
    url_before = page.url
    toast_baseline = await _visible_toast_texts(page, selectors.toast)
    try:
        button_handle: ElementHandle | None = await button.element_handle()
    except Exception:  # noqa: BLE001
        button_handle = None

    outcome: CheckinOutcome | None = None

    if selectors.response_patterns:
        try:
            async with page.expect_response(
                lambda r: _is_checkin_response(r, selectors.response_patterns),
                timeout=response_timeout_s * 1000,
            ) as resp_info:
                try:
                    await button.click(timeout=5000)
                except PWTimeoutError as exc:
                    # 按钮被浮层/遮罩遮挡而不可点击：转 CheckinError 上抛，
                    # 由上层按 Esc 关闭浮层后重试或尝试下一候选
                    raise CheckinError(f"按钮不可点击（可能被浮层遮挡）: {exc}") from exc
            outcome = await _parse_api_response(await resp_info.value)
        except PWTimeoutError:
            # 点击成功但未捕获签到接口响应：退化为点击后新增信号分析
            logger.debug("未捕获签到接口响应，改用页面提示判断结果")
        except CheckinError:
            raise
        except Exception as exc:  # noqa: BLE001 - 其他点击失败需上抛
            raise CheckinError(f"点击签到按钮失败: {exc}") from exc
    else:
        try:
            await button.click(timeout=5000)
        except Exception as exc:
            raise CheckinError(f"点击签到按钮失败: {exc}") from exc

    # 给弹窗 / toast / 页面跳转一点发生时间（不使用长固定等待）
    await asyncio.sleep(0.3)

    # 选择弹窗处理（仅配置了 dialog_selectors 的站点生效，如 ldc-shop 的
    # 「选择签到模式」）：输出选项 → 等待人工选择 → 超时自动选第一个。
    # 人工选择/自动选择完成后，签到请求才真正发出，随后继续走结果信号判定。
    dialog_choice: str | None = None
    if outcome is None and selectors.dialog_selectors:
        dialog_choice = await handle_checkin_dialog(page, selectors, log=log)

    if outcome is None and page.url != url_before:
        # 点击导致页面跳转（如误点活动页链接）：跳转后整页文本不可信
        logger.debug("点击后页面发生跳转，忽略页面文本类信号")
        return CheckinOutcome(status="unknown", message=None, reward=None)

    # 信号一：点击后新出现的 toast（点击前已存在的常驻提示忽略）。
    # 经过选择弹窗的签到：签到请求在选项确定后才发出，toast 等待放宽一倍
    if outcome is None and selectors.toast:
        toast_text = await _wait_toast(
            page, selectors.toast, timeout_s=8.0 if dialog_choice else 4.0
        )
        if toast_text:
            toast_line = toast_text.strip()
            if toast_line in toast_baseline:
                logger.debug("toast 文本点击前已存在，忽略: %s", toast_line[:24])
            else:
                cls = classify_checkin_message(toast_line)
                if cls:
                    outcome = CheckinOutcome(
                        status=cls,
                        message=toast_line,
                        reward=extract_reward(toast_line),
                    )

    # 信号二：按钮自身状态变化（点击后按钮文本变为“已签到 / 明日再来”等；
    # 通过节点句柄读取，避免 React 重渲染导致按旧文本定位失败）
    if outcome is None and button_handle is not None:
        try:
            new_text = ((await button_handle.inner_text()) or "").strip()
        except Exception:  # noqa: BLE001 - 按钮可能已被重渲染移除
            new_text = ""
        if new_text and new_text != button_text:
            cls = classify_checkin_message(new_text)
            if cls:
                outcome = CheckinOutcome(
                    status=cls,
                    message=new_text,
                    reward=extract_reward(new_text) if cls == "success" else None,
                )

    # 无任何可确认信号：返回 unknown，由上层继续尝试下一候选
    if outcome is None:
        outcome = CheckinOutcome(status="unknown", message=None, reward=None)

    return outcome

