"""日期上下文 Tool / API 模块

- ``@API("date" / "date_text")``：供其他插件调用，**仅返回今天**
- ``@API("holiday")``：供其他插件调用，**仅今天** 的特定节日查询（可选按名称过滤）
- ``@Tool("query_date")``：供 LLM 查询昨天/今天/明天（或指定 ISO 日期）
- ``@Tool("query_holiday")``：供 LLM 按名称查询特定节日（支持年份/具体日期）
"""

from __future__ import annotations

import functools
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import cnlunar
# chinese_calendar 暴露的常量/工具（用于法定节假日正向/反查）
try:
    from chinese_calendar import holidays as CC_HOLIDAYS, get_holiday_detail, is_workday  # type: ignore[attr-defined]
except Exception:
    from chinese_calendar.constants import holidays as CC_HOLIDAYS  # type: ignore[attr-defined]
    from chinese_calendar import get_holiday_detail, is_workday  # type: ignore[attr-defined]

from maibot_sdk import API, Tool
from maibot_sdk.types import ToolParameterInfo, ToolParamType

# 与 plugin 内保持一致：不用 locale 的 %A
_WEEKDAY_ZH = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")


@functools.lru_cache(maxsize=1024)
def _get_lunar(year: int, month: int, day: int) -> "cnlunar.Lunar":
    """构造并缓存 ``cnlunar.Lunar`` 对象（按公历日期）。

    ``cnlunar.Lunar`` 构造约 0.1ms/次，Hook 注入（每天 3~6 次）与
    农历节日反查（每年最多 400 天）会大量重复构造同一日期，这里统一做
    LRU 缓存：同一天多次查询、多节日反查共享，冷启动后基本零成本。

    Args:
        year / month / day: 公历年月日。

    Returns:
        cnlunar.Lunar: 该日期的农历对象（只读使用，勿修改其属性）。
    """

    return cnlunar.Lunar(datetime(year, month, day), godType="8char")

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

# ============================================================
# 节日数据（集中在此，便于 Tool / API / Hook 共享）
# ============================================================

# 农历传统节日：(农历月, 农历日) -> 名称（仅非闰月生效）
# 除夕单独按“明日为正月初一”判定（见 _lunar_festival_names）
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

# 节日名称规范化同义词（输入友好 → 规范中文名）
# 覆盖现有映射 + 常见简称 + 英文
_HOLIDAY_SYNONYMS: dict[str, str] = {
    # 农历/传统
    "春节": "春节", "春": "春节",
    "除夕": "除夕",
    "元宵": "元宵节", "元宵节": "元宵节",
    "龙抬头": "龙抬头",
    "端午": "端午节", "端午节": "端午节", "dragon boat": "端午节", "dragon boat festival": "端午节",
    "七夕": "七夕节", "七夕节": "七夕节",
    "中元": "中元节", "中元节": "中元节", "鬼节": "中元节",
    "中秋": "中秋节", "中秋节": "中秋节", "mid-autumn": "中秋节", "mid autumn festival": "中秋节",
    "重阳": "重阳节", "重阳节": "重阳节",
    "腊八": "腊八节", "腊八节": "腊八节",
    # 法定（含清明）
    "元旦": "元旦", "new year": "元旦", "new year's day": "元旦",
    "清明": "清明节", "清明节": "清明节", "tomb-sweeping": "清明节", "tomb sweeping day": "清明节",
    "劳动": "劳动节", "劳动节": "劳动节", "labour day": "劳动节", "labor day": "劳动节",
    "国庆": "国庆节", "国庆节": "国庆节", "national day": "国庆节",
    # 公历/西方固定
    "情人": "情人节", "情人节": "情人节", "valentine": "情人节",
    "妇女": "妇女节", "妇女节": "妇女节",
    "植树": "植树节", "植树节": "植树节",
    "愚人": "愚人节", "愚人节": "愚人节", "april fool": "愚人节",
    "青年": "青年节", "青年节": "青年节",
    "儿童": "儿童节", "儿童节": "儿童节", "children": "儿童节",
    "建党": "建党节", "建党节": "建党节",
    "建军": "建军节", "建军节": "建军节",
    "教师": "教师节", "教师节": "教师节", "teacher": "教师节",
    "万圣夜": "万圣夜", "万圣节": "万圣节", "halloween": "万圣节",
    "平安夜": "平安夜", "圣诞": "圣诞节", "圣诞节": "圣诞节", "christmas": "圣诞节",
    # 按周计算
    "母亲": "母亲节", "母亲节": "母亲节", "mother": "母亲节",
    "父亲": "父亲节", "父亲节": "父亲节", "father": "父亲节",
    "感恩": "感恩节", "感恩节": "感恩节", "thanksgiving": "感恩节",
}

