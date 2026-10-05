# Kudio 1.6.4 更新与维护报告

日期：2026-10-05。基线：最新 `main` 的 Kudio 1.6.3（`b50f414dc892182b07a30e607d4f66dac1f59b8f`）；修改前已核对本地发布仓库与远端一致。

本版收敛 PCS、缓存和编辑器的行为。相同源稿重新编译可得到相同 Event ID；编辑器统一生成连续控制的 canonical 写法；Voice 在结构预览、执行计划、时间轴和三语摘要中获得完整支持。源码失配时保留旧音频，明确要求重新编译。

## 运行逻辑与数据权威

```text
PCS / TXT source_text
  → Backend Lexer / Parser / Validator
  → Ordered AST + diagnostics
  → Compiler
  → Ordered Execution Plan + Segment snapshots
  → GPT-SoVITS / saved WAV
  → One integer PCM Frame Timeline
  → WAV / SRT / KSON
```

**PCS is authored. AST is parsed. Execution Plan is compiled. Timeline is measured. KSON is exported.**

**PCS 用来写，AST 用来解析，Execution Plan 用来执行，Timeline 用来计时，KSON 用来交换。**

| 数据 | 权威关系 |
| --- | --- |
| `source_text` | 唯一正文来源，不自动 trim、格式化或改变换行 |
| AST / diagnostics | 从源稿派生、可重新计算的缓存 |
| Execution Plan / segments | 编译快照；执行顺序和每段实际状态，不能由缓存替代 |
| Timeline | 基于计划与 WAV 帧数计算的统一媒体时钟；缺音频时明确 Estimated |
| KSON | 对外机器可读表示，由已有 Timeline 导出 |

## 需求逐项落实

1. **实际文件**：见后方变更清单。新增公共源码哈希模块与跨层契约测试；沿用标准库本地 HTTP 和原生前端。
2. **PCS grammar**：仍为 0.1 的 `#[command:value]#`，仅 `p/pause/rate/section/voice` 五种指令，没有新增语法或 command。AST 仍为平面有序 TextNode / ControlNode；`raw_value/valid/source_start/source_end` 保留。
3. **Canonical syntax**：连续控制共享边界 `#`，例如 `#[p:1]#[rate:0.9]#正文`。Parser 继续接受旧双 `#` 写法。后端 `format_pcs_result()` 只删除相邻合法控制接缝上的第二个 `#`，保留正文、空白、CRLF、标签原始值和转义；非法源稿拒绝格式化。前端插入与显式格式化均调用后端，不实现第二套 Parser。
4. **Event ID 算法**：取原始 `source_hash`、`source_start`、`source_end`、正式事件 `type`、Parser 转换后的语义 `value`。将身份按 `json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)` 编码为 UTF-8，再取完整 SHA-256，输出 `evt_` 加 64 位小写十六进制。同一源稿重编译稳定；内容或位置变化允许 ID 变化。因为包含整份 source hash，正文变化也可能改变其他事件 ID。
5. **Segment ID 不改**：继续使用既有语义匹配和 fingerprint 复用。Segment ID 关联 WAV 路径、生成状态、音频版本和独立参数；确定化 Event ID 不应破坏这些持久身份。旧随机 Event ID 仍可读取，只有显式应用编译才更新。
6. **唯一 source hash**：合并至 `kudio/source.py`。Compiler / Projects 引用同一函数；算法完全保持 `SHA256(text.encode('utf-8')).hexdigest()`，包括原始空白、换行和控制标签，避免无故令旧项目失效。
7. **AST / diagnostics 持久化**：schema 2 继续保存，明确降为缓存。新增 `parse_cache` 身份（当前源稿 hash、格式、PCS 版本），旧缓存缺身份或失配时重解析。顶层 `source_hash` 仍属于已编译快照，不随缓存刷新更新。推理及正式导出重新解析实际源稿，校验控制语义、跨度、Speech 顺序及事件穿插边界，不能信任旧 AST。读取失效项目时不返回旧 Timeline / KSON，WAV、片段与原计划不会被偷偷覆盖。
8. **chapter / section**：TXT 的 `chapter` 保留“第一章、序章”等自然识别和旧项目兼容。PCS 正式章节使用作者指定的 `section`；片段展示、搜索、分组建议和 KSON 优先使用它。内部 `chapter` 可以保留兼容信息，不作为第二种 PCS 控制维度。
9. **Voice UI**：工具栏与统一 command/value 插入器加入 Voice，默认标签 `narrator`；Control Chip / AST 显示 `VOICE · label`；执行计划显示 Voice Event；时间轴显示 Voice 标记；Parser Summary 统计 Page、Pause、Rate、Section、Voice 五类。中文、English、日本語均进入离线 i18n。格式化响应带光标映射，Unicode codepoint 与 textarea UTF-16 索引正确转换；过期编辑、切换作品和缓存刷新响应不会覆盖新草稿。
10. **KSON schema**：仍为 0.1，顶层十字段冻结为 `format/version/timebase/generator/project/source/timing_status/duration_ms/segments/events`。`speech.rate` 仍在 speech 内，`voice_label/role_id/role_name` 仍在 segment 顶层；保留 Segment 状态快照与 Event。将声线元数据移入 speech 仅记为未来 0.2 的讨论，不在本版实施。
11. **Timeline 行为**：帧时钟、gap/pause 和导出算法不变。`A#[p:2]#B` 中 A 结束于 100ms、gap 为 300ms 时，Page 在 100ms 发生，B 在 400ms 开始。WAV / SRT / KSON 共用同一个 Exact Frame Timeline。失效计划现在隐藏时间轴，保留 WAV 试听，并提供重新编译入口；这修正的是有效性呈现，不是控制事件语义。
12. **server.py 审查**：没有为减小文件做大规模拆分。现有 Parser、Compiler、Projects、Roles、Timeline、Exporters 已独立；剩余引擎进程、锁、队列和路由共享运行状态，贸然拆分会影响任务恢复和模型生命周期。本版只接入格式化 API、失效视图和 section 分组选择。
13. **测试**：完整结果见后方。保留已有 regression tests，并新增稳定 ID、formatter、音频复用、缓存和跨层时钟验证。
14. **技术债**：见后方维护边界，区分本版兼容取舍与后续可改进点。

