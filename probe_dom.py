"""DOM 探针（调试工具）：用已保存的登录态打开指定页面，输出真实 DOM 结构。

用于新站点适配：先 ``--manual-login`` 登录保存会话，再用本脚本分析
「余额 / 签到」相关元素的真实 DOM 层级与文本，据此校准选择器。

用法：
    python probe_dom.py                                  # 默认分析 WindHub 个人中心
    python probe_dom.py --site mysite --url https://xxx/user
    python probe_dom.py --balance-kw 余额 --checkin-kw 签到
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from playwright.async_api import async_playwright

# Windows 控制台默认 GBK，输出 emoji（如 🍥）会失败，统一为 UTF-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

SESSIONS_DIR = Path(__file__).parent / "sessions"
DEFAULT_URL = "https://windhub.cc/console/personal"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="DOM 探针：分析页面真实结构")
    parser.add_argument("--site", default="windhub",
                        help="session 文件名（sessions/<site>.json，默认 windhub）")
    parser.add_argument("--url", default=DEFAULT_URL, help="要分析的页面 URL")
    parser.add_argument("--balance-kw", default="余额", help="余额相关文本关键词")
    parser.add_argument("--checkin-kw", default="签到", help="签到相关文本关键词")
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    session_file = SESSIONS_DIR / f"{args.site}.json"
    if not session_file.exists():
        print(f"session 文件不存在: {session_file}")
        print(f"请先运行: python main.py --manual-login --site {args.site}")
        return
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(storage_state=str(session_file))
        page = await ctx.new_page()
        print(f"打开: {args.url}")
        await page.goto(args.url, wait_until="domcontentloaded", timeout=45_000)
        await page.wait_for_timeout(8_000)  # SPA 渲染

        info = await page.evaluate(
            """(keywords) => {
                const out = {balance: [], checkin: [], links: [], loginState: {}};
                // 1) 含关键词文本的元素祖先链
                const dump = (keyword, sink, maxHits) => {
                    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
                    let hits = 0;
                    while (walker.nextNode() && hits < maxHits) {
                        const t = walker.currentNode.textContent || '';
                        if (t.includes(keyword)) {
                            let el = walker.currentNode.parentElement;
                            const chain = [];
                            for (let i = 0; i < 4 && el; i++) {
                                chain.push({
                                    tag: el.tagName,
                                    cls: String(el.className || '').slice(0, 100),
                                    text: String(el.innerText || '').slice(0, 300),
                                });
                                el = el.parentElement;
                            }
                            sink.push(chain);
                            hits++;
                        }
                    }
                };
                dump(keywords.balance, out.balance, 2);
                dump(keywords.checkin, out.checkin, 6);
                // 2) 所有含「签」字的链接/按钮
                document.querySelectorAll('a, button').forEach(el => {
                    const t = (el.innerText || '').trim();
                    if (t && t.includes('签')) {
                        out.links.push({tag: el.tagName, text: t.slice(0, 50), href: el.href || ''});
                    }
                });
                // 3) 登录态信息（不含敏感值）
                out.loginState.url = location.href;
                out.loginState.hasUser = !!localStorage.getItem('user');
                return out;
            }""",
            {"balance": args.balance_kw, "checkin": args.checkin_kw},
        )
        print(json.dumps(info, ensure_ascii=False, indent=2))
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
