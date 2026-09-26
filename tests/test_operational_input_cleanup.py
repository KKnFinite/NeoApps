"""Presentation contracts for operational inputs and Staffing rail scrolling."""
from pathlib import Path
import re


def test_staffing_sidebar_keeps_scroll_with_dark_track_and_thumb():
    css = Path('app/static/css/neostaffing_shell.css').read_text(encoding='utf-8')
    rule = re.search(r'body\.blueprint-neostaffing \.operational-desktop-sidebar \{([^}]+)', css)[1]
    assert 'overflow-y:auto' in rule
    assert 'scrollbar-width:thin' in rule
    assert 'scrollbar-color:#36534f #0a161c' in rule
    assert '.operational-desktop-sidebar::-webkit-scrollbar-track { background:#0a161c;' in css
    assert '.operational-desktop-sidebar::-webkit-scrollbar-thumb { background:#36534f;' in css


def test_node_clock_inputs_keep_numeric_text_entry_and_minute_display():
    rows = Path('app/templates/neostaffing/_timecard_rows.html').read_text(encoding='utf-8')
    js = Path('app/static/js/neostaffing_node_times.js').read_text(encoding='utf-8')
    for source in (rows, js):
        assert source.count('type="text" inputmode="numeric" maxlength="5" placeholder="HHMM"') == 2
    assert 'segment.start[11:16]' in rows
    assert 'segment.end[11:16]' in rows


def test_nonnegative_operational_counts_request_numeric_keyboard():
    for filename in ('org_chart.html', 'reports.html', 'settings.html', 'vacation_management.html'):
        html = Path('app/templates/neostaffing', filename).read_text(encoding='utf-8')
        for tag in re.findall(r'<input[^>]+>', html):
            if 'type="number"' in tag and 'min=' in tag:
                assert 'inputmode="numeric"' in tag


def test_staffing_bulk_clock_fields_request_numeric_text_entry():
    html = Path('app/templates/neostaffing/_timecard_bulk.html').read_text(encoding='utf-8')
    assert html.count('type="text" inputmode="numeric" placeholder="HHMM" maxlength="5"') == 2
    assert 'type="number"' not in html