## PCS 0.1 支持范围

| 指令 | 参数与行为 |
| --- | --- |
| `p` | 正整数页码，持续 Page 状态；仅语义页码 |
| `pause` | 整数 0–30000ms，插入静音并替代该边界默认 gap |
| `rate` | 十进制数 0.5–2.0，持续 Rate 状态 |
| `section` | trim 后 1–80 字符，持续章节名称 |
| `voice` | trim 后 1–80 字符，不含换行或控制字符；持续声线标签，在文本页绑定实际角色 |

```text
#[p:1]#[section:intro]#[voice:narrator]#大家好。#[pause:800]#[p:2]#[rate:0.9]#下面进入第二部分。
```

每个控制构成硬边界。标签前的正文使用默认角色；未绑定的 Voice 标签可编辑与预览，但阻止正式推理。TXT 不按内容识别 PCS，`#[voice:test]#` 和反斜杠仍是普通正文；`.pcs` 才进入 PCS 模式。非法值、未知指令、未闭合和嵌套标签都有带源码位置的诊断。完整规范见 [PCS_KSON.md](PCS_KSON.md)。

## 实际变更文件

| 范围 | 文件与用途 |
| --- | --- |
| 公共后端 | 新增 `kudio/source.py`；修改 `kudio/compiler.py`、`kudio/projects.py`、`kudio/pcs.py`：公共 hash、稳定事件、缓存权威及 canonical formatter |
| 服务与版本 | `kudio/server.py`：format API、stale 视图、PCS section 建议；`kudio/exporters.py`：只更新默认 generator 版本；`VERSION.txt`：1.6.4 |
| 编辑器与工作区 | `pcs-editor.js`、`index.html`、`rebuild.js`、`studio.js`、`segmentation.js`：Voice、显式格式化、五类摘要、stale 与竞态处理、PCS section |
| 翻译 | `i18n-static.json`、`i18n-core.json`、`i18n-rebuild.json`、`i18n-studio.json`、`i18n-catalog.js`：新 UI 和诊断进入三语目录 |
| 回归测试 | `test_pcs.py`、`test_pipeline.py`、新增 `test_contracts.py`；`test_pcs.cjs`、`test_i18n.cjs`：协议、缓存、UI 与跨层回归 |
| 打包与文档 | `build_release.py`、`.github/workflows/release.yml`、`README.md`、`使用说明.txt`、`docs/PCS_KSON.md`、本报告 |
| PCS 技能 | `skills/README.md`、`skills/kudio-pcs-authoring/SKILL.md`、`references/pcs-v0.1.md`、`references/kudio-compatibility.md`、`examples/basic.pcs`：0.1 冻结与 canonical 示例 |

