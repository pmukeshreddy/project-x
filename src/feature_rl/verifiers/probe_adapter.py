"""Deterministic JSON observation transport; candidate source is only parsed here."""
import ast
from collections import Counter
import io
import textwrap
import tokenize


def validate_actions(source):
    if not source.strip() or len(source.encode('utf-8')) > 8192 or '\x00' in source:
        raise ValueError('actions must be bounded UTF-8 function body without NUL')
    try:
        tree = ast.parse('def _probe(inputs):\n' + textwrap.indent(source, '    '))
        # AST parsing alone accepts break/continue outside loops and duplicate
        # arguments. Compilation checks scope legality but never executes code.
        compile(tree, '<checker-fragment-actions>', 'exec', dont_inherit=True)
    except (SyntaxError, ValueError, RecursionError) as exc:
        raise ValueError('invalid Python action body') from exc
    function = tree.body[0]
    if len(tree.body) != 1 or not isinstance(function, ast.FunctionDef):
        raise ValueError('actions must remain inside the probe function')
    forbidden = (ast.Assert, ast.Global, ast.Nonlocal, ast.Yield, ast.YieldFrom, ast.Await)
    for node in ast.walk(function):
        if isinstance(node, forbidden):
            raise ValueError('actions cannot assert, yield, or alter controller scope')
        if isinstance(node, ast.Name) and node.id.startswith('_m4_'):
            raise ValueError('actions cannot access controller transport names')
        if ((isinstance(node, ast.Name) and node.id in {'eval', 'exec'}) or
                (isinstance(node, ast.Attribute) and node.attr in {'eval', 'exec'})):
            raise ValueError('actions cannot evaluate generated source')
    # A helper's return does not make the outer observation function complete.
    def returns(nodes):
        for node in nodes:
            if isinstance(node, ast.Return):
                return True
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                if returns(ast.iter_child_nodes(node)):
                    return True
        return False
    if not returns(function.body):
        raise ValueError('actions must return an observation dictionary')
    return source


# All transport and type references are captured as function locals before any
# probe imports candidate code. JSON encoding uses the captured C string encoder
# and integer repr, so mutations of json or builtins globals cannot replace it.
# This is transport hardening within M3's sandbox, not a Python security boundary.
_TRANSPORT = '''def _m4_run():
    import json
    import json.encoder
    import os
    import sys
    _m4_type, _m4_str, _m4_int = type, str, int
    _m4_bool, _m4_list, _m4_dict, _m4_none = bool, list, dict, type(None)
    _m4_len, _m4_sorted, _m4_set = len, sorted, set
    _m4_error = ValueError
    _m4_loads, _m4_quote = json.loads, json.encoder.encode_basestring_ascii
    _m4_integer_text, _m4_join = int.__repr__, str.join
    _m4_read = sys.stdin.buffer.read
    _m4_terminal = os.fdopen(os.dup(1), 'w', encoding='utf-8')
    _m4_write, _m4_flush = _m4_terminal.write, _m4_terminal.flush
    _m4_types = {'string': _m4_str, 'integer': _m4_int,
                 'boolean': _m4_bool, 'null': _m4_none}

    def _m4_pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise _m4_error('duplicate input key')
            result[key] = value
        return result

    def _m4_nonfinite(value):
        raise _m4_error('nonfinite input')

    def _m4_copy(value, depth=0):
        kind = _m4_type(value)
        if depth > 3:
            raise _m4_error('excessive observation depth')
        if kind is _m4_str:
            if _m4_len(value) > 16384:
                raise _m4_error('text bound exceeded')
            return value
        if kind is _m4_int:
            if not -(2**63) <= value < 2**63:
                raise _m4_error('integer bound exceeded')
            return value
        if kind is _m4_bool or kind is _m4_none:
            return value
        if kind is _m4_list:
            if _m4_len(value) > 256:
                raise _m4_error('list bound exceeded')
            return [_m4_copy(item, depth+1) for item in value]
        if kind is _m4_dict:
            if _m4_len(value) > 64:
                raise _m4_error('object bound exceeded')
            result = {}
            for key, item in value.items():
                if _m4_type(key) is not _m4_str or _m4_len(key) > 128:
                    raise _m4_error('invalid object key')
                result[key] = _m4_copy(item, depth+1)
            return result
        raise _m4_error('observations require exact JSON built-in types')

    def _m4_encode(value):
        kind = _m4_type(value)
        if kind is _m4_str:
            return _m4_quote(value)
        if kind is _m4_int:
            return _m4_integer_text(value)
        if kind is _m4_bool:
            return 'true' if value else 'false'
        if kind is _m4_none:
            return 'null'
        if kind is _m4_list:
            return '[' + _m4_join(',', (_m4_encode(item) for item in value)) + ']'
        if kind is _m4_dict:
            return '{' + _m4_join(',', (_m4_quote(key)+':'+_m4_encode(value[key])
                                       for key in _m4_sorted(value))) + '}'
        raise _m4_error('non-JSON observation')

    _m4_raw = _m4_read(2097153)
    if _m4_len(_m4_raw) > 2097152:
        raise _m4_error('input byte bound exceeded')
    _m4_envelope = _m4_loads(_m4_raw, object_pairs_hook=_m4_pairs,
                           parse_constant=_m4_nonfinite)
    if (_m4_type(_m4_envelope) is not _m4_dict or
            _m4_set(_m4_envelope) != {'case_id', 'inputs'}):
        raise _m4_error('invalid input envelope')
    _m4_case_id = _m4_envelope['case_id']
    if _m4_type(_m4_case_id) is not _m4_str:
        raise _m4_error('invalid case identity')
    _m4_inputs = _m4_copy(_m4_envelope['inputs'])
    if _m4_type(_m4_inputs) is not _m4_dict:
        raise _m4_error('inputs must be an object')
'''

