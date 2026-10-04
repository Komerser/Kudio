# Kudio

**从第一句话，到一整本有声书。**

Kudio 1.6.1 是基于本地 GPT-SoVITS 的脚本与有声书创作工具。在配置、文本、推理三个工作区中完成角色管理、配音安排、逐段生成、试听与 WAV / SRT / KSON 导出。

完整更新报告与运行逻辑见 [docs/UPDATE_1.6.1.md](docs/UPDATE_1.6.1.md)，正式 PCS / KSON 规范见 [docs/PCS_KSON.md](docs/PCS_KSON.md)。发布包附带 [PCS 编写技能及安装说明](skills/README.md)。

## 功能

- 蓝色界面，支持简体中文 / English / 日本語，顶部菜单切换并记住语言；界面语言与配音语言独立。
- 独立的引擎配置与角色配置页面；角色卡片支持描述、声线标签、头像与立绘。
- TXT / PCS 导入、文本编辑、阅读预览与朗读片段预览；解析结构与原始数据收纳于流程弹窗的高级详情。
- PCS 声线与角色绑定：普通文本使用默认角色，`#[voice:male_elder]#` 等声线标签在文本页分配具体角色。
- 控制边界内自然分段、有序推理计划与片段指纹；重新编译尽可能复用成功音频。
- 跨作品复用角色；作品保留角色配置快照，删除角色库预设后仍可继续推理。
- 角色模型逐段切换、片段独立参数与重新生成；绑定变更仅使受影响音频待生成。
- 可指定模型与参考音频目录，扫描候选文件；现代 Windows 文件选择框按用途记住最近目录。
- 连续试听，刷新生成进度时保留当前播放。
- Estimated / Exact 时间轴、Page / Pause / Rate / Section 事件和 KSON 预览。
- 整本与分组 WAV 导出，自动附带片段级 SRT 字幕和 KSON 时间轴；旧章节分组收纳于高级导出。
- 按作品名组织文件，支持回收站恢复及确认后永久删除。

## 快速开始

1. 准备可正常使用的 Windows GPT-SoVITS 整合包，含 `api_v2.py` 和 `runtime/python.exe`。
2. 解压 Kudio 发布包，或下载源码到可写目录。
3. 双击 `启动Kudio.bat`，按提示选择引擎目录；邻近目录只有一套引擎时自动识别。
4. 打开 http://127.0.0.1:8765/ 。在“配置 → 引擎”选择素材目录并扫描，在“配置 → 角色”选择 GPT、SoVITS 模型和参考音频，填写参考原文并保存角色。可添加角色描述、声线标签、头像和立绘。
5. 在“文本”导入 TXT / PCS，预览分段并创建作品。选择默认角色；PCS 指定了其他声线时，为每种声线选择角色并保存安排。
6. 启动引擎，进入“推理”开始生成；完成后试听、单段重做或导出。右侧流程可点击查看处理详情。

作品保存所选角色的配置快照。修改角色库后，已有作品继续使用原配置；在文本页点击“同步角色库配置”才更新已安排角色的快照，并将声音参数变化的片段标为待生成。普通“保存声音安排”保留已有快照；角色页“保存并设为默认角色”会同步本次保存的角色。

本地开发目录名为 `Kudio_Local`，发布仓库目录为 `Kudio`。程序与启动脚本均根据自身位置定位项目文件，移动或改名后无需修改项目路径。

可用 `配置引擎.bat` 更换引擎。Kudio 不包含 Python、GPT-SoVITS、模型或参考音频；复用整合包运行环境。已验证 v2pro 整合包目录结构，其他版本需自行验证。

## 数据与升级

`data/projects/作品名--编号/` 保存作品和音频，`data/trash/` 保存待恢复作品，`settings.json` 保存本机引擎与素材目录。目录均不提交到 Git。升级前退出程序并备份 `data` 和 `settings.json`，覆盖程序文件即可；旧格式作品在启动时迁移。

回收站仍占用磁盘空间；永久删除需输入作品名。独立历史备份不会随作品删除而清理。现有作品的模型路径是绝对路径，迁移电脑后需重新配置。

1.4.0 使用 `schema_version: 2`，原始脚本保存在 `project.json` 的 `source_text`。旧项目在启动时补充元数据，迁移前保存 `project.schema-1.backup.json`，保留原片段 ID 和 WAV，标记为 `legacy`。旧项目可继续生成、试听、编辑片段、导出；在脚本工作区点击“应用并编译脚本”后才转换为源码项目。源码项目的正文统一在源稿中修改，单段窗口用于声音参数和重做。

## PCS

