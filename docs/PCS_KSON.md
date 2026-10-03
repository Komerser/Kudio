# PCS 0.1 / KSON 0.1 规范与维护说明

Kudio 1.4.0；Python 3.9+ 标准库后端、本地 HTTP、原生前端，无构建依赖。

## 1. 职责与模块

| 文件 | 职责 |
| --- | --- |
| `app.py` | 启动入口，为 Windows 隔离 Python 添加程序路径 |
| `kudio/server.py` | 本地服务、路由、任务锁、模型生命周期、队列与项目存储入口 |
| `kudio/pcs.py` | Lexer、Parser、Validator、Diagnostics、AST |
| `kudio/text.py` | 保留原 TXT 自然分段算法，补充源码位置映射 |
| `kudio/compiler.py` | AST → Execution Plan；指纹和音频复用 |
| `kudio/tts.py` | 参数继承、TTS payload、可注入 RPC 的推理与 WAV 验证 |
| `kudio/models.py` | 音色默认值与校验 |
| `kudio/projects.py` | schema 迁移、源码原子应用前校验、计划有效性检查 |
| `kudio/timeline.py` | 唯一时间轴计算器，PCM 帧时钟 |
| `kudio/exporters.py` | 消费同一时间轴，原子写入 WAV / SRT / KSON |
| `pcs-editor.js` | 源码草稿、Backend Parse、Visual、AST、Plan、事件与 KSON 预览 |

移除了 `app.py` 内重复的自然分段和独立 WAV/SRT 时间计算。旧章节建议和分组保留在折叠的“高级导出 / Legacy Grouping”，避免损坏旧项目；不再占据主创作流程。引擎素材扫描和官方训练面板仍在高级工具中。

## 2. PCS grammar

```text
source   = (text | control)*
control  = "#[" command ":" value "]#"
command  = trim(lowercase(command))
value    = trim(value)
```

只按第一个 `:` 分割；如 `#[section:part:one]#` 的名称是 `part:one`。禁止嵌套。控制可以直接连接正文，无需空格或换行。

连续控制支持两种形式：

```text
#[p:1]#[rate:0.9]#正文
#[p:1]##[rate:0.9]#正文
```

第一种共用一个末尾/起始 `#`；AST 两个控制的源码跨度因此重叠一个字符。第二种保留两个完整分隔符。它们具有相同语义。

反斜杠紧邻 `#[` 时，该标签直到第一个 `]#` 被当作普通文字；无闭合则直到源码结尾。只移除这一条反斜杠，不提供其他通用转义语言：

```text
\#[p:1]#  →  朗读正文 #[p:1]#，不产生 Page 事件
```

`.pcs` 强制 PCS 模式；TXT 或自动模式发现未转义 `#[` 候选时进入 PCS，错误候选也必须诊断。推荐 UTF-8；导入保留原始源码、空白和 CRLF。

## 3. Commands / state / hard boundary

| command | value | state 与事件 |
| --- | --- | --- |
| `p` | 十进制正整数 | 持续 Page；输出 `page` event |
| `pause` | 十进制整数 0–30000 | 顺序插入静音；输出 `pause` event |
| `rate` | 十进制数 0.5–2.0 | 持续 Rate；输出 `rate` event |
| `section` | trim 后 1–80 字符 | 持续 Section；输出 `section` event |

每个控制都形成 Hard Boundary。Compiler 先按控制拆出 TextNode，再在节点内部自然分段；没有任何自然分段可跨控制。Page、Section 初始为 null；Rate 初始 null，含义是继承音色语速。显式 PCS Rate 优先于片段 speed override，片段 override 优先于项目语速。

Page 只表示语义页码；Kudio 不读取或操作 PPT。其余指令、表达式、变量、循环、宏、视频、动画等不在 0.1 范围内。

## 4. Lexer / Parser / Diagnostics / AST

Lexer 顺序扫描文本，识别控制、未闭合和嵌套；Parser 建立节点并按首个冒号分出 command/value；Validator 校验四种指令并转换数值类型。Parser 不依赖服务器、项目或 TTS。

```json
[
  {"type":"control","command":"p","value":1,"raw_value":"1","valid":true,"source_start":0,"source_end":7},
  {"type":"text","text":"你好。","source_start":7,"source_end":10}
]
```

所有跨度是原始源码的 Unicode codepoint 半开区间 `[source_start, source_end)`，不是 UTF-8 字节。前端转换到 JavaScript UTF-16 textarea 索引后定位；转义文字保留解码位置映射。AST 即使错误也保留节点，以便可视化诊断。

