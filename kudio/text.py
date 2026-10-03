"""Natural-language splitting shared by TXT and PCS compilation."""
import re
import uuid


def split_text(text, limit=160):
    """Preserve the legacy splitter's text, chapter, and boundary behavior."""
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError('每段最大字数必须是正整数')
    result, chapter, buf = [], '正文', ''

    def flush():
        nonlocal buf
        if buf.strip():
            result.append({'id': uuid.uuid4().hex, 'chapter': chapter, 'text': buf.strip(),
                           'role': '旁白', 'emotion': '默认', 'status': 'pending', 'error': '', 'duration': 0})
        buf = ''

    for line in text.replace('\r', '').split('\n'):
        line = line.strip()
        if not line:
            flush()
            continue
        if re.match(r'^(第[零〇一二三四五六七八九十百千万两\d]+[章节卷回部篇].{0,60}|序章|序言|楔子|尾声|后记)$', line):
            flush()
            chapter = line
        pieces = re.findall(r'.+?(?:[。！？!?；;]+[”’」』]*|$)', line)
        for piece in pieces:
            if len(piece) > limit:
                clauses = re.findall(r'.+?(?:[，、,:：]+|$)', piece)
            else:
                clauses = [piece]
            for clause in clauses:
                while clause:
                    if len(buf) + len(clause) <= limit:
                        buf += clause
                        break
                    flush()
                    if len(clause) <= limit:
                        buf = clause
                        break
                    buf, clause = clause[:limit], clause[limit:]
                    flush()
        flush()
    return result


def split_text_with_spans(text, limit=160):
    """Return legacy segments with decoded-text spans, including CRLF sources."""
    pieces = split_text(text, limit)
    normalized = text.replace('\r', '')
    positions = [index for index, char in enumerate(text) if char != '\r']
    cursor = 0
    for piece in pieces:
        start = normalized.find(piece['text'], cursor)
        if start < 0:
            raise ValueError('分段文字无法映射到脚本源码')
        end = start + len(piece['text'])
        piece['text_start'] = positions[start]
        piece['text_end'] = positions[end - 1] + 1
        piece['text_positions'] = positions[start:end]
        cursor = end
    return pieces
