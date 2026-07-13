"""日期上下文 Tool / API 模块

- ``@API("date" / "date_text")``：供其他插件调用，**仅返回今天**
- ``@Tool("query_date")``：供 LLM function-calling（昨天/今天/明天，或指定 ISO 日期）
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import cnlunar

from maibot_sdk import API, Tool
from maibot_sdk.types import ToolParameterInfo, ToolParamType

# 与 plugin 内保持一致：不用 locale 的 %A
_WEEKDAY_ZH = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")

# 相对日别名 -> 相对今天的天数偏移
_DAY_ALIASES: dict[str, int] = {
    "today": 0,
    "今天": 0,
    "yesterday": -1,
    "昨天": -1,
    "tomorrow": 1,
    "明天": 1,
    "0": 0,
    "-1": -1,
    "1": 1,
}

_DAY_LABELS: dict[int, str] = {
    -1: "昨天",
    0: "今天",
    1: "明天",
}


def resolve_now(*, timezone: str, at: str | None = None) -> datetime:
    """解析带时区的目标时刻。

    Args:
        timezone: IANA 时区名。
        at: 可选 ISO 日期/时间；为空则取当前时刻。

    Returns:
        datetime: 带时区的 datetime。

    Raises:
        ValueError: 时区非法或 at 无法解析。
    """

    tz_name = str(timezone or "Asia/Shanghai").strip()
    try:
        tz = ZoneInfo(tz_name)
    except Exception as exc:
        raise ValueError(f"非法时区: {tz_name}") from exc

    if not at or not str(at).strip():
        return datetime.now(tz)

    raw = str(at).strip().replace("Z", "+00:00")
    try:
        if "T" in raw or " " in raw:
            parsed = datetime.fromisoformat(raw.replace(" ", "T"))
        else:
            d = date.fromisoformat(raw)
            parsed = datetime(d.year, d.month, d.day)
    except Exception as exc:
        raise ValueError(f"无法解析 at 参数: {at}") from exc

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=tz)
    return parsed.astimezone(tz)


def parse_day_offset(day: str | int | None) -> int:
    """解析相对日期参数为天数偏移。

    Args:
        day: ``today`` / ``昨天`` / ``明天`` / ``yesterday`` / ``tomorrow`` / ``-1`` / ``0`` / ``1``。

    Returns:
        int: 相对今天的天数偏移（昨天=-1，今天=0，明天=1）。

    Raises:
        ValueError: 无法识别时抛出。
    """

    if day is None or str(day).strip() == "":
        return 0
    if isinstance(day, int):
        if day in (-1, 0, 1):
            return day
        raise ValueError(f"day 偏移仅支持 -1/0/1，收到: {day}")

    key = str(day).strip().lower()
    # 中文不 lower 化语义，再试原串
    if key in _DAY_ALIASES:
        return _DAY_ALIASES[key]
    raw = str(day).strip()
    if raw in _DAY_ALIASES:
        return _DAY_ALIASES[raw]
    # 兼容 "昨" "明" 等简写
    short = {"昨": -1, "今": 0, "明": 1}
    if raw in short:
        return short[raw]
    raise ValueError(f"无法识别 day 参数: {day}（可用：今天/昨天/明天 或 today/yesterday/tomorrow）")


def _relabel_phrase(phrase: str, day_label: str) -> str:
    """把短语里的「今天」换成相对日标签。"""

    if day_label == "今天":
        return phrase
    return phrase.replace("今天", day_label)


def build_date_context_from_plugin(
    plugin: Any,
    *,
    day: str | int | None = None,
    at: str | None = None,
    timezone: str | None = None,
    include_lunar: bool | None = None,
    include_traditional_festivals: bool | None = None,
    include_statutory_holidays: bool | None = None,
    include_solar_terms: bool | None = None,
    include_western_festivals: bool | None = None,
) -> dict[str, Any]:
    """基于插件实例构造结构化日期上下文。

    复用插件已有的节日/农历判定方法，保证 Tool 与 Hook 判定逻辑一致。
    - 若提供 ``at``：按该绝对日期查询，``day`` 仅作展示标签（默认「当天」语义用今天模板词替换为该日）。
    - 若未提供 ``at``：按 ``day`` 相对今天偏移（昨天/今天/明天）。

    Args:
        plugin: DateContextPlugin 实例。
        day: 相对日期别名或 -1/0/1。
        at: 可选 ISO 日期时间。
        timezone: 可选时区覆盖。
        include_*: 可选信息源开关覆盖。

    Returns:
        dict[str, Any]: 结构化上下文；失败时含 ``error`` 字段。
    """

    if not getattr(plugin.config.plugin, "enabled", True):
        return {"error": "日期上下文插件已禁用（plugin.enabled=false）"}

    date_config = plugin.config.date
    try:
        base_now = resolve_now(timezone=str(timezone or date_config.timezone), at=None)
        if at and str(at).strip():
            now = resolve_now(timezone=str(timezone or date_config.timezone), at=at)
            day_offset = (now.date() - base_now.date()).days
            day_label = _DAY_LABELS.get(day_offset, "该日")
        else:
            day_offset = parse_day_offset(day)
            target_date = base_now.date() + timedelta(days=day_offset)
            now = datetime(
                target_date.year,
                target_date.month,
                target_date.day,
                tzinfo=base_now.tzinfo,
            )
            day_label = _DAY_LABELS.get(day_offset, "该日")
    except Exception as exc:
        return {"error": str(exc)}

    use_lunar = date_config.include_lunar if include_lunar is None else bool(include_lunar)
    use_traditional = (
        date_config.include_traditional_festivals
        if include_traditional_festivals is None
        else bool(include_traditional_festivals)
    )
    use_statutory = (
        date_config.include_statutory_holidays
        if include_statutory_holidays is None
        else bool(include_statutory_holidays)
    )
    use_solar_terms = date_config.include_solar_terms if include_solar_terms is None else bool(include_solar_terms)
    use_western = (
        date_config.include_western_festivals
        if include_western_festivals is None
        else bool(include_western_festivals)
    )

    naive_now = datetime(now.year, now.month, now.day)
    lunar = cnlunar.Lunar(naive_now, godType="8char")

    datetime_str = now.strftime(date_config.datetime_format)
    weekday = _WEEKDAY_ZH[now.weekday()]
    lunar_text = plugin._build_lunar_text(lunar) if use_lunar else ""

    phrases: list[str] = []
    festival_names: list[str] = []
    covered: set[str] = set()
    statutory: dict[str, Any] = {
        "on_holiday": False,
        "holiday_name": "",
        "is_makeup_workday": False,
        "phrase": "",
        "available": True,
    }
    solar_term: str | None = None

    if use_statutory:
        statutory_phrase, legal_name = plugin._build_statutory_phrase(now)
        if statutory_phrase:
            statutory_phrase = _relabel_phrase(statutory_phrase, day_label)
            phrases.append(statutory_phrase)
            statutory["phrase"] = statutory_phrase
            if "补班" in statutory_phrase:
                statutory["is_makeup_workday"] = True
            if "法定节假日" in statutory_phrase:
                statutory["on_holiday"] = True
        if legal_name:
            covered.add(legal_name)
            festival_names.append(legal_name)
            statutory["holiday_name"] = legal_name
            statutory["on_holiday"] = True

    if use_traditional:
        for name in plugin._lunar_festival_names(now, lunar):
            if name not in covered:
                phrases.append(f"{day_label}是{name}")
                covered.add(name)
                festival_names.append(name)

    if use_western:
        for name in plugin._solar_festival_names(now):
            if name not in covered:
                phrases.append(f"{day_label}是{name}")
                covered.add(name)
                festival_names.append(name)

    if use_solar_terms:
        term = lunar.todaySolarTerms
        if term and term != "无":
            solar_term = str(term)
            phrases.append(f"{day_label}是{term}节气")

    festivals_text = "".join(f"{phrase}。" for phrase in phrases)

    # Hook 注入仍用配置 template（仅今天）；Tool 用带相对日标签的可读文本
    if day_label == "今天" and day_offset == 0 and not (at and str(at).strip()):
        text = date_config.template.format(
            datetime=datetime_str,
            weekday=weekday,
            lunar=lunar_text,
            festivals=festivals_text,
        )
    else:
        text = f"【{day_label}】{day_label}是 {datetime_str} {weekday}{lunar_text}。{festivals_text}".rstrip()
        if not text.endswith("。"):
            text += "。"

    month_cn = lunar.lunarMonthCn
    if month_cn and month_cn[-1] in "大小":
        month_cn = month_cn[:-1]

    lunar_info = {
        "year": int(getattr(lunar, "lunarYear", 0) or 0),
        "month": int(lunar.lunarMonth),
        "day": int(lunar.lunarDay),
        "year_cn": str(lunar.lunarYearCn or ""),
        "month_cn": str(month_cn or ""),
        "day_cn": str(lunar.lunarDayCn or ""),
        "is_leap_month": bool(lunar.isLunarLeapMonth),
        "text": lunar_text,
    }

    return {
        "text": text,
        "day": day_label,
        "day_offset": day_offset,
        "datetime": datetime_str,
        "weekday": weekday,
        "timezone": str(now.tzinfo) if now.tzinfo is not None else date_config.timezone,
        "iso": now.isoformat(),
        "date": now.date().isoformat(),
        "year": now.year,
        "month": now.month,
        "day_of_month": now.day,
        "hour": now.hour,
        "minute": now.minute,
        "lunar": lunar_info if use_lunar else None,
        "lunar_text": lunar_text,
        "festival_names": festival_names,
        "festivals_text": festivals_text,
        "statutory": statutory,
        "solar_term": solar_term,
        "phrases": phrases,
    }


class DateContextAPIMixin:
    """Tool + 公开 API 混入类：挂到插件类上即可注册。

    - 其他插件：``@API("date" / "date_text", public=True)`` —— **仅今天**
    - LLM：``@Tool("query_date")`` —— 昨天/今天/明天

    依赖宿主插件提供：
    - ``self.config.plugin.enabled``
    - ``self.config.date.*``
    - ``self.ctx.logger``
    - ``_build_lunar_text`` / ``_build_statutory_phrase`` /
      ``_lunar_festival_names`` / ``_solar_festival_names``
    """

    # ─── 公开 API：供其他插件调用（仅今天）────────────────────────

    @API(
        "date",
        description="获取今天的结构化日期/农历/节日上下文（仅今天）",
        version="1",
        public=True,
    )
    async def api_date(
        self,
        timezone: str | None = None,
        include_lunar: bool | None = None,
        include_traditional_festivals: bool | None = None,
        include_statutory_holidays: bool | None = None,
        include_solar_terms: bool | None = None,
        include_western_festivals: bool | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """返回**今天**的结构化日期上下文（插件间调用）。

        其他插件示例::

            result = await self.ctx.api.call(
                "github.xiexiaojia780.date-context-plugin.date"
            )
            text = result["text"]

        公开 API 固定为今天；若需昨天/明天，请让 LLM 使用 Tool ``query_date``。

        Args:
            timezone: 可选 IANA 时区，覆盖插件配置。
            include_*: 可选开关，覆盖插件配置。
            **kwargs: Host 透传参数（忽略；若传入 day/at 也会被忽略）。

        Returns:
            dict[str, Any]: 成功为结构化字段；失败为 ``{"error": "..."}``。
        """

        # 固定仅今天：忽略调用方传入的 day/at，避免误用
        del kwargs
        try:
            return build_date_context_from_plugin(
                self,
                day="今天",
                at=None,
                timezone=timezone,
                include_lunar=include_lunar,
                include_traditional_festivals=include_traditional_festivals,
                include_statutory_holidays=include_statutory_holidays,
                include_solar_terms=include_solar_terms,
                include_western_festivals=include_western_festivals,
            )
        except Exception as exc:
            logger = getattr(getattr(self, "ctx", None), "logger", None)
            if logger is not None:
                logger.warning(f"date API 失败: {exc}", exc_info=True)
            return {"error": f"构造日期上下文失败: {exc}"}

    @API(
        "date_text",
        description="获取今天的日期渲染文本（仅今天）",
        version="1",
        public=True,
    )
    async def api_date_text(
        self,
        timezone: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """仅返回**今天**的渲染文本，便于其他插件拼进 prompt。

        Args:
            timezone: 可选时区。
            **kwargs: Host 透传参数（忽略）。

        Returns:
            dict[str, Any]: ``{"text": "..."}`` 或 ``{"error": "..."}``。
        """

        del kwargs
        result = await self.api_date(timezone=timezone)
        if "error" in result:
            return result
        return {"text": result.get("text", "")}

    # ─── LLM Tool ────────────────────────────────────────────────

    @Tool(
        "query_date",
        description="查询今天/昨天/明天（或指定日期）的公历、星期、农历、节日、节气与是否放假调休",
        brief_description="查询今天、昨天或明天的日期与节日信息",
        parameters=[
            ToolParameterInfo(
                name="day",
                param_type=ToolParamType.STRING,
                description="相对日期：今天/昨天/明天，或 today/yesterday/tomorrow；默认今天",
                required=False,
                default="今天",
                enum_values=["今天", "昨天", "明天", "today", "yesterday", "tomorrow"],
            ),
            ToolParameterInfo(
                name="at",
                param_type=ToolParamType.STRING,
                description="可选绝对日期（ISO，如 2026-10-01）；若填写则优先于 day",
                required=False,
            ),
        ],
    )
    async def tool_query_date(
        self,
        day: str = "今天",
        at: str = "",
        **kwargs: Any,
    ) -> dict[str, Any]:
        """供 LLM 调用的日期查询工具。

        组件名使用 ``query_date``，避免与公开 API 名 ``date`` 在 Host 组件表中撞名。

        Args:
            day: 相对日期别名。
            at: 可选 ISO 日期。
            **kwargs: Host 透传参数（忽略）。

        Returns:
            dict[str, Any]: ``{"name": "query_date", "content": "..."}`` 形式的工具结果。
        """

        del kwargs
        try:
            result = build_date_context_from_plugin(
                self,
                day=day or "今天",
                at=at or None,
            )
        except Exception as exc:
            logger = getattr(getattr(self, "ctx", None), "logger", None)
            if logger is not None:
                logger.warning(f"query_date Tool 失败: {exc}", exc_info=True)
            return {"name": "query_date", "content": f"查询日期失败: {exc}"}

        if "error" in result:
            return {"name": "query_date", "content": str(result["error"])}

        # 给模型一段可读摘要；关键字段附在 content 后方便引用
        content = str(result.get("text") or "")
        extra_parts: list[str] = []
        if result.get("date"):
            extra_parts.append(f"公历={result['date']}")
        if result.get("weekday"):
            extra_parts.append(f"星期={result['weekday']}")
        lunar = result.get("lunar") or {}
        if isinstance(lunar, dict) and lunar.get("month_cn"):
            extra_parts.append(
                f"农历={lunar.get('year_cn', '')}年{lunar.get('month_cn', '')}{lunar.get('day_cn', '')}"
            )
        if result.get("festival_names"):
            extra_parts.append(f"节日={','.join(result['festival_names'])}")
        statutory = result.get("statutory") or {}
        if statutory.get("on_holiday"):
            extra_parts.append(f"放假=是({statutory.get('holiday_name') or '法定节假日'})")
        elif statutory.get("is_makeup_workday"):
            extra_parts.append("调休补班=是")
        if result.get("solar_term"):
            extra_parts.append(f"节气={result['solar_term']}")

        if extra_parts:
            content = content.rstrip() + "\n" + "；".join(extra_parts)

        return {"name": "query_date", "content": content}