_DISPATCH = '''
    if _m4_case_id not in _m4_dispatch:
        raise _m4_error('unknown case identity')
    _m4_probe, _m4_input_names, _m4_fields = _m4_dispatch[_m4_case_id]
    if _m4_set(_m4_inputs) != _m4_set(_m4_input_names):
        raise _m4_error('input names differ from the declared probe')
    # Candidate prints cannot write into the protocol stream. The terminal
    # handle was duplicated and captured before any candidate imports.
    _m4_null = os.open(os.devnull, os.O_WRONLY)
    os.dup2(_m4_null, 1)
    os.close(_m4_null)
    _m4_observations = _m4_copy(_m4_probe(_m4_inputs))
    if (_m4_type(_m4_observations) is not _m4_dict or
            _m4_set(_m4_observations) != _m4_set(_m4_fields)):
        raise _m4_error('incomplete or extra observations')
    for _m4_name, _m4_kind in _m4_fields.items():
        _m4_value = _m4_observations[_m4_name]
        if _m4_kind.endswith('_list'):
            if _m4_type(_m4_value) is not _m4_list:
                raise _m4_error('observation list type mismatch')
            for _m4_item in _m4_value:
                if _m4_type(_m4_item) is not _m4_types[_m4_kind[:-5]]:
                    raise _m4_error('observation item type mismatch')
        elif _m4_type(_m4_value) is not _m4_types[_m4_kind]:
            raise _m4_error('observation type mismatch')
    _m4_output = _m4_encode({'case_id': _m4_case_id, 'observations': _m4_observations})
    if _m4_len(_m4_output) > 2097152:
        raise _m4_error('output byte bound exceeded')
    _m4_write(_m4_output + '\\n')
    _m4_flush()

_m4_run()
'''


def build_probe_adapter(cases):
    """Accept only identity/actions/input names/observation types, never oracles."""
    blocks, functions, dispatch = [], {}, {}
    for case_id, actions, input_names, observations in cases:
        validate_actions(actions)
        if actions not in functions:
            name = '_m4_probe_' + str(len(functions))
            functions[actions] = name
            blocks.append('    def ' + name + '(inputs):\n' + textwrap.indent(actions, '        ') + '\n')
        dispatch[case_id] = (functions[actions], tuple(input_names), dict(observations))
    body = _TRANSPORT + '\n'.join(blocks)
    metadata = _dispatch_source(dispatch, body + _DISPATCH)
    source = _compact_source(body + '\n' + textwrap.indent(metadata, '    ') + '\n' + _DISPATCH)
    if len(source.encode('utf-8')) > 65536:
        raise ValueError(f'assembled worker adapter exceeds bounded source size ({len(source.encode("utf-8"))} > 65536 bytes)')
    return source


