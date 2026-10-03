# Kudio

**从第一句话，到一整本有声书。**

Kudio 1.4.0 是基于本地 GPT-SoVITS 的脚本与有声书创作工具。导入 TXT 或 PCS，查看解析结构与推理计划，配置音色，逐段生成音频，试听并导出 WAV / SRT / KSON。

## 功能

- TXT / PCS 导入、源码编辑、可视化控制 Chip、解析诊断与 AST。
- 控制边界内自然分段、有序推理计划与片段指纹；重新编译尽可能复用成功音频。
- 音色预设、片段独立参数与重新生成。
- 可指定模型与参考音频目录，扫描候选文件，也可用 Windows 文件选择框直接挑选模型和音频。
- 连续试听，刷新生成进度时保留当前播放。
- Estimated / Exact 时间轴、Page / Pause / Rate / Section 事件和 KSON 预览。
- 整本与分组 WAV 导出，自动附带片段级 SRT 字幕和 KSON 时间轴；旧章节分组收纳于高级导出。
- 按作品名组织文件，支持回收站恢复及确认后永久删除。

## 快速开始

1. 准备可正常使用的 Windows GPT-SoVITS 整合包，含 `api_v2.py` 和 `runtime/python.exe`。
2. 解压 Kudio 发布包，或下载源码到可写目录。
3. 双击 `启动Kudio.bat`，按提示选择引擎目录；邻近目录只有一套引擎时自动识别。
4. 打开 http://127.0.0.1:8765/ ，导入作品。在声音设置中选择 GPT、SoVITS 模型和参考音频，或指定素材目录后从扫描结果中选择，并填写参考原文。
5. 应用音色配置，启动引擎，开始生成。

本地开发目录名为 `Kudio_Local`。程序、启动脚本和预览脚本均根据自身位置定位项目文件，移动或改名后无需修改项目路径。

可用 `配置引擎.bat` 更换引擎。Kudio 不包含 Python、GPT-SoVITS、模型或参考音频；复用整合包运行环境。已验证 v2pro 整合包目录结构，其他版本需自行验证。

## 数据与升级

`data/projects/作品名--编号/` 保存作品和音频，`data/trash/` 保存待恢复作品，`settings.json` 保存本机引擎与素材目录。目录均不提交到 Git。升级前退出程序并备份 `data` 和 `settings.json`，覆盖程序文件即可；旧格式作品在启动时迁移。

回收站仍占用磁盘空间；永久删除需输入作品名。独立历史备份不会随作品删除而清理。现有作品的模型路径是绝对路径，迁移电脑后需重新配置。

1.4.0 使用 `schema_version: 2`，原始脚本保存在 `project.json` 的 `source_text`。旧项目在启动时补充元数据，迁移前保存 `project.schema-1.backup.json`，保留原片段 ID 和 WAV，标记为 `legacy`。旧项目可继续生成、试听、编辑片段、导出；在脚本工作区点击“应用并编译脚本”后才转换为源码项目。源码项目的正文统一在源稿中修改，单段窗口用于声音参数和重做。

## PCS

PCS（Paragraphs Control Script）是给人类和 AI 编写的简单控制脚本，推荐 UTF-8 `.pcs`。`.txt` 含控制标签时自动识别为 PCS，普通 TXT 继续按自然语言分段。

| 指令 | 示例 | 作用 |
| --- | --- | --- |
| Page | `#[p:1]#` | 后续语音属于正整数页码；只是语义值，不读取 PPT |
| Pause | `#[pause:800]#` | 插入 0–30000ms 静音，取代该边界默认段间停顿 |
| Rate | `#[rate:0.9]#` | 后续语速倍率 0.5–2.0，映射到 GPT-SoVITS `speed_factor` |
| Section | `#[section:intro]#` | 后续语音的章节名称，1–80 个字符 |

```text
#[p:1]#[section:intro]#长假结束，周末还要上班，这种安排是不是只有中国有？
#[p:2]#这期我们从全年日历开始。#[pause:800]#先来看中国。
#[p:3]#[rate:0.9]#这里有一个重要细节。
```

