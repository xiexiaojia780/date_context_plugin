"""日期上下文插件

提供：
- 公开 API ``date`` / ``date_text``：仅返回**今天**的日期/农历/节日信息，供其他插件调用
- 公开 API ``holiday``：仅今天 的特定节日查询（可选按名称过滤）
- LLM Tool ``query_date``：供模型查询昨天 / 今天 / 明天（或指定 ISO 日期）
- LLM Tool ``query_holiday``：按名称查询特定节日（支持年份或具体日期）
- 可选 Hook 注入（WebUI 中两个独立分组、可分别开关，均默认关闭，避免影响前缀缓存）：
  - ``reply_injection.enabled``：回复模型请求前注入（maisaka.replyer.before_model_request）
  - ``planner_injection.enabled``：Planner 模型请求前注入（maisaka.planner.before_request）
  - 两者均向已有 system 消息之后插入日期轻量上下文（星期/节日/节气/调休，不含公历/农历日期），
    注入内容由「日期」分组的 include_* 开关统一控制

命名规范（公开 API 与 LLM Tool 成对）：
- 公开 API = 资源名：``date`` / ``date_text`` / ``holiday``
- LLM Tool = ``query_`` + 同一资源词根：``query_date`` / ``query_holiday``
- Tool 不与 API 直接同名：两者在 Host 组件表中曾因同名撞名，故 Tool 统一加 ``query_`` 前缀

节日数据来源：
- 农历日期、节气、传统节日落点：``cnlunar``
- 法定节假日放假 / 调休补班判定：``chinese_calendar``（数据有年份覆盖上限，
  超出范围时跳过该行并记录日志）
"""

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import cnlunar
from chinese_calendar import get_holiday_detail, is_workday

from maibot_sdk import Field, HookHandler, MaiBotPlugin, PluginConfigBase
from maibot_sdk.types import ErrorPolicy, HookMode, HookOrder

try:
    from .date_api import (
        DateContextAPIMixin,
        # 集中化的节日数据与纯函数（推荐从 date_api 导入）
        _LUNAR_FESTIVALS,
        _SOLAR_FESTIVALS,
        _LEGAL_EN2CN,
        _WEEKDAY_ZH,
        _get_lunar,
        _lunar_festival_names as _lunar_festival_names_fn,
        _solar_festival_names as _solar_festival_names_fn,
    )
except ImportError:
    # PluginLoader 以文件方式加载时相对导入可能失败，回退到同目录绝对导入
    from date_api import (  # type: ignore
        DateContextAPIMixin,
        _LUNAR_FESTIVALS,
        _SOLAR_FESTIVALS,
        _LEGAL_EN2CN,
        _WEEKDAY_ZH,
        _get_lunar,
        _lunar_festival_names as _lunar_festival_names_fn,
        _solar_festival_names as _solar_festival_names_fn,
    )

# 星期中文映射与 _get_lunar 缓存统一从 date_api 导入（单一真相来源）

# 注意：
# 以下节日数据已集中到 date_api.py 以便 Tool/API/Hook 共享。
# 这里通过导入的符号提供模块级名称，保持 _build_* 内部引用不变。
# 具体定义见 date_api.py 中的 _LUNAR_FESTIVALS / _SOLAR_FESTIVALS / _LEGAL_EN2CN。


class PluginSectionConfig(PluginConfigBase):
    """插件基础配置"""

    __ui_label__ = "插件"
    __ui_icon__ = "package"
    __ui_order__ = 0

    enabled: bool = Field(
        default=True,
        description="是否启用插件",
        json_schema_extra={"label": "启用插件"},
    )
    config_version: str = Field(
        default="1.4.5",
        description="配置版本",
        json_schema_extra={"label": "配置版本"},
    )


