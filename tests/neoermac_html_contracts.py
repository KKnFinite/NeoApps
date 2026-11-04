"""Semantic contracts shared by Ermac identity/transport tests."""
import json
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from tests.html_contracts import document


def owned_script(test, response, filename):
    scripts = document(response).findall('script')
    matches = [s for s in scripts if urlparse(s.attrs.get('src', '')).path == '/static/js/' + filename]
    test.assertEqual(len(matches), 1)
    version = parse_qs(urlparse(matches[0].attrs['src']).query).get('v', [])
    test.assertEqual(len(version), 1)
    test.assertTrue(version[0])
    return Path('app/static/js', filename).read_text(encoding='utf-8')


def config(response, screen='door'):
    return json.loads(document(response).one('script', id=f'neoermac-{screen}-config').text)


def option(test, response, gateway, destination, *, selected=False):
    from tests.neoermac_flight_fixture import flight_id
    options = document(response).findall('option', value=flight_id(gateway, destination))
    test.assertTrue(options)
    test.assertTrue(all(destination.upper() in o.text.upper() for o in options))
    if selected:
        test.assertTrue(any('selected' in o.attrs for o in options))


def element_html(element):
    from html import escape
    from tests.html_contracts import Element
    attrs = ''.join(' ' + k + ('="' + escape(v, quote=True) + '"' if v is not None else '')
                    for k, v in element.attrs.items())
    body = ''.join(element_html(c) if isinstance(c, Element) else escape(c) for c in element.children)
    return '<' + element.tag + attrs + '>' + body + '</' + element.tag + '>'
