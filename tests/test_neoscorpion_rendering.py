"""Presentation acceleration preserves complete canonical values and authority."""
import json
import unittest
from html.parser import HTMLParser
from types import SimpleNamespace
from unittest.mock import patch
from flask import Flask,render_template_string
from jinja2 import Template
from markupsafe import escape
from app.extensions import db
from app.services import neoscorpion_dispatch_fragments as fragments
from app.neonodes.neoscorpion import routes
from app.services.neoscorpion_tail_notes import save_tail_note
from tests import test_dispatch_refresh_fragments as fixtures


class TemplateValuesTest(unittest.TestCase):
    def test_native_values_keep_mapping_lookup_missing_values_and_json_shape(self):
        original={'zero':0,'empty':'','no':False,'missing':None,'text':'< & >','items':'stored-key','_private':'legacy'}
        view=fragments._TemplateValues(original)
        self.assertEqual(dict(view),original)
        self.assertEqual(json.loads(json.dumps(view)),original)
        with Flask(__name__).app_context():
            template='{{ r.zero }}|{{ r.empty }}|{{ r.no }}|{{ r.missing }}|{{ r.text }}|{{ r["items"] }}|{{ r._private }}|{{ r.absent|default("undefined") }}|{{ r is mapping }}|{{ r.get("none", "fallback") }}'
            self.assertEqual(render_template_string(template,r=view),render_template_string(template,r=original))
        self.assertEqual(list(view.items()),list(original.items()))

    def test_read_only_copies_keep_original_orm_identity_and_refresh_each_call(self):
        mission=SimpleNamespace(id=1,tail='N1UP');row={'mission':mission,'label':'old'}
        first=fragments.template_context({'rows':[row]})['rows'][0]
        self.assertIs(first.mission,mission)
        for action in (lambda:first.update(label='x'),lambda:first.__setitem__('label','x'),lambda:setattr(first,'label','x'),lambda:first.clear()):
            with self.assertRaises(TypeError):action()
        row['label']='new'
        self.assertEqual(first.label,'old')
        self.assertEqual(fragments.template_context({'rows':[row]})['rows'][0].label,'new')
        self.assertEqual(row['label'],'new')

    def test_data_keys_cannot_grant_markup_trust(self):
        row={'__html__':lambda:'<script>untrusted</script>','value':'<script>untrusted</script>'}
        view=fragments._TemplateValues(row)
        self.assertFalse(hasattr(view,'__html__'))
        self.assertEqual(escape(view),escape(row))
        self.assertEqual(escape(view.value),'&lt;script&gt;untrusted&lt;/script&gt;')

    def test_many_items_keep_distinct_missions_and_shared_request_local_aliases(self):
        rows=[{'mission':SimpleNamespace(id=i),'value':i} for i in range(100)]
        context={'rows':rows,'truck_visuals':list(rows)}
        view=fragments.template_context(context)
        self.assertEqual([v.mission.id for v in view['rows']],list(range(100)))
        self.assertEqual([v.mission.id for v in view['truck_visuals']],list(range(100)))
        for i,row in enumerate(view['rows']):
            self.assertIs(row,view['truck_visuals'][i])


class RenderingCanonicalTest(unittest.TestCase):
    def setUp(self):
        self.case=fixtures.DispatchFragmentsTest();self.case.setUp()
        self.addCleanup(self.case.doCleanups);self.case.ready()

    def test_full_and_critical_html_match_without_the_attribute_adapter(self):
        for query in ('','?completion_only=1&delta=1'):
            with self.subTest(query=query):
                normal=self.case.client.get('/neoscorpion/fuel-dispatch/live-panel'+query)
                with patch.object(fragments,'template_context',side_effect=lambda c:c),patch.object(routes,'dispatch_template_context',side_effect=lambda c:c):
                    reference=self.case.client.get('/neoscorpion/fuel-dispatch/live-panel'+query)
                self.assertEqual(normal.status_code,200);self.assertEqual(reference.status_code,200)
                self.assertEqual(fragments._digest(normal.json['html'],'test','panel'),fragments._digest(reference.json['html'],'test','panel'))

    def test_critical_imports_request_bound_status_macros_once_for_25_missions(self):
        c=self.case
        for i in range(24):c.fixture._mission('UPS'+str(i+300),'N'+str(i+300)+'UP',30000,15+i)
        db.session.commit();calls=[];original=Template.make_module
        def capture(template,*args,**kwargs):
            if template.name.endswith('/_fuel_status.html'):calls.append(template.name)
            return original(template,*args,**kwargs)
        with patch.object(Template,'make_module',capture):
            response=c.client.get('/neoscorpion/fuel-dispatch/live-panel?completion_only=1&delta=1')
        self.assertEqual(response.status_code,200)
        self.assertEqual(len(response.json['mission_manifest']),25)
        self.assertEqual(len(calls),1)

    def test_static_indent_removal_never_changes_saved_note_whitespace_or_escaping(self):
        c=self.case;notes='  Leading spaces\n\tTab and <script> & symbols\n   trailing  '
        save_tail_note(c.gateway,c.dispatcher,dict(tail_number=c.mission.assigned_tail_number,notes=notes,expected_version='0'))
        db.session.commit()
        class Notes(HTMLParser):
            def __init__(self):super().__init__(convert_charrefs=True);self.depth=0;self.text=[]
            def handle_starttag(self,tag,attrs):
                if tag=='textarea' and dict(attrs).get('name')=='notes':self.depth+=1
            def handle_endtag(self,tag):
                if tag=='textarea' and self.depth:self.depth-=1
            def handle_data(self,text):
                if self.depth:self.text.append(text)
        for path in ('/neoscorpion/fuel-dispatch','/neoscorpion/fuel-dispatch/live-panel','/neoscorpion/fuel-dispatch/live-panel?completion_only=1&delta=1'):
            response=c.client.get(path);self.assertEqual(response.status_code,200)
            html=response.json['html'] if response.is_json else response.text
            parser=Notes();parser.feed(html)
            # HTML textarea parsing removes one immediately following newline;
            # the template deliberately emits that newline before saved text.
            self.assertIn('\n'+notes,parser.text)
            self.assertNotIn('<script> & symbols',html)


if __name__=='__main__':unittest.main()
