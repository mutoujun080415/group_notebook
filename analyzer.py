"""把一天的聊天记录压缩成「群聊摘录」文案。

优先调用 LLM 输出结构化 JSON；模型不可用或输出不合法时，退回
基于统计与原始消息的规则版文案，保证插件在任何环境下都能出图。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
import re
from datetime import datetime
from typing import TYPE_CHECKING, Any

from src.app.plugin_system.api.log_api import get_logger

from .collector import (
    local_hour,
    pick_quote_candidates,
    resolve_timezone,
    split_day_periods,
)
from .models import (
    Mood,
    MoodDimension,
    NotebookReport,
    NotebookStats,
    Portrait,
    QuoteItem,
    Topic,
    clamp_text,
    clean_text,
    count_emoji,
    count_visible_chars,
    strip_markup,
)

if TYPE_CHECKING:
    from .config import GroupNotebookConfig

logger = get_logger("group_notebook")

_CODE_FENCE = re.compile(r"```(?:json)?", re.IGNORECASE)

_FALLBACK_TITLES = [
    "龙王",
    "表情包军火库",
    "省流大师",
    "互动达人",
    "潜水闪烁者",
    "独白记录员",
    "评论家",
    "气氛组组长",
    "夜班常驻",
    "考据党",
]
_FALLBACK_TAGS = ["话痨", "夜猫子", "技术流", "乐子人", "潜水员", "接梗王", "考据党", "端水大师"]
_FALLBACK_MBTI = ["ENFP", "INTP", "ESFJ", "ISTJ", "ENTP", "ISFP", "INFJ", "ESTP"]
_FALLBACK_GREETINGS = [
    "又吵又热闹的一天，先翻到第一页吧。",
    "今天的纸页有点满，慢慢看。",
    "热闹都收进这一页了，慢慢翻。",
]
_FALLBACK_COFFEE = "今晚适合一杯温热的拿铁，把聊天里那些没聊完的话题，留给明天接着冒泡。"
_FALLBACK_MURMUR = "今天也把大家的热闹收进本子里了，晚安，明天见。"


def _normalize(text: str) -> str:
    """去掉空白与常见标点，便于做原话比对。"""
    return re.sub(r"[\s，。！？、；：,.!?;:\"'“”‘’（）()【】\[\]…—~～@]", "", str(text or ""))


def _split_json(raw: str) -> dict[str, Any] | None:
    """从模型输出中提取第一个完整 JSON 对象。"""
    text = _CODE_FENCE.sub("", str(raw or "")).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    candidate = text[start : end + 1]
    try:
        data = json.loads(candidate, strict=False)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _member_index(messages: list[dict[str, Any]]) -> dict[str, str]:
    """构建昵称 -> sender_id 的索引。"""
    index: dict[str, str] = {}
    for message in messages:
        name = clean_text(message.get("sender_name") or "", 24)
        if name:
            index.setdefault(name, str(message.get("sender_id") or name))
    return index


def _resolve_member_name(raw_name: str, known: list[str]) -> str:
    """把模型给出的昵称对齐到真实昵称，匹配不到时返回空串。"""
    name = clean_text(strip_markup(raw_name), 24)
    if not name:
        return ""
    if name in known:
        return name
    normalized = _normalize(name)
    for candidate in known:
        if _normalize(candidate) == normalized:
            return candidate
    for candidate in known:
        if normalized and normalized in _normalize(candidate):
            return candidate
    return ""


def _coerce_topics(
    raw: Any,
    *,
    known_names: list[str],
    limit: int,
) -> list[Topic]:
    """校验并裁剪模型输出的话题；参与者昵称对齐不到真实昵称时丢弃。"""
    topics: list[Topic] = []
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            title = clamp_text(strip_markup(item.get("title")), 18)
            detail = clamp_text(strip_markup(item.get("detail")), 200)
            if not title or not detail:
                continue
            contributors: list[str] = []
            if isinstance(item.get("contributors"), list):
                for raw_name in item["contributors"]:
                    name = _resolve_member_name(str(raw_name), known_names)
                    if name and name not in contributors:
                        contributors.append(name)
                    if len(contributors) >= 5:
                        break
            topics.append(Topic(title=title, contributors=contributors, detail=detail))
            if len(topics) >= limit:
                break
    return topics


def _coerce_portraits(
    raw: Any,
    *,
    known_names: list[str],
    limit: int,
) -> list[Portrait]:
    """校验模型输出的群友侧写，未知昵称直接丢弃。"""
    portraits: list[Portrait] = []
    seen: set[str] = set()
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            name = _resolve_member_name(item.get("name") or "", known_names)
            if not name or name in seen:
                continue
            title = clamp_text(strip_markup(item.get("title")), 8)
            mbti = re.sub(r"[^A-Za-z]", "", str(item.get("mbti") or "")).upper()[:4]
            tag = clamp_text(strip_markup(item.get("tag")), 8)
            desc = clamp_text(strip_markup(item.get("desc")), 100)
            if not desc:
                continue
            seen.add(name)
            portraits.append(
                Portrait(name=name, title=title or "群友", mbti=mbti, tag=tag, desc=desc)
            )
            if len(portraits) >= limit:
                break
    return portraits


def _coerce_quotes(
    raw: Any,
    *,
    known_names: list[str],
    candidates: list[dict[str, str]],
    transcript_normalized: str,
    limit: int,
) -> list[QuoteItem]:
    """校验模型输出的金句：原话必须真实出现在聊天记录中。"""
    quotes: list[QuoteItem] = []
    used: set[str] = set()
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            text = clean_text(strip_markup(item.get("text")), 120)
            sender = _resolve_member_name(item.get("sender") or "", known_names)
            comment = clamp_text(strip_markup(item.get("comment")), 80)
            normalized = _normalize(text)
            if not text or not comment or not normalized:
                continue
            if normalized not in transcript_normalized:
                continue
            if text in used:
                continue
            used.add(text)
            quotes.append(QuoteItem(sender=sender or "群友", text=text, comment=comment))
            if len(quotes) >= limit:
                break

    if len(quotes) < limit:
        for candidate in candidates:
            if len(quotes) >= limit:
                break
            if candidate["text"] in used:
                continue
            used.add(candidate["text"])
            quotes.append(
                QuoteItem(
                    sender=candidate["name"],
                    text=candidate["text"],
                    comment="这句被记在了页角，适合下班路上反复回味。",
                )
            )
    return quotes


def mood_shares(stats: NotebookStats) -> list[tuple[str, int]]:
    """按真实统计计算四类天气占比，总和不超过 100。"""
    total = max(1, stats.message_count)
    top = stats.top_members[0]["count"] if stats.top_members else 0
    night = sum(stats.hourly[0:6]) + sum(stats.hourly[22:24])
    active = round(top * 100 / total)
    peak = round(stats.peak_count * 100 / total)
    night_share = round(night * 100 / total)
    rest = max(0, 100 - active - peak - night_share)
    return [
        ("热烈共鸣", active),
        ("高峰浪涌", peak),
        ("深夜含量", night_share),
        ("日常水群", rest),
    ]


def _coerce_mood(raw: Any) -> Mood:
    """校验模型输出的聊天天气；百分比裁剪，总和超过 100 时按比例缩。"""
    if not isinstance(raw, dict):
        return Mood()
    title = clamp_text(strip_markup(raw.get("title")), 12)
    subtitle = clamp_text(strip_markup(raw.get("subtitle")), 30)
    summary = clamp_text(strip_markup(raw.get("summary")), 80)
    dimensions: list[MoodDimension] = []
    if isinstance(raw.get("dimensions"), list):
        for item in raw["dimensions"][:6]:
            if not isinstance(item, dict):
                continue
            name = clamp_text(strip_markup(item.get("name")), 10)
            comment = clamp_text(strip_markup(item.get("comment")), 70)
            if not name or not comment:
                continue
            try:
                percentage = int(float(item.get("percentage") or 0))
            except (TypeError, ValueError):
                percentage = 0
            dimensions.append(
                MoodDimension(name=name, percentage=max(0, min(100, percentage)), comment=comment)
            )
    total_pct = sum(item.percentage for item in dimensions)
    if total_pct > 100 and total_pct > 0:
        factor = 100.0 / total_pct
        for item in dimensions:
            item.percentage = round(item.percentage * factor)
    return Mood(title=title, subtitle=subtitle, dimensions=dimensions, summary=summary)


def build_prompt(
    *,
    report_date: str,
    room_name: str,
    bot_name: str,
    stats: NotebookStats,
    transcript: str,
    config: "GroupNotebookConfig",
    member_lines: str,
) -> tuple[str, str]:
    """构造系统提示与用户提示。"""
    analysis = config.analysis
    notebook = config.notebook
    system_prompt = (
        f"你是群「{room_name}」的群聊摘录编辑，机器人叫「{bot_name}」。"
        "你的工作是把一天的聊天记录整理成一页手账：几个值得夹书签的话题、"
        "几位群友的侧写、几句抄在页角的金句、一场聊天天气。\n"
        f"{analysis.persona}\n"
        "硬性要求：\n"
        "1. 只输出一个 JSON 对象，不要 Markdown 代码块，不要任何解释文字。\n"
        "2. 人物昵称、金句原话必须来自聊天记录，禁止编造昵称、事件与数字。\n"
        "3. 不评价外貌、身份、地域、政治与隐私；不写人身攻击和引战内容。\n"
        "4. 写得具体：写谁说了什么、事情怎么拐弯的；不要「大家聊得很开心」这类空话。\n"
        "5. 用中文短句，语气像整理手账的人，轻松、具体、不堆形容词、不升华。\n"
        "6. 严格遵守字段长度限制，超长会被截断。\n"
    )
    if analysis.custom_prompt.strip():
        system_prompt += f"\n额外要求：\n{analysis.custom_prompt.strip()}\n"

    user_prompt = (
        f"日期：{report_date}\n"
        f"统计：消息 {stats.message_count} 条，参与 {stats.member_count} 人，"
        f"有效字数 {stats.char_count}，表情 {stats.emoji_count} 个，"
        f"最活跃时段 {stats.peak_hour:02d}:00（{stats.peak_count} 条）。\n"
        f"成员发言数：\n{member_lines}\n\n"
        f"聊天记录（{report_date}）：\n{transcript}\n\n"
        "请输出如下结构的 JSON：\n"
        "{\n"
        f'  "greeting": "16 字以内，写给群友的翻页招呼，像手账扉页的一句手写小字",\n'
        f'  "topics": [{{"title": "10 字以内，具体到事", "contributors": ["记录里的昵称", 最多 5 个],'
        f' "detail": "80-150 字，讲清这段在聊什么、怎么聊开的；提到具体群友时用 [昵称] 方括号标注，最多 3 处"}}],\n'
        f'  "recap": "100-160 字，以整理者的视角把今天的聊天内容总结成一段话：先说主要聊了什么、'
        f'事情怎么发展的，再说整体氛围；按时间顺序组织，不要逐条复述话题标题，不要空话",\n'
        f'  "portraits": [{{"name": "记录里的昵称", "title": "6 字以内称号（如 龙王/省流大师/潜水闪烁者）",'
        f' "mbti": "四个大写字母", "tag": "2-4 字标签（如 话痨/夜猫子）",'
        f' "desc": "40-70 字，先写他今天具体做了什么、说话什么风格，'
        f'最后必须带一句符合你人设口吻的吐槽/感慨/批注，禁止只罗列统计数字"}}],\n'
        f'  "quotes": [{{"sender": "记录里的昵称", "text": "记录里的原话，可截取但不要改写",'
        f' "comment": "30-60 字，{bot_name} 视角的锐评或补充"}}],\n'
        f'  "mood": {{"title": "2-6 字，今天聊天天气的总名", "subtitle": "10-20 字副标",'
        f' "dimensions": [{{"name": "2-6 字抽象维度（如 就业焦虑/技术考古/无意义水群）",'
        f' "percentage": 整数占比, "comment": "20-50 字，符合人设的锐评"}}],'
        f' "summary": "30-60 字，一页看完的整体总结"}},\n'
        f'  "coffee": "30-60 字，结合今天聊的内容，给熬夜的群友推荐一杯饮品",\n'
        f'  "murmur": "20-40 字，{bot_name} 第一人称的睡前碎碎念"\n'
        "}\n"
        f"数量：topics 恰好 {analysis.topic_count} 条，portraits 最多 {analysis.portrait_count} 条，"
        f"quotes {analysis.quote_count} 条，mood.dimensions 3-5 条。\n"
        "mood.dimensions 的 percentage 总和不要超过 100；维度名必须抽象，不要出现具体昵称。\n"
        f"页面标题固定为「{notebook.page_title}」，不要在 JSON 里重复输出标题。\n"
    )
    return system_prompt, user_prompt


async def _request_llm(
    system_prompt: str,
    user_prompt: str,
    model_task: str,
    timeout: float = 120.0,
) -> str:
    """调用 LLM 并返回原始文本；失败或超时抛异常由上层回退。"""
    from src.app.plugin_system.api.llm_api import create_llm_request, get_model_set_by_task
    from src.kernel.llm import LLMContextManager, LLMPayload, ROLE, Text

    model_set = get_model_set_by_task(model_task)
    request = create_llm_request(
        model_set=model_set,
        request_name="group_notebook",
        context_manager=LLMContextManager(),
    )
    request.add_payload(LLMPayload(ROLE.SYSTEM, [Text(system_prompt)]))
    request.add_payload(LLMPayload(ROLE.USER, [Text(user_prompt)]))

    async def _run() -> str:
        response = await request.send(stream=False)
        await response
        return str(response.message or "")

    return await asyncio.wait_for(_run(), timeout=max(5.0, float(timeout)))


def _build_member_lines(stats: NotebookStats) -> str:
    """把发言数统计格式化为提示词里的一行行文本。"""
    return "\n".join(
        f"- {item['name']}：{item['count']} 条" for item in stats.top_members[:12]
    ) or "- （无）"


_FALLBACK_COMMENTS = [
    "本子批注：存在感很足，下次让他请客奶茶。",
    "本子批注：翻到他这页就想笑，建议连续刊登。",
    "本子批注：话不算最多，但每句都在点上。",
    "本子批注：安静的时候像背景音，开口就是剧情。",
    "本子批注：这页的气氛，一半是他带起来的。",
    "本子批注：他要是哪天潜水，群里得冷场十分钟。",
    "本子批注：吐槽水平稳定发挥，值得回访。",
    "本子批注：把他的发言裱起来，就是今日份乐子。",
]


def _fallback_portraits(
    messages: list[dict[str, Any]],
    *,
    limit: int,
    room_seed: str,
    timezone_name: str = "Asia/Shanghai",
) -> list[Portrait]:
    """在无模型时，用行为统计（发言量/深夜比/表情包率/最长真实发言）生成带批注的群友侧写。"""
    rng = random.Random(int(hashlib.md5(room_seed.encode("utf-8")).hexdigest(), 16) % (2**32))
    by_name: dict[str, list[dict[str, Any]]] = {}
    for message in messages:
        name = clean_text(message.get("sender_name") or "", 24)
        if not name:
            continue
        text = clean_text(message.get("processed_plain_text") or "", 0)
        hour = local_hour(float(message.get("time") or 0), timezone_name)
        by_name.setdefault(name, []).append(
            {"text": text, "hour": hour, "emoji": count_emoji(text)}
        )

    portraits: list[Portrait] = []
    ranked = sorted(
        by_name.items(), key=lambda item: len(item[1]), reverse=True
    )
    for name, items in ranked[:limit]:
        count = len(items)
        emoji_count = sum(item["emoji"] for item in items)
        night_count = sum(1 for item in items if item["hour"] < 6 or item["hour"] >= 22)
        real_texts = [
            item["text"] for item in items
            if item["text"] and item["text"] not in ("[图]", "[表情包]", "[语音]", "[视频]", "[文件]", "[转发]")
        ]
        sample = max(real_texts, key=count_visible_chars) if real_texts else ""

        traits: list[str] = []
        if count:
            emoji_ratio = emoji_count / count
            if emoji_ratio >= 0.4:
                traits.append("表情包是母语")
            elif emoji_count >= 5:
                traits.append("斗图从不落下风")
        if night_count >= max(3, count // 5):
            traits.append("一到深夜就准时营业")
        flavor = "、".join(traits)

        parts: list[str] = [f"今天冒泡 {count} 次"]
        if sample:
            parts.append(f"最长的一句是「{clamp_text(sample, 36)}」")
        if flavor:
            parts.append(flavor)
        desc = "，".join(parts) + "。"
        desc += rng.choice(_FALLBACK_COMMENTS)
        portraits.append(
            Portrait(
                name=name,
                title=rng.choice(_FALLBACK_TITLES),
                mbti=rng.choice(_FALLBACK_MBTI),
                tag=rng.choice(_FALLBACK_TAGS),
                desc=clamp_text(desc, 110),
            )
        )
    return portraits


def _fallback_topics(
    messages: list[dict[str, Any]],
    *,
    timezone_name: str,
    limit: int,
) -> list[Topic]:
    """在无模型时，按时间段挑最长消息凑出话题摘要。"""
    topics: list[Topic] = []
    for period in split_day_periods(messages, timezone_name):
        if len(topics) >= limit:
            break
        items = sorted(
            period["messages"],
            key=lambda message: count_visible_chars(str(message.get("processed_plain_text") or "")),
            reverse=True,
        )
        picked = [item for item in items[:2] if clean_text(item.get("processed_plain_text") or "", 0)]
        if not picked:
            continue
        head = clean_text(picked[0].get("processed_plain_text") or "", 46)
        speaker = clean_text(picked[0].get("sender_name") or "有人", 16)
        if len(picked) > 1:
            second = clean_text(picked[1].get("processed_plain_text") or "", 40)
            second_speaker = clean_text(picked[1].get("sender_name") or "另一个人", 16)
            detail = f"{speaker} 提到「{head}」，{second_speaker} 接了一句「{second}」。"
        else:
            detail = f"{speaker} 提到「{head}」，这段只有他一个人在说。"
        topics.append(Topic(title=period["label"], contributors=[speaker], detail=detail))
    return topics


def _fallback_mood(stats: NotebookStats) -> Mood:
    """用真实统计生成聊天天气。"""
    shares = mood_shares(stats)
    comments = {
        "热烈共鸣": "最活跃的那位一开口，纸页就写满了半页。",
        "高峰浪涌": "消息挤在同一个小时里，像暴雨前的蚂蚁搬家。",
        "深夜含量": "夜猫子的份量不小，本子都替他们打了个哈欠。",
        "日常水群": "剩下的都是日常的水花，温和，但撑起了厚度。",
    }
    dimensions = [
        MoodDimension(name=name, percentage=share, comment=comments.get(name, ""))
        for name, share in shares
        if share > 0
    ]
    return Mood(
        title="今日聊天天气",
        subtitle="按消息占比分成的几种天气",
        dimensions=dimensions,
        summary="热闹按比例收进了不同格子里，翻完这页，天气就算记录完了。",
    )


def _fallback_recap(
    stats: NotebookStats,
    topics: list[Topic],
    messages: list[dict[str, Any]],
    timezone_name: str,
) -> str:
    """在无模型时，用统计与时段桶拼一段整体总结。"""
    night = sum(stats.hourly[0:6]) + sum(stats.hourly[22:24])
    top_name = stats.top_members[0]["name"] if stats.top_members else "群友"
    parts: list[str] = [
        f"这一天群里共有 {stats.message_count} 条消息、{stats.member_count} 位群友参与，"
        f"{top_name} 是今天最活跃的人。"
    ]
    if topics:
        heads = "、".join(topic.title for topic in topics[:3])
        parts.append(f"主要聊了 {heads}。")
    parts.append(
        f"最热闹的时段在 {stats.peak_hour:02d} 点前后（{stats.peak_count} 条）。"
    )
    if night > 0:
        parts.append(f"夜里还有 {night} 条消息，夜猫子照常营业。")
    return "".join(parts)


def build_fallback_report(
    *,
    report_date: str,
    room_name: str,
    bot_name: str,
    page_title: str,
    stats: NotebookStats,
    messages: list[dict[str, Any]],
    config: "GroupNotebookConfig",
    reason: str = "",
) -> NotebookReport:
    """在无模型或模型失败时生成一份仍然可读的摘录。"""
    analysis = config.analysis
    candidates = pick_quote_candidates(messages, limit=max(analysis.quote_count, 3))
    quotes = [
        QuoteItem(
            sender=item["name"],
            text=item["text"],
            comment="这句被记在了页角，适合下班路上反复回味。",
        )
        for item in candidates[: analysis.quote_count]
    ]
    rng = random.Random(f"{room_name}:{report_date}")
    fallback_topics = _fallback_topics(
        messages, timezone_name=config.general.timezone, limit=analysis.topic_count
    )
    return NotebookReport(
        date=report_date,
        room_name=room_name,
        bot_name=bot_name,
        page_title=page_title,
        stats=stats,
        greeting=rng.choice(_FALLBACK_GREETINGS),
        topics=fallback_topics,
        recap=_fallback_recap(stats, fallback_topics, messages, config.general.timezone),
        portraits=_fallback_portraits(
            messages,
            limit=analysis.portrait_count,
            room_seed=f"{room_name}:{report_date}",
            timezone_name=config.general.timezone,
        ),
        quotes=quotes,
        mood=_fallback_mood(stats),
        coffee=_FALLBACK_COFFEE,
        murmur=_FALLBACK_MURMUR,
        source="fallback" + (f":{reason}" if reason else ""),
        generated_at=datetime.now(resolve_timezone(config.general.timezone)).isoformat(
            timespec="seconds"
        ),
    )


async def analyze_day(
    *,
    report_date: str,
    room_name: str,
    bot_name: str,
    page_title: str,
    stats: NotebookStats,
    messages: list[dict[str, Any]],
    transcript: str,
    config: "GroupNotebookConfig",
) -> NotebookReport:
    """生成摘录文案：优先 LLM，失败时回退规则版。"""
    analysis = config.analysis
    notebook = config.notebook
    fallback = build_fallback_report(
        report_date=report_date,
        room_name=room_name,
        bot_name=bot_name,
        page_title=page_title,
        stats=stats,
        messages=messages,
        config=config,
    )
    if not transcript.strip():
        return fallback

    known_names = list(_member_index(messages).keys())
    system_prompt, user_prompt = build_prompt(
        report_date=report_date,
        room_name=room_name,
        bot_name=bot_name,
        stats=stats,
        transcript=transcript,
        config=config,
        member_lines=_build_member_lines(stats),
    )
    # 模型偶发返回不带 JSON / 被截断的内容，失败就再要一次，仍然不行才退回规则版。
    # 第二次尝试会把聊天记录砍半，避免小模型/慢模型在长提示词上反复超时。
    data: dict[str, Any] | None = None
    last_error: Exception | None = None
    timeout = float(getattr(analysis, "llm_timeout_seconds", 240) or 240)
    transcript_lines = transcript.splitlines()
    for attempt in range(2):
        prompt_for_attempt = user_prompt
        if attempt == 1 and len(transcript_lines) > 40:
            half = transcript_lines[len(transcript_lines) // 2 :]
            prompt_for_attempt = user_prompt.replace(
                transcript, "\n".join(half)
            ) if transcript in user_prompt else user_prompt
            logger.info("LLM 首次尝试失败，已把聊天记录砍半重试")
        try:
            raw = await _request_llm(
                system_prompt, prompt_for_attempt, analysis.model_task, timeout=timeout
            )
        except Exception as exc:  # 模型未配置 / 超时 / 限流 / 账号问题
            last_error = exc
            logger.warning(f"LLM 分析失败（第 {attempt + 1} 次）：{type(exc).__name__}: {exc}")
            continue
        data = _split_json(raw)
        if data:
            break
        logger.warning("LLM 返回内容无法解析为 JSON")

    if not data:
        if last_error is not None:
            fallback.source = f"fallback:llm_error:{type(last_error).__name__}"
            fallback.llm_error = str(last_error)[:200]
        else:
            fallback.source = "fallback:bad_json"
        return fallback

    topics = _coerce_topics(
        data.get("topics"), known_names=known_names, limit=analysis.topic_count
    )
    portraits = _coerce_portraits(
        data.get("portraits"), known_names=known_names, limit=analysis.portrait_count
    )
    quotes = _coerce_quotes(
        data.get("quotes"),
        known_names=known_names,
        candidates=pick_quote_candidates(messages, limit=max(analysis.quote_count, 3)),
        transcript_normalized=_normalize(transcript),
        limit=analysis.quote_count,
    )
    mood = _coerce_mood(data.get("mood"))

    if not topics or not portraits:
        fallback.source = "fallback:incomplete_json"
        return fallback

    if not mood.dimensions:
        mood = fallback.mood
    if not mood.title:
        mood.title = "今日聊天天气"

    return NotebookReport(
        date=report_date,
        room_name=room_name,
        bot_name=bot_name,
        page_title=notebook.page_title or page_title,
        stats=stats,
        greeting=clamp_text(strip_markup(data.get("greeting")), 20) or fallback.greeting,
        topics=topics,
        recap=clamp_text(strip_markup(data.get("recap")), 220) or fallback.recap,
        portraits=portraits,
        quotes=quotes or fallback.quotes,
        mood=mood,
        coffee=clamp_text(strip_markup(data.get("coffee")), 80) or fallback.coffee,
        murmur=clamp_text(strip_markup(data.get("murmur")), 60) or fallback.murmur,
        source="llm",
        generated_at=datetime.now(resolve_timezone(config.general.timezone)).isoformat(
            timespec="seconds"
        ),
    )
