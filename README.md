# 日期上下文注入插件

> 在每次模型请求前注入"当前日期 / 星期 / 农历 / 节日 / 节气 / 是否调休"，并可通过公开 API 供其他插件复用。

- **插件 ID**：`github.xiexiaojia780.date-context-plugin`
- **版本**：1.3.0
- **作者**：[xiexiaojia780](https://github.com/xiexiaojia780)
- **License**：`GPL-3.0-or-later`（与 `_manifest.json` / 根目录 `LICENSE` 一致，GNU GPLv3）
- **Hook**：`maisaka.replyer.before_model_request`（BLOCKING / NORMAL / SKIP）
- **公开 API**：`get_date_context` / `get_date_text`（实现见 `date_api.py`）

## 仓库结构

```
date_context_plugin/
├── _manifest.json   # 插件元数据与依赖声明
├── plugin.py        # 入口：Hook 注入 + 混入公开 API
├── date_api.py      # 独立公开 API 模块
├── config.toml      # 默认配置示例
├── README.md
├── LICENSE          # GPL-3.0-or-later
└── _locales/        # i18n 占位
```

## 功能

### Hook 自动注入

每次发送给 LLM 的消息列表中，会在已有 system 消息之后插入一条 system 消息，内容形如：

```
【当前日期】现在是 2026年06月19日 星期五，农历二零二六年五月初五。今天是法定节假日（端午节），放假。回复时如涉及日期、节日等请以此为准。
```

可注入的信息源（每项可在配置里独立开关）：

- 当前日期与星期（按配置时区计算）
- 农历日期（年/月/日，自动剥掉"大/小月"标识）
- 法定节假日放假与调休补班状态（数据来自 `chinese-calendar`）
- 传统农历节日：春节、元宵、龙抬头、端午、七夕、中元、中秋、重阳、腊八、除夕
- 24 节气
- 常见公历 / 西方节日：元旦、情人节、妇女节、植树节、愚人节、劳动节、青年节、儿童节、建党节、建军节、教师节、国庆节、万圣夜、万圣节、平安夜、圣诞节，以及按"第 N 个周 X"计算的母亲节 / 父亲节 / 感恩节

### 公开 API（供其他插件调用）

API 逻辑独立在 `date_api.py`，通过 `DateContextAPIMixin` 挂到插件上。

| API 名 | 说明 |
|---|---|
| `get_date_context` | 结构化日期上下文（文本 + 农历/节日/调休等） |
| `get_date_text` | 仅返回渲染后的文本（与 Hook 模板同源） |

```python
# 推荐全名，避免短名冲突
result = await self.ctx.api.call(
    "github.xiexiaojia780.date-context-plugin.get_date_context"
)
if isinstance(result, dict) and "error" not in result:
    text = result["text"]
    festivals = result["festival_names"]
    on_holiday = result["statutory"]["on_holiday"]

# 只要文本
r = await self.ctx.api.call(
    "github.xiexiaojia780.date-context-plugin.get_date_text"
)

# 查指定日期
result = await self.ctx.api.call(
    "github.xiexiaojia780.date-context-plugin.get_date_context",
    at="2026-10-01",
    timezone="Asia/Shanghai",
)
```

可选参数：`at`、`timezone`、`include_lunar`、`include_traditional_festivals`、`include_statutory_holidays`、`include_solar_terms`、`include_western_festivals`。

失败时返回 `{"error": "..."}`（例如 `plugin.enabled=false`）。

## 安装

1. 将本目录放到 MaiBot 的plugins目录下。
2. 重启 MaiBot 或等待其热重载插件。
3. **Python 依赖无需手动安装**：`_manifest.json` 已声明，Host 加载时会按 `python_package` 自动安装。

若需在开发环境手动安装（可选）：

```bash
pip install "cnlunar>=0.2.4" "chinese-calendar>=1.11.0"
```

依赖说明（与 manifest 一致）：

| 包 | 用途 | 声明位置 |
|---|---|---|
| `cnlunar>=0.2.4` | 农历日期、节气、传统节日落点 | `_manifest.json` → `dependencies` |
| `chinese-calendar>=1.11.0` | 法定节假日 / 调休补班判定 | `_manifest.json` → `dependencies` |
| `maibot-plugin-sdk`（`maibot_sdk`） | 插件运行时 | Host 自带，插件不重复声明 |

## 启用

插件默认随安装启用。可在 `config.toml` 中切换：

```toml
[plugin]
enabled = true
```

或通过 WebUI 配置面板（"插件" / "日期注入" 两个分组）切换。

## 配置

`config.toml` 完整字段：

```toml
[plugin]
enabled = true               # 是否启用插件
config_version = "1.3.0"     # 配置版本（与 _manifest.json 对齐）

[date]
timezone = "Asia/Shanghai"                       # IANA 时区名
datetime_format = "%Y年%m月%d日"                 # strftime 格式（不含星期）
include_lunar = true                             # 附带农历日期
include_traditional_festivals = true             # 附带传统农历节日
include_statutory_holidays = true                # 附带法定节假日 / 调休补班
include_solar_terms = true                       # 附带 24 节气
include_western_festivals = true                 # 附带常见公历 / 西方节日
template = "【当前日期】现在是 {datetime} {weekday}{lunar}。{festivals}回复时如涉及日期、节日等请以此为准。"
```

`template` 支持的占位符：

- `{datetime}`：按 `datetime_format` 渲染的时间字符串
- `{weekday}`：星期一 ~ 星期日（手动映射，不依赖系统 locale）
- `{lunar}`：形如 `，农历二零二六年五月初五`，关闭 `include_lunar` 后为空
- `{festivals}`：拼接好的节日 / 调休段落，每条以"。"结尾，没有节日时为空

## 命令

本插件不提供用户侧 `/command` 或 `@Tool`。

- **Hook**：自动在模型请求前注入日期上下文（对用户不可见）
- **公开 API**：供其他插件调用 `get_date_context` / `get_date_text`

## 权限 / 能力说明

| 项 | 说明 |
|---|---|
| `capabilities` | 空：不申请 Host 额外能力白名单 |
| 网络 | **不发起**任何外网请求；节日/调休均本地库计算 |
| 文件 / 数据库 | 不读写插件数据目录，无持久化状态 |
| 消息发送 | 不主动发消息到聊天；仅改模型请求中的 `messages` |
| Hook | 订阅 `maisaka.replyer.before_model_request`（`BLOCKING` + `ErrorPolicy.SKIP`，失败跳过注入，不阻断主流程） |
| 公开 API | `public=True`：允许其他插件通过 `self.ctx.api.call(...)` 调用 |
| 用户隐私 | 不采集用户消息内容，不依赖 QQ 号 / 群号 |

## 工作原理

订阅 `maisaka.replyer.before_model_request` Hook（BLOCKING 模式），拿到 Host 序列化后的 `messages` 后：

1. 计算当前带时区时间，渲染日期 / 星期 / 农历
2. 收集节日 / 调休 / 节气信息并按来源去重（法定节假日优先，避免"春节"既出现在法定又出现在传统）
3. 用模板渲染成一条 system 消息，**插在已有连续 system 消息之后**（不是消息列表最顶部）
4. 通过 `{"action": "continue", "modified_kwargs": {"messages": new_messages}}` 把新消息列表交回 Host

公开 API 在 `date_api.py`：复用插件内部的农历/节日判定，保证与 Hook 注入一致；通过 `@API(..., public=True)` 注册，其他插件经 `self.ctx.api.call(...)` 调用。

出错时 Hook 按 `ErrorPolicy.SKIP` 跳过本次注入；API 返回 `{"error": ...}`，不抛异常打断调用方。

### 关于 Prompt 前缀缓存（重要）

以 DeepSeek API 的**上下文硬盘缓存**为例（默认开启，用户无需改代码）：

- 后续请求若与之前请求在**前缀上存在重复**，重复部分可从缓存拉取，计为「缓存命中」。
- **命中前提**：对应前缀单元已经落盘，且后续请求能**完整匹配**该缓存前缀单元（不是“部分相似就算命中”）。
- 落盘时机包括：请求输入/输出结束位置、多次请求间的**公共前缀检测**、长文本按固定 token 间隔截取等。

因此，**前缀是否从消息列表开头保持稳定**，直接决定能不能反复命中固定人设、系统设定等公共内容。

#### 为什么日期注入位置很关键

日期上下文会随时间变化（至少按天；若 `datetime_format` 含时分则更频繁）。两种插法对比：

| 插法 | 请求形态（简化） | 缓存表现（按 DeepSeek 规则理解） |
|---|---|---|
| 插在**最顶部** | `日期(变)` + `固定 system` + … | 开头就变了。新请求无法完整匹配「旧日期 + 固定 system」这类前缀单元；固定 system 也不在请求最前，难以作为可复用的**公共前缀**被命中 |
| 插在**已有 system 之后**（本插件） | `固定 system` + `日期(变)` + … | 开头的固定 system 可在多轮/多请求间形成公共前缀并落盘；日期变化只影响其后段落，不把整段稳定前缀一起打废 |

DeepSeek 文档中的类似关系：

- `A+B` 之后再来 `A+B+C` → 可完整匹配 `A+B`，命中缓存。
- `A+B` 之后再来 `A+C` → **不能**完整匹配 `A+B`，本轮不命中；但系统可能把公共前缀 `A` 单独落盘，供之后的 `A+D` 命中。

把「固定 system」看成 `A`、把「变化的日期」看成 `B`/`C`：  
**应让 `A` 始终在最前**，不要把每天都变的日期当成 `A`。

#### 本插件的做法

- **插入位置**：紧跟在现有**连续** `system` 消息之后，尽量让 Host 侧稳定的 system 前缀留在请求开头，便于公共前缀缓存。
- **默认格式**：`datetime_format = "%Y年%m月%d日"`（不含时分），同一自然日内注入文本更稳定，减少无意义的前缀抖动。

即便如此，日期/节日变化仍会使「注入点之后」的内容无法与昨天完整前缀单元对齐——这是动态时间上下文的固有代价，无法完全消除；本插件只是避免把**整段固定人设**也拖进失效范围。

若你通过公开 API 自行拼 prompt，请同样遵守：

1. 不要把会变的日期文本放进消息列表最顶部的固定 system。
2. 稳定人设 / 系统设定在前，动态日期在后。
3. 非必要不要在 `datetime_format` 里加 `%H:%M` 等分钟级字段。

## 故障排查

| 现象 | 可能原因 | 处理 |
|---|---|---|
| 插件未出现在列表 | 目录缺 `_manifest.json` / `plugin.py`，或 manifest 校验失败 | 检查字段与 URL 是否为合法 `http(s)://`；看 Host 启动日志 |
| 加载失败 `No module named 'date_api'` | 旧版本导入写法 | 使用当前仓库（`plugin.py` 已含相对导入 + 回退） |
| 依赖安装失败 | 网络或 `cnlunar` / `chinese-calendar` 不可用 | 按「安装」节手动 pip；确认 Python ≥ 3.12 |
| 不注入日期 | `plugin.enabled=false`，或 Hook 报错被 SKIP | 查 WebUI 开关；看日志是否有 `chinese_calendar` warning / 注入异常 |
| 其他插件调 API 失败 | 本插件未加载，或短名冲突 | 确认本插件已启用；改用全名 `github.xiexiaojia780.date-context-plugin.get_date_context` |
| API 返回 `error` | 插件禁用、非法时区、非法 `at` | 读返回体 `error` 字段；检查 `timezone` / `at` |
| 调休信息缺失 | `chinese-calendar` 年份覆盖不足 | 升级 `chinese-calendar`；日志会有超范围 warning |
| 与另一份副本冲突 | 同 `id` 的插件目录重复 | 只保留一份 `date_context_plugin`，不要同时放 api 副本 |

## 常见问题

**Q：会不会破坏 DeepSeek 一类模型的前缀缓存？**

会有影响，但已按「缓存前缀须完整匹配」的规则做了缓解：

- 日期消息**不插在最顶部**，而是插在已有 system 之后，让固定 system 仍可作为请求开头的公共前缀被落盘和命中。
- 默认日期格式不含时分，同一天内文本更稳。
- 若改成含 `%H:%M` 等会分钟级变化的格式，注入点之后更难形成可复用的稳定前缀——这是预期行为，不是 bug。

说明：不同厂商的缓存实现细节可能不同；上文以 DeepSeek 官方「上下文硬盘缓存 / 前缀完整匹配 / 公共前缀落盘」规则为参照。原则通用：**易变内容不要挡在稳定前缀前面**。

**Q：调休信息会不会过期？**

`chinese-calendar` 的法定节假日数据有年份覆盖上限（取决于当前安装版本）。超出范围时插件会捕获 `NotImplementedError`，记一条 warning 日志并跳过当行调休信息，其余信息照常注入。如需更新覆盖范围，升级 `chinese-calendar` 即可：

```bash
pip install --upgrade chinese-calendar
```

**Q：能不能自定义注入文本？**

改 `template` 字段即可，四个占位符全部可选。完全删掉占位符也行（例如只想注入纯日期）。

**Q：除夕怎么判定的？**

按"明日是否为正月初一"判定，比硬编码腊月廿九 / 三十更稳（部分年份腊月只有廿九）。

**Q：母亲节 / 父亲节 / 感恩节怎么算？**

按"本月的第 N 个星期 X"计算：5 月第 2 个周日 = 母亲节，6 月第 3 个周日 = 父亲节，11 月第 4 个周四 = 感恩节。

**Q：星期会不会因为系统 locale 不同变成英文？**

不会。星期走的是内置 `_WEEKDAY_ZH` 中文映射，不依赖系统 locale。

## 许可证

本插件以 **GPL-3.0-or-later** 授权：

- `_manifest.json` → `"license": "GPL-3.0-or-later"`
- 根目录 `LICENSE` → GNU General Public License Version 3

分发或修改时请遵循 GPLv3（或后续版本）要求。

