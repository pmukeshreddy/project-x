"""Bounded TOML serialization for the source-free Cargo fetch declaration."""
import json
import math

from .models import PolicyRejected


def cargo_manifest(value, *, max_bytes=1024*1024):
    if type(max_bytes) is not int or not 1 <= max_bytes <= 1024*1024:
        raise PolicyRejected('invalid Cargo manifest byte limit')
    lines, size, members = [], 0, 0

    def emit(line):
        nonlocal size
        size += len(line.encode('utf-8'))+1
        if size > max_bytes:
            raise PolicyRejected('Cargo fetch manifest exceeds byte limit')
        lines.append(line)

    def scalar(item, depth):
        if depth > 16:
            raise PolicyRejected('Cargo manifest nesting limit')
        if type(item) in (str, int, bool) or type(item) is float and math.isfinite(item):
            return json.dumps(item, ensure_ascii=False, allow_nan=False)
        if type(item) is list:
            return '['+', '.join(scalar(child, depth+1) for child in item)+']'
        raise PolicyRejected('unsupported Cargo manifest value')

    def visit(table, path, *, array=False):
        nonlocal members
        if type(table) is not dict or any(type(key) is not str for key in table):
            raise PolicyRejected('unsupported Cargo manifest table')
        if len(path) > 16:
            raise PolicyRejected('Cargo manifest nesting limit')
        members += len(table)
        if members > 4096:
            raise PolicyRejected('Cargo manifest member limit')
        if path:
            name = '.'.join(json.dumps(part, ensure_ascii=False) for part in path)
            emit(('[' if not array else '[[')+name+(']' if not array else ']]'))
        tables = []
        for key, item in sorted(table.items()):
            if type(item) is dict or type(item) is list and any(type(child) is dict for child in item):
                tables.append((key, item))
            else:
                emit(json.dumps(key, ensure_ascii=False)+' = '+scalar(item, len(path)))
        for key, item in tables:
            if type(item) is dict:
                visit(item, (*path, key))
            else:
                if not all(type(child) is dict for child in item):
                    raise PolicyRejected('unsupported mixed Cargo array-of-tables')
                for child in item:
                    visit(child, (*path, key), array=True)

    visit(value, ())
    return ('\n'.join(lines)+'\n').encode('utf-8')
