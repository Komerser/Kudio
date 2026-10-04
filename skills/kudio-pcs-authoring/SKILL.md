---
name: kudio-pcs-authoring
description: Author, insert, review, or repair Kudio Paragraphs Control Script (PCS) markers for PPT page changes, pauses, voices, rates, and sections. Deliver generated or repaired narration scripts only as UTF-8 files ending in .pcs.
---

# Kudio PCS Authoring

Use this skill when the task involves creating, inserting, reviewing, validating, or repairing PCS markers in narration text for Kudio.

PCS is a lightweight control language embedded in otherwise normal narration. It is designed to be easy for humans and language models to write, while Kudio later converts it into structured KSON timeline data.

Read `references/pcs-v0.1.md` when exact PCS syntax, boundary behavior, validation rules, or downstream Kudio behavior is needed.

For Kudio Local 1.6.3, also read [Kudio compatibility](references/kudio-compatibility.md) before authoring or validating PCS. Its concrete parser limits, voice-label binding rules, and export distinctions take precedence over the conceptual examples in `references/pcs-v0.1.md`. The bundled reference remains the semantic explanation; its KSON examples are not the actual Kudio export schema. The implementation in `Kudio_Local/kudio/pcs.py` and the specification in `Kudio_Local/docs/PCS_KSON.md` are authoritative for the installed application.

## Primary goal

Produce narration that remains natural to read aloud while adding the minimum control markers needed for deterministic downstream rendering.

Do not rewrite the user's spoken text unless the user explicitly asks for rewriting. By default, preserve wording, punctuation, ordering, facts, and tone. Insert or repair PCS only.

## Required script format

The only script artifact this plugin generates or repairs is a UTF-8 plain-text file whose filename ends with the exact lowercase suffix `.pcs`.

- Create and deliver an actual `.pcs` file when generating, inserting markers, repairing, or exporting a script. A chat-only script is not a completed file delivery.
- Never save a generated script as `.pcs.txt`, `.txt`, `.md`, `.json`, `.kson`, `.docx`, or any other extension. Do not generate companion copies in other formats.
- Preserve the requested output directory and filename stem, but normalize the script suffix to `.pcs`. Use `script.pcs` in the working/output directory if no filename was given; do not ask for a filename unnecessarily.
- If the requested name has no extension, append `.pcs`. If it ends in `.PCS`, normalize it to `.pcs`. Otherwise replace its final extension with `.pcs`; collapse duplicate trailing `.pcs` suffixes introduced by this replacement. For example, `lesson.txt` becomes `lesson.pcs`, and `lesson.pcs.txt` becomes `lesson.pcs`.
- Legacy input files such as `.txt` and `.pcs.txt` may be read. Save repaired content to the normalized `.pcs` path; do not overwrite a legacy input using its old extension. Existing `.pcs` files may be edited in place when requested. If preserving an input whose path differs only by case (such as `.PCS` on Windows), use a separate lowercase output such as `lesson-repaired.pcs`.
- The file contains only narration and inline PCS controls. Do not add Markdown fences, generated headings, explanations, JSON wrappers, or validation reports to its content. Preserve headings already present in the user's spoken text.
- KSON conversion and audio/timeline rendering belong to downstream Kudio. This plugin produces the `.pcs` source script; it does not export KSON, JSON, audio, or subtitle artifacts.
- If the host cannot create or attach files, state that `.pcs` file delivery is unavailable. Do not claim a file was saved or substitute another file format.

## Core syntax

PCS controls use:

`#[command:value]#`

Supported PCS v0.1 controls:

- `#[p:N]#` — switch to PPT page N.
- `#[pause:N]#` — insert N milliseconds of silence.
- `#[voice:NAME]#` — use voice NAME from this point onward.
- `#[rate:N]#` — set speech-rate multiplier from this point onward.
- `#[section:NAME]#` — begin semantic section NAME.

PCS markers are control instructions. They must never be spoken by TTS.

## Authoring workflow

