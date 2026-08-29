# 日期上下文插件

> 提供**今天**的日期/农历/节日公开 API 供其他插件调用；提供 `query_date`/`query_holiday` 工具供 LLM 查询日期与特定节日。  
> **可选**在回复模型 / Planner 模型请求前注入日期轻量上下文（星期/节日/节气/调休，默认关闭）。

- **插件 ID**：`github.xiexiaojia780.date-context-plugin`
- **版本**：1.4.5
- **作者**：[xiexiaojia780](https://github.com/xiexiaojia780)
- **License**：`GPL-3.0-or-later`（与 `_manifest.json` / 根目录 `LICENSE` 一致，GNU GPLv3）
- **反馈**：[GitHub Issues](https://github.com/xiexiaojia780/date_context_plugin/issues)（发现 Bug 或有建议欢迎提 Issue）
- **LLM Tool**：`query_date`（昨天/今天/明天）、`query_holiday`（按名称查特定节日）
- **公开 API**：`date` / `date_text`（**查今天**）、`holiday`（**今天 + 名称过滤**）
- **可选 Hook**：两个独立 WebUI 分组、单独开关——「回复模型注入」`reply_injection.enabled` / 「Planner 注入」`planner_injection.enabled`（默认均 `false`）

## 命名规范（公开 API 与 LLM Tool 成对）

| 公开 API（资源名，供其他插件） | LLM Tool（`query_` + 同一资源词根，供模型） |
|---|---|
| `date` | `query_date` |
| `date_text` | （`query_date` 的轻量变体，无独立 Tool） |
| `holiday` | `query_holiday` |

- 公开 API 固定查**今天**；Tool 支持相对日（昨天/今天/明天）、绝对日期（`at`）与年份（`year`）。
- Tool 不与 API 直接同名：两者在 Host 组件表中曾因同名撞名，故 Tool 统一加 `query_` 前缀。

## 仓库结构

```
date_context_plugin/
├── _manifest.json   # 插件元数据与依赖声明
├── plugin.py        # 入口：节日判定 + 可选 Hook + 混入 Tool/API
├── date_api.py      # 集中数据 + 公开 API（查今天 / 今日节日） + Tool（日期 + 特定节日）
├── README.md
├── LICENSE          # GPL-3.0-or-later
└── _locales/        # i18n 占位
```

## 功能

### 可选：模型请求前注入（星期/节日/节气/调休）

两个注入功能**完全独立**，在 WebUI 中是两个独立配置分组，各自有自己的开关（均默认关闭）：

| WebUI 分组 | 配置项 | 注入目标 | Hook 事件 |
|---|---|---|---|
| 「回复模型注入」 | `reply_injection.enabled` | 回复模型请求 | `maisaka.replyer.before_model_request` |
| 「Planner 注入」 | `planner_injection.enabled` | Planner 模型请求 | `maisaka.planner.before_request` |

| 值 | 行为 |
|---|---|
| `false`（默认） | **不注入**，不影响 prompt 前缀缓存 |
| `true` | 在已有 system 消息之后插入日期上下文，仅含本周几/节日/节气/调休信息 |

- 两个开关**任意组合**：可以只开回复注入、只开 Planner 注入、都开或都关。
- 注入内容相同，由「日期」分组的 `include_*` 开关统一控制；两个 Hook 的消息格式一致（`[{"role": ..., "content": ...}, ...]`），注入位置一致，共用同一天的注入缓存。

详细日期（公历、农历）需走 Tool `query_date`，Hook 保持轻量以最大程度降低对前缀缓存的影响。

开启后注入示例：

```
[昨天]（星期三）
[今天]（星期四）（端午节）（夏至）（法定节假日放假）
[明天]（星期五）
```

- `[昨天]` / `[今天]` / `[明天]` 三行固定出现，**星期始终有**。
- 节日、节气、法定节假日/调休**有内容时才出现**，无空括号。
- 不含公历日期、农历日期。

插入位置在**已有连续 system 之后**（不是最顶部），以降低对前缀缓存的破坏。

### 公开 API（查今天）

调用 `date` / `date_text` 固定查询**今天**，返回当天的公历、农历、节日、法定节假日/调休等信息。
调用 `holiday` 可查询**今天**是否为特定节日（支持名称过滤）。

| API 名 | 说明 |
|---|---|
| `date` | 结构化结果（文本 + 农历/节日/调休等字段） |
| `date_text` | 仅返回渲染文本 `{"text": "..."}` |
| `holiday` | 今日特定节日匹配（可选 `name` 参数过滤）；无名称时返回今天所有节日摘要 |

```python
result = await self.ctx.api.call(
    "github.xiexiaojia780.date-context-plugin.date"
)
if isinstance(result, dict) and "error" not in result:
    text = result["text"]
    festivals = result.get("festival_names", [])

r = await self.ctx.api.call(
    "github.xiexiaojia780.date-context-plugin.date_text"
)

h = await self.ctx.api.call(
    "github.xiexiaojia780.date-context-plugin.holiday",
    name="端午节"
)
occurs = h.get("occurs")
dates = h.get("dates", [])
```

`holiday` 可选参数：`name`（节日名称，支持别名）、`timezone`。
`date` / `date_text` 可选参数：`timezone`、`include_*` 系列开关。

### LLM Tool：`query_date`

| 参数 | 必填 | 说明 |
|---|---|---|
| `day` | 否 | `今天` / `昨天` / `明天`，或 `today` / `yesterday` / `tomorrow`；默认 `今天` |
| `at` | 否 | 绝对日期 ISO，如 `2026-10-01`；若填写则优先于 `day` |

### LLM Tool：`query_holiday`

按名称查询特定节日的日期与放假/调休信息。支持指定年份，或用 `day`/`at` 精确检查某天。

`year` 查询**只返回落在该公历年份内**的日期：跨年的农历节日按公历年份归位
（例如 2026 年的除夕为 2026-02-16、腊八为 2026-01-26），不会混入相邻年份的结果。

| 参数 | 必填 | 说明 |
|---|---|---|
| `name` | 是 | 节日名称，支持别名（如 `端午节` / `端午` / `Dragon Boat Festival`、`清明节` / `清明`） |
| `year` | 否 | 目标公历年份（默认当前年）；只返回落在该年份内的日期 |
| `day` | 否 | 相对日期：`今天`/`昨天`/`明天` 等；用于精确检查“某天是不是该节日” |
| `at` | 否 | 绝对日期 ISO（如 `2026-06-19`）；优先于 `day`/`year` |

支持的节日示例（部分）：
- 春节（含除夕）、元宵节、端午节、七夕节、中秋节、重阳节、腊八节、龙抬头、中元节
- 元旦、清明节、劳动节、端午节、中秋节、国庆节
- 情人节、妇女节、植树节、愚人节、青年节、儿童节、建党节、建军节、教师节、万圣节、平安夜、圣诞节
- 母亲节、父亲节、感恩节

示例调用（由模型决定何时使用）：
- “端午节是哪天？” → `query_holiday(name="端午节", year=2026)`
- “今天是中秋节吗？” → `query_holiday(name="中秋节", day="今天")`
- “2026 年清明节放假吗？” → 模型会拿到 `query_holiday` 返回的日期与法定信息。

用 `day`/`at` 精确检查某天时，返回文本会带上具体日期标签（如「10月1日是法定节假日（国庆节），放假」），不会误写成“今天”。

### 信息源

- 公历日期与星期（配置时区）
- 农历日期
- 法定节假日 / 调休补班（`chinese-calendar`）
- 传统农历节日、24 节气、常见公历/西方节日

## 安装

1. 放到 MaiBot 的 plugins 目录。
2. 重启或热重载。
3. 依赖由 `_manifest.json` 自动安装。

```bash
pip install "cnlunar>=0.2.4" "chinese-calendar>=1.11.0"   # 可选
```

## 启用

```toml
[plugin]
enabled = true
```

## 配置

```toml
[plugin]
enabled = true
config_version = "1.4.5"

[date]
timezone = "Asia/Shanghai"
datetime_format = "%Y年%m月%d日"
include_lunar = true
include_traditional_festivals = true
include_statutory_holidays = true
include_solar_terms = true
include_western_festivals = true

# 两个独立分组，可分别开关（也可只在 WebUI 中切换）
[reply_injection]
enabled = false  # true=回复模型请求前自动注入（星期/节日/节气/调休）；false=不注入（默认）

[planner_injection]
enabled = false  # true=Planner 模型请求前自动注入（同上格式）；false=不注入（默认）
```

`date`/`date_text`/`query_date` 的日期文本使用固定格式（`【今天】今天是 …`），无自定义模板；`query_holiday` 检查具体日期时带日期标签（如「10月1日是…」）；Hook 注入使用独立轻型格式。

WebUI 中为两个独立分组：「回复模型注入」/「Planner 注入」，各自开关互不影响。

## 命令

本插件不提供用户侧 `/command`。

- **可选 Hook 注入**：`reply_injection.enabled`（回复模型）/ `planner_injection.enabled`（Planner）—— 两个独立分组，单独开关（星期/节日/节气/调休，轻量格式）
- **公开 API**：`date` / `date_text` —— 查今天；`holiday` —— 今日特定节日（可传 `name`）
- **Tool**：`query_date` —— LLM 查昨天 / 今天 / 明天；`query_holiday` —— 按名称查特定节日（支持年份/日期）

## 权限 / 能力说明

| 项 | 说明 |
|---|---|
| 网络 | 无外网请求 |
| 文件 / 数据库 | 无持久化 |
| 消息发送 | 不主动发消息 |
| Hook | 可选；`reply_injection.enabled=true`（回复模型）或 `planner_injection.enabled=true`（Planner）时生效，两开关相互独立 |
| Tool | `query_date`（日期）、`query_holiday`（按名称查特定节日） |
| 公开 API | `date` / `date_text`（查今天）、`holiday`（今天 + 可选名称过滤） |

## 工作原理

1. **API**：固定算今天，返回结构化数据或文本。
2. **Tool**：`query_date` 按 `day`/`at` 查昨天/今天/明天（查询今天时额外提示昨日/明日）；`query_holiday` 按 `name`+`year` 查节日日期（仅返回该年份），或用 `day`/`at` 精确检查某天。
3. **Hook（可选）**：`reply_injection.enabled=true` 时在回复模型请求、`planner_injection.enabled=true` 时在 Planner 模型请求的 system 段落后插入日期轻量上下文（星期/节日/节气/调休），不含公历/农历日期，最大程度降低对前缀缓存的影响。两个开关相互独立、任意组合；注入内容与位置一致，共用同一天内的缓存。

### 关于 Prompt 前缀缓存

- **默认关闭注入**，不改消息列表前缀。
- 若开启注入：日期插在**已有 system 之后**，尽量保留顶部固定人设可被缓存；同一天内默认格式不含时分，抖动较小。
- 开启注入仍会让「注入点之后」的前缀随日期变化——这是动态日期的固有代价。

## 故障排查

| 现象 | 处理 |
|---|---|
| 想注入但没有 | WebUI「回复模型注入」或「Planner 注入」分组中打开开关（或配置 `reply_injection.enabled` / `planner_injection.enabled = true`），且 `plugin.enabled = true`（注入格式为轻量，只有星期/节日/节气/调休） |
| 不想注入但仍有 | 确认两个分组开关均为 `false` 并热重载/重启 |
| 升级后注入开关失效 | 旧版 `date.inject_on_model_request` / `date.inject_on_planner_request` 已拆分为独立分组 `reply_injection.enabled` / `planner_injection.enabled`，请在新分组中重新打开 |
| 模型调不到 `query_date` | 确认插件启用、工具列表含 `query_date` |
| 模型调不到 `query_holiday` | 确认插件启用、工具列表含 `query_holiday`，并提供有效的 `name` |
| 其他插件调 API 失败 | 用全名，如 `github.xiexiaojia780.date-context-plugin.date` 或 `...holiday` |
| 调休信息缺失 | 升级 `chinese-calendar` |
| 报 `ZoneInfoNotFoundError` / 时区错误 | Windows 环境需要 `tzdata` 包（已由 `_manifest.json` 声明自动安装）；若手动部署请 `pip install tzdata` |
| 查询不到某年节日 | 法定节假日受 `chinese_calendar` 数据年份范围限制（超出会跳过并记录日志）；农历节日受 `cnlunar` 支持范围限制 |

## 常见问题

**Q：默认会不会注入？**

不会。`reply_injection.enabled` 与 `planner_injection.enabled` 默认均为 `false`。需要时在 WebUI 对应分组中分别打开。

**Q：回复注入和 Planner 注入有什么区别？**

- 回复注入（`reply_injection.enabled`）：挂在 `maisaka.replyer.before_model_request`，影响最终回复模型看到的上下文。
- Planner 注入（`planner_injection.enabled`）：挂在 `maisaka.planner.before_request`，影响规划模型（决定调用哪些工具）看到的上下文——让 Planner 在规划时就知道今天的星期/节日/调休。
- 两者是独立配置分组、单独开关、任意组合；注入内容与格式完全相同（由「日期」分组的 `include_*` 统一控制）。

**Q：API 能不能单独查某一天？**

公开 API 固定查**今天**。需要任意日期请用 Tool `query_date`。

**Q：除夕怎么判定？**

按「明日是否正月初一」。

**Q：如何查询端午节 / 清明节的日期？**

使用 Tool `query_holiday`，传入 `name`（支持别名）和可选 `year`；`year` 只返回该公历年份内的日期。例如：
- `query_holiday(name="端午节", year=2026)`
- `query_holiday(name="清明", day="今天")`

**Q：API 能查特定节日吗？**

`holiday` API 仅查询**今天**（可传 `name` 过滤）。需要查其他日期/年份请使用 Tool `query_holiday`。

**Q：为什么某年查不到法定节假日？**

`chinese_calendar` 库有数据年份覆盖上限，超出范围时会跳过并记录警告日志。

## 许可证

**GPL-3.0-or-later**（`_manifest.json` 与根目录 `LICENSE` 一致）。