# 所有可识别的规范节日名称集合（用于错误提示）
SUPPORTED_HOLIDAY_NAMES: list[str] = sorted(set(_HOLIDAY_SYNONYMS.values()))

# 小写化映射（英文别名大小写无关匹配用，构造一次避免每次线性扫描）
_HOLIDAY_SYNONYMS_LOWER: dict[str, str] = {
    key.lower(): value for key, value in _HOLIDAY_SYNONYMS.items()
}


def normalize_holiday_name(raw: str | None) -> str | None:
    """将用户输入的节日名称规范化为内部统一中文名。

    Args:
        raw: 原始名称（支持中文简称、英文等）。

    Returns:
        规范中文名，或 None（无法识别）。
    """
    if not raw:
        return None
    key = str(raw).strip()
    if not key:
        return None
    # 直接命中
    if key in _HOLIDAY_SYNONYMS:
        return _HOLIDAY_SYNONYMS[key]
    # 忽略大小写再匹配一次（英文场景）
    lower = key.lower()
    if lower in _HOLIDAY_SYNONYMS_LOWER:
        return _HOLIDAY_SYNONYMS_LOWER[lower]
    # 兜底：如果已经是规范名之一
    if key in SUPPORTED_HOLIDAY_NAMES:
        return key
    return None


def _lunar_festival_names(now: datetime, lunar: "cnlunar.Lunar") -> list[str]:
    """返回当天的传统农历节日名称列表（纯函数版）。

    仅非闰月；除夕按“明日正月初一”判定。
    """
    names: list[str] = []
    if not getattr(lunar, "isLunarLeapMonth", False):
        festival = _LUNAR_FESTIVALS.get((lunar.lunarMonth, lunar.lunarDay))
        if festival:
            names.append(festival)

    # 除夕（明日为正月初一）：只有腊月最后一天才可能是，非腊月直接跳过明日构造
    if lunar.lunarMonth == 12:
        tomorrow = datetime(now.year, now.month, now.day) + timedelta(days=1)
        next_lunar = _get_lunar(tomorrow.year, tomorrow.month, tomorrow.day)
        if next_lunar.lunarMonth == 1 and next_lunar.lunarDay == 1:
            names.append("除夕")
    return names


def _solar_festival_names(now: datetime) -> list[str]:
    """返回当天的常见公历/西方节日名称列表（纯函数版，含母亲节等）。"""
    names: list[str] = []
    fixed = _SOLAR_FESTIVALS.get((now.month, now.day))
    if fixed:
        names.append(fixed)

    # 按“第 N 个星期 X”计算的节日
    weekday = now.weekday()
    week_index = (now.day - 1) // 7 + 1
    if now.month == 5 and weekday == 6 and week_index == 2:
        names.append("母亲节")
    elif now.month == 6 and weekday == 6 and week_index == 3:
        names.append("父亲节")
    elif now.month == 11 and weekday == 3 and week_index == 4:
        names.append("感恩节")
    return names


