"""离线渲染演示：构造假数据直接出一张手账，不启动机器人。

用法（在开发 venv 中）::

    python scripts/demo_render.py

输出 ``demo_out/demo_report.png`` 与 ``demo_out/demo_report.html``。
截图依赖 playwright；默认走 msedge 通道复用系统 Edge，没有就回退内置 chromium。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN_ROOT.parent))

from group_notebook.models import (  # noqa: E402
    Mood,
    MoodDimension,
    NotebookReport,
    NotebookStats,
    Portrait,
    QuoteItem,
    Topic,
)
from group_notebook.renderer import NotebookRenderer  # noqa: E402


def build_config() -> SimpleNamespace:
    """离线演示配置：结构与 GroupNotebookConfig.notebook 对齐，不依赖框架。"""
    return SimpleNamespace(
        notebook=SimpleNamespace(
            room_name="示例群 · 演示用",
            bot_name="安安",
            page_title="",
            brand_text="GROUP NOTEBOOK",
            stamp_text="今日答案：完成 × 1",
            time_section_title="哪一页最热闹",
            topic_section_title="值得夹个书签的话题",
            recap_enabled=True,
            recap_section_title="今日总结",
            people_section_title="群友的这一页侧写",
            quote_section_title="这几句，抄在页角",
            mood_section_title="这一页的聊天天气",
            coffee_enabled=True,
            murmur_enabled=True,
            character_path="",
            browser_channel="msedge",
            viewport_scale=2,
            render_timeout_seconds=90,
        )
    )


def build_report() -> NotebookReport:
    """构造一份与真实输出同构的示例数据。"""
    stats = NotebookStats(
        date="2026-11-05",
        message_count=300,
        member_count=24,
        char_count=3577,
        emoji_count=27,
        hourly=[8, 7, 5, 0, 3, 0, 2, 0, 11, 20, 130, 12, 7, 9, 6, 8, 5, 12, 10, 14, 13, 14, 11, 6],
        top_members=[
            {"name": "龙套先生", "count": 52},
            {"name": "表情包军火库", "count": 41},
            {"name": "省流大师", "count": 33},
            {"name": "互动达人", "count": 27},
            {"name": "潜水闪烁者", "count": 18},
            {"name": "独白记录员", "count": 15},
        ],
        peak_hour=10,
        peak_count=130,
        avg_chars=11.9,
    )
    return NotebookReport(
        date="2026-11-05",
        room_name="示例群 · 演示用",
        bot_name="安安",
        page_title="安安的群聊摘录",
        stats=stats,
        greeting="你们聊过的事情，都记在这里了。这么高一册，也是一册的结尾。",
        topics=[
            Topic(
                title="异环抽卡与角色强度之争",
                contributors=["龙套先生", "表情包军火库"],
                detail=(
                    "[龙套先生] 分享了大保底出金的新角色零命伤害测算，直言现环境深渊满星很难，"
                    "纠结要不要为真爱硬抽二命。[表情包军火库] 全程用表情包参战，"
                    "最后全群达成共识：强度是版本的，老婆是自己的。"
                ),
            ),
            Topic(
                title="C 盘空间清理实战",
                contributors=["省流大师", "互动达人"],
                detail=(
                    "[互动达人] 的 C 盘又红了，[省流大师] 推荐先用 WizTree 找大文件，"
                    "再用 Geek Uninstaller 和 Dism++ 深度清理，顺手附上了新缓存迁移方案。"
                    "三天后他回来汇报：成功清出 60G，全群为他鼓掌。"
                ),
            ),
            Topic(
                title="新键盘到货，全群围观开箱",
                contributors=["独白记录员", "评论家"],
                detail=(
                    "[独白记录员] 晒了刚到的机械键盘和鼠标垫，[评论家] 从轴体润丝聊到键帽材质，"
                    "全群围绕手感争论了半小时，最后以「先别买，等双十一」收场。"
                ),
            ),
            Topic(
                title="深夜 emo 电台照常营业",
                contributors=["潜水闪烁者"],
                detail=(
                    "十二点之后 [潜水闪烁者] 准时上线，发了一首冷门歌，接了两句人生感悟，"
                    "被 [表情包军火库] 用表情包糊脸后安静下线，本子替他记一笔。"
                ),
            ),
        ],
        portraits=[
            Portrait(name="龙套先生", title="龙王", mbti="ENFP", tag="话痨",
                     desc="发言总数 52 条，几乎每个话题都有他。今天主要在聊新角色强度，顺手接了所有人的梗。"),
            Portrait(name="表情包军火库", title="斗图之王", mbti="ESFP", tag="斗图",
                     desc="发言总数 41 条但字数不多，三七开都靠表情包。盘点了一下，本子都替他贴了 18 张贴纸。"),
            Portrait(name="省流大师", title="省流大师", mbti="INTJ", tag="技术流",
                     desc="先把三十层楼压缩成三行结论，再附一个传送门。今天用三句话救了三个人的 C 盘。"),
            Portrait(name="互动达人", title="互动达人", mbti="ENFJ", tag="捧场",
                     desc="每个人的消息都有回应，接话永远在最合适的时机。今天贡献了 27 条高质量捧场。"),
            Portrait(name="潜水闪烁者", title="深海生物", mbti="INFP", tag="夜猫子",
                     desc="白天几乎不存在，深夜准时上线，发言像深海生物的生物荧光，一闪一闪。"),
            Portrait(name="独白记录员", title="独白记录员", mbti="ISTJ", tag="记录",
                     desc="喜欢用长句独白，从不@任何人，但每一条都被认真读完。今天记录了开箱全过程。"),
            Portrait(name="评论家", title="全场嘴替", mbti="ENTP", tag="锐评",
                     desc="平均发言长度三十字以上，喜欢边看边下判断。今天对抽卡玄学发表了大段锐评。"),
            Portrait(name="气氛组组长", title="哈哈哈哈机", mbti="ESFJ", tag="捧哏",
                     desc="话题冷场时总会冒泡，今天贡献了本群三分之一的「哈哈哈哈」。"),
        ],
        quotes=[
            QuoteItem(
                sender="省流大师",
                text="省流：先别买，等双十一，钱包再打开就关不上了。",
                comment="逻辑闭环，无懈可击，但 [龙套先生] 显然没听进去。",
            ),
            QuoteItem(
                sender="潜水闪烁者",
                text="我的薄荷不是没水培，但我一直按水培在养。",
                comment="深海生物的发言，白天看不懂，凌晨两点看泪目。",
            ),
            QuoteItem(
                sender="龙套先生",
                text="今晚先救队友，明显再早点就上发射了。",
                comment="宣言写得像战书，落地只差一个闹钟。",
            ),
        ],
        mood=Mood(
            title="热烈且滴水不漏",
            subtitle="按消息占比分成的几种天气",
            dimensions=[
                MoodDimension(
                    name="热烈共鸣",
                    percentage=45,
                    comment="围绕新角色的厨力对轰从上午烧到中午，人与人之间的网络拉扯一发不可收拾。",
                ),
                MoodDimension(
                    name="数据拆解",
                    percentage=25,
                    comment="伤害测算、清理工具、缓存迁移，今天的技术含量全靠两个人扛。",
                ),
                MoodDimension(
                    name="虚实实境",
                    percentage=15,
                    comment="深夜电台准时开播，孤独感按小时计费，本子只好陪他熬夜。",
                ),
                MoodDimension(
                    name="天聊水群",
                    percentage=15,
                    comment="剩下的都是日常水花，温和，但撑起了这一页的厚度。",
                ),
            ],
            summary="强度党与技术流各占半页，深夜电台照常营业。除了抽卡玄学，其余时间全在认真生活。",
        ),
        recap=(
            "上午从新角色的强度测算开聊，技术流两位随后把话题带去了 C 盘清理，"
            "中午开箱晒物又热闹了一轮；傍晚群里安静下来，深夜十二点电台准时营业，"
            "一句冷门歌词接住了全天的情绪。整体是热闹但不吵的一天，"
            "该干活的时候干活，该 emo 的时候 emo，收放得刚刚好。"
        ),
        coffee="今晚适合一杯温热的拿铁——把抽卡的遗憾和没聊完的话题，一起留给明天冒泡。",
        murmur="今天也把大家的热闹收进本子里了，晚安，明天见。",
        source="llm",
        generated_at="2026-11-05T23:30:12",
    )


async def main() -> int:
    engine = "pillow" if "--engine" in sys.argv and "pillow" in sys.argv else "playwright"
    config = build_config()
    config.notebook.page_title = f"{config.notebook.bot_name}的群聊摘录"
    out_dir = PLUGIN_ROOT / "demo_out"
    out_dir.mkdir(exist_ok=True)
    report = build_report()
    if engine == "pillow":
        from group_notebook.pillow_renderer import PillowNotebookRenderer

        renderer = PillowNotebookRenderer(config, base_dir=PLUGIN_ROOT)
        width, height = await asyncio.to_thread(
            renderer.render, report, out_dir / "demo_report.png"
        )
        print(f"OK: {out_dir / 'demo_report.png'}（pillow 引擎，{width}x{height}）")
        return 0
    renderer = NotebookRenderer(config, base_dir=PLUGIN_ROOT)
    try:
        width, _height = await renderer.render(
            report, out_dir / "demo_report.png", out_dir / "demo_report.html"
        )
    finally:
        await renderer.shutdown()
    print(f"OK: {out_dir / 'demo_report.png'}（playwright 引擎，逻辑宽度 {width}px）")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
