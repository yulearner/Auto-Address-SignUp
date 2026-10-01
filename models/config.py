"""配置模型：读取 sites.yaml 并解析环境变量，生成强类型配置对象。

设计要点：
- 账号密码只通过 ``*_env`` 指定的环境变量读取，绝不硬编码；
- 配置文件中不出现任何明文密码；
- 解析失败时抛出 ``ConfigError``，给出清晰可操作的提示。
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from core.exceptions import ConfigError

logger = logging.getLogger(__name__)


__all__ = [
    "ConfigError",
    "BrowserSettings",
    "RunSettings",
    "SiteConfig",
    "AppConfig",
    "load_config",
]


@dataclass
class BrowserSettings:
    """浏览器全局设置。"""

    headless: bool = False
    timeout: int = 30_000          # 页面默认超时（毫秒）
    slow_mo: int = 0               # 每步操作延迟（毫秒），便于调试
    user_agent: str = ""           # 留空使用默认 UA
    viewport_width: int = 1280
    viewport_height: int = 800


@dataclass
class RunSettings:
    """运行时全局设置。"""

    site_timeout: int = 60         # 单个站点最大执行时间（秒）
    captcha_wait: int = 0          # 验证码出现时等待人工处理的秒数（0=不等待）
    manual_login: bool = False     # 手动登录模式：打开浏览器等待人工登录
    manual_login_wait: int = 300   # 手动登录最长等待时间（秒）
    save_session: bool = True      # 是否保存登录态
    database_path: str = "data/checkin.db"
    sessions_dir: str = "sessions"
    screenshots_dir: str = "screenshots"


@dataclass
class SiteConfig:
    """单个站点的配置。"""

    name: str
    adapter: str = "generic"
    enabled: bool = True
    url: str = ""
    login_url: str = ""
    # 签到页面 URL（与个人中心不同页时指定；为空则直接在 url 页面签到）
    checkin_url: str = ""
    username: str | None = None
    password: str | None = None
    checkin_enabled: bool = True
    logout_after: bool = False
    save_session: bool = True
    # 覆盖全局超时；None 表示沿用全局设置
    site_timeout: int | None = None
    browser_timeout: int | None = None
    # 站点特有选择器与选项（原始字典透传，由适配器自行解释）
    selectors: dict[str, Any] = field(default_factory=dict)

    @property
    def session_key(self) -> str:
        """登录态文件的键名（取站点名的小写安全形式）。"""
        return re.sub(r"[^a-zA-Z0-9_-]+", "_", self.name).strip("_").lower() or "site"


@dataclass
class AppConfig:
    """应用顶层配置。"""

    browser: BrowserSettings
    run: RunSettings
    sites: list[SiteConfig]


def _site_key(site_name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", site_name).strip("_").lower()


def _parse_browser(raw: dict[str, Any]) -> BrowserSettings:
    b = raw or {}
    return BrowserSettings(
        headless=bool(b.get("headless", False)),
        timeout=int(b.get("timeout", 30_000)),
        slow_mo=int(b.get("slow_mo", 0)),
        user_agent=str(b.get("user_agent", "") or ""),
        viewport_width=int((b.get("viewport") or {}).get("width", 1280)),
        viewport_height=int((b.get("viewport") or {}).get("height", 800)),
    )


def _parse_run(raw: dict[str, Any]) -> RunSettings:
    r = raw or {}
    return RunSettings(
        site_timeout=int(r.get("site_timeout", 60)),
        captcha_wait=int(r.get("captcha_wait", 0)),
        manual_login=bool(r.get("manual_login", False)),
        manual_login_wait=int(r.get("manual_login_wait", 300)),
        save_session=bool(r.get("save_session", True)),
        database_path=str(r.get("database", "data/checkin.db")),
        sessions_dir=str(r.get("sessions_dir", "sessions")),
        screenshots_dir=str(r.get("screenshots_dir", "screenshots")),
    )


def _parse_site(index: int, raw: dict[str, Any]) -> SiteConfig:
    if not isinstance(raw, dict):
        raise ConfigError(f"第 {index + 1} 个站点配置格式错误，应为字典")

    name = str(raw.get("name") or "").strip()
    if not name:
        raise ConfigError(f"第 {index + 1} 个站点缺少 name 字段")

    url = str(raw.get("url") or "").strip()
    if not url:
        raise ConfigError(f"站点 {name} 缺少 url 字段")

    adapter = str(raw.get("adapter") or "").strip() or _site_key(name)

    # ---- 账号信息只从环境变量读取 ----
    auth = raw.get("auth") or {}
    username_env = str(auth.get("username_env") or "").strip()
    password_env = str(auth.get("password_env") or "").strip()

    username = os.environ.get(username_env) if username_env else None
    password = os.environ.get(password_env) if password_env else None

    if not username or not password:
        # 不输出任何账号值，仅提示变量名
        missing = [v for v, ok in (
            (username_env, bool(username)), (password_env, bool(password))
        ) if v and not ok]
        extra = f"（缺失环境变量：{', '.join(missing)}）" if missing else ""
        logger.warning(
            "[%s] 未提供账号信息%s，将在登录阶段直接失败", name, extra
        )

    checkin_raw = raw.get("checkin") or {}
    login_url = str(raw.get("login_url") or "").strip()
    if not login_url:
        # 未显式指定登录页时，默认使用站点根路径下的 /login
        prefix = url.rstrip("/")
        login_url = prefix + "/login"

    # 签到页面 URL（checkin.url）；为空则直接在 url 页面执行签到
    checkin_url = str((checkin_raw or {}).get("url") or "").strip()

    return SiteConfig(
        name=name,
        adapter=adapter,
        enabled=bool(raw.get("enabled", True)),
        url=url,
        login_url=login_url,
        checkin_url=checkin_url,
        username=username,
        password=password,
        checkin_enabled=bool((checkin_raw or {}).get("enabled", True)),
        logout_after=bool(raw.get("logout_after", False)),
        save_session=bool(raw.get("save_session", True)),
        site_timeout=int(raw["site_timeout"]) if raw.get("site_timeout") else None,
        browser_timeout=int(raw["browser_timeout"]) if raw.get("browser_timeout") else None,
        selectors=dict(raw.get("selectors") or {}),
    )


def load_config(path: Path) -> AppConfig:
    """读取 YAML 配置并构造 ``AppConfig``。

    :param path: 配置文件路径，例如 config/sites.yaml
    :raises ConfigError: 配置文件不存在或格式非法时抛出
    """
    if not path.exists():
        raise ConfigError(
            f"配置文件不存在: {path}\n"
            f"请复制 config/sites.example.yaml 为 config/sites.yaml 后编辑"
        )

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"配置文件 YAML 解析失败: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError("配置文件顶层结构必须是字典")

    sites_raw = raw.get("sites")
    if not sites_raw:
        raise ConfigError("配置文件中未找到任何站点（sites 列表为空）")

    sites = [_parse_site(i, s) for i, s in enumerate(sites_raw)]
    enabled = [s for s in sites if s.enabled]
    if not enabled:
        raise ConfigError("没有处于启用状态（enabled: true）的站点")

    return AppConfig(
        browser=_parse_browser(raw.get("browser") or {}),
        run=_parse_run(raw.get("run") or {}),
        sites=enabled,
    )
