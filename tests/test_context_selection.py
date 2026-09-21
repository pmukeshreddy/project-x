"""Request-grounded baseline spans retain separated methods and complete bodies."""
import ast

from feature_rl.environments import SourceArchive, SourceFile
from feature_rl.intake.request_document import render_authoring_request


def request_document(request):
    provenance = dict(source_url='https://api.github.com/repos/example/project/pulls/1',
        source_response_sha256='a'*64, retrieved_at='2026-09-20T00:00:00Z', edit_history='unavailable')
    projection = dict(provenance_label='reconstructed_specification',
        admissible_cutoff='2026-01-01T00:00:00Z', caveat='Reconstructed test evidence.',
        pull_request=None, issue=None, comments=[], pr_comments=[], reviews=[],
        review_comments=[], changed_files=[])
    for name in ('pull_request', 'issue'):
        if name in request:
            projection[name] = provenance | request[name]
    if 'body' in request:
        projection['pull_request'] = provenance | {'body': request['body']}
    if 'metadata' in request:
        projection['pull_request']['user'] = request['metadata']
    return render_authoring_request(projection).decode()


def archive(files):
    return SourceArchive({path: SourceFile(text.encode(), False) for path, text in files.items()})


def selected_text(source, requests, path):
    lines = source.files[path].data.decode().splitlines()
    return ''.join('\n'.join(lines[start-1:end])+'\n' for item in requests if item.path == path
                   for start, end in item.line_ranges)


def select(source, request, **changes):
    from feature_rl.pipeline.context import select_source_spans
    return select_source_spans(source, tuple(source.files), request_document(request),
        **({'max_files': 12, 'max_lines': 180, 'max_bytes': 131072} | changes))


def test_option_help_selection_keeps_existing_method_and_called_helper_far_apart():
    code = ('class Parameter:\n    def get_help_record(self, ctx):\n        return None\n' + '\n'*280
        + 'class Option:\n    """Option implementation."""\n' + '\n'*280
        + '    def get_help_record(self, ctx):\n        if self.hidden:\n            return None\n'
          '        return self.get_help_extra(ctx)\n' + '\n'*280
        + '    def get_help_extra(self, ctx):\n        value = self.default\n        return str(value)\n')
    tree = archive({'src/click/core.py': code})
    result = select(tree, {'pull_request': {'title': 'Add Option.get_help_spec',
        'body': '`get_help_record()` remains unchanged; the new `Option.get_help_spec` returns the left column even for hidden options.'}})
    visible = selected_text(tree, result, 'src/click/core.py')
    assert 'if self.hidden:\n            return None\n        return self.get_help_extra(ctx)' in visible
    assert 'value = self.default\n        return str(value)' in visible
    assert len(result[0].line_ranges) > 1


def test_command_suggestion_selection_includes_resolution_and_option_error_bodies():
    tree = archive({'src/click/core.py': '\n'*250 + 'class Group:\n'
        '    def resolve_command(self, ctx, args):\n        command = self.get_command(ctx, args[0])\n'
        '        if command is None:\n            ctx.fail("No such command")\n        return command\n' + '\n'*250,
        'src/click/exceptions.py': '\n'*250 + 'class NoSuchOption(Exception):\n'
        '    def format_message(self):\n        return "Did you mean " + str(self.possibilities)\n',
        'src/click/parser.py': '\n'*250 + 'def _process_opts(option):\n'
        '    if not option:\n        raise NoSuchOption(option)\n    return option\n'})
    result = select(tree, {'issue': {'title': 'Suggest Did you mean for misspelled commands',
        'body': 'Currently our group overrides `resolve_command`. Preserve `NoSuchOption` behavior.'}})
    assert 'ctx.fail("No such command")\n        return command' in selected_text(tree, result, 'src/click/core.py')
    assert 'return "Did you mean " + str(self.possibilities)' in selected_text(tree, result, 'src/click/exceptions.py')
    assert 'raise NoSuchOption(option)\n    return option' in selected_text(tree, result, 'src/click/parser.py')


def test_key_rotation_selection_keeps_distant_serializer_loading_and_signer_verification():
    tree = archive({'src/itsdangerous/serializer.py': 'class Serializer:\n'
        '    def __init__(self, secret_key):\n        self.secret_key = secret_key\n' + '\n'*300
        + '    def dumps(self, value):\n        return self.make_signer().sign(value)\n' + '\n'*300
        + '    def loads(self, value):\n        return self.make_signer().unsign(value)\n',
        'src/itsdangerous/signer.py': 'class Signer:\n    def verify_signature(self, value, signature):\n'
        '        return self.algorithm.verify_signature(self.secret_key, value, signature)\n'})
    result = select(tree, {'pull_request': {'title': 'Key rotate', 'body':
        '`Signer` and `Serializer` accept a list of secret keys. Old dumped values can still be loaded, new values use the newest key.'}})
    assert 'def loads(self, value):\n        return self.make_signer().unsign(value)' in selected_text(tree, result, 'src/itsdangerous/serializer.py')
    assert 'return self.algorithm.verify_signature(self.secret_key, value, signature)' in selected_text(tree, result, 'src/itsdangerous/signer.py')


def test_selection_ignores_url_metadata_noise_and_stays_within_all_bounds():
    tree = archive({'uv.lock': '\n'.join('registry hostname timestamp sha256 metadata' for _ in range(1000)),
        'src/core.py': 'def resolve_command(value):\n    return value\n',
        'private/hidden.py': 'def resolve_command(value):\n    return "hidden"\n'})
    from feature_rl.pipeline.context import select_source_spans
    request = request_document({'body': 'Please use `resolve_command`. https://example.com/registry/metadata',
                          'metadata': {'sha256': 'registry hostname timestamp metadata '*100}})
    result = select_source_spans(tree, ('src', 'uv.lock'), request, max_files=1, max_lines=8, max_bytes=128)
    assert [item.path for item in result] == ['src/core.py']
    assert len(selected_text(tree, result, 'src/core.py').encode()) <= 128
    assert sum(end-start+1 for item in result for start, end in item.line_ranges) <= 16
    assert sum(len(item.line_ranges) for item in result) <= 4
    reversed_tree = SourceArchive(dict(reversed(tuple(tree.files.items()))))
    assert select_source_spans(reversed_tree, ('src', 'uv.lock'), request,
        max_files=1, max_lines=8, max_bytes=128) == result


def test_named_class_does_not_expand_unrelated_common_constructor_calls():
    code = ('class Context:\n    def __init__(self):\n'
        + '        option_help_record_default_hidden = None\n'*99
        + '        return "unrelated constructor"\n' + '\n'*180
        + 'class Option:\n    def __init__(self):\n        super().__init__()\n'
        + '\n'*180 + '    def get_help_record(self):\n'
        + '        option_help_record_default_hidden = None\n'*12
        + '        return self.get_help_extra()\n' + '\n'*180
        + '    def get_help_extra(self):\n' + '        value = self.default\n'*45
        + '        return str(value)\n')
    tree = archive({'src/core.py': code})
    result = select(tree, {'body': '`Option.get_help_spec` should preserve `get_help_record` '
        'and option help record default hidden behavior.'}, max_lines=80)
    visible = selected_text(tree, result, 'src/core.py')
    assert 'return self.get_help_extra()' in visible
    helper = next(node for node in ast.walk(ast.parse(code))
                  if isinstance(node, ast.FunctionDef) and node.name == 'get_help_extra')
    assert any(start <= helper.lineno and end >= helper.end_lineno
               for item in result for start, end in item.line_ranges)
    assert 'return "unrelated constructor"' not in visible
