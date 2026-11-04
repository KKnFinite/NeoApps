"""Shared assertions for current Staffing scope and unified editor contracts."""
from urllib.parse import parse_qs,urlsplit
from tests.html_contracts import document


def selected_attendance_area(test,response,area_id):
    links=document(response).findall('a','neostaffing-attendance-scope-node',**{'data-unit-type':'work_area'})
    selected=[link for link in links if 'is-selected' in link.attrs.get('class','').split()
              and parse_qs(urlsplit(link.attrs['href']).query).get('work_area_id')==[str(area_id)]]
    test.assertEqual(len(selected),1)


def employee_editor(test,client,response,person_id):
    dialog=document(response).one('dialog',**{'data-employee-editor':None})
    test.assertEqual(dialog.attrs.get('data-initial-person'),str(person_id))
    fragment=client.get(dialog.attrs['data-editor-url'],query_string={'person_id':person_id})
    test.assertEqual(fragment.status_code,200)
    form=document(fragment).one('form',**{'data-employee-form':None})
    test.assertTrue(form.attrs['action'].endswith(f'/people/{person_id}/update'))
    return fragment