PCS（Paragraphs Control Script）是给人类和 AI 编写的简单控制脚本，推荐 UTF-8 `.pcs`。导入 `.pcs` 使用“PCS · 控制脚本”；导入 `.txt` 使用“TXT · 自动分段”，沿用原有自然语言分段，不按文件内容识别脚本格式。TXT 中的 `#[p:1]#`、未知标签和反斜杠均作为普通正文保留。直接粘贴正文时默认 TXT，需要控制指令时手动选择 PCS。

| 指令 | 示例 | 作用 |
| --- | --- | --- |
| Page | `#[p:1]#` | 后续语音属于正整数页码；只是语义值，不读取 PPT |
| Pause | `#[pause:800]#` | 插入 0–30000ms 静音，取代该边界默认段间停顿 |
| Rate | `#[rate:0.9]#` | 后续语速倍率 0.5–2.0，映射到 GPT-SoVITS `speed_factor` |
| Section | `#[section:intro]#` | 后续语音的章节名称，1–80 个字符 |
| Voice | `#[voice:male_elder]#` | 后续语音的声线标签，在文本页绑定角色；不直接引用角色名 |

```text
#[p:1]#[section:intro]#长假结束，周末还要上班，这种安排是不是只有中国有？
#[p:2]#这期我们从全年日历开始。#[pause:800]#先来看中国。
#[p:3]#[rate:0.9]#这里有一个重要细节。
```

五种控制都构成硬边界，前后正文不会合并到同一次推理。Page、Rate、Section、Voice 持续生效；未指定 Rate 时沿用角色或片段语速。连续标签既支持上例共用 `#` 的写法，也支持完整相接的 `]##[`。`\#[p:1]#` 表示普通正文，推理文本恢复为 `#[p:1]#`；这是显式的文字转义。

日常操作在文本页编辑正文、预览片段与安排角色，推理页负责进度、试听、重做与导出。右侧流程按钮打开大弹窗，按需查看解析结构、推理计划与高级 JSON。点击控制标签或诊断可返回原文定位。“应用文本修改”才更改项目，打字期间保留旧音频；未应用修改、未保存角色安排或解析错误时禁止启动推理和导出。TXT 中的声线指令也会当作普通文字；只有 PCS 支持多角色控制。

未知指令、非法参数、未闭合或嵌套标签均产生带源码位置的错误；控制标签不会进入 TTS。支持上表五种指令，不执行 PPT、视频、鼠标、镜头或动画操作。

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

生成 `dist/Kudio-版本-windows.zip` 及 SHA-256 校验文件。打包采用文件白名单，包含运行程序、对应版本更新报告、PCS / KSON 规范和 PCS 技能；不复制个人数据、配置、日志、测试或开发工具。`dist/` 已加入 `.gitignore`。

仓库的 Windows 发布流程在推送 `v版本` 标签后运行后端与前端回归检查，再打包并上传 ZIP 和校验文件到 GitHub Release。标签必须与 `VERSION.txt` 一致，失败时不会发布附件；不执行需要本机模型的真实引擎测试。流程采用 [GitHub 官方令牌机制](https://docs.github.com/en/actions/concepts/security/github_token) 与 [GitHub CLI 发布命令](https://cli.github.com/manual/gh_release_create)。

## 开发验证

```console
python test_app.py
python test_pcs.py
python test_timeline.py
python test_pipeline.py
node test_switching.cjs
node test_pcs.cjs
node test_rebuild.cjs
node test_i18n.cjs
```

后端仅使用 Python 标准库。前端为原生 HTML/CSS/JavaScript，无需 npm 安装。混合格式 WAV 导出使用引擎自带的 `runtime/ffmpeg.exe`。

界面翻译源文件为 `i18n-static.json`、`i18n-core.json`、`i18n-rebuild.json` 与 `i18n-studio.json`。修改后运行 `python build_i18n.py` 生成离线字典；构建发布包时也会自动生成并检查插值参数。语言选择保存于浏览器，不影响正文和配音设置。

配置了可用本机音色预设后，可选择运行 `python test_real_engine.py`（可加 `--preset <预设ID>`）。它用隔离项目验证真实本地推理与三种导出，结果位于 `dist/pcs-smoke/`；测试结束只停止由该测试启动的引擎，不改动现有项目。

## 使用边界

- 标准 WAV 最大约 4 GB，超出时应分段导出。
- SRT 为片段级时间轴，不是逐字强制对齐。
- Visual 是可靠的只读结构预览；原始源码在 Raw 模式编辑。未应用草稿只在当前页面会话中保留，刷新前请应用。
- 删除或变化的片段旧 WAV 暂留项目目录作缓存，只有当前计划中的 Done 片段参与导出。
- 推理与官方训练面板依赖所选 GPT-SoVITS 版本和硬件。
- 工具监听本机地址；请勿直接作为公网服务部署。

GPT-SoVITS、模型及素材使用各自许可。本仓库尚未选定开源许可证；公开仓库不自动授予开源使用许可。
