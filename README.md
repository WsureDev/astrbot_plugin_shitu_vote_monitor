# 师徒杯S3投票监控

AstrBot 插件，定时轮询 B 站师徒杯 S3 投票排行接口，将每次快照追加写入本地 JSONL 文件，并提供 `/rank` 指令查看实时榜单。数据格式含 Unix 时间戳，方便对接 Grafana 绘制票数变化折线图。

## 功能

- 后台每隔 N 秒（默认 30s）自动请求 B 站投票排行接口（第一页 12 条，按票数排序）
- 每次抓取结果作为一条快照追加写入 `data/astrbot_plugin_shitu_vote_monitor/vote_snapshots.jsonl`
- `/rank` 指令返回最新一次快照的前 12 名排行
- 快照含 Unix 时间戳，可直接对接 Grafana / InfluxDB 绘制票数变化折线图

## 安装

### 方法一：命令行（推荐）

```bash
cd /path/to/AstrBot/data/plugins
git clone https://github.com/WsureDev/astrbot_plugin_shitu_vote_monitor
```

然后在 AstrBot WebUI → 插件管理 → 找到插件 → **启用** → **重载**。

### 方法二：WebUI 直接安装

AstrBot WebUI → 插件管理 → 安装插件，填入：

```
https://github.com/WsureDev/astrbot_plugin_shitu_vote_monitor
```

> AstrBot 会自动执行 `pip install -r requirements.txt` 安装 `aiohttp` 依赖。

## 配置

在 AstrBot WebUI → 插件管理 → 师徒杯S3投票监控 → 配置 中填写：

| 配置项 | 说明 | 默认值 |
|---|---|---|
| `bilibili_cookie` | B 站账号 Cookie，格式：`SESSDATA=xxx; bili_jct=xxx; ...` | 空（**必填**） |
| `poll_interval` | 轮询间隔（秒），最低 10，建议不低于 30 | `30` |

> ⚠️ **`bilibili_cookie` 必须填写**，否则插件不会启动轮询任务。  
> 插件会自动从 Cookie 中提取 `bili_jct` 作为请求的 `csrf` 参数，无需手动填写。

保存配置后点击**重载插件**，日志出现 `[shitu_vote] 插件启动` 即表示成功。

## 指令

| 指令 | 说明 |
|---|---|
| `/rank` | 查看最新一次快照的前 12 名投票排行 |

示例输出：

```
🏆 师徒杯S3 实时排行 Top12 / 共27人
🕐 更新于 2025-06-19T14:00:00+08:00
────────────────────────────────
🥇 是Winter喵              37661 票
🥈 Yuko咩咩                37155 票
🥉 勾檀Mayumi              35754 票
 4. 千郁郁sen               34955 票
 5. Akishi白鸽              28232 票
...
```

## 数据存储

每次成功抓取的快照以 **JSONL**（每行一个 JSON 对象）格式追加写入：

```
data/astrbot_plugin_shitu_vote_monitor/vote_snapshots.jsonl
```

### 快照结构

```json
{
  "timestamp": 1718791200,
  "ts": "2025-06-19T14:00:00+08:00",
  "page_total": 27,
  "items": [
    {"title": "是Winter喵", "vote": 37661, "url": "https://live.bilibili.com/27887575"},
    {"title": "Yuko咩咩",   "vote": 37155, "url": "https://live.bilibili.com/31539810"}
  ]
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `timestamp` | `int`（Unix 秒） | **Grafana time field**，直接用作时间轴 |
| `ts` | `str`（ISO 8601 含时区） | 人类可读时间，方便日志排查 |
| `page_total` | `int` | 本次接口返回的总参赛人数 |
| `items[].title` | `str` | 选手名，作为 Grafana series label |
| `items[].vote` | `int` | 票数，作为 metric value |
| `items[].url` | `str` | B 站直播间链接 |

`/rank` 指令读取文件**最后一行**获取最新数据，开销极低，支持并发读取。

## 对接 Grafana

推荐两种方式：

**方案一：JSON API Datasource（最轻量）**  
写一个小 HTTP 服务，读取 `vote_snapshots.jsonl`，将每个选手的历史数据转换为 [Grafana JSON Datasource](https://grafana.github.io/grafana-json-datasource/) 格式（`timestamp` 为时间轴，`vote` 为值，`title` 为 series 名称）。

**方案二：InfluxDB / Prometheus Pushgateway**  
写旁路脚本 tail `vote_snapshots.jsonl`，将新增行展开为每个选手一条 metric 推入 InfluxDB 或 Pushgateway，再用对应 datasource 绘折线图。

## 注意事项

- **Cookie 安全**：Cookie 包含敏感登录凭据，请勿泄露，不要提交到公开仓库。
- **轮询频率**：`poll_interval` 建议不低于 30 秒，过于频繁可能触发 B 站风控或被封 IP。
- **数据积累**：`vote_snapshots.jsonl` 会随时间持续增长，如需清理可手动删除，不影响插件运行。
- **接口变动**：若 B 站修改接口结构，需相应调整 `_fetch_and_save` 中的字段解析逻辑。

## License

AGPL-3.0
