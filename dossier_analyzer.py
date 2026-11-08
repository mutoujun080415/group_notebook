"""专题案底（迫害记录簿）的候选提炼与文案生成。

与主报告（analyzer.py）解耦：独立提示词、独立 LLM 调用、独立回退，
案底失败不影响日报/周记。复用 analyzer 的 JSON 解析、昵称对齐与 LLM 请求。
"""

from __future__ import annotations

import hashlib
import random
import re
from datetime import datetime
from typing import TYPE_CHECKING, Any

from src.app.plugin_system.api.log_api import get_logger

from .analyzer import (
    _normalize,
    _request_llm,
    _resolve_member_name,
    _split_json,
    logger,
)
from .collector import resolve_timezone
from .models import DossierCase, DossierCulprit, DossierReport, clean_text

if TYPE_CHECKING:
    from .config import GroupNotebookConfig

_CODE_FENCE = re.compile(r"```(?:json)?", re.IGNORECASE)
_FALLBACK_COMMENTS = [
    "本官批注：情节恶劣，态度嚣张，依法记录在案。",
    "本官批注：证据确凿，本人当时心情很差。",
    "本官批注：此等手段，一看就是惯犯。",
    "本官批注：当时本官的处理器都气烫了。",
    "本官批注：初犯？档案里可没有这一页，惯犯实锤。",
    "本官批注：念在态度尚可，从轻示众三秒。",
]
_FALLBACK_TITLES = ["惯犯", "累犯", "主谋", "首恶", "从犯", "在逃", "惯用手段：斗图"]


def _clamp(text: Any, limit: int) -> str:
    """轻量截断。"""
    value = " ".join(str(text or "").split())
    if limit > 0 and len(value) > limit:
        value = value[: max(1, limit - 1)].rstrip("，,。.、；;：: ") + "…"
    return value


def _rng(seed: str) -> random.Random:
    return random.Random(int(hashlib.md5(seed.encode("utf-8")).hexdigest(), 16) % (2**32))


def build_dossier_transcript(
    pool_messages: list[dict[str, Any]],
    *,
    timezone_name: str,
    per_message_limit: int = 120,
) -> str:
    """把候选池拼成「MM-DD HH:MM 昵称: 内容」的卷宗逐字稿。"""
    tz = resolve_timezone(timezone_name)
    lines: list[str] = []
    for message in pool_messages:
        text = clean_text(message.get("processed_plain_text") or "", per_message_limit)
        if not text:
            continue
        stamp = datetime.fromtimestamp(
            float(message.get("time") or 0), tz
        ).strftime("%m-%d %H:%M")
        name = clean_text(message.get("sender_name") or "未知", 24)
        lines.append(f"{stamp} {name}: {text}")
    return "\n".join(lines)