```json
{"level":"error","code":"PCS_INVALID_PAGE","message":"PPT 页码必须是正整数","source_start":0,"source_end":9,"line":1,"column":1}
```

结构支持 `error` / `warning` / `info`；当前正式诊断主要为 error。代码包含 `PCS_UNKNOWN_COMMAND`、`PCS_INVALID_SYNTAX`、`PCS_INVALID_PAGE`、`PCS_INVALID_PAUSE`、`PCS_INVALID_RATE`、`PCS_INVALID_SECTION`、`PCS_UNCLOSED_TAG`、`PCS_NESTED_TAG`、`PCS_UNEXPECTED_CLOSE`。Errors 令 `valid=false`；Compiler 返回空片段/计划，禁止推理和正式导出。

## 5. Execution Plan / fingerprint

```json
[
  {"kind":"event","id":"event-id","type":"page","page":1,"source_start":0,"source_end":7},
  {"kind":"speech","segment_id":"segment-id"},
  {"kind":"event","id":"pause-id","type":"pause","duration_ms":800},
  {"kind":"speech","segment_id":"next-id"}
]
```

Speech 引用独立 segment，包含 `id/text/chapter/page/section/rate/source_start/source_end/fingerprint/status/error/duration/overrides/audio_version`。AST 表达源结构；计划表达实际执行顺序，二者分别保存。

SHA-256 fingerprint 基于文本、Page、Section、Rate、有效 GPT/SoVITS 模型路径及完整 TTS payload。排除音色显示名称和 gap。依照语义键的有序队列匹配旧片段，每个旧 ID 只能消费一次，避免重复文本错误复用。完全一致才复用 ID、Done、WAV、duration、audio_version、声音覆盖和可恢复的上一版本；变化或新增 Pending；删除片段不进入当前计划。模型路径内容替换但路径不变无法自动检测，需要用户主动重做。

`build_tts_payload` 不解析 PCS；未授权的标签残留会被拒绝。仅 Compiler 记录的显式转义文字可以含字面标签。payload 使用现有 `cut5`，`speed_factor` 来自有效语速。TTS 模块校验真实 PCM 数据，队列保留三次尝试、暂停完成当前片段、失败重试和单段重做。

## 6. Timeline / gap / pause / event ordering

Exact Timeline 使用实际 WAV 帧数及统一 PCM 格式，忽略 `project.json` 中缓存 duration。混合格式由本机引擎 ffmpeg 在临时文件转换，原 WAV 不变。累计整数帧时钟在全部出口统一四舍五入为整数毫秒，避免逐片段取整积累误差。

- 相邻 speech 间无显式 pause：插入 `int(gap_seconds * sample_rate)` 帧，与旧版兼容。
- 有 pause（包括 0ms）：该边界不用默认 gap。
- 连续 pause 顺序累计；完整项目保留开头和结尾 pause。
- 元数据控制在当前 cursor 立即执行；隐式 gap 在下一段语音前插入。
- event 同时刻保留源码顺序，不按类型排序。
- 范围导出从 0 重新计时，并在开头恢复此前最后 Page / Rate / Section 状态；不带入范围外停顿。

假设 A、B 都是 100ms，默认 gap=300ms：

| source | event 时刻 | B 开始 | 总长 |
| --- | --- | --- | --- |
| `A#[p:2]#[pause:800]#B` | Page 100ms；Pause 100–900ms | 900ms | 1000ms |
| `A#[pause:800]#[p:2]#B` | Pause 100–900ms；Page 900ms | 900ms | 1000ms |
| `A#[p:2]#B` | Page 100ms | 400ms | 500ms |

要求 speech start≥0、end>start、事件不为负，时序单调。小于一个毫秒且不能产生正整数毫秒区间的 WAV 会明确拒绝。WAV 导出流式写入并检查 4GB RIFF 上限；8-bit PCM 静音值为 128，其余位深为 0。

## 7. KSON schema / example

扩展名 `.kson`，内容为严格 JSON（不允许 NaN/Infinity），版本独立于 Kudio 程序版本。

