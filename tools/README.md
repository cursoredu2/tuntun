# tuntun-auto — 自动体检 + 自动生成接口

给 `ge6bu6/tuntun`（或任何同类 TVBox py 源仓库）加一套"自动维护"能力：

1. **自动体检**——把上游 `py/` 下每个爬虫真的加载起来跑一遍，**必须真能拉到片单**才算通过；
2. **自动生成接口 JSON**——只保留通过体检、且响应够快的源；
3. **自动提交**——结果写回本仓库，你用固定地址就行，永远最新。

**源文件全部来自上游 `ge6bu6/tuntun`**，本仓库只放脚本和产物，所以上游新增/删除源，
下一次体检结果会自动跟着变，**不需要手动同步 fork**。

## 目录结构

```
.github/workflows/update-sources.yml   定时任务（每天 + 手动 + tools 变更时）
tools/requirements.txt                 探测所需依赖
tools/tvbox_shim/base/spider.py        "假运行时"（模拟播放器注入的 base.spider）
tools/probe_one.py                     探测单个源（独立子进程，硬超时）
tools/probe_all.py                     拉取上游 py/ 并并发探测 -> tools/probe.json
tools/gen_sources.py                   生成 tuntun.json / tuntun-all.json / 报告
```

产物：

| 文件 | 说明 |
| --- | --- |
| `tuntun.json` | **只含体检通过且响应快的源**，手机里加这个 |
| `tuntun-all.json` | 上游全部源，不做筛选（备用） |
| `reports/latest.md` | 人看的明细表（每个源的阶段 / 条数 / 耗时 / 失败原因） |
| `tools/probe.json` | 机器读的完整探测结果（不提交，仅在 Actions artifact 里） |
| `tools/state.json` | 连续失败计数，决定什么时候淘汰 |

## 手机端怎么用

数据源填（`<你>/<fork>` 换成你的）：

```
https://ghfast.top/https://raw.githubusercontent.com/<你>/tuntun/main/tuntun.json
```

- `ghfast.top` 是 GitHub 加速前缀，国内直连 raw 不稳；不需要就删掉
- **不能填单个 `.py` 地址**——播放器的"数据源"只认 JSON 配置文件

## 判定规则

一个源要留在 `tuntun.json` 里，必须同时满足：

1. **真的能出内容**：`homeContent` 拿到分类后，再调 `categoryContent` 真的拉到片单
   （分类是静态写死的、但拉不到片单的源，会被判为 `no-listing` 淘汰）；
   只有分类、只支持搜索的源会依次回退到 `homeVideoContent` / `searchContent`
2. **响应够快**：上面那次真实调用的耗时 ≤ `--max-seconds`（默认 **1.0 秒**）
3. **不是连续失败**：连续失败次数 < `--min-failures`（默认 **2**，即失败两次才淘汰）

> 第 3 条是防误杀的：某天网络抖动不会立刻删掉一个好源。想更激进就设 1，更保守设 3。

## 调参

Actions 页面手动运行时可以填，也可以本地命令行传：

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `concurrency` / `--concurrency` | 12 | 并发探测数 |
| `timeout` / `--timeout` | 20 | 单个源的进程超时（秒），超过直接判失败 |
| `max_seconds` / `--max-seconds` | 1.0 | 响应超过这个秒数就淘汰（0 = 关闭速度检查） |
| `min_failures` / `--min-failures` | 2 | 连续失败几次才淘汰 |
| `--prefix` | `https://ghfast.top/` | 加速前缀，留空则用纯 raw 地址 |

换上游仓库 / 分支：改 workflow 顶部的 `UPSTREAM` / `UPSTREAM_REF`。

手动本地跑：

```bash
pip install -r tools/requirements.txt
# 从上游拉取并探测
GITHUB_TOKEN=... python tools/probe_all.py --fetch-upstream ge6bu6/tuntun \
    --py-dir .probe/py --out tools/probe.json --concurrency 8 --timeout 20
python tools/gen_sources.py --py-dir .probe/py --probe tools/probe.json \
    --repo ge6bu6/tuntun --ref main --out tuntun.json --max-seconds 1.0
```

国内跑不动就给探测流量挂代理（需要 `requests[socks]`，已包含）：

```bash
export TVBOX_SHIM_PROXY=socks5://127.0.0.1:1080
```

> 注意：`--fetch-upstream` 用的是标准库 urllib，它**不支持 socks 代理**。
> 本地挂 socks 时需要先给 socket 打补丁（见 README 末尾的示例）。

## 它是怎么"体检"的

播放器里的 py 源不能直接 `python xxx.py`，它们要在播放器注入的运行时里跑。所以这里做了一个最小替身
（`tools/tvbox_shim/base/spider.py`），补上爬虫真正用到的接口：

- `self.fetch(url, params=..., headers=...)` → 返回 requests 响应对象（爬虫读 `.text` / `.json()`）
- `self.getProxy(url)` / `self.getProxyUrl(url)` / `self.getCache(k)` / `self.setCache(k,v)` / `self.log(...)`

实测结论：这批爬虫基本自包含，运行时真正需要的就是上面这些。

每个源**单独跑在子进程里**并带硬超时，某个源卡死或崩掉不会拖垮整轮。

## 已知局限（重要）

- **只测"能拉到片单"，不测"能播放"**。列表通了但播放失败仍是可能的漏报
- **CI 网络 ≠ 你的手机**：机房 IP 可能被风控或地区限制，会有误杀和漏报。
  `--min-failures 2` 就是为此准备的
- 需要登录 token / JS 引擎解密 / Cloudflare 强校验 / 依赖 App 专属 `localProxy` 的源，
  在 CI 里会一直失败而被淘汰，想保留就手动把 `tools/state.json` 里对应的 `fails` 改成 `0`
- **`--max-seconds 1.0` 比较激进**：实测通过体检的源里，中位响应约 1 秒，
  放宽到 2 秒能多留约 30% 的源。想多要源就调这个值
- 探测会真实访问几十个第三方站点，一轮上百次请求

## 手动保留 / 剔除某个源

淘汰完全由 `tools/state.json` 的连续失败计数决定：

- 强行保留：把对应 `fails` 改成 `0`
- 立刻剔除：把对应 `fails` 改成 `999`

## 本地 socks 代理打补丁示例

```python
import socket, socks
socks.set_default_proxy(socks.SOCKS5, "127.0.0.1", 1080)
socket.socket = socks.socksocket
# 之后再调用 probe_all.main()
```
