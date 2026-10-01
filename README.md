# Auto Checkin - 多站点自动签到系统

基于 **Python 3.10+ / Playwright / asyncio** 的多站点自动签到框架。
依次访问配置的网站，自动检测登录态、登录、执行签到、解析奖励，最后生成汇总报告。

```
==============================
      Auto Checkin
==============================
共启用 1 个站点

[1/1] WindHub
2026-09-28 08:20:02 [INFO] [WindHub] 打开网站: https://windhub.cc/console/personal
...
============================== 今日自动签到汇总 ==============================
📅 今日自动签到汇总
...
```

## 特性

- **模块化设计**：`BrowserManager` / `SessionManager` / `SiteManager` / `Scheduler` / `BaseSite` 各司其职
- **登录态持久化**：Playwright `storage_state` 保存 Cookie + localStorage，有效期内免登录
- **站点完全隔离**：每个站点独立 `BrowserContext`，互不串扰；单站点失败不影响其他站点
- **单站点超时控制**：默认 60 秒，超时标记 `TIMEOUT` 后继续下一个站点
- **验证码零绕过**：检测到验证码只截图 + 暂停请求人工处理，绝不破解
- **敏感信息保护**：密码只从环境变量读取；日志自动脱敏（password/token/cookie 等值替换为 `***`）
- **异常自动截图**：`screenshots/windhub_20260928_082001_error.png` 形式命名
- **统一结果结构**：`CheckinResult` + SQLite 历史记录
- **可扩展通知**：`NotificationManager` + `ConsoleNotifier`，后续可加 Telegram/企业微信/邮件

## 项目结构

```
auto_checkin/
├── main.py                  # 入口（CLI 参数解析）
├── requirements.txt
├── .env.example             # 环境变量示例（复制为 .env 使用）
├── config/
│   └── sites.example.yaml   # 站点配置示例（复制为 sites.yaml 使用）
├── core/
│   ├── browser.py           # BrowserManager：浏览器与 Context 生命周期
│   ├── session.py           # SessionManager：登录态保存/读取/清理
│   ├── login.py             # 通用表单登录流程
│   ├── checkin.py           # 通用签到流程（找按钮→点击→等响应→解析）
│   ├── page_utils.py        # 验证码检测、多选择器查找、截图等
│   ├── scheduler.py         # Scheduler：顺序调度 + 站点隔离 + 超时控制
│   └── exceptions.py        # 异常体系（SiteError/LoginError/CaptchaDetectedError…）
├── sites/
│   ├── base.py              # BaseSite 模板方法（run 统一编排）
│   ├── manager.py           # SiteManager：适配器注册表
│   └── windhub.py           # WindHubSite（New API 框架站点适配器）
├── models/
│   ├── config.py            # 配置模型（yaml + 环境变量解析）
│   └── result.py            # CheckinResult / CheckinStatus
├── notifications/           # 通知接口 + Console 实现
├── storage/
│   └── database.py          # SQLite 结果存储
├── utils/
│   ├── logger.py            # 日志（含敏感信息脱敏、SUCCESS 级别）
│   ├── parser.py            # 奖励/状态文本解析
│   └── report.py            # 汇总报告生成
├── sessions/                # 登录态文件（运行时生成，git 忽略）
├── screenshots/             # 异常截图（运行时生成，git 忽略）
├── logs/                    # 运行日志（运行时生成，git 忽略）
└── data/                    # SQLite 数据库（运行时生成，git 忽略）
```

## 安装

```bash
cd auto_checkin

# 1. 安装依赖（建议 Python 3.10+ 虚拟环境）
pip install -r requirements.txt

# 2. 安装 Playwright 浏览器（Chromium）
python -m playwright install chromium
```

## 配置

### 1. 准备账号环境变量

```bash
cp .env.example .env
# 编辑 .env，填入 WINDHUB_USERNAME / WINDHUB_PASSWORD
```

### 2. 准备站点配置

```bash
cp config/sites.example.yaml config/sites.yaml
# 按需增删站点（accounts 始终来自环境变量，不写入 yaml）
```

## 运行

```bash
python main.py                # 运行所有启用站点
python main.py --site WindHub # 只运行指定站点（名称模糊匹配）
python main.py --headless      # 无头模式
python main.py --manual-login  # 手动登录模式（见下方）
python main.py --list          # 列出已配置站点
python main.py --config config/sites.yaml   # 指定配置文件
```

