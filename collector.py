"""聊天记录采集、抽样与统计。

只在这里依赖 Neo-MoFox 的消息 API，且采用函数内延迟导入，
使得统计相关的纯函数可以被离线脚本与单元测试直接复用。
"""

from __future__ import annotations

from datetime import date, datetime, time as dt_time, timedelta, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .models import (
    NotebookStats,
    clean_text,
    count_emoji,
    count_visible_chars,
)

_QUOTE_MIN_CHARS = 10
_QUOTE_MAX_CHARS = 90
_SKIP_TEXTS = {"[图片]", "[表情]", "[语音]", "[视频]", "[文件]", "[转发消息]", "。"}


def resolve_timezone(name: str) -> ZoneInfo | tzinfo:
    """解析时区名称，缺少时区数据时退回固定 UTC+8。"""
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        from datetime import timezone

        return timezone(timedelta(hours=8), name="Asia/Shanghai")


def day_bounds(day: str, timezone_name: str) -> tuple[float, float]:
    """返回指定自然日的起止时间戳（左闭右开）。"""
    tz = resolve_timezone(timezone_name)
    current = date.fromisoformat(day)
    start = datetime.combine(current, dt_time.min, tzinfo=tz)
    end = start + timedelta(days=1)
    return start.timestamp(), end.timestamp()


def period_bounds(start_day: str, days: int, timezone_name: str) -> tuple[float, float]:
    """返回从 start_day 起连续 days 天的起止时间戳（左闭右开）。

    end_ts 不会超过当前时刻，因此未结束的周只统计到「现在」。
    """
    start_ts, full_end = day_bounds(start_day, timezone_name)
    end_ts = full_end + (max(1, days) - 1) * 86400
    now_ts = datetime.now(resolve_timezone(timezone_name)).timestamp()
    return start_ts, min(end_ts, now_ts)


def monday_of(day: str) -> str:
    """返回给定日期所在自然周（周一为一周之始）的周一日期。"""
    current = date.fromisoformat(day)
    return (current - timedelta(days=current.weekday())).isoformat()


def range_days(start_day: str, end_day: str) -> list[str]:
    """返回 [start_day, end_day] 闭区间内的日期列表（YYYY-MM-DD）。"""
    start = date.fromisoformat(start_day)
    end = date.fromisoformat(end_day)
    if end < start:
        return []
    return [(start + timedelta(days=offset)).isoformat()
            for offset in range((end - start).days + 1)]


def today_in_timezone(timezone_name: str) -> str:
    """返回指定时区下的今天日期字符串。"""
    tz = resolve_timezone(timezone_name)
    return datetime.now(tz).date().isoformat()


def sample_messages(messages: list[dict[str, Any]], cap: int) -> list[dict[str, Any]]:
    """在保持时间顺序的前提下，把消息均匀抽样到 ``cap`` 条以内。"""
    if cap <= 0 or len(messages) <= cap:
        return list(messages)
    step = len(messages) / float(cap)
    picked = [messages[min(len(messages) - 1, int(index * step))] for index in range(cap)]
    result: list[dict[str, Any]] = []
    seen: set[int] = set()
    for item in picked:
        key = id(item)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


async def fetch_day_messages(
    stream_id: str,
    day: str,
    timezone_name: str,
    *,
    include_bot: bool = True,
    max_messages: int = 0,
) -> list[dict[str, Any]]:
    """读取指定聊天流某一天的全部消息。"""
    return await fetch_range_messages(
        stream_id, day, 1, timezone_name,
        include_bot=include_bot, max_messages=max_messages,
    )


async def fetch_range_messages(
    stream_id: str,
    start_day: str,
    days: int,
    timezone_name: str,
    *,
    include_bot: bool = True,
    max_messages: int = 0,
) -> list[dict[str, Any]]:
    """读取指定聊天流从 start_day 起连续 days 天的全部消息（最多到当前时刻）。"""
    from src.app.plugin_system.api import message_api

    start_ts, end_ts = period_bounds(start_day, days, timezone_name)
    if end_ts <= start_ts:
        return []
    messages = await message_api.get_messages_by_time_in_chat(
        stream_id=stream_id,
        start_time=start_ts,
        end_time=end_ts,
        limit=max(int(max_messages), 0),
        limit_mode="latest" if max_messages else "earliest",
        filter_bot=False,
        filter_command=False,
    )
    if not include_bot:
        messages = await message_api.filter_bot_messages(messages)
    return [item for item in messages if float(item.get("time") or 0) > 0]


