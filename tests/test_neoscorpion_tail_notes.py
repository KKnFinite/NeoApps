"""Tail identity, isolation, optimistic concurrency and existing-work protections."""
import unittest
from unittest.mock import patch
from sqlalchemy import event
from app.extensions import db
from app.models import Gateway, NeoScorpionTailNote, NeoScorpionTailFuelState
from app.services.neoscorpion import fuel_dispatch_context, fueler_context
from app.services.neoscorpion_tail_notes import save_tail_note, tail_notes_revision, tail_notes_by_tail
from tests import test_neoscorpion_dispatch_planning as fixtures


class TailNotesTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.NeoScorpionDispatchPlanningTest(); self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.gateway, self.client = self.fixture.gateway, self.fixture.client
        self.user = self.fixture._login_user('tail-dispatcher', 'simulator')

    def save(self, tail='N432UP', notes='Aircraft note', version=0):
        return self.client.post('/neoscorpion/fuel-dispatch/tail-notes', data=dict(
            tail_number=tail, notes=notes, expected_version=str(version)))

    def test_normalization_persistence_clear_actor_and_stale_writes(self):
        response = self.save(' n432up ', '<script>literal text</script>')
        self.assertEqual(response.status_code, 200)
        note = response.json['note']; self.assertEqual(note['tail'], 'N432UP')
        self.assertTrue(note['editor']); self.assertTrue(note['updated_at'].endswith('Z'))
        self.assertEqual(self.save(notes='stale').status_code, 409)
        self.assertEqual(NeoScorpionTailNote.query.one().notes, '<script>literal text</script>')
        db.session.remove()
        self.assertEqual(self.save(notes='', version=1).status_code, 200)
        self.assertEqual(NeoScorpionTailNote.query.one().notes, '')
        self.assertEqual(NeoScorpionTailNote.query.count(), 1)
        self.assertEqual(tail_notes_revision(self.gateway), 2)

    def test_gateway_tail_and_sort_isolation_without_legacy_migration(self):
        legacy = NeoScorpionTailFuelState(sort_date_operation_id=self.fixture.operation.id, tail_number='N432UP', notes='Legacy Fueler Notes')
        db.session.add(legacy); db.session.commit()
        self.assertEqual(self.save().status_code, 200)
        self.assertEqual(self.save('N433UP', 'Other aircraft').status_code, 200)
        other = Gateway(code='NOTE', name='Other gateway'); db.session.add(other); db.session.flush()
        save_tail_note(other, self.user, dict(tail_number='N432UP', notes='Other gateway', expected_version='0'))
        db.session.commit()
        self.assertEqual(tail_notes_by_tail(self.gateway.id, ['N432UP'])['N432UP']['notes'], 'Aircraft note')
        self.assertEqual(tail_notes_by_tail(other.id, ['N432UP'])['N432UP']['notes'], 'Other gateway')
        self.assertEqual(legacy.notes, 'Legacy Fueler Notes')
        self.assertEqual(tail_notes_by_tail(self.gateway.id, []) , {})
        self.assertEqual(NeoScorpionTailNote.query.count(), 3)

    def test_dispatch_and_fueler_rows_follow_exact_tail_after_swap_and_live_poll(self):
        mission = self.fixture._mission('UPS101', 'N432UP', 25000, 1)
        assignment = self.fixture._assignment(mission); assignment.assigned_fueler_user_id = self.user.id
        db.session.commit()
        self.assertEqual(self.save(notes='Tail A').status_code, 200)
        self.assertEqual(self.save('N433UP', 'Tail B').status_code, 200)
        for context in (fuel_dispatch_context(self.gateway), fueler_context(self.gateway, self.user)):
            self.assertEqual(context['rows'][0]['tail_note']['notes'], 'Tail A')
        mission.assigned_tail_number = 'N433UP'; db.session.commit()
        self.assertEqual(fuel_dispatch_context(self.gateway)['rows'][0]['tail_note']['notes'], 'Tail B')
        html = self.client.get('/neoscorpion/fuel-dispatch/live-panel').json['html']
        self.assertIn('Tail B', html)
        self.assertEqual(self.client.get('/neoscorpion/fuel-dispatch/revision').json['tail_notes_revision'], 2)
        with patch('app.neonodes.neoscorpion.routes.permission_access', return_value={'can_view': True, 'can_edit': False}):
            self.assertEqual(self.save(notes='denied', version=1).status_code, 403)
            html = self.client.get('/neoscorpion/fuel-dispatch').get_data(as_text=True)
            self.assertNotIn('data-tail-note-form', html)
            self.assertIn('Tail B', html)
        fueler_html = self.client.get('/neoscorpion/fueler').get_data(as_text=True)
        self.assertIn('Tail B', fueler_html)
        self.assertNotIn('data-tail-note-form', fueler_html)
        self.assertEqual(self.client.get('/neoscorpion/fuel-assignments/revision').json['tail_notes_revision'], 2)

    def test_validation_permissions_and_text_escaping(self):
        for tail, version, note in (('', '0', ''), ('<bad>', '0', ''), ('N432UP','-1',''), ('N432UP','x',''), ('N432UP','0','a'*10001)):
            self.assertEqual(self.client.post('/neoscorpion/fuel-dispatch/tail-notes',data=dict(
                tail_number=tail, expected_version=version, notes=note)).status_code, 400)
        self.assertEqual(NeoScorpionTailNote.query.count(), 0)
        self.fixture._mission('UPS101', 'N432UP', 25000, 1); db.session.commit()
        self.assertEqual(self.save(notes='<script>alert(1)</script>').status_code, 200)
        html=self.client.get('/neoscorpion/fuel-dispatch').get_data(as_text=True)
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;', html)
        self.assertNotIn('<script>alert(1)</script>', html)
        self.fixture._login_user('tail-fueler', 'operator')
        self.assertEqual(self.save(notes='not authorized', version=1).status_code, 403)
        self.assertEqual(NeoScorpionTailNote.query.one().notes, '<script>alert(1)</script>')

    def test_batch_read_is_one_query_and_bootstrap_is_additive(self):
        self.save(); self.save('N433UP')
        statements=[]
        def capture(conn, cursor, statement, parameters, context, executemany): statements.append(statement)
        event.listen(db.engine, 'before_cursor_execute', capture)
        try: notes=tail_notes_by_tail(self.gateway.id, ['N432UP','N433UP']*20)
        finally: event.remove(db.engine, 'before_cursor_execute', capture)
        self.assertEqual(len(notes), 2); self.assertEqual(len(statements), 1)
        NeoScorpionTailNote.__table__.drop(db.engine)
        for _ in range(2): db.create_all()
        self.assertEqual(NeoScorpionTailNote.query.count(), 0)
        self.assertIsNotNone(self.fixture.operation.id)