退出码：`0` 全部成功/已签到；`1` 存在失败或需人工处理；`2` 配置错误。

### 手动登录模式（首次使用推荐）

无需把密码写入 `.env`：程序打开可视化浏览器并停在登录页，你在浏览器里手动登录，
程序自动检测登录成功后继续签到，并把登录态保存到 `sessions/windhub.json`；
之后每次运行都会自动复用该登录态，实现「登录一次，长期免登录」。

```bash
python main.py --manual-login [--site WindHub]
```

登录态失效（被踢下线 / 过期）时重新跑一次 `--manual-login` 即可。

## 工作机制

### 单站点流程（BaseSite.run 模板方法）

```
打开站点 → 验证码检测 → 登录态检测
  ├─ 已登录（DOM/URL/localStorage/用户接口 多重判断）→ 直接签到
  └─ 未登录 → 表单登录（填账号→填密码→点登录→等待跳转/错误提示）→ 复检登录态 → 签到
签到：只信任「点击后产生」的信号 —— 签到接口响应（New API 风格 {success, message}，最强）
      → 点击后新出现的 toast → 按钮自身状态变化（如“立即签到”→“今日已签到”）
      均未出现视为未确认，自动继续尝试下一候选按钮（优先级：CSS → 文本关键词），
      防止误点“签到详情/日历/活动链接”等伪按钮后被页面常驻文案误导提前终止
结果映射：success / already_checked / failed / login_failed / timeout / captcha / unknown
```

### 验证码处理原则

程序检测到 CAPTCHA / 滑块 / 人机验证时：

1. 自动截图保存；
2. 日志输出 `[需要人工处理] 站点: xxx 原因: 检测到验证码`；
3. 若配置 `run.captcha_wait > 0` 且为可视化模式，暂停等待人工在浏览器中完成验证后继续；
4. **绝不尝试绕过、破解或规避任何验证机制**。

## 安全原则

- 账号密码只从环境变量读取，源代码 / yaml / 日志中不存在明文凭据；
- 日志过滤器自动脱敏 `password / token / cookie / authorization / api key` 等键值；
- `sessions/*.json`（含 Cookie）与 `.env` 已加入 `.gitignore`，禁止提交；
- 登录失败自动清理本地登录态，避免失效凭据反复使用。

## ⚠️ 需要你提供的信息（选择器核实）

WindHub 为 **New API 框架** 的 React SPA 站点，登录后才渲染完整 DOM。
2026-09-29 已通过 `probe_dom.py`（DOM 探针调试工具）核实大部分选择器：

| 元素 | 已核实结构 |
|---|---|
| 登录态检测 | `/api/user/self` 接口返回 `success=true` + localStorage `user` 键 |
| 登录方式 | 登录页存在「账号登录 / 邮箱登录」二选一 Tab，`login_tab` 选择器先切到邮箱登录 |
| 签到卡片 | 位于个人中心 `/console/personal`，已签到时按钮 disabled 文案「今日已签到」 |
| 余额 badge | `span.semi-badge` 文本形如「🍥27.05\n当前余额」（站点自定义 emoji 货币符号） |
| 今日已签判定 | 「今日已签到，累计签到 N 天」状态文案 |

**仍待下次真实签到时确认**（不影响今日已签到判定）：

1. **未签到状态下签到按钮的可点击文案**（预计「签到 / 立即签到」类；
   现有候选 `button:not(.semi-button-disabled):has-text("签到")`）；
2. **签到 XHR 接口地址**（浏览器 F12 → Network → 点击签到 → 找到 XHR 请求 URL；
   默认匹配关键词：`check_in / checkin / sign_in / qiandao`）；
3. **签到成功提示文案**（用于奖励解析，默认解析 toast 与接口响应）。

调试技巧：`python probe_dom.py` 可用已保存的登录态打开个人中心，
输出「余额 / 签到」相关元素的真实 DOM 层级与文本（新站点适配时同样适用）。

拿到以上信息后，直接修改 `config/sites.yaml` 对应站点的 `selectors` 字段即可，
无需改动代码。

## 新增站点

### 方式一：add_site.py 添加接口（推荐，New API 同类站点）

