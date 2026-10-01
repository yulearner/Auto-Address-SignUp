"""Auto Checkin - 多站点自动签到系统入口。

用法::

    python main.py                                # 运行配置中的所有站点
    python main.py --config config/sites.yaml     # 指定配置文件
    python main.py --site WindHub                 # 只运行指定站点（支持模糊匹配）
    python main.py --headless                     # 无头模式运行（定时任务用，自动将 slow_mo 置 0）
    python main.py --list                         # 列出已配置站点
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# 确保 import 路径基于项目根目录
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.exceptions import ConfigError                        # noqa: E402
from core.scheduler import Scheduler                           # noqa: E402
from models.config import AppConfig, load_config               # noqa: E402
from models.result import CheckinStatus                        # noqa: E402
from notifications import ConsoleNotifier, NotificationManager  # noqa: E402
from sites.manager import SiteManager                          # noqa: E402
from storage.database import Database                          # noqa: E402
from utils.logger import setup_logger                          # noqa: E402
from utils.report import build_report                          # noqa: E402

BANNER = """
==============================
      Auto Checkin
==============================
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="多站点自动签到系统")
    parser.add_argument(
        "--config", default=str(ROOT / "config" / "sites.yaml"),
        help="站点配置文件路径（默认 config/sites.yaml）",
    )
    parser.add_argument(
        "--site", default=None,
        help="只运行指定站点（名称支持模糊匹配，区分大小写）",
    )
    parser.add_argument(
        "--headless", action="store_true",
        help="以无头模式运行浏览器",
    )
    parser.add_argument(
        "--manual-login", action="store_true",
        help="手动登录模式：打开可视化浏览器，等待你手动登录后自动保存登录态并继续签到",
    )
    parser.add_argument(
        "--list", action="store_true",
        help="列出配置中的所有站点后退出",
    )
    return parser.parse_args()


async def run(config: AppConfig, only_site: str | None) -> int:
    """执行签到主流程，返回退出码（0=全部成功，1=存在失败/人工处理）。"""
    site_manager = SiteManager()
    database = Database(ROOT / config.run.database_path)
    database.connect()

    try:
        scheduler = Scheduler(
            site_manager=site_manager,
            config=config,
            database=database,
            root_dir=ROOT,
        )
        results = await scheduler.run(only_site=only_site)
    finally:
        database.close()

    # 汇总报告
    report = build_report(results)
    notifier = NotificationManager([ConsoleNotifier()])
    await notifier.notify("今日自动签到汇总", report)

    # 存在失败或需要人工处理的站点 → 退出码 1
    bad = {
        CheckinStatus.FAILED,
        CheckinStatus.LOGIN_FAILED,
        CheckinStatus.TIMEOUT,
        CheckinStatus.CAPTCHA,
        CheckinStatus.UNKNOWN,
    }
    return 1 if any(r.status in bad for r in results) else 0


def main() -> int:
    args = parse_args()

    # Windows 控制台默认 GBK 编码，强制 UTF-8 以正确输出 emoji 报告
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass

    # 加载 .env（若存在），使环境变量中的账号信息可用
    env_path = ROOT / ".env"
    if env_path.exists():
        try:
            from dotenv import load_dotenv

            load_dotenv(env_path, override=False)
        except ImportError:
            print("警告：未安装 python-dotenv，将仅使用系统环境变量中的账号信息")

    setup_logger(ROOT / "logs")

    try:
        config = load_config(Path(args.config))
    except ConfigError as exc:
        print(f"[配置错误] {exc}", file=sys.stderr)
        return 2

    if args.headless:
        config.browser.headless = True
        # 无人值守模式：调试用的每步延迟已无意义，置 0 加速执行
        if config.browser.slow_mo:
            config.browser.slow_mo = 0

    if args.manual_login:
        config.run.manual_login = True
        # 手动登录需要充足时间，扩展单站点超时上限
        config.run.site_timeout = max(
            config.run.site_timeout, config.run.manual_login_wait + 60
        )
        if config.browser.headless:
            config.browser.headless = False
            print("提示：手动登录模式已强制使用可视化浏览器")

    if args.list:
        print(f"共 {len(config.sites)} 个启用站点：")
        for s in config.sites:
            print(f"  - {s.name} (adapter={s.adapter}, url={s.url})")
        return 0

    print(BANNER)
    print(f"共启用 {len(config.sites)} 个站点")
    try:
        return asyncio.run(run(config, only_site=args.site))
    except KeyboardInterrupt:
        print("\n用户中断执行")
        return 130


if __name__ == "__main__":
    sys.exit(main())