def _get_statutory_info(dt: datetime | date) -> dict[str, Any]:
    """返回某天的法定节假日/调休信息（纯函数）。

    Returns:
        dict: {
            "on_holiday": bool,
            "holiday_name": str,          # 中文
            "is_makeup_workday": bool,
            "phrase": str                 # 人类可读短语
        }
    """
    d = dt.date() if isinstance(dt, datetime) else dt
    try:
        on_holiday, holiday_en = get_holiday_detail(d)
    except NotImplementedError:
        return {"on_holiday": False, "holiday_name": "", "is_makeup_workday": False, "phrase": ""}

    if on_holiday and holiday_en:
        holiday_cn = _LEGAL_EN2CN.get(holiday_en, holiday_en)
        return {
            "on_holiday": True,
            "holiday_name": holiday_cn,
            "is_makeup_workday": False,
            "phrase": f"今天是法定节假日（{holiday_cn}），放假",
        }

    # 周末补班
    if d.weekday() >= 5 and is_workday(d):
        return {
            "on_holiday": False,
            "holiday_name": "",
            "is_makeup_workday": True,
            "phrase": "今天因节假日调休需要上班（周末补班）",
        }

    return {"on_holiday": False, "holiday_name": "", "is_makeup_workday": False, "phrase": ""}


# ============================================================
# 特定节日查询（反向查找）：名称 -> 日期
# ============================================================

def _lunar_to_gregorian_in_year(
    lunar_month: int,
    lunar_day: int,
    target_solar_year: int,
) -> list[date]:
    """在目标公历年份内搜索匹配指定农历月日的公历日期。

    只返回**落在 ``target_solar_year`` 内**的日期。农历与公历年份不对齐
    （春节在 1/21~2/21 之间、腊月节日可能跨到次年年初），因此直接按
    目标公历年整年扫描即可覆盖所有可能落点；同一公历年内同一农历月日
    通常 0~1 个（个别年份可能出现两次，如双腊八），故不提前 break，
    最后统一去重排序。仅考虑非闰月（调用方保证或在此过滤）。
    """
    results: list[date] = []
    try:
        d = datetime(target_solar_year, 1, 1)
        while d.year == target_solar_year:
            try:
                ln = _get_lunar(d.year, d.month, d.day)
                if (not getattr(ln, "isLunarLeapMonth", False)
                        and ln.lunarMonth == lunar_month
                        and ln.lunarDay == lunar_day):
                    results.append(d.date())
            except Exception:
                pass
            d += timedelta(days=1)
    except Exception:
        pass
    return sorted(set(results))


def _find_chuxi_in_year(target_solar_year: int) -> list[date]:
    """查找落在 ``target_solar_year`` 内的“除夕”（明日为正月初一）。

    除夕必然是春节前一天（春节在 1/21~2/21），只需扫描该年 1~2 月。
    """
    results: list[date] = []
    try:
        d = datetime(target_solar_year, 1, 1)
        while d.year == target_solar_year and d.month <= 2:
            tomorrow = d + timedelta(days=1)
            try:
                next_ln = _get_lunar(tomorrow.year, tomorrow.month, tomorrow.day)
                if next_ln.lunarMonth == 1 and next_ln.lunarDay == 1:
                    results.append(d.date())
            except Exception:
                pass
            d += timedelta(days=1)
    except Exception:
        pass
    return sorted(set(results))


def _solar_fixed_and_variable_in_year(name: str, year: int) -> list[date]:
    """根据规范名称返回某年该公历/西方节日的日期列表（支持母亲节等）。"""
    results: list[date] = []

    # 反向查找固定日期
    for (m, d), n in _SOLAR_FESTIVALS.items():
        if n == name:
            try:
                results.append(date(year, m, d))
            except ValueError:
                pass

    # 母亲节：5 月第 2 个星期日
    if name == "母亲节":
        for day in range(8, 15):  # 8~14
            try:
                dt = date(year, 5, day)
                if dt.weekday() == 6:
                    results.append(dt)
                    break
            except ValueError:
                pass

    # 父亲节：6 月第 3 个星期日
    if name == "父亲节":
        for day in range(15, 22):
            try:
                dt = date(year, 6, day)
                if dt.weekday() == 6:
                    results.append(dt)
                    break
            except ValueError:
                pass

    # 感恩节：11 月第 4 个星期四
    if name == "感恩节":
        for day in range(22, 29):
            try:
                dt = date(year, 11, day)
                if dt.weekday() == 3:
                    results.append(dt)
                    break
            except ValueError:
                pass

    return sorted(set(results))


