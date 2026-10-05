# Kudio Local 1.6.4 compatibility

This supplement records the constraints checked against Kudio Local 1.6.4. Use it when generating or validating scripts for that application. It takes precedence over the broader conceptual examples in `pcs-v0.1.md`.

The authoritative parser is `Kudio_Local/kudio/pcs.py` (the release contains `kudio/pcs.py`). The application specification and actual KSON schema are documented in `Kudio_Local/docs/PCS_KSON.md` (the release contains `docs/PCS_KSON.md`). These repository paths are source references, not dependencies required to install this skill. This supplement travels with the standalone skill folder.

## Script delivery

Deliver generated or repaired narration only as an actual UTF-8 plain-text file ending in lowercase `.pcs`. Do not deliver `.pcs.txt`, a chat-only script, companion JSON, audio, subtitles, or KSON. Kudio handles compilation, voice binding, audio generation, and exports. Preserve the user's narration unless rewriting is requested.

## Exact parser constraints

Only five commands are supported. Each marker uses `#[command:value]#`. Command names are trimmed and lowercased; values are trimmed. The parser splits at the first `:` only, so a section name may contain further colons. Nested markers are invalid.

| Command | Accepted value in Kudio Local 1.6.4 | Scope |
| --- | --- | --- |
| `p` | ASCII decimal digits `[0-9]+`, representing a positive integer. Signs, fractions, and non-ASCII digits are rejected. | Persistent page state. |
| `pause` | ASCII decimal digits representing an integer from `0` through `30000`, inclusive; unit is milliseconds. | One silence event. |
| `rate` | A finite ordinary decimal number from `0.5` through `2.0`, inclusive. Forms such as `0.8`, `.8`, and `1.` are accepted. Signs, scientific notation, `NaN`, and infinity are rejected. | Persistent speech-rate state. |
| `section` | Between 1 and 80 Unicode codepoints after trimming. A colon is allowed in the name. | Persistent section state. |
| `voice` | Between 1 and 80 Unicode codepoints after trimming, with no characters whose codepoint is below 32, including internal newlines and tabs. Label case is preserved. | Persistent voice-label state. |

Do not silently reduce an out-of-range pause or rate. Report the issue or repair it only within the user's requested scope. The implementation allows repeated or decreasing page numbers and does not currently emit a page-order warning. As an authoring check, flag suspicious ordering without rewriting an intentional return to an earlier slide.

## Boundaries and literal text

Every supported command is a hard TTS boundary: spoken text on opposite sides must not be merged into one inference unit. Page, rate, section, and voice state persists until changed; pause is a one-time event. A `pause:0` is valid and prevents the default inter-segment gap at that boundary. Consecutive pauses accumulate in source order.

PCS 0.1 grammar is frozen. Author, insert and repair adjacent controls in canonical shared-boundary form:

```text
#[p:1]#[rate:0.9]#
```

The parser still accepts double-hash input such as `#[p:1]##[rate:0.9]#`, but do not generate that compatibility form. Kudio's formatter only removes redundant hashes between valid neighboring AST controls; it does not rewrite narration, spacing, escapes or marker values. It refuses invalid scripts instead of guessing repairs.

An immediately preceding backslash makes `#[` literal through the next `]#`; only that one backslash is removed from the spoken text. An escaped opener without a closing marker remains literal through the end of the source. This is not a general-purpose escape language. Do not escape a control that the user intends Kudio to execute.

The source format is explicit. Import `.pcs` as PCS, or select PCS for pasted source. TXT mode never parses PCS: markers and backslashes are ordinary spoken text. Do not assume a pasted script is automatically recognized as PCS.

TXT `chapter` retains natural heading detection for old projects. In PCS, `section` is the authored semantic chapter used by the UI, grouping suggestions and KSON; an internal legacy `chapter` value is not a second PCS control dimension.

## Voice labels and real characters

`#[voice:male_elder]#` names a voice feature, not a character ID, a character name, a model path, or an engine voice command. Kudio's Text page binds each used label to a saved character. Labels are case-sensitive because their case is preserved.

- TXT always uses one default character.
- PCS without `voice` uses the default character; text before the first voice marker also uses it.
- A voice marker applies to following text until another voice marker changes it.
- Use project-provided labels when known. Otherwise prefer stable labels that describe the requested voices and tell the user which labels need character binding.
- An unbound label used by spoken text permits source preview and saving but blocks audio generation.
- A trailing voice marker with no following spoken text does not require a binding.
- A page marker records a semantic page number; Kudio does not open or control PPT files.

## Errors and validation

Unknown commands, missing `:`, nested or unclosed markers, an unescaped `]#` with no matching opener, and invalid values are errors. Unknown commands are not narration and are not experimental features that can be executed. A script containing parser errors has `valid=false`; compilation yields no segments or execution plan and generation/final export is blocked.

The current diagnostic codes include `PCS_UNKNOWN_COMMAND`, `PCS_INVALID_SYNTAX`, `PCS_INVALID_PAGE`, `PCS_INVALID_PAUSE`, `PCS_INVALID_RATE`, `PCS_INVALID_SECTION`, `PCS_INVALID_VOICE`, `PCS_UNCLOSED_TAG`, `PCS_NESTED_TAG`, and `PCS_UNEXPECTED_CLOSE`.

Report syntax and value errors separately from editorial warnings, such as suspicious page ordering. Do not claim that an authoring warning is a diagnostic emitted by the current backend. Source spans count Unicode codepoints and use half-open intervals; they are not UTF-8 byte offsets or JavaScript UTF-16 positions.

## Actual KSON export

The JSON snippets in `pcs-v0.1.md` explain concepts only. They are not the schema generated by Kudio and must not be copied into a fabricated `.kson` file. The authoring skill still delivers only `.pcs`.

The actual KSON format version is `0.1`, independent of the Kudio application version. The frozen top-level model contains `format`, `version`, `timebase` (`ms`), `generator`, `project`, `source`, `timing_status`, `duration_ms`, `segments`, and `events`. `timing_status` is `estimated` or `exact`. A segment describes speech over a time interval; an event describes a control at its execution position. `speech.rate` stays nested while `voice_label`, `role_id` and `role_name` stay at segment level. Event fields differ by type:

| Event | Actual fields relevant to that control |
| --- | --- |
| Page | `type: page`, `time_ms`, `page` |
| Rate | `type: rate`, `time_ms`, `value` |
| Section | `type: section`, `time_ms`, `name` |
| Voice | `type: voice`, `time_ms`, `label` |
| Pause | `type: pause`, `start_ms`, `end_ms`, `duration_ms`, `requested_duration_ms` |

Before all audio exists, Kudio may show an estimated preview. Formal KSON, SRT, and WAV exports require complete validated audio and exact timing derived from actual audio frames. Never invent timestamps or claim that a PCS authoring pass produced real timing.

Control events occur immediately at their source boundary, before any implicit gap preceding the next speech. For `A#[p:2]#B`, if A ends at 100ms and the gap is 300ms, Page occurs at 100ms and B starts at 400ms. New Event IDs are deterministic `evt_` SHA-256 identifiers based on raw source hash, spans, type and semantic value; persistent Segment IDs and existing audio remain reuse-based. These implementation details do not add authoring syntax or let the skill produce measured media.