1. Inspect the narration and any PPT, slide outline, page mapping, or structural metadata provided by the user.
2. Determine where deterministic control boundaries are required.
3. Insert only the markers needed to express those boundaries.
4. Keep page markers monotonic unless the source intentionally returns to an earlier slide.
5. Place a page marker immediately before the first spoken text belonging to that slide.
6. Treat `p`, `pause`, `voice`, `rate`, and `section` as hard TTS boundaries in PCS v0.1.
7. Never merge spoken text across a hard boundary when describing expected Kudio behavior.
8. Preserve all spoken text around markers exactly unless rewriting was requested.
9. Validate the final PCS syntax and preserve the user's spoken text.
10. Normalize the output filename, save the complete script as UTF-8 `.pcs`, and read it back to verify its suffix, content, and control boundaries before returning a file link.

## Page-marker rules

When a slide mapping is known, prefer:

`#[p:1]#第一页讲稿。#[p:2]#第二页讲稿。`

Each `#[p:N]#` applies to following narration until another page marker appears.

Do not invent slide numbers when the source gives no reliable slide mapping. If page boundaries are uncertain, preserve existing page markers and identify uncertain positions rather than guessing.

When analyzing a PPT directly, base page boundaries on the actual slide content and the semantic point at which the narration begins discussing that slide.

## Pause rules

Use `#[pause:N]#` only for intentional silence that should be deterministic.

Examples:

- short rhetorical pause: `#[pause:300]#`
- normal emphasis pause: `#[pause:600]#`
- strong transition pause: `#[pause:1000]#`

Do not replace ordinary punctuation with pause markers unless precise timing is needed.

## Voice and rate rules

`#[voice:NAME]#` persists until another voice marker changes it.

`#[rate:N]#` persists until another rate marker changes it.

Prefer stable, machine-friendly names such as:

- `narrator`
- `quote_male`
- `quote_female`

Prefer decimal rate multipliers such as `0.9`, `1.0`, and `1.1`.

Do not invent unavailable voice IDs when the project's valid voices are known. Reuse project-defined names.

## Section rules

Use `#[section:NAME]#` for semantic chapters or major transitions, not every paragraph.

Prefer stable identifiers in lowercase kebab-case when the value is intended for machines:

`#[section:international-comparison]#`

If the user's project already uses another naming convention, follow it consistently.

## Output behavior

When asked to generate PCS, save the complete PCS text in the required `.pcs` file and return its file link with a short completion note. Do not substitute a prose description or a chat-only code block for the file.

When asked to validate PCS:
- report syntax errors;
- report invalid values;
- report suspicious page ordering;
- report empty hard-boundary segments;
- report unsupported commands;
- preserve valid content.

Validation-only requests may receive a concise diagnostic report in chat without generating a new file. If a corrected script is produced, deliver it as `.pcs` and keep diagnostics outside the file.

When asked to repair PCS, make the smallest safe change needed and save the repaired script using the required `.pcs` format.

When asked to produce both readable narration and PCS, deliver one readable `.pcs` script with controls inline at the exact execution points.

## Constraints

- Never place spoken content inside a PCS marker.
- Never output malformed controls such as `#[p=2]#` or `[p:2]`.
- Never silently reinterpret an unknown command as a supported command.
- Never allow PCS markers to appear in TTS text.
- Do not add future/reserved controls such as `focus`, `cursor`, `zoom`, `click`, or `highlight` unless the user explicitly requests an experimental extension.
- Do not export a KSON timeline or fabricate audio timestamps. The reference's KSON examples explain downstream processing only.

## Minimal example

Input narration:

`长假结束，周末还要上班，这种安排是不是只有中国有？这期我们就从2026年的全年日历出发，看看其他国家。`

With a known two-slide mapping:

`#[p:1]#长假结束，周末还要上班，这种安排是不是只有中国有？#[p:2]#这期我们就从2026年的全年日历出发，看看其他国家。`

See `examples/basic.pcs` for a fuller example.