def find_holiday_occurrences(name: str, year: int | None = None) -> list[dict[str, Any]]:
    """按规范节日名称查找某年的出现日期。

    Args:
        name: 规范中文名（例如 "端午节"、"母亲节"、"清明节"）。
        year: 目标公历年份，默认当前年。

    Returns:
        list[dict]: 每项包含 date(ISO)、type(lunar|solar|statutory)、phrase 等。
    """
    y = year or datetime.now().year
    canon = normalize_holiday_name(name) or name  # 已假定传入已规范，或在此再试

    occ: list[dict[str, Any]] = []

    # 1. 法定节假日（来自 chinese_calendar 的 holidays 反查）
    try:
        for d, en_name in CC_HOLIDAYS.items():
            if d.year == y:
                cn = _LEGAL_EN2CN.get(en_name, en_name)
                if cn == canon:
                    occ.append({
                        "date": d.isoformat(),
                        "type": "statutory",
                        "phrase": f"{d.month}月{d.day}日是法定节假日（{cn}）",
                    })
    except Exception:
        pass

    # 2. 农历传统节日
    if canon == "除夕":
        for d in _find_chuxi_in_year(y):
            occ.append({
                "date": d.isoformat(),
                "type": "lunar",
                "phrase": f"{d.month}月{d.day}日是除夕",
            })
    else:
        # 注意：items() 产出 (key, value)，key 本身是 (农历月, 农历日) 元组
        for (lm, ld), _festival_name in _LUNAR_FESTIVALS.items():
            if _festival_name == canon:
                for d in _lunar_to_gregorian_in_year(lm, ld, y):
                    occ.append({
                        "date": d.isoformat(),
                        "type": "lunar",
                        "phrase": f"{d.month}月{d.day}日是{canon}",
                    })

    # 3. 公历/西方（含按周）
    for d in _solar_fixed_and_variable_in_year(canon, y):
        occ.append({
            "date": d.isoformat(),
            "type": "solar",
            "phrase": f"{d.month}月{d.day}日是{canon}",
        })

    # 去重 + 排序
    seen: set[str] = set()
    uniq: list[dict[str, Any]] = []
    for item in sorted(occ, key=lambda x: x["date"]):
        if item["date"] not in seen:
            seen.add(item["date"])
            uniq.append(item)

    return uniq


def check_holiday_match_on_date(
    name: str,
    target: datetime | date,
    *,
    day_label: str | None = None,
) -> dict[str, Any]:
    """检查指定日期是否匹配该节日名称（正向检测复用）。

    Args:
        name: 节日名称（支持别名，会自动规范化）。
        target: 目标日期。
        day_label: 短语中替代“今天”的标签（如 ``"10月1日"``）；默认 ``"今天"``。

    Returns:
        dict: {
            "occurs": bool,
            "date": "YYYY-MM-DD",
            "holiday_name": str,
            "type": "lunar" | "solar" | "statutory" | "",
            "phrase": str,
            "details": {...}  # 可扩展
        }
    """
    d = target.date() if isinstance(target, datetime) else target
    label = day_label or "今天"
    ln = _get_lunar(d.year, d.month, d.day)

    canon = normalize_holiday_name(name)
    if not canon:
        return {"occurs": False, "date": d.isoformat(), "holiday_name": name, "type": "", "phrase": "", "details": {}}

    # 法定（get_holiday_detail 在放假期间每天返回同名节日，
    # 故 holiday_name == canon 即足够判定；普通周末 holiday_en 为空不会误报）
    stat = _get_statutory_info(d)
    if stat.get("holiday_name") == canon:
        phrase = _relabel_phrase(stat.get("phrase") or f"{label}是{canon}", label)
        return {
            "occurs": True,
            "date": d.isoformat(),
            "holiday_name": stat.get("holiday_name") or canon,
            "type": "statutory",
            "phrase": phrase,
            "details": stat,
        }

    # 传统农历
    for n in _lunar_festival_names(d, ln):
        if n == canon:
            return {
                "occurs": True,
                "date": d.isoformat(),
                "holiday_name": n,
                "type": "lunar",
                "phrase": f"{label}是{n}",
                "details": {"lunar": {"month": ln.lunarMonth, "day": ln.lunarDay}},
            }

    # 公历/西方
    for n in _solar_festival_names(d):
        if n == canon:
            return {
                "occurs": True,
                "date": d.isoformat(),
                "holiday_name": n,
                "type": "solar",
                "phrase": f"{label}是{n}",
                "details": {},
            }

    # 特殊：除夕匹配（明日为正月初一）
    if canon == "除夕":
        tomorrow = d + timedelta(days=1)
        next_ln = _get_lunar(tomorrow.year, tomorrow.month, tomorrow.day)
        if next_ln.lunarMonth == 1 and next_ln.lunarDay == 1:
            return {
                "occurs": True,
                "date": d.isoformat(),
                "holiday_name": "除夕",
                "type": "lunar",
                "phrase": f"{label}是除夕",
                "details": {},
            }

    return {"occurs": False, "date": d.isoformat(), "holiday_name": canon, "type": "", "phrase": "", "details": {}}


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

    lunar = _get_lunar(now.year, now.month, now.day)

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

    # 今天/昨天/明天/指定日期统一使用带相对日标签的固定文本格式（不再使用可配置 template）。
    # 注意：Hook 注入不经过这里，走 plugin._build_context_text 的固定轻型格式。
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