def _dispatch_source(dispatch, source):
    """Share repeated controller metadata, preserving every complete case ID.

    Only immutable dispatch strings are pooled. Probe source is untouched and
    never executed. Fresh local names cannot shadow any name in that source.
    """
    occupied = {token.string for token in tokenize.generate_tokens(io.StringIO(source).readline)
                if token.type == tokenize.NAME}
    occupied.update(name for name, _, _ in dispatch.values())
    counts, keys = Counter(), {}
    for key, (_, names, fields) in dispatch.items():
        prefix, separator, suffix = key.rpartition('_')
        parts = (prefix + separator, suffix) if separator else (key,)
        keys[key] = parts
        counts.update(parts)
        counts.update(names)
        counts.update(fields.keys())
        counts.update(fields.values())
    symbols, declarations, index = {}, [], 0
    for value, count in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
        while '_' + str(index) in occupied:
            index += 1
        symbol = '_' + str(index)
        literal = repr(value)
        if count * (len(literal) - len(symbol)) <= len(symbol) + len(literal) + 2:
            continue
        symbols[value] = symbol
        declarations.append(symbol + '=' + literal)
        occupied.add(symbol)
        index += 1
    def literal(value):
        return symbols.get(value, repr(value))
    entries = []
    for key, (function, names, fields) in dispatch.items():
        parts = keys[key]
        identity = '+'.join(literal(part) for part in parts) if any(part in symbols for part in parts) else repr(key)
        names_source = '(' + ','.join(literal(name) for name in names) + (',' if len(names) == 1 else '') + ')'
        fields_source = '{' + ','.join(literal(name) + ':' + literal(kind) for name, kind in fields.items()) + '}'
        entries.append(identity + ':(' + function + ',' + names_source + ',' + fields_source + ')')
    return ';'.join((*declarations, '_m4_dispatch={' + ','.join(entries) + '}'))


def _compact_source(source):
    """Format generated code compactly; require its complete AST to stay identical.

    Only statement separators, comments and whitespace change. Literal contents,
    identifiers, scope and statement order remain intact; nothing executes here.
    """
    original = ast.parse(source)
    source = ast.unparse(original)
    tree = ast.parse(source)
    raw = source.encode('utf-8')
    offsets = [0]
    for line in raw.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    simple = (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Return, ast.Delete,
        ast.Pass, ast.Break, ast.Continue, ast.Expr, ast.Import, ast.ImportFrom, ast.Raise)
    separators = []
    for node in ast.walk(tree):
        for _, sequence in ast.iter_fields(node):
            if not isinstance(sequence, list):
                continue
            for left, right in zip(sequence, sequence[1:]):
                if isinstance(left, simple) and isinstance(right, simple):
                    separators.append((offsets[left.end_lineno-1] + left.end_col_offset,
                        offsets[right.lineno-1] + right.col_offset))
    for start, end in sorted(separators, reverse=True):
        raw = raw[:start] + b';' + raw[end:]
    depth, tokens = 0, []
    for token in tokenize.generate_tokens(io.StringIO(raw.decode('utf-8')).readline):
        kind, value = token.type, token.string
        if kind in (tokenize.COMMENT, tokenize.NL):
            continue
        if kind == tokenize.INDENT:
            depth += 1
            value = ' ' * depth
        elif kind == tokenize.DEDENT:
            depth -= 1
        tokens.append((kind, value))
    source = tokenize.untokenize(tokens)
    # untokenize adds padding after every name/number, including before ordinary
    # delimiters. Retain lexical separators and the space in e.g. 1 .real.
    lines, previous, cuts = source.splitlines(keepends=True), None, {}
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if (previous is not None and previous.type in (tokenize.NAME, tokenize.NUMBER)
                and token.type in (tokenize.OP, tokenize.NEWLINE)
                and not (previous.type == tokenize.NUMBER and token.string == '.')
                and previous.end[0] == token.start[0] and previous.end[1] < token.start[1]):
            cuts.setdefault(token.start[0]-1, []).append((previous.end[1], token.start[1]))
        previous = token
    for row, ranges in cuts.items():
        for start, end in reversed(ranges):
            lines[row] = lines[row][:start] + lines[row][end:]
    compact = ''.join(lines)
    # A simple suite can occupy its header's line. Compound suites retain their
    # indentation; the final AST check covers try/else/finally and literal text.
    lines, joins = compact.splitlines(keepends=True), set()
    for node in ast.walk(ast.parse(compact)):
        for _, sequence in ast.iter_fields(node):
            if (isinstance(sequence, list) and sequence
                    and all(isinstance(item, simple) for item in sequence)):
                first, last = sequence[0], sequence[-1]
                if (first.lineno == last.end_lineno and first.lineno > 1
                        and lines[first.lineno-2].rstrip().endswith(':')):
                    joins.add(first.lineno-1)
    for row in sorted(joins, reverse=True):
        lines[row-1] = lines[row-1].rstrip() + lines[row].lstrip()
        lines[row] = ''
    compact = ''.join(lines)
    parsed = ast.parse(compact)
    if ast.dump(original) != ast.dump(parsed):
        raise ValueError('adapter formatting changed its Python semantics')
    compile(parsed, '<checker-adapter>', 'exec', dont_inherit=True)
    return compact
