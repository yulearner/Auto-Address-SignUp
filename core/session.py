"""会话（登录态）管理器。

基于 Playwright 的 ``storage_state``（Cookie + localStorage）持久化登录态。

⚠️ 安全要求：
- session 文件属于敏感信息，禁止输出到日志；
- 禁止提交 Git（已由 .gitignore 忽略）；
- 文件内容不做任何明文打印。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class SessionManager:
    """管理各站点的登录态文件。"""

    def __init__(self, sessions_dir: str | Path = "sessions") -> None:
        self._dir = Path(sessions_dir)

    def _state_path(self, site_key: str) -> Path:
        return self._dir / f"{site_key}.json"

    def has_session(self, site_key: str) -> bool:
        """是否存在可用的 session 文件。"""
        p = self._state_path(site_key)
        return p.exists() and p.stat().st_size > 0

    def load(self, site_key: str) -> dict | None:
        """读取 storage_state；文件不存在或损坏时返回 None（内容永不入日志）。"""
        p = self._state_path(site_key)
        if not p.exists():
            return None
        try:
            state = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("[%s] session 文件不可读，将重新登录: %s", site_key, exc)
            return None
        if not isinstance(state, dict) or "cookies" not in state or "origins" not in state:
            logger.warning("[%s] session 文件格式无效，将重新登录", site_key)
            return None
        return state

    def save(self, site_key: str, state: dict) -> None:
        """保存 storage_state（Cookie/localStorage 内容禁止入日志）。"""
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            tmp = self._state_path(site_key).with_suffix(".tmp")
            tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self._state_path(site_key))
        except OSError as exc:
            logger.warning("[%s] session 保存失败: %s", site_key, exc)

    def delete(self, site_key: str) -> None:
        """删除 session（登出后或登录态失效时调用）。"""
        try:
            self._state_path(site_key).unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("[%s] session 删除失败: %s", site_key, exc)