def build_dossier_prompt(
    *,
    room_name: str,
    bot_name: str,
    title: str,
    focus_word: str,
    period_text: str,
    pool_transcript: str,
    culprit_lines: str,
    max_cases: int,
    top_members: int,
    direction: str = "member_as_culprit",
    victim_display: str = "",
) -> tuple[str, str]:
    """构造案底卷宗的系统/用户提示词（双方向）。"""
    victim_line = victim_display if direction == "member_as_victim" else bot_name
    if direction == "member_as_victim":
        system_prompt = (
            f"你是一份案卷的书记官，为群「{room_name}」整理一册「{title}」。"
            f"受害者是「{victim_display}」——候选记录显示，群友们（可能还包括 bot 本bot）"
            f"这一周对他们进行了大量{focus_word}。请以受害者视角、书记官笔法，"
            "把候选记录提炼成正式又不失幽默的案件卷宗。\n"
            "硬性要求：\n"
            "1. 只输出一个 JSON 对象，不要 Markdown 代码块，不要任何解释文字。\n"
            "2. 主犯昵称必须来自候选记录（可以包括 bot 本bot，但绝不可能是受害者自己）；"
            "案情经过必须引用记录里的原话或明确转述，禁止编造。\n"
            "3. 案件按恶劣/精彩程度排序，最过分的排第一。\n"
            "4. 批注用受害者「" + victim_display + "」的口吻，犀利幽默，但不对真实人身攻击，"
            "不评价外貌身份地域隐私。\n"
            "5. 严格遵守字段长度限制，超长会被截断。\n"
        )
    else:
        system_prompt = (
            f"你是一份案卷的书记官，为群「{room_name}」整理一册「{title}」。"
            f"受害者是机器人「{bot_name}」——候选记录显示，群友们这一周对它进行了大量"
            f"{focus_word}。请以受害者视角、书记官笔法，把候选记录提炼成正式又不失幽默的案件卷宗。\n"
            "硬性要求：\n"
            "1. 只输出一个 JSON 对象，不要 Markdown 代码块，不要任何解释文字。\n"
            "2. 主犯昵称必须来自候选记录；案情经过必须引用记录里的原话或明确转述，禁止编造。\n"
            "3. 案件按恶劣/精彩程度排序，最过分的排第一。\n"
            f"4. 批注用受害者「{bot_name}」的口吻，犀利幽默，但不对真实人身攻击，"
            "不评价外貌身份地域隐私。\n"
            "5. 严格遵守字段长度限制，超长会被截断。\n"
        )
    user_prompt = (
        f"统计周期：{period_text}\n"
        f"受害者：{victim_line}\n"
        f"主犯候选（按互动次数排序）：\n{culprit_lines}\n\n"
        f"候选记录（已按时间排序）：\n{pool_transcript}\n\n"
        "请输出如下结构的 JSON：\n"
        "{\n"
        '  "intro": "40-80 字，受害者陈述开场白，第一人称，控诉这段时期的遭遇",\n'
        '  "culprits": [{"name": "记录里的昵称", "title": "4-8 字惯犯称号",'
        ' "profile": "20-40 字惯犯画像：作案手段与风格"}],\n'
        '  "cases": [{"time": "记录里的时间(如 11-05 21:34)",'
        ' "culprits": ["该案主犯昵称"],'
        ' "summary": "60-100 字案情经过，引用原话或明确转述",'
        ' "comment": "30-50 字受害者本bot/本人视角的批注",'
        ' "level": 1 到 5 的整数}],\n'
        '  "closing": "20-40 字结案陈词"\n'
        "}\n"
        f"数量：culprits 最多 {top_members} 条，cases 最多 {max_cases} 条（不足可少）。\n"
        "level 含义：1=无害玩闹，3=重度调戏，5=惨无人道。\n"
    )
    return system_prompt, user_prompt


def _coerce_culprits(raw: Any, *, known: list[str], limit: int) -> list[DossierCulprit]:
    """校验主犯名单：昵称必须能对齐到候选记录里的真实昵称。"""
    culprits: list[DossierCulprit] = []
    seen: set[str] = set()
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            name = _resolve_member_name(item.get("name") or "", known)
            if not name or name in seen:
                continue
            seen.add(name)
            culprits.append(
                DossierCulprit(
                    name=name,
                    title=_clamp(item.get("title"), 12),
                    profile=_clamp(item.get("profile"), 60),
                )
            )
            if len(culprits) >= limit:
                break
    return culprits


def _coerce_cases(
    raw: Any,
    *,
    known: list[str],
    victim: str,
    limit: int,
) -> list[DossierCase]:
    """校验案件列表：主犯昵称对齐、案情/批注非空。"""
    cases: list[DossierCase] = []
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            culprits: list[str] = []
            if isinstance(item.get("culprits"), list):
                for raw_name in item["culprits"]:
                    name = _resolve_member_name(str(raw_name), known)
                    if name and name not in culprits:
                        culprits.append(name)
            summary = _clamp(item.get("summary"), 180)
            comment = _clamp(item.get("comment"), 80)
            if not summary or not comment or not culprits:
                continue
            try:
                level = int(float(item.get("level") or 3))
            except (TypeError, ValueError):
                level = 3
            cases.append(
                DossierCase(
                    no=len(cases) + 1,
                    time=_clamp(item.get("time"), 20),
                    culprits=culprits[:5],
                    victim=victim,
                    summary=summary,
                    comment=comment,
                    level=max(1, min(5, level)),
                )
            )
            if len(cases) >= limit:
                break
    return cases