from tests import test_neoscorpion_segments_postgres as postgres


@unittest.skipUnless(postgres.URI, 'Requires disposable PostgreSQL')
class TailNotesPostgresTest(unittest.TestCase):
    setUp = postgres.PhysicalSegmentPostgresTest.setUp
    drop_schema = postgres.PhysicalSegmentPostgresTest.drop_schema
    prepare = postgres.PhysicalSegmentPostgresTest.prepare
    concurrent = postgres.PhysicalSegmentPostgresTest.concurrent

    def test_concurrent_initial_and_existing_edits_and_production_bootstrap(self):
        from app.services.neoscorpion_schema import ensure_neoscorpion_production_schema
        from app.services.neoscorpion_tail_notes import TailNoteConflict
        NeoScorpionTailNote.__table__.drop(db.engine)
        for _ in range(2):
            with patch.dict(self.fixture.app.config, {'TESTING': False}): ensure_neoscorpion_production_schema(self.fixture.app)
        expected_version = '0'
        def change(index, gateway, fueler, dispatcher):
            try:
                save_tail_note(gateway, dispatcher, dict(tail_number='N432UP', notes=str(index),expected_version=expected_version))
                return 'saved'
            except TailNoteConflict:
                db.session.rollback(); return 'conflict'
        self.assertEqual(sorted(self.concurrent(change)), ['conflict','saved'])
        self.assertEqual(NeoScorpionTailNote.query.count(), 1)
        self.assertEqual(NeoScorpionTailNote.query.one().version, 1)
        expected_version = '1'
        self.assertEqual(sorted(self.concurrent(change)), ['conflict','saved'])
        self.assertEqual(NeoScorpionTailNote.query.one().version, 2)
        from app.models import NeoScorpionFuelWorkState
        self.assertIsNotNone(db.session.get(NeoScorpionFuelWorkState,self.ids[-1]))
