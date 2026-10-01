"""自定义异常体系。

所有站点级异常均继承自 ``SiteError``，便于调度器统一捕获，保证站点之间互不影响。
"""

from __future__ import annotations


class SiteError(Exception):
    """站点处理基础异常。"""


class LoginError(SiteError):
    """登录失败（凭据错误、登录元素缺失、登录后状态检测未通过等）。"""


class CaptchaDetectedError(SiteError):
    """检测到验证码 / 人机验证，需要人工处理。

    程序不会尝试绕过或破解验证码，只会暂停并请求人工完成。
    """


class CheckinError(SiteError):
    """签到操作失败（找不到签到入口、点击无响应、服务器报错等）。"""


class SelectorNotFoundError(SiteError):
    """页面目标元素未找到，通常意味着页面结构发生了变化。"""


class ConfigError(Exception):
    """配置文件错误（缺失、格式非法、缺少必需字段等）。"""