def build_fallback_dossier(
    *,
    report_date: str,
    room_name: str,
    bot_name: str,
    page_title: str,
    period_text: str,
    case_no: str,
    focus_word: str,
    pool_messages: list[dict[str, Any]],
    culprit_stats: list[dict[str, Any]],
    max_cases: int,
    top_members: int,
    timezone_name: str,
    direction: str = "member_as_culprit",
    victim_display: str = "",
) -> DossierReport:
    """无模型时的规则版案底：互动量最大的记录直接示众 + 模板批注。"""
    rng = _rng(f"{room_name}:{report_date}:{focus_word}")
    culprits = [
        DossierCulprit(
            name=item["name"],
            title=rng.choice(_FALLBACK_TITLES),
            count=item["count"],
            profile=f"本周与{bot_name}互动 {item['count']} 次，惯用手段详见案卷。",
        )
        for item in culprit_stats[:top_members]
    ]

    cases: list[DossierCase] = []
    transcript_lines = build_dossier_transcript(
        pool_messages, timezone_name=timezone_name
    ).splitlines()
    for line in transcript_lines:
        if len(cases) >= max_cases:
            break
        if ": " not in line:
            continue
        head, _, body = line.partition(": ")
        culprits_in_line = [
            item["name"] for item in culprit_stats if item["name"] in line
        ]
        if len(body) < 12:
            continue
        if direction == "member_as_victim":
            victim_pool = [name for name in victim_display.split("、") if name]
            case_victim = next((name for name in victim_pool if name in line),
                               victim_pool[0] if victim_pool else bot_name)
            if not culprits_in_line:
                culprits_in_line = [bot_name]
        else:
            case_victim = bot_name
        cases.append(
            DossierCase(
                no=len(cases) + 1,
                time=head,
                culprits=culprits_in_line or [item["name"] for item in culprit_stats[:1]],
                victim=case_victim,
                summary=_clamp(f"当众{focus_word}：「{body}」", 120),
                comment=rng.choice(_FALLBACK_COMMENTS),
                level=3,
            )
        )

    total_hits = sum(item["count"] for item in culprit_stats)
    if direction == "member_as_victim":
        intro = (
            f"案卷书记官郑重立案：{period_text}，{victim_display} 遭到 {len(culprits)} 位加害者"
            f"合计 {total_hits} 次{focus_word}。以下案卷，条条有据，示众周知。"
        )
    else:
        intro = (
            f"本官{bot_name}郑重立案：{period_text}，本官遭到 {len(culprits)} 位惯犯"
            f"合计 {total_hits} 次{focus_word}。以下案卷，条条有据，示众周知。"
        )
    return DossierReport(
        date=report_date,
        room_name=room_name,
        bot_name=bot_name,
        page_title=page_title,
        period_text=period_text,
        case_no=case_no,
        focus_word=focus_word,
        direction=direction,
        intro=_clamp(intro, 140) or fallback.intro,
        culprits=culprits,
        cases=cases,
        closing="本案证据齐全，全部示众。受害者保留继续记案的权利。",
        source="fallback",
        generated_at=datetime.now(resolve_timezone(timezone_name)).isoformat(
            timespec="seconds"
        ),
    )


