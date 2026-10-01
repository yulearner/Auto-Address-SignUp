"""站点框架探测工具：判断目标站是否为 New API / One API 类框架。

探测原理：New API 框架有公开状态接口 ``GET /api/status``，
未登录访问返回 ``{"success": true, "data": {system_name, version, ...}}``——
这是比页面文本更可靠的框架判定依据。

用法：
    python probe_site.py https://example.com https://another.com

输出每站的：/api/status 判定、/api/user/self 未登录特征、/login 与首页 title。
"""

from __future__ import annotations

import json
import re
import ssl
import sys
import urllib.error
import urllib.request

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def fetch(url: str, timeout: float = 15.0) -> tuple[int | None, str]:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read(64_000).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read(64_000).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            body = ""
        return exc.code, body
    except Exception as exc:  # noqa: BLE001 - 网络/SSL 等一切错误都降级为提示
        return None, f"ERR: {exc}"


def title_of(html: str) -> str | None:
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    return m.group(1).strip()[:60] if m else None


def probe(root: str) -> None:
    root = root.rstrip("/")
    print(f"\n=== {root} ===")

    # 1) /api/status：New API 公开状态接口（最强特征）
    is_newapi = False
    status, body = fetch(f"{root}/api/status")
    if status == 200:
        try:
            data = json.loads(body)
            if isinstance(data, dict) and data.get("success"):
                d = data.get("data") or {}
                print(f"  /api/status   → 200 ✓ New API 确认 "
                      f"(system_name={d.get('system_name')!r}, version={d.get('version')!r})")
                is_newapi = True
            else:
                print("  /api/status   → 200 但结构非 New API")
        except json.JSONDecodeError:
            print("  /api/status   → 200 但非 JSON")
    else:
        note = {403: "（有 WAF 防护）", 503: "（有 WAF 防护）"}.get(status, "")
        print(f"  /api/status   → {status} {note}".rstrip())

    # 2) /api/user/self：未登录特征（New API 返回 401/JSON）
    status, _ = fetch(f"{root}/api/user/self")
    print(f"  /api/user/self→ {status}")

    # 3) 登录页
    status, body = fetch(f"{root}/login")
    print(f"  /login        → {status} title={title_of(body)!r}")

    # 4) 首页 title
    status, body = fetch(root)
    print(f"  /             → {status} title={title_of(body)!r}")

    verdict = ("New API 框架 ✓ 复用 adapter: windhub" if is_newapi
               else "未确认 New API → 先用 adapter: generic，跑通后按日志校准")
    print(f"  判定: {verdict}")


def main() -> None:
    urls = sys.argv[1:]
    if not urls:
        print(__doc__)
        print("示例: python probe_site.py https://windhub.cc https://zero.cat")
        sys.exit(2)
    for u in urls:
        probe(u)


if __name__ == "__main__":
    main()