class DateInjectionConfig(PluginConfigBase):
    """日期内容设置（API/Tool/Hook 注入共用的信息源开关）"""

    __ui_label__ = "日期"
    __ui_icon__ = "calendar"
    __ui_order__ = 1

    timezone: str = Field(
        default="Asia/Shanghai",
        description="计算当前日期所用的时区（IANA 名称，如 Asia/Shanghai）",
        json_schema_extra={"label": "时区"},
    )
    datetime_format: str = Field(
        default="%Y年%m月%d日",
        description="日期格式（strftime），不含星期（仅 API/Tool）",
        json_schema_extra={"label": "公历日期格式"},
    )
    include_lunar: bool = Field(
        default=True,
        description="是否附带农历日期（仅 API/Tool；Hook 不含农历）",
        json_schema_extra={"label": "附带农历日期"},
    )
    include_traditional_festivals: bool = Field(
        default=True,
        description="是否附带传统农历节日（春节/端午/中秋等）",
        json_schema_extra={"label": "附带农历节日"},
    )
    include_statutory_holidays: bool = Field(
        default=True,
        description="是否附带法定节假日放假/调休补班信息",
        json_schema_extra={"label": "附带法定节假日"},
    )
    include_solar_terms: bool = Field(
        default=True,
        description="是否附带 24 节气信息",
        json_schema_extra={"label": "附带节气"},
    )
    include_western_festivals: bool = Field(
        default=True,
        description="是否附带常见公历/西方节日（情人节/圣诞节等）",
        json_schema_extra={"label": "附带公历/西方节日"},
    )


class ReplyInjectConfig(PluginConfigBase):
    """回复模型请求前注入（独立开关；Hook: maisaka.replyer.before_model_request）

    注入内容由「日期」分组的 include_* 开关控制，与此处开关相互独立。
    """

    __ui_label__ = "回复模型注入"
    __ui_icon__ = "message-square"
    __ui_order__ = 2

    enabled: bool = Field(
        default=False,
        description="是否在回复模型请求前自动注入日期轻量上下文（星期/节日/节气/调休，不含公历/农历日期；内容见「日期」分组；默认关闭；开启可能影响前缀缓存）",
        json_schema_extra={"label": "回复模型请求前注入日期"},
    )


class PlannerInjectConfig(PluginConfigBase):
    """Planner 模型请求前注入（独立开关；Hook: maisaka.planner.before_request）

    注入内容由「日期」分组的 include_* 开关控制，与此处开关相互独立。
    """

    __ui_label__ = "Planner 注入"
    __ui_icon__ = "compass"
    __ui_order__ = 3

    enabled: bool = Field(
        default=False,
        description="是否在 Planner 模型请求前自动注入日期轻量上下文（星期/节日/节气/调休，不含公历/农历日期；内容见「日期」分组；默认关闭；开启可能影响前缀缓存）",
        json_schema_extra={"label": "Planner 请求前注入日期"},
    )


class DateContextPluginConfig(PluginConfigBase):
    """日期上下文插件配置"""

    plugin: PluginSectionConfig = Field(default_factory=PluginSectionConfig)
    date: DateInjectionConfig = Field(default_factory=DateInjectionConfig)
    reply_injection: ReplyInjectConfig = Field(default_factory=ReplyInjectConfig)
    planner_injection: PlannerInjectConfig = Field(default_factory=PlannerInjectConfig)


