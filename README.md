# 日期上下文插件

> 提供**今天**的日期/农历/节日公开 API 供其他插件调用；提供 `query_date` 工具供 LLM 查询昨天/今天/明天。  
> **可选**在模型请求前注入日期轻量上下文（星期/节日/节气/调休，默认关闭）。

- **插件 ID**：`github.xiexiaojia780.date-context-plugin`
- **版本**：1.4.2
- **作者**：[xiexiaojia780](https://github.com/xiexiaojia780)
- **License**：`GPL-3.0-or-later`（与 `_manifest.json` / 根目录 `LICENSE` 一致，GNU GPLv3）
- **反馈**：`483403354@qq.com`（发现 Bug 或有建议欢迎来邮）
- **LLM Tool**：`query_date`（昨天/今天/明天）
- **公开 API**：`date` / `date_text`（**查今天**）
- **可选 Hook**：`date.inject_on_model_request`（默认 `false`）

## 仓库结构

```
date_context_plugin/
├── _manifest.json   # 插件元数据与依赖声明
├── plugin.py        # 入口：节日判定 + 可选 Hook + 混入 Tool/API
├── date_api.py      # 公开 API（查今天）+ Tool（昨天/今天/明天）
├── README.md
├── LICENSE          # GPL-3.0-or-later
└── _locales/        # i18n 占位
```

## 功能

### 可选：模型请求前注入（星期/节日/节气/调休）

配置项 `date.inject_on_model_request`：

| 值 | 行为 |
|---|---|
| `false`（默认） | **不注入**，不影响 prompt 前缀缓存 |
| `true` | 在已有 system 消息之后插入日期上下文，仅含本周几/节日/节气/调休信息 |

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

| API 名 | 说明 |
|---|---|
| `date` | 结构化结果（文本 + 农历/节日/调休等字段） |
| `date_text` | 仅返回渲染文本 `{"text": "..."}` |

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
```

可选参数：`timezone`、`include_lunar`、`include_traditional_festivals`、`include_statutory_holidays`、`include_solar_terms`、`include_western_festivals`。

### LLM Tool：`query_date`

| 参数 | 必填 | 说明 |
|---|---|---|
| `day` | 否 | `今天` / `昨天` / `明天`，或 `today` / `yesterday` / `tomorrow`；默认 `今天` |
| `at` | 否 | 绝对日期 ISO，如 `2026-10-01`；若填写则优先于 `day` |

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
config_version = "1.4.2"

[date]
timezone = "Asia/Shanghai"
datetime_format = "%Y年%m月%d日"
include_lunar = true
include_traditional_festivals = true
include_statutory_holidays = true
include_solar_terms = true
include_western_festivals = true
inject_on_model_request = false   # true=自动注入（昨天/今天/明天，星期/节日/节气/调休）；false=不注入（默认）
```

API/Tool 返回的日期文本使用固定格式（`【今天】今天是 …`），无自定义模板；Hook 注入使用独立轻型格式。

也可在 WebUI「日期」分组里切换 **是否在模型请求前自动注入**。

## 命令

本插件不提供用户侧 `/command`。

- **可选 Hook 注入**：`inject_on_model_request` 控制是否注入（星期/节日/节气/调休，轻量格式）
- **公开 API**：`date` / `date_text` —— 查今天
- **Tool**：`query_date` —— LLM 查昨天 / 今天 / 明天

## 权限 / 能力说明

| 项 | 说明 |
|---|---|
| 网络 | 无外网请求 |
| 文件 / 数据库 | 无持久化 |
| 消息发送 | 不主动发消息 |
| Hook | 可选；`inject_on_model_request=true` 时生效 |
| Tool | `query_date` |
| 公开 API | `date` / `date_text`（查今天） |

## 工作原理

1. **API**：固定算今天，返回结构化数据或文本。
2. **Tool**：按 `day`/`at` 查昨天/今天/明天，查询今天时额外提示昨日/明日。
3. **Hook（可选）**：`inject_on_model_request=true` 时，在 system 段落后插入日期轻量上下文（星期/节日/节气/调休），不含公历/农历日期，最大程度降低对前缀缓存的影响。

### 关于 Prompt 前缀缓存

- **默认关闭注入**，不改消息列表前缀。
- 若开启注入：日期插在**已有 system 之后**，尽量保留顶部固定人设可被缓存；同一天内默认格式不含时分，抖动较小。
- 开启注入仍会让「注入点之后」的前缀随日期变化——这是动态日期的固有代价。

## 故障排查

| 现象 | 处理 |
|---|---|
| 想注入但没有 | 设 `date.inject_on_model_request = true`，且 `plugin.enabled = true`（注入格式为轻量，只有星期/节日/节气/调休） |
| 不想注入但仍有 | 确认配置为 `false` 并热重载/重启 |
| 模型调不到 `query_date` | 确认插件启用、工具列表含 `query_date` |
| 其他插件调 API 失败 | 用全名 `github.xiexiaojia780.date-context-plugin.date` |
| 调休信息缺失 | 升级 `chinese-calendar` |

## 常见问题

**Q：默认会不会注入？**

不会。默认 `inject_on_model_request = false`。需要时再打开。

**Q：API 能不能单独查某一天？**

公开 API 固定查**今天**。需要任意日期请用 Tool `query_date`。

**Q：除夕怎么判定？**

按「明日是否正月初一」。

## 许可证

**GPL-3.0-or-later**（`_manifest.json` 与根目录 `LICENSE` 一致）。
