import asyncio
import json
import re
from datetime import datetime
from pathlib import Path

import aiohttp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register

# 投票排行接口（固定参数，第一页12条，按票数排序）
_VOTE_ID = "23ERA1wloghvxay00"
_GROUP_ID = "24ERA1wloghvtc600"
VOTE_URL = (
    "https://api.bilibili.com/x/activity_components/vote_new/rank"
    f"?group_id={_GROUP_ID}&pn=1&ps=12&random_version=&type=2&vote_id={_VOTE_ID}"
    "&web_location=888.148305"
)

# 数据存储目录和日志文件路径（存放在 data/ 下，插件卸载重装不丢失）
DATA_DIR = Path("data") / "astrbot_plugin_shitu_vote_monitor"
LOG_FILE = DATA_DIR / "vote_log.jsonl"


def _extract_csrf(cookie: str) -> str:
    """从 Cookie 字符串中提取 bili_jct 作为 csrf。"""
    m = re.search(r"bili_jct=([^;]+)", cookie)
    return m.group(1).strip() if m else ""


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
        """请求 B 站排行接口，提取投票数据并追加写入 JSONL 日志文件。"""
        csrf = _extract_csrf(self.cookie)
        # csrf 拼到 URL（GET 参数）
        url = VOTE_URL + (f"&csrf={csrf}" if csrf else "")

        headers = {
            "Cookie": self.cookie,
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/127.0.0.0 Safari/537.36"
            ),
            # 与抓包 Referer 保持一致
            "Referer": "https://live.bilibili.com/blackboard/era/eXYVPfN7lWHVt7vY.html",
            "Origin": "https://live.bilibili.com",
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                resp.raise_for_status()
                payload = await resp.json(content_type=None)

        code = payload.get("code", -1)
        if code != 0:
            logger.warning(
                f"[shitu_vote] 接口返回错误: code={code} message={payload.get('message')}"
            )
            return

        # 数据结构: data.items[]
        # 每条: { item_id, vote, item: { title, jump_url, ... }, ... }
        items_raw: list = payload.get("data", {}).get("items", [])
        if not items_raw:
            logger.warning("[shitu_vote] 接口返回 items 为空")
            return

        # 只保留关键字段：title（选手名）、vote（票数）、jump_url（直播间）
        items = [
            {
                "title": it["item"].get("title", ""),
                "vote": it.get("vote", 0),
                "url": it["item"].get("jump_url", ""),
            }
            for it in items_raw
        ]

        # 分页信息（方便后续分析）
        page = payload.get("data", {}).get("page", {})

        record = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "items": items,
            "page_total": page.get("total", 0),
        }

        # 追加写入 JSONL（一行一条记录，便于并发读取最后一行）
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

        logger.info(
            f"[shitu_vote] 写入 {len(items)} 条, 总参赛人数 {record['page_total']}, ts={record['ts']}"
        )

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
                stripped = line.strip()
                if stripped:
                    last_line = stripped

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

        # 接口已按票数排序返回，直接用；保险起见再排一次
        items = sorted(record["items"], key=lambda x: x["vote"], reverse=True)[:12]
        ts = record.get("ts", "未知")
        total = record.get("page_total", "?")

        lines = [
            f"🏆 师徒杯S3 实时排行 Top12 / 共{total}人 (更新于 {ts})",
            "─" * 32,
        ]
        medals = ["🥇", "🥈", "🥉"]
        for i, it in enumerate(items, start=1):
            prefix = medals[i - 1] if i <= 3 else f"{i:2d}."
            name = it["title"][:16]  # 截断超长名字防止排版崩坏
            vote = it["vote"]
            lines.append(f"{prefix} {name:<18} {vote:>8} 票")

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
