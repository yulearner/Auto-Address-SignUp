"""删除站点接口：从 config/sites.yaml 移除或停用站点，并清理运行时产物（与 add_site.py 对称）。

用法：
    python remove_site.py --list                        # 列出全部站点及运行时产物
    python remove_site.py --name ZeroCat               # 删除配置+登录态+截图（交互确认）
    python remove_site.py --name zerocat --yes         # 跳过确认（名称不分大小写、支持模糊）
    python remove_site.py --name ZeroCat --clean-db     # 连带删除数据库历史记录
    python remove_site.py --name ZeroCat --clean-env    # 连带删除 .env 中该站凭据行
    python remove_site.py --name ZeroCat --all         # = --clean-db + --clean-env
    python remove_site.py --name ZeroCat --dry-run     # 仅预览，不做任何修改
    python remove_site.py --name ZeroCat --disable     # 不删除，仅停用（enabled: false，可逆）
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

import yaml

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).parent
SITES_YAML = ROOT / "config" / "sites.yaml"
DOTENV = ROOT / ".env"

# 站点条目行：- name: "带空格的名称" / '单引号' / 不带引号（可含空格，行内 # 后视为注释）。
# 注意：名称可能包含空格（如 "Ark API"、"魔方 API"），不能用 [^"#\s]+ 这类排除空格的模式。
NAME_RE = re.compile(
    r"^\s*-\s*name:\s*(?:\"([^\"]*)\"|'([^']*)'|([^#]+?))\s*(?:#.*)?$"
)


def session_key(name: str) -> str:
    """与 models/config.py SiteConfig.session_key 完全一致（登录态文件名）。"""
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", name).strip("_").lower() or "site"


def load_yaml() -> dict:
    return yaml.safe_load(SITES_YAML.read_text(encoding="utf-8")) or {}


def entries(lines: list[str]) -> list[tuple[str, int]]:
    """所有站点条目 [(名称, '- name:' 所在行号)]。名称取引号内或裸值（可含空格）。"""
    result = []
    for i, l in enumerate(lines):
        m = NAME_RE.match(l)
        if m:
            name = next(g for g in m.groups() if g is not None).strip()
            result.append((name, i))
    return result


def block_start(lines: list[str], idx: int) -> int:
    """条目起始行：向上合并紧邻的普通注释（含 ---- 的区块标题注释不合并）。"""
    s = idx
    while s > 0 and re.match(r"^\s+#", lines[s - 1]) and "----" not in lines[s - 1]:
        s -= 1
    return s


def block_end(lines: list[str], idx: int) -> int:
    """条目结束行（不含）：下一个条目起始 / 区块标题注释 / 顶格行 / EOF，取最先者。"""
    end = len(lines)
    for _, j in entries(lines):
        if j > idx:
            end = min(end, block_start(lines, j))
            break
    for j in range(idx + 1, len(lines)):
        if re.match(r"^\s+#.*----", lines[j]) or (lines[j] and not lines[j][0].isspace()):
            end = min(end, j)
            break
    return end


def resolve_name(data: dict, keyword: str) -> tuple[str, dict]:
    """定位站点：先精确匹配（不分大小写），否则要求唯一模糊匹配。"""
    sites = list(data.get("sites") or [])
    for s in sites:
        if str(s.get("name", "")).lower() == keyword.strip().lower():
            return str(s.get("name")), s
    kw = keyword.strip().lower()
    matched = [s for s in sites if kw in str(s.get("name", "")).lower()]
    if len(matched) == 1:
        return str(matched[0].get("name")), matched[0]
    names = [str(s.get("name")) for s in sites]
    if not matched:
        raise SystemExit(f"错误：未找到站点 '{keyword}'（可用: {', '.join(names)}）")
    raise SystemExit(
        f"错误：'{keyword}' 匹配到多个站点: "
        f"{', '.join(str(m.get('name')) for m in matched)}，请用完整名称"
    )


def artifacts(data: dict, name: str) -> dict:
    """该站点的运行时产物路径（登录态 / 截图 / 数据库）。"""
    run = data.get("run") or {}
    # 显式 session_key 优先（与 models/config.py SiteConfig.session_key 一致）
    key = ""
    for s in data.get("sites") or []:
        if str(s.get("name", "")) == name:
            key = str(s.get("session_key") or "").strip().lower()
            break
    if not key:
        key = session_key(name)
    return {
        "session": ROOT / str(run.get("sessions_dir", "sessions")) / f"{key}.json",
        "shots": sorted(
            (ROOT / str(run.get("screenshots_dir", "screenshots"))).glob(f"{key}_*.png")
        ),
        "db": ROOT / str(run.get("database", "data/checkin.db")),
    }


def db_rows(db_path: Path, name: str) -> int:
    if not db_path.exists():
        return 0
    con = sqlite3.connect(str(db_path))
    try:
        return int(con.execute(
            "SELECT COUNT(*) FROM checkin_results WHERE site = ?", (name,)
        ).fetchone()[0])
    except sqlite3.Error:
        return 0
    finally:
        con.close()


def db_delete(db_path: Path, name: str) -> int:
    if not db_path.exists():
        return 0
    con = sqlite3.connect(str(db_path))
    try:
        cur = con.execute("DELETE FROM checkin_results WHERE site = ?", (name,))
        con.commit()
        return cur.rowcount
    finally:
        con.close()


def env_names(entry: dict) -> list[str]:
    auth = entry.get("auth") or {}
    return [str(auth[k]) for k in ("username_env", "password_env") if auth.get(k)]


def clean_envfile(names: list[str]) -> int:
    if not DOTENV.exists() or not names:
        return 0
    kept, removed = [], 0
    for line in DOTENV.read_text(encoding="utf-8").splitlines():
        if line.split("=", 1)[0].strip() in names:
            removed += 1
            continue
        kept.append(line)
    DOTENV.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
    return removed


def locate(name: str) -> tuple[list[str], int, int, str]:
    """返回 (全部行, 块起始行, 块结束行, 原文)。"""
    text = SITES_YAML.read_text(encoding="utf-8")
    lines = text.splitlines()
    target = next((i for n, i in entries(lines) if n == name), None)
    if target is None:
        raise SystemExit(f"错误：yaml 中未找到站点 '{name}'")
    return lines, block_start(lines, target), block_end(lines, target), text


def remove_block(name: str) -> None:
    """从 yaml 文本中删除站点块（保留其他注释），校验失败自动回滚。"""
    lines, s, e, text = locate(name)
    del lines[s:e]
    new = re.sub(r"\n{3,}", "\n\n", "\n".join(lines))
    SITES_YAML.write_text(new + "\n", encoding="utf-8")
    data = load_yaml()
    names = [str(x.get("name")) for x in (data.get("sites") or [])]
    if name in names:
        SITES_YAML.write_text(text, encoding="utf-8")
        raise SystemExit("错误：删除后校验失败（站点仍存在），已回滚")


def disable_block(name: str) -> bool:
    """把 enabled: true 改为 false；无 true 可改时返回 False。"""
    lines, s, e, text = locate(name)
    block = "\n".join(lines[s:e])
    new_block, cnt = re.subn(
        r"^(\s*enabled:\s*)true(\s*(?:#.*)?)$", r"\g<1>false\g<2>",
        block, count=1, flags=re.M,
    )
    if cnt == 0:
        return False
    lines[s:e] = new_block.splitlines()
    SITES_YAML.write_text("\n".join(lines) + "\n", encoding="utf-8")
    data = load_yaml()
    ent = next(
        (x for x in (data.get("sites") or []) if str(x.get("name")) == name), None
    )
    if ent is None or ent.get("enabled") is not False:
        SITES_YAML.write_text(text, encoding="utf-8")
        raise SystemExit("错误：停用后校验失败，已回滚")
    return True


def do_list() -> None:
    data = load_yaml()
    sites = list(data.get("sites") or [])
    print(f"共 {len(sites)} 个站点条目：")
    for s in sites:
        name = str(s.get("name", ""))
        a = artifacts(data, name)
        flag = "启用" if s.get("enabled", True) else "停用"
        print(f"  - {name:<12} [{flag}] {s.get('url', '')}")
        print(f"      登录态:{'有' if a['session'].exists() else '无'}"
              f" | 截图:{len(a['shots'])} | 数据库记录:{db_rows(a['db'], name)}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="删除站点接口：从 sites.yaml 移除/停用站点并清理运行时产物")
    parser.add_argument("--name", help="站点名称（不分大小写，支持唯一模糊匹配）")
    parser.add_argument("--yes", action="store_true", help="跳过交互确认")
    parser.add_argument("--dry-run", action="store_true", help="仅预览，不做任何修改")
    parser.add_argument("--clean-db", action="store_true", help="同时删除数据库历史记录")
    parser.add_argument("--clean-env", action="store_true", help="同时删除 .env 中该站凭据行")
    parser.add_argument("--all", action="store_true", help="--clean-db + --clean-env")
    parser.add_argument("--disable", action="store_true", help="仅停用（enabled: false），不删除")
    parser.add_argument("--list", action="store_true", help="列出全部站点及运行时产物")
    args = parser.parse_args()

    if args.list or not args.name:
        do_list()
        return

    data = load_yaml()
    name, entry = resolve_name(data, args.name)
    a = artifacts(data, name)
    clean_db = args.clean_db or args.all
    clean_env = args.clean_env or args.all

    if args.disable:
        if args.dry_run:
            print(f"[dry-run] 将把 {name} 的 enabled 改为 false（不删除任何文件）")
            return
        if disable_block(name):
            print(f"✓ {name} 已停用。恢复：把 yaml 中该站 enabled 改回 true")
        else:
            print(f"{name} 无 enabled: true（已停用或缺失），无需操作")
        return

    plan = ["sites.yaml 中的配置条目"]
    if a["session"].exists():
        plan.append(f"登录态 {a['session'].relative_to(ROOT)}")
    if a["shots"]:
        plan.append(f"{len(a['shots'])} 个截图（如 {a['shots'][0].name}）")
    if clean_db:
        plan.append(f"数据库记录 {db_rows(a['db'], name)} 条")
    if clean_env:
        vars_ = env_names(entry)
        plan.append(f".env 凭据行 {', '.join(vars_) if vars_ else '（未配置）'}")

    print(f"将删除站点: {name}  ({entry.get('url', '')})")
    for p in plan:
        print(f"  - {p}")
    if args.dry_run:
        print("[dry-run] 未做任何修改")
        return
    if not args.yes:
        if not sys.stdin.isatty():
            raise SystemExit("错误：非交互环境，请加 --yes 确认删除")
        if input("确认删除？[y/N] ").strip().lower() not in ("y", "yes"):
            print("已取消")
            return

    remove_block(name)
    print(f"✓ 已从 sites.yaml 删除 {name}")
    if a["session"].exists():
        a["session"].unlink()
        print(f"✓ 已删除登录态 {a['session'].name}")
    if a["shots"]:
        for shot in a["shots"]:
            shot.unlink()
        print(f"✓ 已删除 {len(a['shots'])} 个截图")
    if clean_db:
        print(f"✓ 已删除数据库记录 {db_delete(a['db'], name)} 条")
    if clean_env:
        print(f"✓ 已从 .env 移除 {clean_envfile(env_names(entry))} 行")
    remain = len(load_yaml().get("sites") or [])
    print(f"完成。剩余 {remain} 个站点条目。")


if __name__ == "__main__":
    main()


