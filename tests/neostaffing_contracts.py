"""Shared assertions for current Staffing scope and unified editor contracts."""
from tests.html_contracts import document


def selected_attendance_area(test,response,area_id):
    roster = document(response).one('section', **{'data-attendance-roster': None})
    selected = roster.findall("input", name="work_area_ids")
    test.assertEqual([field.attrs["value"] for field in selected], [str(area_id)])


def employee_editor(test,client,response,person_id):
    dialog=document(response).one('dialog',**{'data-employee-editor':None})
    test.assertEqual(dialog.attrs.get('data-initial-person'),str(person_id))
    fragment=client.get(dialog.attrs['data-editor-url'],query_string={'person_id':person_id})
    test.assertEqual(fragment.status_code,200)
    form=document(fragment).one('form',**{'data-employee-form':None})
    test.assertTrue(form.attrs['action'].endswith(f'/people/{person_id}/update'))
    return fragment