def build_holiday_context_from_plugin(
    plugin: Any,
    *,
    name: str,
    year: int | None = None,
    day: str | int | None = None,
    at: str | None = None,
    timezone: str | None = None,
) -> dict[str, Any]:
    """基于插件实例构造特定节日的查询上下文。

    - 若提供 ``at`` 或 ``day``：检查该具体日期是否匹配该节日。
    - 否则：查询 ``year``（默认当前年）该节日的出现日期列表。
    - API 层会强制今天；Tool 层支持年份/相对/绝对日期。

    Args:
        plugin: DateContextPlugin 实例。
        name: 节日名称（支持别名，会自动规范化）。
        year: 目标年份（用于 occurrence 查询）。
        day / at: 同日期查询语义，用于精确匹配某天。
        timezone: 可选时区覆盖。

    Returns:
        dict: 包含 text / occurs / dates / holiday_name / type / details 等；
              失败时含 ``error``。
    """
    if not getattr(plugin.config.plugin, "enabled", True):
        return {"error": "日期上下文插件已禁用（plugin.enabled=false）"}

    date_config = plugin.config.date

    # 规范化名称
    canon = normalize_holiday_name(name)
    if not canon:
        return {
            "error": f"无法识别的节日名称: {name}。支持示例：{', '.join(SUPPORTED_HOLIDAY_NAMES[:12])} ..."
        }

    try:
        # 解析目标时间基准
        base_now = resolve_now(timezone=str(timezone or date_config.timezone), at=None)
        if at and str(at).strip():
            target_dt = resolve_now(timezone=str(timezone or date_config.timezone), at=at)
            target_year = target_dt.year
            target_date = target_dt.date()
            mode = "specific"
        elif day is not None and str(day).strip() not in ("", "None"):
            offset = parse_day_offset(day)
            target_date = (base_now.date() + timedelta(days=offset))
            target_dt = datetime(target_date.year, target_date.month, target_date.day, tzinfo=base_now.tzinfo)
            target_year = target_date.year
            mode = "specific"
        else:
            target_year = year or base_now.year
            target_dt = None
            target_date = None
            mode = "year"
    except Exception as exc:
        return {"error": str(exc)}

    if mode == "specific" and target_date is not None:
        # 短语标签：绝对日期用 “M月D日”，相对日沿用 今天/昨天/明天
        if at and str(at).strip():
            label = f"{target_date.month}月{target_date.day}日"
        else:
            label = _DAY_LABELS.get(parse_day_offset(day), "该日")
        match = check_holiday_match_on_date(canon, target_date, day_label=label)
        occurs = bool(match.get("occurs"))
        dates = [match["date"]] if occurs else []
        text = match.get("phrase") or (f"{label}是{canon}" if occurs else f"{target_date} 不是 {canon}")
        return {
            "text": text,
            "occurs": occurs,
            "dates": dates,
            "holiday_name": match.get("holiday_name") or canon,
            "canonical": canon,
            "year": target_date.year,
            "date": match["date"],
            "type": match.get("type", ""),
            "details": match.get("details", {}),
            "query_mode": "specific",
        }

    # year 模式
    occs = find_holiday_occurrences(canon, target_year)
    dates = [o["date"] for o in occs]
    occurs = len(dates) > 0

    if occurs:
        pretty = "、".join(dates)
        text = f"{target_year} 年 {canon} 在 {pretty}"
    else:
        text = f"{target_year} 年未找到 {canon}（可能因数据范围或闰月限制）"

    # 附加法定信息（如适用）
    extra_statutory = None
    for o in occs:
        if o.get("type") == "statutory":
            extra_statutory = o
            break

    return {
        "text": text,
        "occurs": occurs,
        "dates": dates,
        "holiday_name": canon,
        "canonical": canon,
        "year": target_year,
        "type": occs[0]["type"] if occs else "",
        "occurrences": occs,
        "statutory": extra_statutory,
        "query_mode": "year",
    }


