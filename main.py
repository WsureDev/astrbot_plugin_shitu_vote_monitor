import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register

# 投票排行接口（固定参数，第一页最多28条，按票数排序）
_VOTE_ID = "23ERA1wloghvxay00"
_GROUP_ID = "24ERA1wloghvtc600"
VOTE_URL = (
    "https://api.bilibili.com/x/activity_components/vote_new/rank"
    f"?group_id={_GROUP_ID}&pn=1&ps=28&random_version=&type=2&vote_id={_VOTE_ID}"
    "&web_location=888.148305"
)

# 数据存储目录（存放在 data/ 下，插件卸载重装不丢失）
DATA_DIR = Path("data") / "astrbot_plugin_shitu_vote_monitor"

# 每次轮询所有选手的快照（一次轮询 = 一行，含 timestamp）
# 格式（每行一个 JSON）：
#   {
#     "timestamp": 1718000000,          # Unix 秒，Grafana time field
#     "ts":        "2025-06-19T14:00:00",  # 人类可读，方便直接看日志
#     "page_total": 27,                  # 本次接口返回的总参赛人数
#     "items": [
#       {"title": "是Winter喵", "vote": 37661, "url": "https://live.bilibili.com/..."},
#       ...
#     ]
#   }
SNAPSHOT_FILE = DATA_DIR / "vote_snapshots.jsonl"
FIX_VOTES_FILE = Path(__file__).resolve().parent / "fix_votes.json"


def _extract_csrf(cookie: str) -> str:
    """从 Cookie 字符串中提取 bili_jct 作为 csrf。"""
    m = re.search(r"bili_jct=([^;]+)", cookie)
    return m.group(1).strip() if m else ""


