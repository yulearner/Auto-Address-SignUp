"""日志系统。

特性：
- 控制台 + 文件双输出
- 自定义 ``SUCCESS`` 日志级别（介于 INFO 与 WARNING 之间）
- 敏感信息脱敏：密码 / Token / Cookie / Authorization / API Key 永不进入日志
"""

from __future__ import annotations

import logging
import re
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

# ------------------------------------------------------------------
# 自定义 SUCCESS 级别
# ------------------------------------------------------------------
SUCCESS_LEVEL = 25
logging.addLevelName(SUCCESS_LEVEL, "SUCCESS")


def _install_success_method() -> None:
    """为 Logger 动态安装 success() 方法。"""

    def success(self: logging.Logger, message: str, *args: object) -> None:
        if self.isEnabledFor(SUCCESS_LEVEL):
            self._log(SUCCESS_LEVEL, message, args)

    logging.Logger.success = success  # type: ignore[attr-defined]


_install_success_method()

# ------------------------------------------------------------------
# 敏感信息脱敏
# ------------------------------------------------------------------
# 形如  password=xxx / "password": "xxx" / token: xxx 的键值，值替换为 ***
_SENSITIVE_KEY = (
    r"(?:password|passwd|pwd|secret|token|authorization|cookie|api[_-]?key"
    r"|access[_-]?key|session[_-]?id)"
)
_RE_KV = re.compile(
    rf"(['\"]?{_SENSITIVE_KEY}['\"]?\s*[:=]\s*)(['\"]?)([^'\"\s,}}\]]{{2,}})(['\"]?)",
    re.IGNORECASE,
)
# 形如 https://user:pass@host 的 URL 内嵌凭据
_RE_URL_CRED = re.compile(r"(https?://)([^/@:\s]+):([^@/\s]+)@", re.IGNORECASE)


def redact(text: str) -> str:
    """将文本中的敏感值替换为 ***。"""
    text = _RE_KV.sub(lambda m: f"{m.group(1)}{m.group(2)}***{m.group(4)}", text)
    text = _RE_URL_CRED.sub(r"\1***:***@", text)
    return text


class RedactingFilter(logging.Filter):
    """日志过滤器：写入前对消息与参数执行脱敏。"""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        if record.args:
            record.args = tuple(
                redact(a) if isinstance(a, str) else a for a in record.args
            )
        return True


_FORMATTER = logging.Formatter(
    fmt="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


def setup_logger(logs_dir: str | Path = "logs") -> None:
    """初始化根日志器：控制台(INFO+) + 文件(DEBUG+, 轮转)。"""
    logs = Path(logs_dir)
    logs.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.handlers.clear()

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(_FORMATTER)
    console.addFilter(RedactingFilter())
    root.addHandler(console)

    file_handler = RotatingFileHandler(
        logs / "auto_checkin.log",
        maxBytes=2 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(_FORMATTER)
    file_handler.addFilter(RedactingFilter())
    root.addHandler(file_handler)