`kudio/timeline.py`、TXT 分段器、TTS 引擎接口、模型与角色解析算法未改变。历史 1.6.3 报告保留用于追溯。

## 验证记录

| 检查 | 结果 |
| --- | --- |
| `python -B test_app.py` | 24 项通过，含本机 ffmpeg 混合格式转换 |
| `python -B test_pcs.py` | 33 项通过 |
| `python -B test_timeline.py` | 16 项通过，含帧时钟与统一时间轴验证 |
| `python -B test_pipeline.py` | 25 项通过 |
| `python -B test_contracts.py` | 7 项通过 |
| `node test_switching.cjs` | 3 组通过 |
| `node test_pcs.cjs` | 11 组通过 |
| `node test_rebuild.cjs` | 17 项通过 |
| `node test_i18n.cjs` | 17 项通过；三语目录 725 keys |
| 离线发布包 | 白名单打包；核对运行文件、版本、技能与当前报告，CRC 和 SHA-256 校验 |

后端合计 105 项，前端合计 48 项/组。测试使用隔离临时数据，不调用正式项目或真实 GPU 推理。干净 CI 没有引擎 ffmpeg 时，混合格式集成检查明确跳过；其余测试不依赖本机模型。发布工作流加入 `test_contracts`，继续在 Windows 上测试后再发布。

另在临时数据目录启动隔离界面，实测中文 / English / 日本語、双 `#` 格式化、共享边界插入、Unicode 光标、五类统计、Voice AST 和执行计划。正式引擎与项目未用于界面测试。

新增验证覆盖：两次编译相同源码 ID 相同；控制值/位置改变 ID 相应改变；Segment / Done / audio_version / WAV 复用；shared-hash 与旧 double-hash 兼容；formatter 幂等、正文/转义/CRLF 保留、非法候选与光标映射；Voice Chip、五类统计、计划、时间轴、KSON；TXT 字面标签；公共 hash 与旧算法一致；cache 失配与伪造事件拒绝；Page 100ms / B 400ms；三种出口统一精确帧时钟；旧随机 Event ID 与 schema 2 兼容。

## 发布内容与维护边界

发布 ZIP 使用显式白名单，包含程序、当前报告、规范与可复制安装的 PCS authoring skill，新增 `kudio/source.py`。不含 `data`、`settings.json`、模型、参考音频、日志、缓存、测试或开发工具。技能位置和安装方法见包内 `skills/README.md`。

本版兼容取舍：AST / diagnostics 继续占用项目 JSON，但已有明确 cache 身份与重建规则；旧随机 Event ID 不批量迁移；源稿项目若丢失可信语音源码 span，需显式重新编译，原 WAV 保留。

后续维护可考虑：给引擎进程、锁与数据路径建立显式依赖，再小步提取 server 服务；收敛前端工作区之间的包装覆盖逻辑；为弃用 WAV 增加可审查的缓存清理入口。模型文件内容在路径不变时替换，现有合成指纹仍无法自动感知，需要主动重做。KSON 0.2 元数据布局应另行版本化讨论。

这些事项不影响本版已验证的五层协议约定；本次没有迁移框架、扩展 grammar 或开发下游视频/PPT 功能。
