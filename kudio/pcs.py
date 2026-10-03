"""PCS 0.1 lexer, parser and validator; no server or TTS dependencies.

Source spans are half-open Unicode codepoint offsets into the untouched source.
A backslash immediately before ``#[`` escapes that literal through its first
``]#`` (or the end of source). Only that backslash is removed from spoken text.
"""
import math
import re


COMMANDS = ('p', 'pause', 'rate', 'section')
PCS_VERSION = '0.1'


def _diagnostic(text, code, message, start, end, level='error'):
    """Build a diagnostic with source offsets and human-readable positions."""
    prefix = text[:start]
    end_prefix = text[:end]
    return {'level': level, 'code': code, 'message': message,
            'source_start': start, 'source_end': end,
            'line': prefix.count('\n') + 1, 'column': start - prefix.rfind('\n'),
            'end_line': end_prefix.count('\n') + 1,
            'end_column': end - end_prefix.rfind('\n')}


def decode_literal_text(text, source_start=0):
    """Decode escaped openers and return a per-character raw-source mapping."""
    characters, spans, literals = [], [], []
    cursor = 0
    while cursor < len(text):
        if text.startswith('\\#[', cursor):
            close = text.find(']#', cursor + 3)
            end = len(text) if close < 0 else close + 2
            start = len(characters)
            for index in range(cursor + 1, end):
                characters.append(text[index])
                spans.append((source_start + (cursor if index == cursor + 1 else index),
                              source_start + index + 1))
            literals.append({'start': start, 'end': len(characters),
                             'text': ''.join(characters[start:])})
            cursor = end
        else:
            characters.append(text[cursor])
            spans.append((source_start + cursor, source_start + cursor + 1))
            cursor += 1
    return ''.join(characters), spans, literals


def lex_source(text):
    """Tokenize literal text and controls without interpreting command values."""
    if not isinstance(text, str):
        raise ValueError('脚本源码必须是文字')
    tokens, diagnostics = [], []
    cursor, text_start = 0, 0

    def flush(end):
        if end > text_start:
            decoded, _, literals = decode_literal_text(text[text_start:end], text_start)
            token = {'kind': 'text', 'text': decoded,
                     'source_start': text_start, 'source_end': end}
            if literals:
                token['escaped_literals'] = literals
            tokens.append(token)

    while cursor < len(text):
        if text.startswith('\\#[', cursor):
            close = text.find(']#', cursor + 3)
            cursor = len(text) if close < 0 else close + 2
            continue
        if not text.startswith('#[', cursor):
            cursor += 1
            continue
        flush(cursor)
        start, depth, nested = cursor, 1, False
        cursor += 2
        while cursor < len(text) and depth:
            if text.startswith('#[', cursor):
                depth += 1
                nested = True
                cursor += 2
            elif text.startswith(']#', cursor):
                depth -= 1
                cursor += 2
            else:
                cursor += 1
        valid = not nested and depth == 0
        if nested:
            diagnostics.append(_diagnostic(text, 'PCS_NESTED_TAG', 'PCS 标签不能嵌套', start, cursor))
        if depth:
            diagnostics.append(_diagnostic(text, 'PCS_UNCLOSED_TAG', 'PCS 标签未闭合，需要 ]#', start, cursor))
        tokens.append({'kind': 'control', 'body': text[start + 2:cursor - 2] if not depth else text[start + 2:cursor],
                       'source_start': start, 'source_end': cursor, 'lexical_valid': valid})
        # Consecutive controls may share the closing/opening hash: ]#[.
        # Each tag keeps its complete raw span, so the shared hash overlaps.
        if not depth and text.startswith('#[', cursor - 1):
            cursor -= 1
        text_start = cursor
    flush(len(text))
    return {'tokens': tokens, 'diagnostics': diagnostics}


def parse_tokens(tokens, source):
    """Build an ordered AST, retaining malformed nodes for visual diagnostics."""
    ast, diagnostics = [], []
    for token in tokens:
        span = {'source_start': token['source_start'], 'source_end': token['source_end']}
        if token['kind'] == 'text':
            node = dict(span, type='text', text=token['text'])
            if token.get('escaped_literals'):
                node['escaped_literals'] = token['escaped_literals']
            ast.append(node)
            continue
        body = token['body']
        command, colon, value = body.partition(':')
        command, value = command.strip().lower(), value.strip()
        node = dict(span, type='control', command=command, value=value,
                    raw_value=value, valid=token['lexical_valid'])
        if not colon and node['valid']:
            node['valid'] = False
            diagnostics.append(_diagnostic(source, 'PCS_INVALID_SYNTAX',
                                           'PCS 标签必须使用 #[command:value]# 格式', **_span_args(span)))
        ast.append(node)
    return {'ast': ast, 'diagnostics': diagnostics}


