"""日期上下文公开 API 模块

独立模块：把「日期/农历/节日」能力以 MaiBot 公开 API 形式暴露给其他插件。
与 Hook 注入解耦，便于单独维护；合并回主插件时只需：

1. 保留本文件
2. 让插件类混入 ``DateContextAPIMixin``
3. 从 README 同步 API 说明

其他插件调用示例::

    result = await self.ctx.api.call("date")
    # 推荐使用全名，避免短名冲突：
    # result = await self.ctx.api.call(
    #     "github.xiexiaojia780.date-context-plugin.date"
    # )
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import cnlunar

from maibot_sdk import API

# 与 plugin 内保持一致：不用 locale 的 %A
_WEEKDAY_ZH = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")


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


def build_date_context_from_plugin(
    plugin: Any,
    *,
    at: str | None = None,
    timezone: str | None = None,
    include_lunar: bool | None = None,
    include_traditional_festivals: bool | None = None,
    include_statutory_holidays: bool | None = None,
    include_solar_terms: bool | None = None,
    include_western_festivals: bool | None = None,
) -> dict[str, Any]:
    """基于插件实例构造结构化日期上下文。

    复用插件已有的节日/农历判定方法，保证 API 返回与 Hook 注入逻辑一致。
    include_* 为 None 时沿用插件配置；传入时仅影响本次 API 返回，不改配置。

    Args:
        plugin: DateContextPlugin 实例（需具备 config / ctx / 内部构建方法）。
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
        now = resolve_now(timezone=str(timezone or date_config.timezone), at=at)
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
                phrases.append(f"今天是{name}")
                covered.add(name)
                festival_names.append(name)

    if use_western:
        for name in plugin._solar_festival_names(now):
            if name not in covered:
                phrases.append(f"今天是{name}")
                covered.add(name)
                festival_names.append(name)

    if use_solar_terms:
        term = lunar.todaySolarTerms
        if term and term != "无":
            solar_term = str(term)
            phrases.append(f"今天是{term}节气")

    festivals_text = "".join(f"{phrase}。" for phrase in phrases)
    text = date_config.template.format(
        datetime=datetime_str,
        weekday=weekday,
        lunar=lunar_text,
        festivals=festivals_text,
    )

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
        "datetime": datetime_str,
        "weekday": weekday,
        "timezone": str(now.tzinfo) if now.tzinfo is not None else date_config.timezone,
        "iso": now.isoformat(),
        "date": now.date().isoformat(),
        "year": now.year,
        "month": now.month,
        "day": now.day,
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
    """公开 API 混入类：挂到插件类上即可注册 API 组件。

    依赖宿主插件提供：
    - ``self.config.plugin.enabled``
    - ``self.config.date.*``
    - ``self.ctx.logger``
    - ``_build_lunar_text`` / ``_build_statutory_phrase`` /
      ``_lunar_festival_names`` / ``_solar_festival_names``
    """

    @API(
        "date",
        description="获取当前（或指定时刻）的日期/星期/农历/节日/节气上下文，供其他插件复用",
        version="1",
        public=True,
    )
    async def api_date(
        self,
        at: str | None = None,
        timezone: str | None = None,
        include_lunar: bool | None = None,
        include_traditional_festivals: bool | None = None,
        include_statutory_holidays: bool | None = None,
        include_solar_terms: bool | None = None,
        include_western_festivals: bool | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """返回结构化日期上下文。

        Args:
            at: 可选 ISO 日期/时间（``2026-10-01`` / ``2026-10-01T12:00:00``）。
            timezone: 可选 IANA 时区，覆盖插件配置。
            include_*: 可选开关，覆盖插件配置。
            **kwargs: Host 透传参数（忽略）。

        Returns:
            dict[str, Any]: 成功为结构化字段；失败为 ``{"error": "..."}``。
        """

        del kwargs
        try:
            return build_date_context_from_plugin(
                self,
                at=at,
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
        description="获取渲染后的日期上下文字符串（与 Hook 注入模板同源）",
        version="1",
        public=True,
    )
    async def api_date_text(
        self,
        at: str | None = None,
        timezone: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """仅返回渲染文本，便于其他插件直接拼进 prompt。

        Args:
            at: 可选 ISO 日期/时间。
            timezone: 可选时区。
            **kwargs: Host 透传参数（忽略）。

        Returns:
            dict[str, Any]: ``{"text": "..."}`` 或 ``{"error": "..."}``。
        """

        del kwargs
        result = await self.api_date(at=at, timezone=timezone)
        if "error" in result:
            return result
        return {"text": result.get("text", "")}
