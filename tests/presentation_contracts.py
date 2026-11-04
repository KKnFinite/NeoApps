"""Rendered, same-origin asset and semantic control contracts for W5."""
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
from tests.html_contracts import document

ROOT = Path(__file__).resolve().parents[1]


def asset(test, response, filename, *, tag=None):
    root = document(response)
    matches = [node for node in root.findall(tag) if urlsplit(node.attrs.get('src') or node.attrs.get('href') or '').path == '/static/' + filename]
    test.assertEqual(len(matches), 1, filename)
    node = matches[0]
    test.assertFalse(urlsplit(node.attrs.get('src') or node.attrs.get('href')).netloc)
    return node


def asset_source(test, response, filename, *, tag=None):
    asset(test, response, filename, tag=tag)
    return (ROOT / 'app/static' / filename).read_text(encoding='utf-8')


def json_script(test, response, marker):
    import json
    node = document(response).one('script', type='application/json', **{marker: None})
    # A trusted template explicitly owns this nonce; parsing must tolerate its attributes.
    test.assertTrue(node.attrs.get('nonce'))
    return json.loads(node.text)


def assert_versioned(test, node, expected=None):
    query = parse_qs(urlsplit(node.attrs.get('src') or node.attrs.get('href')).query)
    test.assertTrue(query.get('v'), node.attrs)
    if expected is not None:
        test.assertEqual(query['v'], [expected])
    return query


def assert_scripts_parse(test, response):
    import subprocess
    root = document(response)
    scripts = []
    for node in root.findall('script'):
        if node.attrs.get('type') == 'application/json':
            continue
        if node.attrs.get('src'):
            path = urlsplit(node.attrs['src']).path
            test.assertTrue(path.startswith('/static/'))
            scripts.append((path, (ROOT / 'app' / path.lstrip('/')).read_text(encoding='utf-8')))
        else:
            test.assertTrue(node.attrs.get('nonce'), 'Executable template script requires explicit nonce')
            scripts.append(('trusted inline script', node.text))
    for name, source in scripts:
        checked = subprocess.run(['node', '--check', '-'], input=source, text=True, capture_output=True)
        test.assertEqual(checked.returncode, 0, name + ': ' + checked.stderr)
    for node in root.findall():
        test.assertFalse([a for a in node.attrs if a.startswith('on')], node.attrs)
    test.assertTrue(scripts)
    return root
