import asyncio
import json
from datetime import datetime
from pathlib import Path

import aiohttp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register

# 投票接口地址（固定）
VOTE_URL = "https://api.bilibili.com/x/activity/vote/info?vote_id=1372306"

# 数据存储目录和日志文件路径（存放在 data/ 下，插件卸载重装不丢失）
DATA_DIR = Path("data") / "astrbot_plugin_shitu_vote_monitor"
LOG_FILE = DATA_DIR / "vote_log.jsonl"


@register(
    "astrbot_plugin_shitu_vote_monitor",
    "WsureDev",
    "定时轮询 B 站师徒杯 S3 投票排行数据，支持 /rank 查看实时榜单。",
    "0.1.0",
)
class ShituVoteMonitor(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)

        # 从 WebUI 配置读取参数
        self.cookie: str = config.get("bilibili_cookie", "").strip()
        raw_interval = config.get("poll_interval", 30)
        # 最小 10 秒，防止频率过高被封
        self.poll_interval: int = max(10, int(raw_interval))

        # 确保数据目录存在
        DATA_DIR.mkdir(parents=True, exist_ok=True)

        # 后台轮询任务句柄
        self._task: asyncio.Task | None = None

    async def initialize(self):
        """插件初始化：启动后台轮询任务。"""
        if not self.cookie:
            logger.warning(
                "[shitu_vote] 未配置 bilibili_cookie，已跳过轮询。"
                "请在 WebUI -> 插件管理 -> 师徒杯S3投票监控 -> 配置 中填写 Cookie。"
            )
            return

        logger.info(
            f"[shitu_vote] 插件启动，轮询间隔 {self.poll_interval}s，数据写入 {LOG_FILE}"
        )
        self._task = asyncio.create_task(self._poll_loop())

    # ------------------------------------------------------------------ #
    #  后台轮询                                                             #
    # ------------------------------------------------------------------ #

    async def _poll_loop(self):
        """无限循环：抓取 -> 保存 -> 等待。"""
        while True:
            try:
                await self._fetch_and_save()
            except asyncio.CancelledError:
                # 插件被卸载，正常退出
                break
            except Exception as e:
                logger.error(f"[shitu_vote] 轮询出错: {e}")
            await asyncio.sleep(self.poll_interval)

    async def _fetch_and_save(self):
        """请求 B 站接口，提取投票数据并追加写入 JSONL 日志文件。"""
        headers = {
            "Cookie": self.cookie,
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Referer": "https://www.bilibili.com/",
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(VOTE_URL, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                resp.raise_for_status()
                payload = await resp.json(content_type=None)

        # 提取选项列表，结构: data.info.options
        options_raw: list = payload.get("data", {}).get("info", {}).get("options", [])
        if not options_raw:
            logger.warning(f"[shitu_vote] 接口返回数据为空或结构异常: {payload.get('code')} {payload.get('message')}")
            return

        # 只保留关键字段
        options = [
            {"idx": o["idx"], "desc": o["desc"], "cnt": o["cnt"]}
            for o in options_raw
        ]

        record = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "options": options,
        }

        # 追加写入 JSONL（一行一条记录，便于并发读取最后一行）
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

        logger.info(f"[shitu_vote] 写入 {len(options)} 条选项, ts={record['ts']}")

    # ------------------------------------------------------------------ #
    #  读取最新数据                                                          #
    # ------------------------------------------------------------------ #

    async def _read_latest(self) -> dict | None:
        """从 JSONL 日志读取最后一条有效记录。"""
        if not LOG_FILE.exists():
            return None

        last_line = ""
        with LOG_FILE.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    last_line = line

        if not last_line:
            return None

        try:
            return json.loads(last_line)
        except json.JSONDecodeError as e:
            logger.error(f"[shitu_vote] 解析最新记录失败: {e}")
            return None

    # ------------------------------------------------------------------ #
    #  指令处理                                                             #
    # ------------------------------------------------------------------ #

    @filter.command("rank")
    async def rank(self, event: AstrMessageEvent):
        """查看师徒杯S3实时投票排行（前12名）"""
        record = await self._read_latest()

        if record is None:
            yield event.plain_result(
                "暂无数据，请等待首次轮询完成（或检查 Cookie 配置）。"
            )
            return

        # 按票数降序，取前 12 条
        options = sorted(record["options"], key=lambda x: x["cnt"], reverse=True)[:12]
        ts = record.get("ts", "未知")

        lines = [
            f"🏆 师徒杯S3 实时排行 (更新于 {ts})",
            "─" * 28,
        ]
        medals = ["🥇", "🥈", "🥉"]
        for i, opt in enumerate(options, start=1):
            prefix = medals[i - 1] if i <= 3 else f"{i:2d}."
            name = opt['desc'][:16]  # 防止名字过长撑爆排版
            cnt = opt['cnt']
            lines.append(f"{prefix} {name:<18} {cnt:>8} 票")

        yield event.plain_result("\n".join(lines))

    # ------------------------------------------------------------------ #
    #  生命周期                                                             #
    # ------------------------------------------------------------------ #

    async def terminate(self):
        """插件卸载时取消后台任务。"""
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("[shitu_vote] 插件已停止。")
