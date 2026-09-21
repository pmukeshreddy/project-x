"""Deterministic, bounded source spans from request prose and baseline bytes only.

Each file contributes at most four ranges and twice the configured window's line
count. The existing total byte/file caps still apply. Python definitions are
parsed inertly to keep complete small methods and one level of referenced helpers.
Other source languages use bounded lexical windows without executing their code.
"""
import ast
from dataclasses import dataclass
import re

from feature_rl.intake.request_document import request_ranking_prose
from feature_rl.requirements.retrieval import RetrievalRequest


_WORDS = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')
_URL = re.compile(r'https?://[^\s<>`]+')
_STOP = frozenset('the and with this that from true false null none self cls return def class '
    'for not but can will would should could have has had are was were been being which '
    'when then than they their these those into its our your you use used using also '
    'some any all new old one two out just here there does done did now get add change '
    'changes please before after example code implement implemented implementation'.split())
_CODE_SUFFIXES = ('.py', '.pyi', '.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs', '.rs', '.go',
                  '.c', '.h', '.cc', '.cpp', '.hpp', '.java', '.rb', '.php', '.swift', '.kt')


def _prose(text):
    return _URL.sub('', request_ranking_prose(text.encode('utf-8')))


def _terms(text):
    values = set()
    for word in _WORDS.findall(text):
        pieces = [word, *re.sub(r'([a-z])([A-Z])', r'\1 \2', word).split('_')]
        for piece in pieces:
            values.update(part.lower() for part in piece.split() if len(part) >= 3)
    return values - _STOP


def _symbols(text):
    values = set()
    for match in re.finditer(r'\b[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*\b', text):
        token = match[0]
        if '_' in token or '.' in token or any(char.isupper() for char in token):
            values.update(piece.lower() for piece in (token, *token.split('.')) if len(piece) >= 3)
    for match in re.finditer(r'`([^`\n]+)`', text):
        values.update(word.lower() for word in _WORDS.findall(match[1]) if len(word) >= 3)
    return values - _STOP


def _definitions(tree):
    result = []
    def visit(node, parents=()):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            names = (*parents, node.name)
            result.append((node, names))
            parents = names
        for child in ast.iter_child_nodes(node):
            visit(child, parents)
    visit(tree)
    return result


def _merge(ranges):
    result = []
    for start, end in sorted(ranges):
        if result and start <= result[-1][1] + 2:
            result[-1] = (result[-1][0], max(end, result[-1][1]))
        else:
            result.append((start, end))
    return tuple(result)


@dataclass(frozen=True)
class _Candidate:
    path: str
    start: int
    end: int
    priority: int
    relevance: int


def select_source_spans(source, source_roots, request_text, *, max_files, max_lines, max_bytes):
    """Return one exact retrieval request per file with bounded disjoint ranges."""
    if (type(max_files) is not int or not 1 <= max_files <= 32 or type(max_lines) is not int
            or not 8 <= max_lines <= 512 or type(max_bytes) is not int or not 1 <= max_bytes <= 524288):
        raise ValueError('invalid source context bounds')
    prose = _prose(request_text)
    terms, symbols = _terms(prose), _symbols(prose)
    files, candidates = {}, []
    for path, entry in sorted(source.files.items()):
        if not any(path == root or path.startswith(root.rstrip('/') + '/') for root in source_roots):
            continue
        try:
            lines = entry.data.decode('utf-8').splitlines()
        except UnicodeError:
            continue
        if not lines:
            continue
        files[path] = lines
        is_code = path.endswith(_CODE_SUFFIXES)
        implementation = is_code and not any(part in {'test', 'tests', 'testing', 'docs', 'examples'} for part in path.split('/')[:-1])
        path_priority = 80 if implementation else 30 if is_code else 0
        definitions = []
        if path.endswith(('.py', '.pyi')) and len(entry.data) <= 2*1024*1024:
            try:
                definitions = _definitions(ast.parse(entry.data))
            except (SyntaxError, RecursionError, UnicodeError):
                definitions = []
        related = set()
        for node, names in definitions:
            # A named class can contain hundreds of unrelated calls. Follow
            # only explicitly mentioned functions, not every method of a class.
            if isinstance(node, ast.ClassDef):
                continue
            if node.name.lower() not in symbols and '.'.join(names).lower() not in symbols:
                continue
            for child in ast.walk(node):
                if isinstance(child, ast.Call):
                    if isinstance(child.func, ast.Attribute):
                        related.add(child.func.attr.lower())
                    elif isinstance(child.func, ast.Name):
                        related.add(child.func.id.lower())
        for node, names in definitions:
            start = min([node.lineno, *(decorator.lineno for decorator in node.decorator_list)])
            end = node.end_lineno
            name, qualified = node.name.lower(), '.'.join(names).lower()
            body = '\n'.join(lines[start-1:end])
            body_words = {word.lower() for word in _WORDS.findall(body)}
            direct = name in symbols or qualified in symbols
            is_class = isinstance(node, ast.ClassDef)
            priority = path_priority
            if direct:
                priority += 2400 if is_class else 3000
            elif name in related:
                priority += 1900
            elif any(parent.lower() in symbols for parent in names[:-1]):
                priority += 1200
            elif body_words & symbols:
                priority += 1000
            if end-start+1 > 2*max_lines:
                # Oversized classes/functions still contribute their declaration,
                # while separate method candidates expose the relevant bodies.
                end = min(end, start+min(max_lines, 48)-1)
                priority = min(priority, 1800+path_priority)
            relevance = len(terms & _terms(body))
            candidates.append(_Candidate(path, start, end, priority, relevance))
        # Lexical windows also serve other languages and declarations outside
        # Python definitions. Do not let lockfile length multiply its relevance.
        scores = [len(terms & _terms(line)) for line in lines]
        centers = sorted(range(len(lines)), key=lambda index: (-scores[index], index))
        windows = set()
        for center in centers:
            start = max(0, min(center-max_lines//2, len(lines)-max_lines))
            end = min(len(lines), start+max_lines)
            if any(abs(start-prior) < max_lines//2 for prior in windows):
                continue
            windows.add(start)
            relevance = len(terms & _terms('\n'.join(lines[start:end])))
            candidates.append(_Candidate(path, start+1, end, path_priority, relevance))
            if len(windows) >= 8:
                break
    selected, selected_sizes, total = {}, {}, 0
    ordering = sorted(candidates, key=lambda item: (-item.priority, -item.relevance,
        item.end-item.start, item.path, item.start, item.end))
    for candidate in ordering:
        if candidate.path not in selected and len(selected) >= max_files:
            continue
        previous = selected.get(candidate.path, ())
        ranges = _merge((*previous, (candidate.start, candidate.end)))
        if ranges == previous or len(ranges) > 4 or sum(end-start+1 for start, end in ranges) > 2*max_lines:
            continue
        lines = files[candidate.path]
        size = sum(len(('\n'.join(lines[start-1:end])+'\n').encode()) for start, end in ranges)
        change = size-selected_sizes.get(candidate.path, 0)
        if total+change > max_bytes:
            continue
        selected[candidate.path] = ranges
        selected_sizes[candidate.path] = size
        total += change
    if not selected:
        raise ValueError('profile has no bounded author-visible source context')
    return tuple(RetrievalRequest(context_id='B_'+str(index), path=path, line_ranges=ranges)
                 for index, (path, ranges) in enumerate(selected.items(), 1))
