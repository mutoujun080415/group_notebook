"""群聊摘录插件配置定义。

配置写入 ``config/plugins/group_notebook/config.toml``，由 Neo-MoFox
配置管理器在插件加载时自动生成与热更新。
"""

from __future__ import annotations

from typing import ClassVar

from src.app.plugin_system.base import BaseConfig, Field, SectionBase, config_section


class GroupNotebookConfig(BaseConfig):
    """群聊摘录插件运行配置。"""

    name: ClassVar[str] = "config"
    description: ClassVar[str] = "群聊摘录（Group Notebook）手账长图生成与定时投递配置"

    @config_section("general", title="常规设置", tag="general")
    class GeneralSection(SectionBase):
        """调度与投递设置。"""

        enabled: bool = Field(
            default=True,
            description="是否启用群聊摘录",
            label="启用插件",
        )
        timezone: str = Field(
            default="Asia/Shanghai",
            description="计算摘录日期与发送时间使用的时区",
            label="时区",
        )
        groups: list[str] = Field(
            default_factory=list,
            description="要投递摘录的群号列表；为空时只允许手动 /摘录 触发",
            label="群号列表",
        )
        platform: str = Field(
            default="qq",
            description="群号所属平台标识，用于生成聊天流 ID 与发送消息",
            label="平台",
        )
        group_aliases: list[str] = Field(
            default_factory=list,
            description="群号到群名的别名，格式为 群号=群名，例如 123456=大肥鱼值班室",
            label="群名别名",
        )
        send_time: str = Field(
            default="23:30",
            description="每天自动发送摘录的时间，格式为 HH:MM",
            label="发送时间",
        )
        auto_send: bool = Field(
            default=False,
            description="是否在到达发送时间后自动生成并发送摘录；与群日报插件并存时注意别重复投递",
            label="自动发送",
        )
        check_interval_seconds: int = Field(
            default=60,
            description="后台检查发送时间的间隔秒数，不建议低于 30",
            label="检查间隔",
        )
        command_permission: str = Field(
            default="user",
            description="手动 /摘录 命令所需权限：guest / user / operator / owner",
            label="命令权限",
        )
        send_timeout_seconds: int = Field(
            default=120,
            description="发送图片的超时秒数，超时只报失败不会卡住整个任务",
            label="发送超时",
        )

    general: GeneralSection = Field(default_factory=GeneralSection)

    @config_section("analysis", title="分析设置", tag="analysis")
    class AnalysisSection(SectionBase):
        """聊天记录分析与文案生成设置。"""

        model_task: str = Field(
            default="utils",
            description="用于生成摘录文案的模型任务名（config/model.toml 中的 model_tasks）；"
            "小模型跑长提示词容易超时，可换成能力更强/更稳定的任务（如 actor）",
            label="模型任务",
        )
        max_messages: int = Field(
            default=1200,
            description="单次分析最多读取的消息条数，超出时按时间均匀抽样",
            label="最大消息数",
        )
        message_char_limit: int = Field(
            default=160,
            description="单条消息进入提示词的最大字符数",
            label="单条截断长度",
        )
        transcript_budget: int = Field(
            default=12000,
            description="聊天记录进入提示词的字符预算；小模型建议 8000-12000，首次失败会自动砍半重试",
            label="提示词预算",
        )
        topic_count: int = Field(
            default=4,
            description="生成的今日话题数量",
            label="话题数量",
        )
        portrait_count: int = Field(
            default=8,
            description="生成的群友侧写数量",
            label="侧写数量",
        )
        quote_count: int = Field(
            default=3,
            description="生成的页角金句数量",
            label="金句数量",
        )
        include_bot: bool = Field(
            default=True,
            description="统计与提示词中是否包含机器人自己的消息",
            label="包含机器人消息",
        )
        persona: str = Field(
            default=(
                "你是一册手账的整理者：观察细、记性好、语气轻松，"
                "写东西像在给群友留纪念，不端水、不说教、不堆形容词。"
            ),
            description="生成文案时使用的人设提示",
            label="人设提示",
        )
        custom_prompt: str = Field(
            default="",
            description="附加在系统提示末尾的额外要求，留空使用内置要求",
            label="附加提示",
        )
        llm_timeout_seconds: int = Field(
            default=240,
            description="单次模型调用的超时秒数；超时会砍半提示词重试一次，再不行走规则回退",
            label="模型超时",
        )

    analysis: AnalysisSection = Field(default_factory=AnalysisSection)

    @config_section("notebook", title="手账设置", tag="notebook")
    class NotebookSection(SectionBase):
        """手账页面外观设置。"""

        room_name: str = Field(
            default="本群",
            description="页面顶部展示的群名，可按群别名覆盖",
            label="群名",
        )
        bot_name: str = Field(
            default="安安",
            description="手账所有者的名字（机器人自己）",
            label="机器人名",
        )
        page_title: str = Field(
            default="",
            description="手账主标题；留空时自动使用「{bot_name}的群聊摘录」",
            label="主标题",
        )
        brand_text: str = Field(
            default="GROUP NOTEBOOK",
            description="页面顶部的小字品牌行",
            label="品牌行",
        )
        stamp_text: str = Field(
            default="今日答案：完成 × 1",
            description="标题下的小印章文字；留空则不显示",
            label="印章文字",
        )
        time_section_title: str = Field(
            default="哪一页最热闹",
            description="24 小时活跃度小节的标题",
            label="时间小节标题",
        )
        topic_section_title: str = Field(
            default="值得夹个书签的话题",
            description="话题小节的标题",
            label="话题小节标题",
        )
        recap_enabled: bool = Field(
            default=True,
            description="是否显示「今日总结」小节（一整段聊天内容回顾）",
            label="显示今日总结",
        )
        recap_section_title: str = Field(
            default="今日总结",
            description="今日总结小节的标题",
            label="今日总结标题",
        )
        people_section_title: str = Field(
            default="群友的这一页侧写",
            description="群友侧写小节的标题",
            label="侧写小节标题",
        )
        quote_section_title: str = Field(
            default="这几句，抄在页角",
            description="金句小节的标题",
            label="金句小节标题",
        )
        mood_section_title: str = Field(
            default="这一页的聊天天气",
            description="聊天天气小节的标题",
            label="天气小节标题",
        )
        coffee_enabled: bool = Field(
            default=True,
            description="是否显示「{bot_name}的咖啡推荐」小节",
            label="显示咖啡推荐",
        )
        murmur_enabled: bool = Field(
            default=True,
            description="是否显示「{bot_name}的碎碎念」小节",
            label="显示碎碎念",
        )
        character_path: str = Field(
            default="",
            description="右侧立绘图片路径（绝对路径，或相对插件目录）；留空则不显示立绘（WebUI 上传的封面优先）",
            label="立绘路径",
        )
        render_engine: str = Field(
            default="auto",
            description="渲染引擎：auto（有浏览器用 Playwright，否则 Pillow）/ playwright / pillow",
            label="渲染引擎",
        )
        browser_channel: str = Field(
            default="msedge",
            description="截图使用的浏览器通道：msedge / chrome / 留空用 Playwright 自带 chromium",
            label="浏览器通道",
        )
        viewport_scale: int = Field(
            default=2,
            description="截图倍率（deviceScaleFactor），2 表示输出 1600px 宽的清晰长图",
            label="截图倍率",
        )
        render_timeout_seconds: int = Field(
            default=90,
            description="单次截图渲染的超时秒数",
            label="渲染超时",
        )

    notebook: NotebookSection = Field(default_factory=NotebookSection)

    @config_section("storage", title="存储设置", tag="storage")
    class StorageSection(SectionBase):
        """摘录产物留存设置。"""

        keep_days: int = Field(
            default=30,
            description="本地保留多少天的摘录图片与 JSON，0 表示不清理",
            label="保留天数",
        )

    storage: StorageSection = Field(default_factory=StorageSection)

    @config_section("webui", title="WebUI 设置", tag="webui")
    class WebUISection(SectionBase):
        """独立页面设置（不依赖官方 Plugin UI）。"""

        mount_on_main_http: bool = Field(
            default=True,
            description=(
                "是否把独立页面挂到主程序 HTTP 服务器（改了需要重载插件才生效）"
            ),
            label="挂载独立页面",
        )
        route_path: str = Field(
            default="/plugins/group-notebook",
            description="独立页面挂载到主程序 HTTP 服务器时使用的子路径",
            label="独立页面路径",
        )
        default_limit: int = Field(
            default=30,
            description="页面默认展示多少条历史摘录",
            label="历史条数",
        )

    webui: WebUISection = Field(default_factory=WebUISection)

    def resolved_page_title(self) -> str:
        """返回最终的主标题：未配置时按机器人名自动生成。"""
        title = (self.notebook.page_title or "").strip()
        if title:
            return title
        bot_name = (self.notebook.bot_name or "").strip() or "安安"
        return f"{bot_name}的群聊摘录"
