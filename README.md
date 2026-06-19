# 师徒杯S3投票监控

AstrBot 插件，定时轮询 B 站师徒杯 S3 投票排行接口，将数据追加写入本地 JSONL 日志，并提供 `/rank` 指令查看实时榜单。

## 功能

- 后台每隔 N 秒（默认 30s）自动请求 B 站投票接口
- 将每次抓取结果追加写入 `data/astrbot_plugin_shitu_vote_monitor/vote_log.jsonl`
- `/rank` 指令返回当前最新一次抓取的前 12 名排行

## 安装

1. 将本仓库克隆 / 下载到 AstrBot 的 `data/plugins/` 目录：
   ```bash
   cd /path/to/AstrBot/data/plugins
   git clone https://github.com/WsureDev/astrbot_plugin_shitu_vote_monitor
   ```
2. 在 AstrBot WebUI 插件管理页中启用插件（或重启 AstrBot）。
3. AstrBot 会自动安装 `requirements.txt` 中的依赖。

## 配置

在 AstrBot WebUI → 插件管理 → 师徒杯S3投票监控 → 配置 中填写：

| 配置项 | 说明 | 默认值 |
|---|---|---|
| `bilibili_cookie` | B 站账号 Cookie，格式：`SESSDATA=xxx; bili_jct=xxx; ...` | 空（必填） |
| `poll_interval` | 轮询间隔（秒），建议不低于 30 | `30` |

> ⚠️ **Cookie 必须填写**，否则插件不会启动轮询任务。

## 指令

| 指令 | 说明 |
|---|---|
| `/rank` | 查看最新一次抓取的前 12 名投票排行 |

示例输出：

```
🏆 师徒杯S3 实时排行 (更新于 2025-01-01T12:00:00)
────────────────────────────
🥇 某选手A                12345 票
🥈 某选手B                11234 票
🥉 某选手C                10000 票
 4. 某选手D                 9876 票
...
```

## 数据存储

每次成功抓取的数据以 **JSONL**（每行一个 JSON 对象）格式追加写入：

```
data/astrbot_plugin_shitu_vote_monitor/vote_log.jsonl
```

每条记录结构：

```json
{"ts": "2025-01-01T12:00:00", "options": [{"idx": 1, "desc": "某选手", "cnt": 12345}, ...]}
```

- `ts`：抓取时间（ISO 8601）
- `options`：选项列表，每项含 `idx`（序号）、`desc`（名称）、`cnt`（票数）

`/rank` 指令读取文件**最后一行**，为最新数据，开销极低，支持并发读取。

## 注意事项

- **Cookie 安全**：Cookie 包含敏感的登录凭据，请勿泄露给他人，也不要将其提交到公开仓库。
- **轮询频率**：建议 `poll_interval` 不低于 30 秒，过于频繁可能触发 B 站风控或被封 IP。
- **数据积累**：`vote_log.jsonl` 会随时间持续增长，如需清理可手动删除该文件，不影响插件正常运行。
- **接口变动**：若 B 站修改接口结构，需相应调整 `_fetch_and_save` 中的字段解析逻辑。

## License

AGPL-3.0