```bash
# 基本用法（自动探测框架、派生 login_url、生成环境变量模板、写入 sites.yaml）
python add_site.py --name MySite --url https://example.com/console/personal

# 完整参数
python add_site.py --name MySite --url <个人中心页> \
    --adapter windhub \        # 适配器，New API 站默认 windhub
    --login-url <登录页> \      # 缺省为 根域名 + /login
    --checkin-url <签到页> \    # 签到入口与个人中心不同页时才需要
    --disabled \                # 添加但暂不启用
    --dry-run                   # 只预览生成的配置，不写入
```

脚本会自动：① `GET /api/status` 探测是否 New API 框架；② 检查站点名/URL 重复；
③ 追加写入 `config/sites.yaml` 并做 YAML 合法性校验（失败自动回滚）；
④ 打印 `.env` 变量名与首次登录命令。

添加后的步骤：
1. 在 `.env` 中填入 `<NAME>_USERNAME` / `<NAME>_PASSWORD`（或用 `--manual-login` 手动登录）；
2. 首次运行 `python main.py --manual-login --site <名称>` 保存登录态；
3. 若选择器不匹配，用 `python probe_dom.py --site <session_key> --url <页面>` 分析真实 DOM，
   按需在 yaml 覆盖 `selectors`（`session_key` = 站点名小写、非法字符转 `_`，如 `ZeroCat → zerocat`）。

> New API 框架识别特征：`GET /api/status` 返回 `{"success": true, "data": {system_name, ...}}`。
> 可先运行 `python probe_site.py https://a.com https://b.com` 批量探测未知站点。

### 方式二：手动添加 / 结构特殊的站点

1. 在 `config/sites.yaml` 手写站点条目（可参照 `config/sites.example.yaml`）；
2. 结构特殊的站点：在 `sites/` 新建适配器，继承 `BaseSite`，按需覆盖：

```python
# sites/example.py
from sites.base import BaseSite

class ExampleSite(BaseSite):
    ADAPTER_NAME = "example"

    async def is_logged_in(self): ...
    async def login(self): ...
    async def checkin(self): ...
```

3. 在 `sites/manager.py` 的 `SiteManager.__init__` 中注册：`self.register(ExampleSite)`。

### 删除 / 停用站点（remove_site.py）

```bash
python remove_site.py --list                      # 查看全部站点及登录态/截图/数据库情况
python remove_site.py --name ZeroCat              # 删除该站配置+登录态+截图（交互确认）
python remove_site.py --name zerocat --all --yes  # 连带清理数据库历史与 .env 凭据
python remove_site.py --name ZeroCat --disable    # 仅停用不删除（可逆）
```

- 默认删除：`sites.yaml` 配置条目 + `sessions/<key>.json` + `screenshots/<key>_*.png`；
- `--clean-db` 额外删除数据库历史记录，`--clean-env` 额外删除 `.env` 凭据行，`--all` 两者都删；
- `--dry-run` 仅预览；非交互环境（CI/管道）需加 `--yes`；yaml 修改后自动校验，失败回滚；
- `--disable` 只把 `enabled` 改为 `false`（临时下线，改回 `true` 即恢复）。

## 常见问题

| 现象 | 处理 |
|---|---|
| `未找到用户名输入框` | 核实登录页 placeholder，更新 yaml `selectors.username` |
| `所有签到按钮候选均未成功` | 候选按钮已按优先级逐个尝试（CSS → 关键词）；核实个人中心 DOM，更新 `selectors.checkin_css / checkin_keywords` |
| `登录失败：xxx` | 检查环境变量是否正确；查看截图 `screenshots/*_login_failed.png` |
| 反复要求登录 | 删除 `sessions/windhub.json` 后重试；确认勾选"记住我" |
| 超时 | 调大 `run.site_timeout` / `browser.timeout`；检查网络代理 |

## Roadmap

- [ ] Telegram / 企业微信 / 邮件通知渠道
- [ ] 连续签到天数、积分趋势统计报表
- [ ] 更多站点适配器（百度贴吧、Discourse 论坛等）

## 免责声明

本项目仅用于个人账号的每日签到自动化，请遵守目标站点的服务条款与频率限制，
勿用于任何违规用途。程序不会绕过任何安全验证机制。