@register(
    "astrbot_plugin_shitu_vote_monitor",
    "WsureDev",
    "定时轮询 B 站师徒杯 S3 投票排行数据，支持 /rank 查看实时榜单。",
    "0.1.2",
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
        self.fix_map: dict[str, int] = {}
        self._load_fix_map()

    def _load_fix_map(self):
        self.fix_map = {}
        if not FIX_VOTES_FILE.exists():
            logger.warning(f"[shitu_vote] 补正配置不存在: {FIX_VOTES_FILE}")
            return

        try:
            data = json.loads(FIX_VOTES_FILE.read_text(encoding="utf-8"))
            if not isinstance(data, list):
                raise ValueError("fix_votes.json 顶层必须为数组")

            for it in data:
                if not isinstance(it, dict):
                    continue
                title = str(it.get("title", "")).strip()
                fix_vote = int(it.get("fix_vote", 0))
                if title and fix_vote > 0:
                    self.fix_map[title] = fix_vote
        except Exception as e:
            logger.warning(f"[shitu_vote] 加载补正配置失败: {e}")
            self.fix_map = {}

    async def initialize(self):
        """插件初始化：启动后台轮询任务。"""
        if not self.cookie:
            logger.warning(
                "[shitu_vote] 未配置 bilibili_cookie，已跳过轮询。"
                "请在 WebUI -> 插件管理 -> 师徒杯S3投票监控 -> 配置 中填写 Cookie。"
            )
            return

        logger.info(
            f"[shitu_vote] 插件启动，轮询间隔 {self.poll_interval}s，"
            f"快照写入 {SNAPSHOT_FILE}"
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
                break
            except Exception as e:
                logger.error(f"[shitu_vote] 轮询出错: {e}")
            await asyncio.sleep(self.poll_interval)

    async def _fetch_and_save(self):
        """
        请求 B 站排行接口，将一次快照写入 JSONL。

        存储格式（每行一个 JSON 快照）：
        {
          "timestamp": <Unix秒, int>,   ← Grafana 时间字段
          "ts":        <ISO8601, str>,   ← 人类可读
          "page_total": <int>,
          "items": [
            {"title": str, "vote": int, "url": str},
            ...
          ]
        }
        """
        csrf = _extract_csrf(self.cookie)
        url = VOTE_URL + (f"&csrf={csrf}" if csrf else "")

        headers = {
            "Cookie": self.cookie,
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/127.0.0.0 Safari/537.36"
            ),
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
                f"[shitu_vote] 接口返回错误: code={code} "
                f"message={payload.get('message')}"
            )
            return

        items_raw: list = payload.get("data", {}).get("items", [])
        if not items_raw:
            logger.warning("[shitu_vote] 接口返回 items 为空")
            return

        page = payload.get("data", {}).get("page", {})

        # 采集时间：同时记录 Unix 时间戳（Grafana 用）和 ISO 字符串（人类可读）
        now = datetime.now(timezone.utc)
        items = []
        for it in items_raw:
            title = it["item"].get("title", "")
            fix_vote = self.fix_map.get(title, 0)
            item = {
                "title": title,
                "vote": int(it.get("vote", 0)) + fix_vote,
                "url": it["item"].get("jump_url", ""),
            }
            if fix_vote > 0:
                item["fix_vote"] = fix_vote
            items.append(item)

        snapshot = {
            "timestamp": int(now.timestamp()),          # Unix 秒，Grafana time field
            "ts": now.astimezone().isoformat(timespec="seconds"),  # 本地时间 ISO8601
            "page_total": page.get("total", 0),
            "items": items,
        }

        # 追加写入 JSONL（一行一条快照，独享写/并发读安全）
        with SNAPSHOT_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(snapshot, ensure_ascii=False) + "\n")

        logger.info(
            f"[shitu_vote] 快照写入: {len(snapshot['items'])} 位选手, "
            f"总人数 {snapshot['page_total']}, ts={snapshot['ts']}"
        )

    # ------------------------------------------------------------------ #
    #  读取最新快照                                                          #
    # ------------------------------------------------------------------ #

    async def _read_latest(self) -> dict | None:
        """从 JSONL 日志读取最后一条有效快照。"""
        if not SNAPSHOT_FILE.exists():
            return None

        last_line = ""
        with SNAPSHOT_FILE.open("r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if stripped:
                    last_line = stripped

        if not last_line:
            return None

        try:
            return json.loads(last_line)
        except json.JSONDecodeError as e:
            logger.error(f"[shitu_vote] 解析最新快照失败: {e}")
            return None

    # ------------------------------------------------------------------ #
    #  指令处理                                                             #
    # ------------------------------------------------------------------ #

    @filter.command("rank")
    async def rank(self, event: AstrMessageEvent):
        """查看师徒杯S3实时投票排行（最多28名）"""
        snapshot = await self._read_latest()

        if snapshot is None:
            yield event.plain_result(
                "暂无数据，请等待首次轮询完成（或检查 Cookie 配置）。"
            )
            return

        # 按票数降序，展示全部（最多 28 条）
        items = sorted(snapshot["items"], key=lambda x: x["vote"], reverse=True)
        ts = snapshot.get("ts", "未知")
        total = snapshot.get("page_total", "?")

        lines = [
            f"🏆 师徒杯S3 实时排行 Top28 / 共{total}人",
            f"🕐 更新于 {ts}",
            "─" * 32,
        ]
        medals = ["🥇", "🥈", "🥉"]
        for i, it in enumerate(items, start=1):
            prefix = medals[i - 1] if i <= 3 else f"{i:2d}."
            name = it["title"][:16]
            vote = it["vote"]
            fix_vote = int(it.get("fix_vote", 0))
            if fix_vote > 0:
                fix_k = f"{fix_vote // 1000}k"
                vote_text = f"{vote}（含补正{fix_k}票）"
            else:
                vote_text = f"{vote:>8} 票"
            lines.append(f"{prefix} {name:<18} {vote_text}")

        lines.append("─" * 32)
        lines.append("📈 趋势面板：https://static-host-aetilet6-shitu-vote.sealoshzh.site/")

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