async def split_bot_messages(
    messages: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """区分机器人消息与真人消息。"""
    from src.app.plugin_system.api import message_api

    try:
        human = await message_api.filter_bot_messages(messages)
    except Exception:
        return list(messages), []

    # filter_bot_messages 返回的是原字典的浅拷贝列表，元素仍是同一批对象；
    # 用对象身份区分，避免 message_id 缺失时按字符串比较误判。
    human_ids = {id(item) for item in human}
    bots = [item for item in messages if id(item) not in human_ids]
    if not human and messages:
        return list(messages), []
    return human, bots


def local_hour(timestamp: float, timezone_name: str) -> int:
    """把时间戳转换为指定时区的小时（0-23）。"""
    tz = resolve_timezone(timezone_name)
    return datetime.fromtimestamp(timestamp, tz).hour


def compute_stats(
    messages: list[dict[str, Any]],
    *,
    day: str,
    timezone_name: str,
    bot_messages: list[dict[str, Any]] | None = None,
) -> NotebookStats:
    """根据消息列表计算摘录所需的统计指标。"""
    stats = NotebookStats(date=day)
    hourly = [0] * 24
    member_counter: dict[str, int] = {}
    member_names: dict[str, str] = {}
    total_chars = 0
    longest_text = ""
    longest_name = ""

    for message in messages:
        timestamp = float(message.get("time") or 0)
        hour = local_hour(timestamp, timezone_name) if timestamp else 0
        hourly[hour] += 1

        name = clean_text(message.get("sender_name") or message.get("sender_id") or "未知", 24)
        sender = str(message.get("sender_id") or name)
        member_counter[sender] = member_counter.get(sender, 0) + 1
        member_names[sender] = name

        text = clean_text(message.get("processed_plain_text") or "", 0)
        chars = count_visible_chars(text)
        total_chars += chars
        stats.emoji_count += count_emoji(text)
        if chars > len(longest_text):
            longest_text = text
            longest_name = name

    stats.message_count = len(messages)
    stats.member_count = len(member_counter)
    stats.char_count = total_chars
    stats.hourly = hourly
    stats.avg_chars = round(total_chars / len(messages), 1) if messages else 0.0
    stats.top_members = [
        {"name": member_names[sender], "count": count}
        for sender, count in sorted(member_counter.items(), key=lambda item: item[1], reverse=True)[:12]
    ]
    if any(hourly):
        peak = max(range(24), key=lambda index: hourly[index])
        stats.peak_hour = peak
        stats.peak_count = hourly[peak]
    if longest_text:
        stats.longest = {"name": longest_name, "text": clean_text(longest_text, 120)}
    if bot_messages:
        stats.bot_message_count = len(bot_messages)
        stats.bot_char_count = sum(
            count_visible_chars(clean_text(item.get("processed_plain_text") or "", 0))
            for item in bot_messages
        )
    return stats


def build_transcript(
    messages: list[dict[str, Any]],
    *,
    timezone_name: str,
    per_message_limit: int = 160,
    budget: int = 24000,
) -> str:
    """把消息列表拼成带时间戳的聊天记录文本。

    从最新的消息开始向前填充，保证预算内保留最靠近日报时间的内容。
    """
    tz = resolve_timezone(timezone_name)
    lines: list[str] = []
    used = 0
    for message in reversed(messages):
        text = clean_text(message.get("processed_plain_text") or "", per_message_limit)
        if not text:
            continue
        stamp = datetime.fromtimestamp(float(message.get("time") or 0), tz).strftime("%H:%M")
        name = clean_text(message.get("sender_name") or "未知", 24)
        line = f"{stamp} {name}: {text}"
        if used + len(line) + 1 > budget:
            break
        lines.append(line)
        used += len(line) + 1
    lines.reverse()
    return "\n".join(lines)


def pick_quote_candidates(
    messages: list[dict[str, Any]],
    *,
    limit: int = 12,
) -> list[dict[str, str]]:
    """挑选适合抄在页角的金句候选消息。

    规则偏保守：过滤命令、链接、纯表情与过短文本，并按长度与话题感打分。
    """
    scored: list[tuple[float, dict[str, str]]] = []
    seen: set[str] = set()
    for message in messages:
        raw = clean_text(message.get("processed_plain_text") or "", 0)
        if not raw or raw in _SKIP_TEXTS:
            continue
        if raw.startswith(("/", "!", "！", "#")) or "http://" in raw or "https://" in raw:
            continue
        if raw.startswith("[CQ:"):
            continue
        length = count_visible_chars(raw)
        if length < _QUOTE_MIN_CHARS or length > _QUOTE_MAX_CHARS:
            continue
        if raw in seen:
            continue
        seen.add(raw)
        score = float(length)
        if any(mark in raw for mark in ("？", "?", "！", "!", "…")):
            score += 6
        if "\n" in raw:
            score += 3
        scored.append(
            (
                score,
                {
                    "name": clean_text(message.get("sender_name") or "未知", 24),
                    "text": clean_text(raw, _QUOTE_MAX_CHARS),
                },
            )
        )

    scored.sort(key=lambda item: item[0], reverse=True)
    picked: list[dict[str, str]] = []
    used_names: set[str] = set()
    for _, item in scored:
        if item["name"] in used_names and len(picked) < limit // 2:
            continue
        used_names.add(item["name"])
        picked.append(item)
        if len(picked) >= limit:
            break
    return picked


def split_day_periods(messages: list[dict[str, Any]], timezone_name: str) -> list[dict[str, Any]]:
    """按凌晨/上午/下午/晚上把消息切成四段，供无模型回退使用。"""
    buckets: list[list[dict[str, Any]]] = [[], [], [], []]
    labels = ["凌晨的纸页", "上午的纸页", "下午的纸页", "晚上的纸页"]
    for message in messages:
        hour = local_hour(float(message.get("time") or 0), timezone_name)
        if hour < 6:
            index = 0
        elif hour < 12:
            index = 1
        elif hour < 18:
            index = 2
        else:
            index = 3
        buckets[index].append(message)
    return [
        {"label": labels[index], "messages": items}
        for index, items in enumerate(buckets)
        if items
    ]


def member_target_stats(
    messages: list[dict[str, Any]],
    *,
    exclude_ids: set[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """统计每个成员『被指向』的次数（被回复 / 被@ / 名字被提及）。

    Returns:
        ``{victim_id: {"name": 昵称, "count": 被指向次数}}``。
    """
    id_to_name: dict[str, str] = {}
    for message in messages:
        sender = str(message.get("sender_id") or "")
        if sender:
            id_to_name.setdefault(sender, clean_text(message.get("sender_name") or sender, 24))
    name_to_id = {name: sender for sender, name in id_to_name.items() if len(name) >= 2}

    victim_counts: dict[str, dict[str, Any]] = {}
    for message in messages:
        sender = str(message.get("sender_id") or "")
        text = clean_text(message.get("processed_plain_text") or "", 0)
        reply_to = str(message.get("reply_to") or "")
        targets: set[str] = set()
        if reply_to and reply_to in id_to_name and reply_to != sender:
            targets.add(reply_to)
        for name, member_id in name_to_id.items():
            if name in text and member_id != sender:
                targets.add(member_id)
        for target in targets:
            if exclude_ids and target in exclude_ids:
                continue
            entry = victim_counts.setdefault(
                target, {"name": id_to_name.get(target, target), "count": 0}
            )
            entry["count"] += 1
    return victim_counts


def member_interaction_stats(
    messages: list[dict[str, Any]],
    *,
    member_ids: set[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """统计每个成员对其他成员的互动次数（回复他人 / @或提及他人）。

    Args:
        member_ids: 只统计这些成员；为空统计全部。

    Returns:
        ``{member_id: {"name": 昵称, "count": 对他人互动次数}}``。
    """
    id_to_name: dict[str, str] = {}
    for message in messages:
        sender = str(message.get("sender_id") or "")
        if sender:
            id_to_name.setdefault(sender, clean_text(message.get("sender_name") or sender, 24))
    name_to_id = {name: sender for sender, name in id_to_name.items() if len(name) >= 2}

    counts: dict[str, dict[str, Any]] = {}
    for message in messages:
        sender = str(message.get("sender_id") or "")
        if member_ids is not None and sender not in member_ids:
            continue
        text = clean_text(message.get("processed_plain_text") or "", 0)
        reply_to = str(message.get("reply_to") or "")
        targets: set[str] = set()
        if reply_to and reply_to in id_to_name and reply_to != sender:
            targets.add(reply_to)
        for name, member_id in name_to_id.items():
            if member_id != sender and name and name in text:
                targets.add(member_id)
        if not targets:
            continue
        entry = counts.setdefault(
            sender, {"name": id_to_name.get(sender, sender), "count": 0}
        )
        entry["count"] += 1
    return counts


def member_bot_stats(
    messages: list[dict[str, Any]],
    *,
    bot_ids: set[str],
    bot_name: str,
) -> dict[str, dict[str, Any]]:
    """统计每个成员与 bot 的互动次数（回复 bot / @bot / 提及 bot）。

    Returns:
        ``{sender_id: {"name": 昵称, "count": 互动次数}}``，不含 bot 自己。
    """
    bot_message_ids = {
        str(message.get("message_id") or "")
        for message in messages
        if str(message.get("sender_id") or "") in bot_ids
    }
    stats: dict[str, dict[str, Any]] = {}
    for message in messages:
        sender = str(message.get("sender_id") or "")
        if not sender or sender in bot_ids:
            continue
        reply_to = str(message.get("reply_to") or "")
        text = clean_text(message.get("processed_plain_text") or "", 0)
        is_hit = (
            (reply_to and reply_to in bot_message_ids)
            or (bot_name and bot_name in text)
            or text.startswith("@")
        )
        if not is_hit:
            continue
        entry = stats.setdefault(
            sender, {"name": clean_text(message.get("sender_name") or sender, 24), "count": 0}
        )
        entry["count"] += 1
    return stats


def collect_dossier_pool(
    messages: list[dict[str, Any]],
    *,
    bot_ids: set[str],
    bot_name: str,
    direction: str = "member_as_culprit",
    focus_members: list[str] | None = None,
    victim_names: dict[str, str] | None = None,
    context: int = 2,
) -> list[dict[str, Any]]:
    """提取案底候选消息池（含前后 context 条上下文）。

    Args:
        direction: ``member_as_culprit`` 收集「聚焦成员 → bot」的迫害记录；
            ``member_as_victim`` 收集「全群 → 聚焦成员」的围攻记录。
        focus_members: 聚焦的 QQ 号列表（culprit 方向=主犯，victim 方向=受害者）。
    """
    bot_message_ids = {
        str(message.get("message_id") or "")
        for message in messages
        if str(message.get("sender_id") or "") in bot_ids
    }
    id_to_index = {
        str(message.get("message_id") or ""): index
        for index, message in enumerate(messages)
    }
    id_to_name: dict[str, str] = {}
    for message in messages:
        sender = str(message.get("sender_id") or "")
        if sender:
            id_to_name.setdefault(sender, clean_text(message.get("sender_name") or sender, 24))
    focus_set = {str(member).strip() for member in (focus_members or []) if str(member).strip()}

    def _sender_of(index_or_id: Any) -> str:
        if isinstance(index_or_id, int):
            return str(messages[index_or_index].get("sender_id") or "")
        idx = id_to_index.get(str(index_or_id))
        return str(messages[idx].get("sender_id") or "") if idx is not None else ""

    hit_indexes: set[int] = set()
    for index, message in enumerate(messages):
        sender = str(message.get("sender_id") or "")
        reply_to = str(message.get("reply_to") or "")
        text = clean_text(message.get("processed_plain_text") or "", 0)
        replied_sender = _sender_of(reply_to) if reply_to else ""

        if direction == "member_as_victim":
            # 围攻方向：命中 = 消息指向聚焦成员（回复他们 / 文本提及他们的昵称）
            if sender in focus_set:
                continue  # 受害者自己的发言不算被围攻
            is_targeting = any(
                name and name in text
                for member_id, name in (victim_names or {}).items()
                if member_id in focus_set
            )
            is_reply_to_victim = bool(reply_to) and _sender_of(reply_to) in focus_set
            if not (is_targeting or is_reply_to_victim):
                continue
        elif direction == "member_as_culprit":
            # 加害方向：命中 = 聚焦成员对「其他人」（群友或各 bot）的互动消息
            if focus_set and sender not in focus_set:
                continue
            is_reply_to_other = bool(reply_to) and _sender_of(reply_to) not in (sender,)
            mentions_other = any(
                name and name in text
                for member_id, name in id_to_name.items()
                if member_id != sender
            )
            if not (is_reply_to_other or mentions_other):
                continue

        for offset in range(-context, context + 1):
            pos = index + offset
            if 0 <= pos < len(messages):
                hit_indexes.add(pos)

    return [messages[index] for index in sorted(hit_indexes)]


def member_incoming_stats(
    messages: list[dict[str, Any]],
    *,
    victim_ids: set[str],
    victim_names: dict[str, str],
) -> dict[str, dict[str, Any]]:
    """统计每个发送者对聚焦成员的「围攻」次数（回复/@/提及）。

    Returns:
        ``{sender_id: {"name": 昵称, "count": 次数}}``，不含受害者自己。
    """
    victim_message_ids = {
        str(message.get("message_id") or "")
        for message in messages
        if str(message.get("sender_id") or "") in victim_ids
    }
    stats: dict[str, dict[str, Any]] = {}
    for message in messages:
        sender = str(message.get("sender_id") or "")
        if not sender or sender in victim_ids:
            continue
        reply_to = str(message.get("reply_to") or "")
        text = clean_text(message.get("processed_plain_text") or "", 0)
        is_reply_to_victim = bool(reply_to) and reply_to in victim_message_ids
        mentions_victim = any(
            name and name in text for name in victim_names.values()
        )
        if not (is_reply_to_victim or mentions_victim):
            continue
        entry = stats.setdefault(
            sender, {"name": clean_text(message.get("sender_name") or sender, 24), "count": 0}
        )
        entry["count"] += 1
    return stats
