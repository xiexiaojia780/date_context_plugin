"""日期上下文插件

提供：
- 公开 API ``date`` / ``date_text``：仅返回**今天**的日期/农历/节日信息，供其他插件调用
- LLM Tool ``query_date``：供模型查询昨天 / 今天 / 明天（或指定 ISO 日期）
- 可选 Hook 注入：配置 ``date.inject_on_model_request`` 为 true 时，在模型请求前
  向已有 system 消息之后插入今天的日期上下文（默认关闭，避免影响前缀缓存）

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
    from .date_api import DateContextAPIMixin
except ImportError:
    # PluginLoader 以文件方式加载时相对导入可能失败，回退到同目录绝对导入
    from date_api import DateContextAPIMixin  # type: ignore

# 星期中文映射：datetime 的 %A 依赖系统 locale，结果不稳定，这里按 weekday() 自行映射
_WEEKDAY_ZH = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")

# 农历传统节日：(农历月, 农历日) -> 名称（仅非闰月生效）除夕单独按"明日为正月初一"判定
_LUNAR_FESTIVALS: dict[tuple[int, int], str] = {
    (1, 1): "春节",
    (1, 15): "元宵节",
    (2, 2): "龙抬头",
    (5, 5): "端午节",
    (7, 7): "七夕节",
    (7, 15): "中元节",
    (8, 15): "中秋节",
    (9, 9): "重阳节",
    (12, 8): "腊八节",
}

# 公历常见 / 西方节日（固定日期）：(公历月, 公历日) -> 名称
_SOLAR_FESTIVALS: dict[tuple[int, int], str] = {
    (1, 1): "元旦",
    (2, 14): "情人节",
    (3, 8): "妇女节",
    (3, 12): "植树节",
    (4, 1): "愚人节",
    (5, 1): "劳动节",
    (5, 4): "青年节",
    (6, 1): "儿童节",
    (7, 1): "建党节",
    (8, 1): "建军节",
    (9, 10): "教师节",
    (10, 1): "国庆节",
    (10, 31): "万圣夜",
    (11, 1): "万圣节",
    (12, 24): "平安夜",
    (12, 25): "圣诞节",
}

# chinese_calendar 返回的法定节假日英文名 -> 中文名
_LEGAL_EN2CN: dict[str, str] = {
    "New Year's Day": "元旦",
    "Spring Festival": "春节",
    "Tomb-sweeping Day": "清明节",
    "Labour Day": "劳动节",
    "Dragon Boat Festival": "端午节",
    "Mid-autumn Festival": "中秋节",
    "National Day": "国庆节",
}


class PluginSectionConfig(PluginConfigBase):
    """插件基础配置"""

    __ui_label__ = "插件"
    __ui_icon__ = "package"
    __ui_order__ = 0

    enabled: bool = Field(default=True, description="是否启用插件")
    config_version: str = Field(default="1.4.0", description="配置版本")


class DateInjectionConfig(PluginConfigBase):
    """日期查询与可选注入配置"""

    __ui_label__ = "日期"
    __ui_icon__ = "calendar"
    __ui_order__ = 1

    timezone: str = Field(default="Asia/Shanghai", description="计算当前日期所用的时区（IANA 名称，如 Asia/Shanghai）")
    datetime_format: str = Field(default="%Y年%m月%d日", description="日期格式（strftime），不含星期")
    include_lunar: bool = Field(default=True, description="是否附带农历日期")
    include_traditional_festivals: bool = Field(default=True, description="是否附带传统农历节日（春节/端午/中秋等）")
    include_statutory_holidays: bool = Field(default=True, description="是否附带法定节假日放假/调休补班信息")
    include_solar_terms: bool = Field(default=True, description="是否附带 24 节气信息")
    include_western_festivals: bool = Field(default=True, description="是否附带常见公历/西方节日（情人节/圣诞节等）")
    inject_on_model_request: bool = Field(
        default=False,
        description="是否在模型请求前自动注入今天的日期上下文（默认关闭；开启可能影响前缀缓存）",
    )
    template: str = Field(
        default="【当前日期】现在是 {datetime} {weekday}{lunar}。{festivals}回复时如涉及日期、节日等请以此为准。",
        description="今天文本模板，可使用占位符 {datetime} {weekday} {lunar} {festivals}",
    )


class DateContextPluginConfig(PluginConfigBase):
    """日期上下文插件配置"""

    plugin: PluginSectionConfig = Field(default_factory=PluginSectionConfig)
    date: DateInjectionConfig = Field(default_factory=DateInjectionConfig)


class DateContextPlugin(DateContextAPIMixin, MaiBotPlugin):
    """日期上下文插件（可选注入 + API 仅今天 + Tool 可查昨天/今天/明天）"""

    config_model = DateContextPluginConfig

    async def on_load(self) -> None:
        """处理插件加载"""
        inject = bool(getattr(self.config.date, "inject_on_model_request", False))
        self.ctx.logger.info(
            "日期上下文插件已加载（inject_on_model_request=%s；API: date/date_text 仅今天；Tool: query_date）"
            % inject
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
        description="可选：向模型请求注入今天的日期/星期/农历/节日/节气上下文",
        mode=HookMode.BLOCKING,
        order=HookOrder.NORMAL,
        error_policy=ErrorPolicy.SKIP,
    )
    async def inject_date(self, messages: Any = None, **kwargs: Any) -> dict[str, Any] | None:
        """在已有 system 消息之后注入今天的日期信息（受配置开关控制）

        Args:
            messages: Host 传入的序列化消息列表
            **kwargs: Hook 透传上下文（不使用）

        Returns:
            dict | None: 改写后的 Hook 结果；未启用注入时返回 ``None``
        """

        del kwargs

        if not self.config.plugin.enabled:
            return None
        if not self.config.date.inject_on_model_request:
            return None
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
        """构造今天的上下文文本（兼容内部调用）

        Returns:
            str: 渲染后的文本
        """

        date_config = self.config.date

        # 计算带时区的当前时间
        now = datetime.now(ZoneInfo(date_config.timezone))
        naive_now = datetime(now.year, now.month, now.day)
        lunar = cnlunar.Lunar(naive_now, godType="8char")

        datetime_str = now.strftime(date_config.datetime_format)
        weekday = _WEEKDAY_ZH[now.weekday()]
        lunar_text = self._build_lunar_text(lunar) if date_config.include_lunar else ""
        festivals_text = self._build_festivals_text(now, lunar)

        return date_config.template.format(
            datetime=datetime_str,
            weekday=weekday,
            lunar=lunar_text,
            festivals=festivals_text,
        )

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
        """返回当天的传统农历节日名称列表

        Args:
            now: 带时区的当前时间
            lunar: cnlunar 农历对象

        Returns:
            list[str]: 传统节日名称列表（可能为空）
        """

        names: list[str] = []
        if not lunar.isLunarLeapMonth:
            festival = _LUNAR_FESTIVALS.get((lunar.lunarMonth, lunar.lunarDay))
            if festival:
                names.append(festival)

        # 除夕：明日为正月初一（腊月末日可能是廿九或三十，按"明日初一"判定最稳）
        tomorrow = datetime(now.year, now.month, now.day) + timedelta(days=1)
        next_lunar = cnlunar.Lunar(tomorrow, godType="8char")
        if next_lunar.lunarMonth == 1 and next_lunar.lunarDay == 1:
            names.append("除夕")
        return names

    @staticmethod
    def _solar_festival_names(now: datetime) -> list[str]:
        """返回当天的常见公历/西方节日名称列表（含按周计算的母亲节/父亲节/感恩节）

        Args:
            now: 带时区的当前时间

        Returns:
            list[str]: 公历节日名称列表（可能为空）
        """

        names: list[str] = []
        fixed = _SOLAR_FESTIVALS.get((now.month, now.day))
        if fixed:
            names.append(fixed)

        # 按"第 N 个星期 X"计算的节日
        weekday = now.weekday()
        week_index = (now.day - 1) // 7 + 1  # 当天是本月第几个该星期
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
