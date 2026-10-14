"""添加站点接口：一键把新站点写入 config/sites.yaml 并生成对应环境变量模板。

用法：
    python add_site.py --name MySite --url https://example.com/console/personal
    python add_site.py --name MySite --url ... [--adapter windhub] [--checkin-url URL]
                       [--login-url URL] [--disabled] [--dry-run]

流程：
  1. 探测目标站框架（GET /api/status，New API 特征），打印 system_name / version；
  2. 派生 login_url（默认「根域名 + /login」）与环境变量名（<NAME>_USERNAME / <NAME>_PASSWORD）；
  3. 以文本追加方式写入 config/sites.yaml（保留已有注释），并用 PyYAML 校验合法性；
  4. 打印后续步骤（.env 设置凭据、--manual-login 首次登录、probe_dom 校准选择器）。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import yaml

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).parent
SITES_YAML = ROOT / "config" / "sites.yaml"


def env_prefix(name: str) -> str:
    """站点名 → 环境变量前缀（仅字母数字，其余转下划线，大写）。"""
    prefix = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_").upper()
    if not prefix:
        raise SystemExit("错误：站点名必须包含字母或数字（用于生成环境变量名）")
    return prefix


def session_key(name: str) -> str:
    """与 models/config.py SiteConfig.session_key 完全一致（登录态文件名）。"""
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", name).strip("_").lower() or "site"


def root_of(url: str) -> str:
    m = re.match(r"^(https?://[^/]+)", url)
    if not m:
        raise SystemExit(f"错误：URL 格式非法: {url}")
    return m.group(1)


def detect_framework(url: str) -> tuple[bool, str | None, str | None]:
    """调用 probe_site 的探测逻辑判断是否 New API 框架。"""
    from probe_site import fetch  # 局部导入，避免脚本独立运行时报循环

    root = root_of(url)
    status, body = fetch(f"{root}/api/status", timeout=10)
    if status == 200:
        try:
            data = json.loads(body)
            if isinstance(data, dict) and data.get("success"):
                d = data.get("data") or {}
                return True, str(d.get("system_name") or ""), str(d.get("version") or "")
        except json.JSONDecodeError:
            pass
    return False, None, None


def build_block(args: argparse.Namespace, prefix: str) -> str:
    checkin_lines = ["    checkin:", "      enabled: true"]
    if args.checkin_url:
        checkin_lines.append(f'      url: "{args.checkin_url}"')
    if args.oauth:
        # OAuth 站点无账密表单：不生成 auth 块（配置层据此跳过缺失环境变量提示）
        auth_lines = ["    # OAuth 登录站点：无账密表单，首次登录请用 --manual-login"]
    else:
        auth_lines = [
            "    auth:",
            f'      username_env: "{prefix}_USERNAME"',
            f'      password_env: "{prefix}_PASSWORD"',
        ]
    lines = [
        f"  # {args.name}（{root_of(args.url)}，由 add_site.py 生成）",
        f'  - name: "{args.name}"',
        f"    adapter: {args.adapter}",
        f"    enabled: {str(not args.disabled).lower()}",
        f'    url: "{args.url}"',
        f'    login_url: "{args.login_url}"',
        *checkin_lines,
        *auth_lines,
        "",
    ]
    return "\n".join(lines)


def load_sites(path: Path) -> list[dict]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return list(data.get("sites") or [])


def main() -> None:
    parser = argparse.ArgumentParser(
        description="添加站点接口：生成配置追加到 config/sites.yaml"
    )
    parser.add_argument("--name", required=True, help="站点名称（日志/报告/会话文件标识）")
    parser.add_argument("--url", required=True, help="签到/个人中心页面 URL")
    parser.add_argument("--adapter", default="windhub",
                        help="适配器（默认 windhub，New API 同类站通用）")
    parser.add_argument("--login-url", default="", help="登录页 URL（默认 根域名+/login）")
    parser.add_argument("--checkin-url", default="",
                        help="签到页 URL（与 url 不同页时才需要）")
    parser.add_argument("--disabled", action="store_true", help="添加但暂不启用")
    parser.add_argument("--oauth", action="store_true",
                        help="OAuth 登录站点（如 ldc-shop 框架）：无账密表单，"
                             "不生成 auth 块，首次登录用 --manual-login")
    parser.add_argument("--dry-run", action="store_true", help="只打印生成的配置，不写入")
    args = parser.parse_args()

    if not SITES_YAML.exists():
        raise SystemExit("错误：config/sites.yaml 不存在，请先复制 sites.example.yaml")
    args.url = args.url.strip().rstrip("/")
    args.login_url = args.login_url.strip() or root_of(args.url) + "/login"
    prefix = env_prefix(args.name)

    # 重复检测
    sites = load_sites(SITES_YAML)
    for s in sites:
        if str(s.get("name", "")).lower() == args.name.lower():
            raise SystemExit(f"错误：站点名 '{args.name}' 已存在")
        if str(s.get("url", "")).rstrip("/") == args.url:
            raise SystemExit(f"错误：URL 已被站点 '{s.get('name')}' 使用: {args.url}")

    # 框架探测
    is_newapi, sys_name, version = detect_framework(args.url)
    if is_newapi:
        print(f"探测：New API 框架 ✓ (system_name={sys_name!r}, version={version!r})")
        if args.adapter != "windhub":
            print("提示：检测到 New API 框架，建议 adapter 用 windhub（可复用登录/签到逻辑）")
    else:
        print("探测：未能确认 New API 框架（可能非 New API / 有 WAF / 网络不可达）")
        print("提示：非 New API 站点请先跑一次观察日志，必要时用 probe_dom.py 校准 selectors")

    block = build_block(args, prefix)
    print("\n---- 生成的配置 ----")
    print(block)
    print("--------------------")

    if args.dry_run:
        print("[dry-run] 未写入文件")
        return

    # 追加写入（文本方式保留注释），并校验
    text = SITES_YAML.read_text(encoding="utf-8")
    if not text.endswith("\n"):
        text += "\n"
    SITES_YAML.write_text(text + block + "\n", encoding="utf-8")

    sites_after = load_sites(SITES_YAML)
    names = [str(s.get("name", "")) for s in sites_after]
    if args.name not in names:
        # 回滚，避免破坏配置
        SITES_YAML.write_text(text, encoding="utf-8")
        raise SystemExit("错误：写入后校验失败（新站点未出现在 sites 列表），已回滚")
    print(f"✓ 已写入 config/sites.yaml（共 {len(sites_after)} 个站点条目）")

    if args.oauth:
        print(f"""
后续步骤（OAuth 站点，无账密表单）：
  1. 首次运行（必须在浏览器中手动完成 OAuth 登录，凭据不落盘）：
       python main.py --manual-login --site {args.name}
  2. 校准选择器（登录后分析真实 DOM）：
       python probe_dom.py --site {session_key(args.name)} --url {args.url}
  3. 之后全自动签到：
       python main.py --site {args.name}
""")
    else:
        print(f"""
后续步骤：
  1. 在 .env 中设置凭据（或用 --manual-login 手动登录，无需密码落盘）：
       {prefix}_USERNAME=你的账号
       {prefix}_PASSWORD=你的密码
  2. 首次运行（推荐手动登录模式）：
       python main.py --manual-login --site {args.name}
  3. 校准选择器（登录后分析真实 DOM）：
       python probe_dom.py --site {session_key(args.name)} --url {args.url}
  4. 之后全自动签到：
       python main.py --site {args.name}
""")


if __name__ == "__main__":
    main()