class DateContextPlugin(DateContextAPIMixin, MaiBotPlugin):
    """日期上下文插件（可选注入 replyer/planner + API 仅今天 + Tool 查日期/节日）"""

    config_model = DateContextPluginConfig

    async def on_load(self) -> None:
        """处理插件加载"""
        self.ctx.logger.info(
            "日期上下文插件已加载（回复模型注入=%s；Planner 注入=%s；"
            "API: date/date_text/holiday（仅今天）；Tool: query_date, query_holiday）"
            % (
                bool(self.config.reply_injection.enabled),
                bool(self.config.planner_injection.enabled),
            )
        )

    async def on_unload(self) -> None:
        """处理插件卸载（本插件无定时任务/连接/文件句柄等需要清理的资源）"""

    async def on_config_update(self, scope: str, config_data: dict[str, Any], version: str) -> None:
        """处理配置热重载事件"""
        del scope
        del config_data
        del version

    @HookHandler(
        "maisaka.replyer.before_model_request",
        name="inject_date_context",
        description="可选：向回复模型请求注入日期轻量上下文（星期/节日/节气/调休）",
        mode=HookMode.BLOCKING,
        order=HookOrder.NORMAL,
        error_policy=ErrorPolicy.SKIP,
    )
    async def inject_date(self, messages: Any = None, **kwargs: Any) -> dict[str, Any] | None:
        """在回复模型请求的已有 system 消息之后注入日期轻量上下文（受配置开关控制）

        Args:
            messages: Host 传入的序列化消息列表
            **kwargs: Hook 透传上下文（不使用）

        Returns:
            dict | None: 改写后的 Hook 结果；未启用注入时返回 ``None``
        """

        del kwargs

        if not self.config.plugin.enabled:
            return None
        if not self.config.reply_injection.enabled:
            return None

        return self._insert_context_into_messages(messages)

    @HookHandler(
        "maisaka.planner.before_request",
        name="inject_date_context_planner",
        description="可选：向 Planner 模型请求注入日期轻量上下文（星期/节日/节气/调休）",
        mode=HookMode.BLOCKING,
        order=HookOrder.NORMAL,
        error_policy=ErrorPolicy.SKIP,
    )
    async def inject_date_planner(self, messages: Any = None, **kwargs: Any) -> dict[str, Any] | None:
        """在 Planner 模型请求的已有 system 消息之后注入日期轻量上下文（受配置开关控制）

        Planner Hook（maisaka.planner.before_request）的消息格式与回复 Hook 一致
        （``[{"role": ..., "content": ...}, ...]``），因此复用同一注入逻辑与缓存。

        Args:
            messages: Host 传入的序列化 PromptMessage 列表
            **kwargs: Hook 透传上下文（tool_definitions / session_id 等，不使用）

        Returns:
            dict | None: 改写后的 Hook 结果（modified_kwargs.messages）；未启用注入时返回 ``None``
        """

        del kwargs

        if not self.config.plugin.enabled:
            return None
        if not self.config.planner_injection.enabled:
            return None

        return self._insert_context_into_messages(messages)

    def _insert_context_into_messages(self, messages: Any) -> dict[str, Any] | None:
        """把日期轻量上下文插入消息列表中已有 system 消息之后（replyer/planner 共用）。

        Args:
            messages: 序列化消息列表（``[{"role": ..., "content": ...}, ...]``）

        Returns:
            dict | None: ``{"action": "continue", "modified_kwargs": {"messages": [...]}}``；
            消息列表非法时返回 ``None``（不改动）。
        """

        if not isinstance(messages, list):
            return None

        context_text = self._build_context_text()

        # 在现有 system 消息之后插入，避免破坏缓存前缀
        new_messages = list(messages)
        insert_pos = 0
        for i, msg in enumerate(messages):
            if isinstance(msg, dict) and msg.get("role") == "system":
                insert_pos = i + 1
            else:
                break
        new_messages.insert(insert_pos, {"role": "system", "content": context_text})

        return {"action": "continue", "modified_kwargs": {"messages": new_messages}}

    def _build_context_text(self) -> str:
        """构造今天/昨天/明天的轻量上下文文本。

        格式：``[标签]（星期X）（节日）（节气）（节假日/调休）``，仅有的内容才带括号。
        不含公历日期和农历日期——详细日期走 Tool。

        Returns:
            str: 形如 ``[今天]（星期四）（端午节）（夏至）（法定节假日放假）``
        """

        date_config = self.config.date
        now = datetime.now(ZoneInfo(date_config.timezone))

        # 按“时区 + 本地日期 + 信息源开关”缓存当天结果：一天内多次模型请求零成本，
        # 跨天自然失效（只保留一条，随日期滚动）。
        cache_key = (
            date_config.timezone,
            now.date().isoformat(),
            date_config.include_traditional_festivals,
            date_config.include_statutory_holidays,
            date_config.include_solar_terms,
            date_config.include_western_festivals,
        )
        cached = getattr(self, "_inject_cache", None)
        if cached is not None and cached[0] == cache_key:
            return cached[1]

        lines: list[str] = []

        for day_label, day_offset in [("昨天", -1), ("今天", 0), ("明天", 1)]:
            dt = now + timedelta(days=day_offset)
            lunar = _get_lunar(dt.year, dt.month, dt.day)

            # [标签]（有内容时才加括号）
            segments: list[str] = [f"[{day_label}]"]

            # 星期
            segments.append(f"（{_WEEKDAY_ZH[dt.weekday()]}）")

            # 节日（传统节日 + 公历节日，去重）
            if date_config.include_traditional_festivals or date_config.include_western_festivals:
                festivals: list[str] = []
                if date_config.include_traditional_festivals:
                    festivals = self._lunar_festival_names(dt, lunar)
                if date_config.include_western_festivals:
                    for name in self._solar_festival_names(dt):
                        if name not in festivals:
                            festivals.append(name)
                if festivals:
                    segments.append(f"（{'、'.join(festivals)}）")

            # 节气
            if date_config.include_solar_terms:
                term = lunar.todaySolarTerms
                if term and term != "无":
                    segments.append(f"（{term}节气）")

            # 法定节假日 / 调休补班
            if date_config.include_statutory_holidays:
                try:
                    on_holiday, holiday_en = get_holiday_detail(dt.date())
                    if on_holiday and holiday_en:
                        holiday_cn = _LEGAL_EN2CN.get(holiday_en, holiday_en)
                        segments.append(f"（{holiday_cn}放假）")
                    elif dt.weekday() >= 5 and is_workday(dt.date()):
                        segments.append("（调休补班）")
                except NotImplementedError:
                    self._get_logger().warning(
                        f"chinese_calendar 无 {dt.date()} 的法定节假日数据（超出库覆盖范围），已跳过"
                    )

            lines.append("".join(segments))

        text = "\n".join(lines)
        self._inject_cache = (cache_key, text)
        return text

    @staticmethod
    def _build_lunar_text(lunar: "cnlunar.Lunar") -> str:
        """构造农历日期文本，形如 ``，农历二零二六年五月初五``

        Args:
            lunar: cnlunar 农历对象

        Returns:
            str: 带前导分隔符的农历文本
        """

        # lunarMonthCn 形如 "五月小"/"五月大"，去掉末尾的大小标识
        month_cn = lunar.lunarMonthCn
        if month_cn and month_cn[-1] in "大小":
            month_cn = month_cn[:-1]
        return f"，农历{lunar.lunarYearCn}年{month_cn}{lunar.lunarDayCn}"

    def _build_festivals_text(self, now: datetime, lunar: "cnlunar.Lunar") -> str:
        """汇总当天的法定节假日 / 传统节日 / 西方节日 / 节气信息

        Args:
            now: 带时区的当前时间
            lunar: cnlunar 农历对象

        Returns:
            str: 拼接好的节日文本（每条以句号结尾），无任何节日时返回空串
        """

        date_config = self.config.date
        phrases: list[str] = []
        covered: set[str] = set()  # 已提及的节日名，避免不同来源重复

        # 1. 法定节假日放假 / 调休补班
        if date_config.include_statutory_holidays:
            statutory_phrase, legal_name = self._build_statutory_phrase(now)
            if statutory_phrase:
                phrases.append(statutory_phrase)
            if legal_name:
                covered.add(legal_name)

        # 2. 传统农历节日
        if date_config.include_traditional_festivals:
            for name in self._lunar_festival_names(now, lunar):
                if name not in covered:
                    phrases.append(f"今天是{name}")
                    covered.add(name)

        # 3. 常见公历 / 西方节日
        if date_config.include_western_festivals:
            for name in self._solar_festival_names(now):
                if name not in covered:
                    phrases.append(f"今天是{name}")
                    covered.add(name)

        # 4. 24 节气
        if date_config.include_solar_terms:
            term = lunar.todaySolarTerms
            if term and term != "无":
                phrases.append(f"今天是{term}节气")

        return "".join(f"{phrase}。" for phrase in phrases)

    def _build_statutory_phrase(self, now: datetime) -> tuple[str, str]:
        """判定当天的法定节假日放假或调休补班状态

        Args:
            now: 带时区的当前时间

        Returns:
            tuple[str, str]: ``(描述文本, 法定节日中文名)``；中文名用于跨来源去重，
            无放假信息时描述文本为空
        """

        today = now.date()
        try:
            on_holiday, holiday_en = get_holiday_detail(today)
        except NotImplementedError:
            # chinese_calendar 数据有覆盖上限，超出范围属已知边界，记录日志并跳过该行
            self.ctx.logger.warning(f"chinese_calendar 无 {today} 的法定节假日数据（超出库覆盖范围），已跳过调休信息")
            return "", ""

        if on_holiday and holiday_en:
            # holiday_en 有值才是真正的法定节假日；为空表示只是普通周末休息，无需提示
            holiday_cn = _LEGAL_EN2CN.get(holiday_en, holiday_en)
            return f"今天是法定节假日（{holiday_cn}），放假", holiday_cn

        # 周末却需要上班 -> 调休补班
        if today.weekday() >= 5 and is_workday(today):
            return "今天因节假日调休需要上班（周末补班）", ""

        return "", ""

    @staticmethod
    def _lunar_festival_names(now: datetime, lunar: "cnlunar.Lunar") -> list[str]:
        """返回当天的传统农历节日名称列表（委托到 date_api 集中实现）。"""
        # 委托到 date_api 中的纯函数实现，保证单一真相来源
        try:
            return _lunar_festival_names_fn(now, lunar)
        except NameError:
            # 极端回退（极少发生）：若导入符号丢失则使用局部最小实现
            names: list[str] = []
            if not getattr(lunar, "isLunarLeapMonth", False):
                festival = _LUNAR_FESTIVALS.get((lunar.lunarMonth, lunar.lunarDay))
                if festival:
                    names.append(festival)
            tomorrow = datetime(now.year, now.month, now.day) + timedelta(days=1)
            next_lunar = cnlunar.Lunar(tomorrow, godType="8char")
            if next_lunar.lunarMonth == 1 and next_lunar.lunarDay == 1:
                names.append("除夕")
            return names

    @staticmethod
    def _solar_festival_names(now: datetime) -> list[str]:
        """返回当天的常见公历/西方节日名称列表（含母亲节等，按周计算；委托到 date_api）。"""
        try:
            return _solar_festival_names_fn(now)
        except NameError:
            names: list[str] = []
            fixed = _SOLAR_FESTIVALS.get((now.month, now.day))
            if fixed:
                names.append(fixed)
            weekday = now.weekday()
            week_index = (now.day - 1) // 7 + 1
            if now.month == 5 and weekday == 6 and week_index == 2:
                names.append("母亲节")
            elif now.month == 6 and weekday == 6 and week_index == 3:
                names.append("父亲节")
            elif now.month == 11 and weekday == 3 and week_index == 4:
                names.append("感恩节")
            return names


def create_plugin() -> DateContextPlugin:
    """创建日期上下文注入插件实例

    Returns:
        DateContextPlugin: 新的插件实例
    """

    return DateContextPlugin()
