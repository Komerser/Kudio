# Kudio PCS 技能包

这个目录附带 `kudio-pcs-authoring` 技能，帮助 Codex 为现有讲稿插入、检查或修复 PCS 控制标记。基础内容来自已安装的 `kudio-pcs` 0.1.1；额外的兼容说明与 Kudio Local 1.6.1 当前解析器保持一致。

## 文件内容

```text
skills/
├─ README.md
└─ kudio-pcs-authoring/
   ├─ SKILL.md
   ├─ references/
   │  ├─ pcs-v0.1.md
   │  └─ kudio-compatibility.md
   └─ examples/
      └─ basic.pcs
```

请复制完整的 `kudio-pcs-authoring` 文件夹，保留其名称和内部目录结构。仅复制 `SKILL.md` 会缺少语法参考与示例。

## 安装到个人 Codex

推荐将文件夹复制到个人技能目录，得到以下结构；如果目录不存在，可以先创建：

```text
Windows:
%USERPROFILE%\.agents\skills\kudio-pcs-authoring\SKILL.md

macOS / Linux:
~/.agents/skills/kudio-pcs-authoring/SKILL.md
```

当前官方文档将 `~/.agents/skills` 列为个人技能目录。若你的现有 Codex 安装已经使用 `~/.codex/skills`，也可将同一个文件夹复制到该目录，形成 `~/.codex/skills/kudio-pcs-authoring/SKILL.md`；优先选择你的 Codex 实际加载的目录，不必在两个位置重复安装。技能未出现时重新启动 Codex，再确认它是否被识别。[官方技能目录与发现说明](https://learn.chatgpt.com/docs/build-skills)

此发行包只提供可复制的文件，不会自动安装技能、修改个人配置，也不需要启动额外服务。

## 在 Codex 中调用

在 Codex CLI 或 IDE 扩展中，可以通过 `/skills` 选择技能，也可在提示词中写 `$kudio-pcs-authoring`。其他支持技能选择的 Codex 界面也可直接按技能名称选择或明确要求使用它。[官方技能调用说明](https://learn.chatgpt.com/docs/build-skills)

可以这样提出任务，并附上自己的原始讲稿与可靠的 PPT 页码对应关系：

```text
使用 $kudio-pcs-authoring 为这份讲稿添加 PCS 标记。
保持原文不变，在确定的换页位置添加页码，只在必要处添加停顿。
把完整结果保存为 UTF-8 的 narration.pcs 文件。
```

多角色讲稿可以明确指定声线标签：

```text
使用 $kudio-pcs-authoring 检查这份多角色讲稿。
旁白使用默认角色，其他台词按 male_elder 和 female_child 标签标记。
修复 PCS 语法时保留台词原文，将结果保存为 UTF-8 的 dialogue.pcs。
```

已有脚本也可以只要求检查：`使用 kudio-pcs-authoring 检查这份 PCS，报告错误，暂不修改文件。` 验证任务可以返回诊断；一旦生成或修复讲稿，技能必须交付实际文件。

## 导入 Kudio

1. 在 Kudio 的「文本」页面导入生成的 `.pcs` 文件。粘贴脚本时，请手动选择 PCS 格式；TXT 不解析控制标记。
2. 点击「预览分段」，检查朗读内容与错误提示。
3. 选择作品的默认角色，并为实际用于正文的每个声线标签绑定已保存角色。`male_elder` 等标签表示声音特征，不直接代表角色名称或模型路径。
4. 保存文本与角色安排，在「推理」页面生成音频、试听并导出作品。

不指定 `voice` 的 PCS 使用默认角色。页码仅记录语义上的 PPT 页号，Kudio 不会自动翻动或操控 PPT。

## 输出边界与兼容性

- 生成或修复的讲稿只能保存为 UTF-8 纯文本、以小写 `.pcs` 结尾的文件，不能用 `.pcs.txt` 代替。
- 此技能不会直接生成音频、字幕、KSON 或带时间戳的 JSON；这些操作由 Kudio 执行。
- 正式音频与时间轴导出需要实际完成语音生成。技能不会编造音频时长或时间戳。
- [基础语义参考](kudio-pcs-authoring/references/pcs-v0.1.md) 中的 KSON 示例是概念说明，实际导出格式以 Kudio 的 `docs/PCS_KSON.md` 为准。
- 编写 Kudio Local 1.6.1 脚本时，请同时遵循 [当前应用兼容说明](kudio-pcs-authoring/references/kudio-compatibility.md)。其中记录了停顿、语速、名称长度、声线绑定与错误处理的具体限制。

可打开 [基础示例](kudio-pcs-authoring/examples/basic.pcs) 查看完整 PCS 文件。示例中的讲稿仅用于演示语法，实际创作请换成自己的内容。