def _span_args(span):
    return {'start': span['source_start'], 'end': span['source_end']}


def validate_ast(ast, source):
    """Validate supported controls and convert their values into typed data."""
    diagnostics = []
    for node in ast:
        if node['type'] != 'control' or not node['valid']:
            continue
        command, raw = node['command'], node['raw_value']
        code, message = '', ''
        if command not in COMMANDS:
            code, message = 'PCS_UNKNOWN_COMMAND', '未知 PCS 指令：' + (command or '（空）')
        elif command == 'p':
            try:
                page = int(raw) if re.fullmatch(r'[0-9]+', raw) else 0
            except ValueError:
                page = 0
            if page <= 0:
                code, message = 'PCS_INVALID_PAGE', 'PPT 页码必须是正整数'
            else:
                node['value'] = page
        elif command == 'pause':
            try:
                duration = int(raw) if re.fullmatch(r'[0-9]+', raw) else -1
            except ValueError:
                duration = -1
            if not 0 <= duration <= 30000:
                code, message = 'PCS_INVALID_PAUSE', '停顿必须是 0–30000 毫秒的整数'
            else:
                node['value'] = duration
        elif command == 'rate':
            try:
                rate = float(raw)
            except (ValueError, OverflowError):
                rate = float('nan')
            if not re.fullmatch(r'(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)', raw) or not math.isfinite(rate) or not 0.5 <= rate <= 2.0:
                code, message = 'PCS_INVALID_RATE', '语速倍率必须在 0.5–2.0 之间'
            else:
                node['value'] = rate
        elif command == 'section':
            if not raw or len(raw) > 80:
                code, message = 'PCS_INVALID_SECTION', '章节名称必须是 1–80 个字符'
            else:
                node['value'] = raw
        if code:
            node['valid'] = False
            diagnostics.append(_diagnostic(source, code, message, **_span_args(node)))
    return diagnostics


def parse_source(text, source_format='auto'):
    """Parse plain TXT or PCS and report structured diagnostics without TTS."""
    if source_format not in ('auto', 'txt', 'pcs'):
        raise ValueError('源码格式必须是 auto、txt 或 pcs')
    lexed = lex_source(text)
    pcs = source_format == 'pcs' or any(t['kind'] == 'control' for t in lexed['tokens'])
    parsed = parse_tokens(lexed['tokens'], text)
    ast = parsed['ast']
    diagnostics = lexed['diagnostics'] + parsed['diagnostics'] + validate_ast(ast, text)
    if pcs:
        for node in ast:
            if node['type'] != 'text':
                continue
            raw = text[node['source_start']:node['source_end']]
            _, mapping, literals = decode_literal_text(raw, node['source_start'])
            cursor = 0
            while True:
                cursor = node['text'].find(']#', cursor)
                if cursor < 0:
                    break
                if not any(literal['start'] <= cursor < literal['end'] for literal in literals):
                    diagnostics.append(_diagnostic(text, 'PCS_UNEXPECTED_CLOSE', '发现没有对应起始标签的 ]#',
                                                   mapping[cursor][0], mapping[cursor + 1][1]))
                cursor += 2
    diagnostics.sort(key=lambda d: (d['source_start'], d['source_end']))
    stats = {'text_nodes': sum(n['type'] == 'text' for n in ast),
             'control_nodes': sum(n['type'] == 'control' for n in ast),
             'errors': sum(d['level'] == 'error' for d in diagnostics),
             'warnings': sum(d['level'] == 'warning' for d in diagnostics),
             'info': sum(d['level'] == 'info' for d in diagnostics),
             'characters': len(text)}
    for command, key in (('p', 'pages'), ('pause', 'pauses'), ('rate', 'rates'), ('section', 'sections')):
        stats[key] = sum(n['type'] == 'control' and n['command'] == command and n['valid'] for n in ast)
    return {'source_format': 'pcs' if pcs else 'txt', 'ast': ast,
            'diagnostics': diagnostics, 'stats': stats, 'valid': stats['errors'] == 0}