class DateContextAPIMixin:
    """Tool + 公开 API 混入类：挂到插件类上即可注册。

    - 其他插件：``@API("date" / "date_text" / "holiday", public=True)`` —— **仅今天**
    - LLM：``@Tool("query_date")`` —— 昨天/今天/明天
    - LLM：``@Tool("query_holiday")`` —— 按名称查询特定节日（支持年份/日期）

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

        # 查询今天时额外附加昨天和明天的日期提示
        if result.get("day_offset") == 0 and result.get("date"):
            today_date = date.fromisoformat(result["date"])
            yesterday_date = today_date + timedelta(days=-1)
            tomorrow_date = today_date + timedelta(days=1)
            extra_parts.append(
                f"昨天={yesterday_date.isoformat()}，{_WEEKDAY_ZH[yesterday_date.weekday()]}"
            )
            extra_parts.append(
                f"明天={tomorrow_date.isoformat()}，{_WEEKDAY_ZH[tomorrow_date.weekday()]}"
            )

        if extra_parts:
            content = content.rstrip() + "\n" + "；".join(extra_parts)

        return {"name": "query_date", "content": content}

    # ─── 公开 API：holiday（仅今天 + 可选名称过滤）──────────────────

    @API(
        "holiday",
        description="查询今天是否为特定节日（可选按名称过滤）；供其他插件调用，仅今天",
        version="1",
        public=True,
    )
    async def api_holiday(
        self,
        name: str | None = None,
        timezone: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """返回**今天**的特定节日匹配信息（插件间调用）。

        其他插件示例::

            result = await self.ctx.api.call(
                "github.xiexiaojia780.date-context-plugin.holiday",
                name="端午节"
            )
            occurs = result.get("occurs")
            dates = result.get("dates", [])

        公开 API 固定为今天；若需查询其他日期或年份，请让 LLM 使用 Tool ``query_holiday``。

        Args:
            name: 可选节日名称（支持别名）。为空则返回今天所有匹配的节日信息摘要。
            timezone: 可选时区覆盖。
            **kwargs: Host 透传（忽略）。

        Returns:
            dict[str, Any]: 成功为结构化字段；失败为 ``{"error": "..."}``。
        """
        del kwargs
        try:
            if name:
                canon = normalize_holiday_name(name)
                if not canon:
                    return {"error": f"无法识别的节日名称: {name}。支持示例：{', '.join(SUPPORTED_HOLIDAY_NAMES[:8])} ..."}
                # 强制仅今天
                return build_holiday_context_from_plugin(
                    self,
                    name=canon,
                    year=None,
                    day="今天",
                    at=None,
                    timezone=timezone,
                )
            else:
                # 无名称：返回今天日期上下文中的节日部分
                date_res = await self.api_date(timezone=timezone)
                if "error" in date_res:
                    return date_res
                fns = date_res.get("festival_names", []) or []
                return {
                    "text": date_res.get("text", ""),
                    "occurs": bool(fns),
                    "dates": [date_res.get("date")] if date_res.get("date") else [],
                    "holiday_names": fns,
                    "year": date_res.get("year"),
                    "date": date_res.get("date"),
                    "query_mode": "today_all",
                }
        except Exception as exc:
            logger = getattr(getattr(self, "ctx", None), "logger", None)
            if logger is not None:
                logger.warning(f"holiday API 失败: {exc}", exc_info=True)
            return {"error": f"查询节日失败: {exc}"}

    # ─── LLM Tool：query_holiday ───────────────────────────────────

    @Tool(
        "query_holiday",
        description="按名称查询特定节日（春节/端午/中秋/母亲节/清明节/国庆节等）的日期、是否放假等。支持指定年份或具体日期。",
        brief_description="查询特定节日的日期与详情",
        parameters=[
            ToolParameterInfo(
                name="name",
                param_type=ToolParamType.STRING,
                description="节日名称（支持别名，如 端午节 / 端午 / Dragon Boat Festival / 清明节 / 清明）",
                required=True,
            ),
            ToolParameterInfo(
                name="year",
                param_type=ToolParamType.INTEGER,
                description="目标公历年份，用于查询该年的节日日期（默认当前年）",
                required=False,
            ),
            ToolParameterInfo(
                name="day",
                param_type=ToolParamType.STRING,
                description="相对日期：今天/昨天/明天，或 today/yesterday/tomorrow；用于精确检查某天是否为该节日",
                required=False,
            ),
            ToolParameterInfo(
                name="at",
                param_type=ToolParamType.STRING,
                description="可选绝对日期（ISO，如 2026-10-01）；若填写则优先于 day/year",
                required=False,
            ),
        ],
    )
    async def tool_query_holiday(
        self,
        name: str = "",
        year: int | None = None,
        day: str = "",
        at: str = "",
        **kwargs: Any,
    ) -> dict[str, Any]:
        """供 LLM 调用的特定节日查询工具。

        Args:
            name: 节日名称（必填，支持别名）。
            year: 可选目标年份。
            day: 相对日期别名（用于精确匹配某天）。
            at: 绝对日期 ISO。
            **kwargs: Host 透传（忽略）。

        Returns:
            dict[str, Any]: ``{"name": "query_holiday", "content": "..."}``
        """
        del kwargs
        if not name or not str(name).strip():
            supported = ", ".join(SUPPORTED_HOLIDAY_NAMES[:10])
            return {
                "name": "query_holiday",
                "content": f"请提供节日名称（例如：端午节、清明节、母亲节）。支持：{supported} ..."
            }

        try:
            result = build_holiday_context_from_plugin(
                self,
                name=name,
                year=year,
                day=day or None,
                at=at or None,
            )
        except Exception as exc:
            logger = getattr(getattr(self, "ctx", None), "logger", None)
            if logger is not None:
                logger.warning(f"query_holiday Tool 失败: {exc}", exc_info=True)
            return {"name": "query_holiday", "content": f"查询节日失败: {exc}"}

        if "error" in result:
            return {"name": "query_holiday", "content": str(result["error"])}

        # 构造 LLM 友好摘要
        content = str(result.get("text") or "")
        extra_parts: list[str] = []
        if result.get("holiday_name"):
            extra_parts.append(f"节日={result['holiday_name']}")
        if result.get("dates"):
            extra_parts.append(f"日期={','.join(result['dates'])}")
        if result.get("year"):
            extra_parts.append(f"年份={result['year']}")
        if result.get("occurs") is not None:
            extra_parts.append("出现=是" if result["occurs"] else "出现=否")
        if result.get("type"):
            extra_parts.append(f"类型={result['type']}")
        # 法定放假提示
        statutory = result.get("statutory") or {}
        if statutory and statutory.get("on_holiday"):
            extra_parts.append(f"放假=是({statutory.get('holiday_name') or '法定节假日'})")
        elif statutory and statutory.get("is_makeup_workday"):
            extra_parts.append("调休补班=是")

        if extra_parts:
            content = content.rstrip() + "\n" + "；".join(extra_parts)

        return {"name": "query_holiday", "content": content}
