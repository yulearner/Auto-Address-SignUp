"""数据模型层：统一结果结构与配置模型。"""

from models.result import CheckinResult, CheckinStatus
from models.config import AppConfig, load_config

__all__ = ["CheckinResult", "CheckinStatus", "AppConfig", "load_config"]
