# PCS v0.1 Reference

## File format

Generated and repaired scripts use the exact lowercase `.pcs` extension and
UTF-8 plain text containing narration and inline PCS controls only. `.pcs.txt`
is a legacy input name, not an output format. Validation reports stay outside
the script. The KSON examples below document downstream Kudio behavior; this
plugin delivers `.pcs` scripts and does not export KSON or JSON artifacts.

## 1. Grammar

General form:

```text
#[command:value]#
```

PCS v0.1 intentionally keeps values simple. A parser should first identify complete `#[ ... ]#` blocks, then split the block body at the first `:` into `command` and `value`.

Whitespace immediately outside a marker remains normal narration whitespace.

The five-command PCS 0.1 grammar is frozen. Generated and repaired adjacent
controls use a shared boundary hash, for example
`#[p:1]#[section:intro]#[voice:narrator]#`. Kudio accepts older double-hash
input for compatibility, but authoring output uses the canonical form.

## 2. Supported controls

### `p`

```text
#[p:N]#
```

Meaning: following narration belongs to PPT page `N` until another `p` control occurs.

Validation:
- `N` must be a positive integer.
- Repeated page numbers are allowed when intentional.
- Decreasing page numbers should produce a warning, not an automatic rewrite.

KSON event concept:

```json
{"type":"page","value":2}
```

### `pause`

```text
#[pause:N]#
```

Meaning: insert `N` milliseconds of silence.

Validation:
- `N` must be a non-negative integer.
- Extremely large values may produce a warning.

KSON event concept:

```json
{"type":"pause","duration_ms":800}
```

### `voice`

```text
#[voice:NAME]#
```

Meaning: set persistent voice state.

KSON event concept:

```json
{"type":"voice","value":"narrator"}
```

### `rate`

```text
#[rate:N]#
```

Meaning: set persistent speech-rate multiplier.

Recommended normal range: `0.5` to `2.0`.

KSON event concept:

```json
{"type":"rate","value":0.9}
```

### `section`

```text
#[section:NAME]#
```

Meaning: begin a semantic section.

KSON event concept:

```json
{"type":"section","value":"conclusion"}
```

## 3. Hard-boundary semantics

PCS v0.1 treats all supported controls as TTS hard boundaries.

Given:

```text
ABC#[p:2]#DEF
```

Kudio must never send `ABCDEF` as one TTS inference unit across the page control.

Conceptually:

```text
TTS("ABC")
PAGE(2)
TTS("DEF")
```

The same rule applies to pause, voice, rate, and section controls.

This makes control timing deterministic without requiring forced alignment.

## 4. State model

Persistent state:
- current page
- current voice
- current rate
- current section

Transient event:
- pause

A narration segment inherits the current persistent state at the point where it begins.

## 5. PCS to KSON transformation

This conversion is performed by downstream Kudio, not by this authoring plugin.

KSON should separate:

- `segments`: what is being spoken;
- `events`: what control action occurs.

Before TTS inference, timestamps may be null or omitted.

After inference, Kudio may populate actual timing data from rendered audio.

Example pre-inference concept:

```json
{
  "format": "kson",
  "version": "0.1",
  "segments": [
    {
      "id": "seg_001",
      "page": 1,
      "text": "第一页讲稿。",
      "voice": "narrator",
      "rate": 1.0
    }
  ],
  "events": [
    {
      "type": "page",
      "value": 1
    }
  ]
}
```

Do not fabricate `start_ms` or `end_ms` before timing is known.

## 6. Reserved future namespace

The following controls are not part of PCS v0.1 but may be added later:

- `focus`
- `cursor`
- `highlight`
- `zoom`
- `click`
- `transition`

A v0.1 parser should preserve or report unknown controls rather than silently executing them.

## 7. Validation checklist

A valid PCS document should satisfy:

1. Every control starts with `#[` and ends with `]#`.
2. Every supported control contains exactly one command/value separator at the grammar level.
3. `p` values are positive integers.
4. `pause` values are non-negative integer milliseconds.
5. `rate` values are numeric.
6. No control marker is included in spoken TTS payload.
7. No hard-boundary merge crosses a supported control.
8. Unsupported controls produce diagnostics rather than silent reinterpretation.