四种控制都构成硬边界，前后正文不会合并到同一次推理。Page、Rate、Section 持续生效；未指定 Rate 时沿用项目或片段语速。连续标签既支持上例共用 `#` 的写法，也支持完整相接的 `]##[`。`\#[p:1]#` 表示普通正文，推理文本恢复为 `#[p:1]#`；这是显式的文字转义。

界面流程为“脚本 → 解析 → 音色 → 推理 → 时间轴与导出”。Raw 编辑源码，Visual 查看控制 Chip；点击 Chip 或诊断定位源码。解析显示有序 AST，预览显示实际 Speech / Control 计划。“应用并编译”才更改项目，打字期间保留旧音频；未应用修改或解析错误时禁止启动推理和导出。

未知指令、非法参数、未闭合或嵌套标签均产生带源码位置的错误；控制标签不会进入 TTS。仅支持上表四种指令，不实现 voice、PPT、视频、鼠标、镜头或动画。

## KSON

`.kson` 是合法标准 JSON，表示 Kudio 的机器可读时间轴。`format: "kson"`、`version: "0.1"`、`timebase: "ms"`、`duration_ms`、`segments` 和 `events` 为核心字段。

- `segments`：什么时候说了什么，包括起止毫秒、页码、章节和实际语速。
- `events`：控制发生的时刻；Pause 含起止时间，其他控制含 `time_ms`。同一时刻保留源码顺序。
- `timing_status`：生成前为 `estimated`，完成后为 `exact`；正式导出仅接受完整实测时间轴。

WAV、SRT、KSON 共用一个 Timeline Builder。实测时读取 WAV 帧数，不相信缓存时长；混合格式统一采样格式后计算。普通边界使用项目 `gap`，显式 Pause 替代它，连续 Pause 相加，开头和结尾的 Pause 保留。整本 WAV 导出自动附带同名 SRT / KSON，也可单独导出 SRT / KSON。

## PCS → KSON Pipeline

```text
PCS Source → Lexer / Parser / Validator → AST → Compiler → Execution Plan
→ GPT-SoVITS → Audio Timeline → WAV / SRT / KSON
```

后端权威解析；前端不维护另一套 Parser。模块职责和正式格式规范见 [docs/PCS_KSON.md](docs/PCS_KSON.md)。

## 构建发布包

使用 Python 3.9 或以上运行：

```console
python build_release.py
```

生成 `dist/Kudio-版本-windows.zip` 及 SHA-256 校验文件。打包采用文件白名单，不复制个人数据、配置和日志。`dist/` 已加入 `.gitignore`，可将 ZIP 手动附加到 GitHub Release。

## 开发验证

```console
python test_app.py
python test_pcs.py
python test_timeline.py
python test_pipeline.py
node test_switching.cjs
node test_pcs.cjs
```

后端仅使用 Python 标准库。前端为原生 HTML/CSS/JavaScript，无需 npm 安装。混合格式 WAV 导出使用引擎自带的 `runtime/ffmpeg.exe`。

配置了可用本机音色预设后，可选择运行 `python test_real_engine.py`（可加 `--preset <预设ID>`）。它用隔离项目验证真实本地推理与三种导出，结果位于 `dist/pcs-smoke/`；测试结束只停止由该测试启动的引擎，不改动现有项目。

## 使用边界

- 标准 WAV 最大约 4 GB，超出时应分段导出。
- SRT 为片段级时间轴，不是逐字强制对齐。
- Visual 是可靠的只读结构预览；原始源码在 Raw 模式编辑。未应用草稿只在当前页面会话中保留，刷新前请应用。
- 删除或变化的片段旧 WAV 暂留项目目录作缓存，只有当前计划中的 Done 片段参与导出。
- 推理与官方训练面板依赖所选 GPT-SoVITS 版本和硬件。
- 工具监听本机地址；请勿直接作为公网服务部署。

GPT-SoVITS、模型及素材使用各自许可。本仓库尚未选定开源许可证；公开仓库不自动授予开源使用许可。