```json
{
  "format":"kson",
  "version":"0.1",
  "timebase":"ms",
  "generator":{"name":"Kudio","version":"1.4.0"},
  "project":{"id":"example","title":"示例"},
  "source":{"format":"pcs","pcs_version":"0.1"},
  "timing_status":"exact",
  "duration_ms":1000,
  "segments":[
    {"id":"a","index":1,"text":"A","start_ms":0,"end_ms":100,"duration_ms":100,"page":1,"section":null,"speech":{"rate":1.0},"estimated":false},
    {"id":"b","index":2,"text":"B","start_ms":900,"end_ms":1000,"duration_ms":100,"page":2,"section":null,"speech":{"rate":0.9},"estimated":false}
  ],
  "events":[
    {"id":"p1","type":"page","time_ms":0,"page":1,"estimated":false},
    {"id":"p2","type":"page","time_ms":100,"page":2,"estimated":false},
    {"id":"pause","type":"pause","start_ms":100,"end_ms":900,"duration_ms":800,"requested_duration_ms":800,"estimated":false},
    {"id":"rate","type":"rate","time_ms":900,"value":0.9,"estimated":false}
  ]
}
```

必须保留 `format/version/timebase/duration_ms/segments/events`。segment 是“什么时候说什么”，event 是“什么时候控制发生”；segment metadata 是状态快照，不能替代 event。section event 使用 `name`，rate 使用 `value`；可附加 source span、source hash、inherited 等兼容字段。

生成前预览使用 `timing_status: "estimated"`；未知 speech 按文本长度和语速估算，之后所有受累积误差影响的区间标记 estimated。音频全部可用且真实帧验证通过后自动变为 exact。正式 `.kson`、SRT、WAV 出口均要求 exact，三者消费同一个 frame plan，Exporter 不再解析源码。

## 8. Project schema / migration / source invalidation

schema 2 保留 `source_format/source_text/pcs_version/source_hash/limit/ast/diagnostics/execution_plan`，源码及计划与片段一起写入一个 `project.json`，temp → `os.replace` 原子替换。

旧 project 无源稿时标记 `legacy`，按现有片段顺序重建可编辑草稿；现有 segments 仍权威。迁移前备份旧 JSON，原 WAV 不删除。running 恢复为 pending，Done 缺失 WAV 恢复 pending。老分组元数据继续可用。

浏览器按项目保存当前会话源稿草稿，并按源码 revision/navigation token 丢弃过期响应。编辑使 Parser / AST / Plan / Timeline / Export 标记待更新，禁止用未应用草稿推理。Parse / Preview 不写项目；“应用并编译”先验证整份源码，再替换当前项目计划。错误源码不覆盖已有成功项目。指纹相同复用音频，弃用 WAV 可暂留缓存且无法进入当前导出。

源码项目只在 Source Editor 修改正文；单段窗口允许语音覆盖和重做，避免双重正文来源。恢复旧音频时检查其 fingerprint 与当前音色及源稿语义匹配，防止把旧语速的音频误标成新参数。

## 9. API

| method / path | 作用 |
| --- | --- |
| `POST /api/pcs/parse` | `{text,source_format}` → AST / diagnostics / stats / valid |
| `POST /api/pcs/compile` | `{text,limit,voice,source_format}` → 上述结构 + segments / execution_plan；无推理副作用 |
| `POST /api/create` | 保存源稿创建项目；错误可保存但不可推理 |
| `POST /api/project/source` | `{id,text,limit,source_format}` → 校验并应用编译，匹配旧音频 |
| `GET /api/project?id=...` | 源码、计划、实时状态、timeline、draft kson |
| `POST /api/export-kson` | `{id}` → exact `.kson` |
| `POST /api/export-all` | `{id}` → WAV + SRT + KSON |
| `POST /api/export-srt` | `{id,group?}` → exact SRT |

服务始终监听 loopback；POST 校验同源 Origin。没有云端分析、上传或 telemetry。

## 10. Versioning / testing / limits

新增指令需要同时扩展 Lexer/Validator → Compiler 事件/state → Timeline → KSON → UI，并添加单元及集成测试；未知指令必须继续报错，不能静默当正文。向后兼容新增字段保持 format 版本；语义或必需字段改变必须升级格式版本和迁移策略。

```console
python test_app.py
python test_pcs.py
python test_timeline.py
python test_pipeline.py
node test_switching.cjs
node test_pcs.cjs
python build_release.py
```

可选真实引擎验证：`python test_real_engine.py`。它复用已保存音色预设，在隔离项目运行两段正文并检验实际输出，结果保存于 `dist/pcs-smoke`。需可用 GPT-SoVITS 与模型、参考素材；自动回归用注入 WAV 和 RPC，不依赖 GPU。

当前限制：SRT 是片段级，不是逐字对齐；Visual 只读，Raw 为唯一编辑入口；未应用草稿刷新会丢失；旧缓存 WAV 暂不自动回收；模型路径不变时不会侦测文件内容替换；不提供其他下游系统功能。