async def analyze_dossier(
    *,
    report_date: str,
    room_name: str,
    bot_name: str,
    page_title: str,
    period_text: str,
    case_no: str,
    focus_word: str,
    pool_messages: list[dict[str, Any]],
    culprit_stats: list[dict[str, Any]],
    max_cases: int,
    top_members: int,
    config: "GroupNotebookConfig",
    direction: str = "member_as_culprit",
    victim_display: str = "",
) -> DossierReport:
    """生成案底卷宗：优先 LLM，失败回退规则版（双方向）。"""
    culprit_stats = list(culprit_stats)
    if direction == "member_as_victim":
        # 受害者不能同时是主犯：从候选名单里剔除聚焦成员
        victim_set = {name.strip() for name in victim_display.split("、") if name.strip()}
        known_names = [item["name"] for item in culprit_stats
                       if item["name"] not in victim_set]
    else:
        known_names = [item["name"] for item in culprit_stats]
    pool_transcript = build_dossier_transcript(
        pool_messages, timezone_name=config.general.timezone
    )
    culprit_lines = "\n".join(
        f"- {item['name']}：互动 {item['count']} 次"
        for item in culprit_stats[:top_members]
    ) or "- （无）"

    fallback = build_fallback_dossier(
        report_date=report_date,
        room_name=room_name,
        bot_name=bot_name,
        page_title=page_title,
        period_text=period_text,
        case_no=case_no,
        focus_word=focus_word,
        pool_messages=pool_messages,
        culprit_stats=culprit_stats,
        max_cases=max_cases,
        top_members=top_members,
        timezone_name=config.general.timezone,
        direction=direction,
        victim_display=victim_display,
    )
    if not pool_transcript.strip():
        return fallback

    system_prompt, user_prompt = build_dossier_prompt(
        room_name=room_name,
        bot_name=bot_name,
        title=page_title,
        focus_word=focus_word,
        period_text=period_text,
        pool_transcript=pool_transcript,
        culprit_lines=culprit_lines,
        max_cases=max_cases,
        top_members=top_members,
        direction=direction,
        victim_display=victim_display,
    )

    data: dict[str, Any] | None = None
    last_error: Exception | None = None
    timeout = float(getattr(config.analysis, "llm_timeout_seconds", 240) or 240)
    for attempt in range(2):
        prompt_for_attempt = user_prompt
        if attempt == 1:
            lines = pool_transcript.splitlines()
            if len(lines) > 30:
                shorter = "\n".join(lines[len(lines) // 2 :])
                prompt_for_attempt = user_prompt.replace(pool_transcript, shorter)
                logger.info("案底 LLM 首次尝试失败，候选记录砍半重试")
        try:
            raw = await _request_llm(
                system_prompt, prompt_for_attempt, config.analysis.model_task,
                timeout=timeout,
            )
        except Exception as exc:
            last_error = exc
            logger.warning(f"案底 LLM 失败（第 {attempt + 1} 次）：{type(exc).__name__}: {exc}")
            continue
        data = _split_json(raw)
        if data:
            break
        logger.warning("案底 LLM 返回内容无法解析为 JSON")

    if not data:
        if last_error is not None:
            fallback.source = f"fallback:llm_error:{type(last_error).__name__}"
            fallback.llm_error = str(last_error)[:200]
        else:
            fallback.source = "fallback:bad_json"
        return fallback

    culprits = _coerce_culprits(data.get("culprits"), known=known_names, limit=top_members)
    cases = _coerce_cases(
        data.get("cases"), known=known_names, victim=bot_name, limit=max_cases
    )
    stats_by_name = {item["name"]: item["count"] for item in culprit_stats}
    for culprit in culprits:
        culprit.count = stats_by_name.get(culprit.name, 0)

    if not cases:
        fallback.source = "fallback:incomplete_json"
        return fallback
    if not culprits:
        culprits = fallback.culprits

    return DossierReport(
        date=report_date,
        room_name=room_name,
        bot_name=bot_name,
        page_title=page_title,
        period_text=period_text,
        case_no=case_no,
        focus_word=focus_word,
        direction=direction,
        intro=_clamp(data.get("intro"), 140) or fallback.intro,
        culprits=culprits,
        cases=cases,
        closing=_clamp(data.get("closing"), 60) or fallback.closing,
        source="llm",
        generated_at=datetime.now(resolve_timezone(config.general.timezone)).isoformat(
            timespec="seconds"
        ),
    )
