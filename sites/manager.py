"""站点管理器：维护适配器注册表并按配置实例化站点。"""

from __future__ import annotations

import logging
from typing import Type

from core.exceptions import ConfigError
from models.config import SiteConfig
from sites.base import BaseSite, SiteRuntime
from sites.windhub import WindHubSite

logger = logging.getLogger(__name__)


class SiteManager:
    """适配器注册表与工厂。"""

    def __init__(self) -> None:
        self._adapters: dict[str, Type[BaseSite]] = {}
        # 内置适配器注册（新增站点在此登记即可）
        self.register(WindHubSite)

    def register(self, adapter_cls: Type[BaseSite]) -> None:
        """注册站点适配器类。"""
        name = getattr(adapter_cls, "ADAPTER_NAME", None)
        if not name:
            raise ConfigError(f"适配器 {adapter_cls.__name__} 缺少 ADAPTER_NAME 属性")
        self._adapters[name] = adapter_cls

    @property
    def available(self) -> list[str]:
        return sorted(self._adapters.keys())

    def create(self, config: SiteConfig, runtime: SiteRuntime) -> BaseSite:
        """根据站点配置中的 adapter 名称实例化站点适配器。"""
        adapter_cls = self._adapters.get(config.adapter)
        if adapter_cls is None:
            raise ConfigError(
                f"站点 {config.name} 的 adapter '{config.adapter}' 不存在，"
                f"当前可用: {', '.join(self.available)}"
            )
        logger.debug("[%s] 使用适配器: %s", config.name, config.adapter)
        return adapter_cls(config, runtime)
