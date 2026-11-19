"""Registration-only identity contract; legacy accounts are not converted."""
import unittest
from unittest.mock import patch

from app.extensions import db
from app.models import FuelDriverIdentity, GatewayMembership, GatewayNodeRole, PortalAppAccess, StaffingPerson, User, UserToken
from tests import test_auth_account_flows as fixtures


class RegistrationEmployeeIdTest(unittest.TestCase):
    _account_form = fixtures.AuthAccountFlowsTest._account_form
    _user = fixtures.AuthAccountFlowsTest._user
    tearDown = fixtures.AuthAccountFlowsTest.tearDown

    def setUp(self):
        fixtures.AuthAccountFlowsTest.setUp(self)
        self.app.config.update(AUTH_RATE_LIMIT_ENABLED=False,
                               AUTH_REGISTRATION_IP_MAX_ATTEMPTS=1000)

    def register(self, value, number=0, account_type='full'):
        return self.client.post('/create-account', data=self._account_form(
            employee_id=value, email=f'{account_type}-{number}@example.test',
            account_type=account_type, app_codes=['neogateway']))

    def test_invalid_formats_reject_both_account_types_before_user_writes(self):
        invalid = ('', ' ', '123456', '12345678', '123456789', '12345678901',
                   'CRN12345678', 'crn12345678', '123-4567', '+1234567', '1234567.0',
                   'CRN 12345678', '１２３４５６７', '١٢٣٤٥٦٧', '123\n4567', 'CRN12345678x')
        with patch('app.auth.routes.set_user_password') as password, \
                patch('app.auth.routes.email_service.send_email_verification') as send:
            for account_type in ('full', 'fuel_driver'):
                for number,value in enumerate(invalid):
                    with self.subTest(account_type=account_type, value=value):
                        response=self.register(value, number, account_type)
                        self.assertEqual(response.status_code, 400)
                        self.assertIn(b'7 or 10 digits (1234567 or 1234567890), numbers only', response.data)
            password.assert_not_called()
            send.assert_not_called()
        for model in (User, UserToken, GatewayMembership, PortalAppAccess, GatewayNodeRole, FuelDriverIdentity):
            self.assertEqual(model.query.count(), 0)

    def test_valid_ids_normalize_and_keep_access_pending(self):
        cases = (('0001234', '0001234'), (' 7654321\r\n', '7654321'),
                 ('1234567890', '1234567890'), (' 0001234567 ', '0001234567'))
        with patch('app.auth.routes.email_service.send_email_verification') as send:
            for account_type in ('full', 'fuel_driver'):
                # Separate ID values so the two account types cannot collide.
                for number,(raw,normalized) in enumerate(cases):
                    if account_type=='fuel_driver':
                        raw=raw.replace('1234','2345').replace('4321','5432')
                        normalized=normalized.replace('1234','2345').replace('4321','5432')
                    with self.subTest(account_type=account_type, raw=raw):
                        response=self.register(raw,number,account_type)
                        self.assertEqual(response.status_code,200)
                        user=User.query.filter_by(email=f'{account_type}-{number}@example.test').one()
                        self.assertEqual(user.employee_id,normalized)
                        self.assertEqual(user.role,'watcher')
                        self.assertIsNone(user.email_verified_at)
                        self.assertEqual(PortalAppAccess.query.filter_by(user_id=user.id).one().status,'pending')
                        self.assertEqual(GatewayNodeRole.query.count(),0)
                        driver=db.session.get(FuelDriverIdentity,user.id)
                        self.assertEqual(bool(driver and driver.requested_driver),account_type=='fuel_driver')
            self.assertEqual(send.call_count,8)

    def test_normalized_duplicate_id_does_not_change_existing_account(self):
        user=self._user('legacy-id',verified=True)
        user.employee_id=' 0001234567 '
        db.session.commit()
        with patch('app.auth.routes.email_service.send_email_verification') as send:
            for account_type in ('full','fuel_driver'):
                self.assertEqual(self.register('0001234567',account_type=account_type).status_code,400)
            send.assert_not_called()
        self.assertEqual(User.query.count(),1)
        self.assertEqual(db.session.get(User,user.id).employee_id,' 0001234567 ')
        self.assertIsNotNone(db.session.get(User,user.id).email_verified_at)

    def test_registration_preserves_matching_staffing_person(self):
        person=StaffingPerson(employee_id='0001234567',first_name='Existing',last_name='Person',classification='seasonal')
        db.session.add(person);db.session.commit()
        person_id=person.id
        with patch('app.auth.routes.email_service.send_email_verification'):
            response=self.register(' 0001234567 ')
        self.assertEqual(response.status_code,200)
        user=User.query.filter_by(email='full-0@example.test').one()
        self.assertEqual(user.employee_id,person.employee_id)
        self.assertEqual(StaffingPerson.query.count(),1)
        self.assertEqual(db.session.get(StaffingPerson,person_id).first_name,'Existing')

    def test_missing_field_and_csrf_bypass_reject(self):
        form=self._account_form();form.pop('employee_id')
        self.assertEqual(self.client.post('/create-account',data=form).status_code,400)
        self.app.config['CSRF_PROTECT_TESTING']=True
        self.assertEqual(self.register('1234567').status_code,400)
        self.assertEqual(User.query.count(),0)

    def test_rendered_form_requires_both_formats_for_every_account_type(self):
        from tests.html_contracts import document
        for account_type in ('full','fuel_driver'):
            response=self.client.get('/create-account?account_type='+account_type)
            field=document(response).one('input',name='employee_id').attrs
            self.assertIn('required',field)
            self.assertEqual(field['pattern'],'(?:[0-9]{7}|[0-9]{10})')
            self.assertIn('7 or 10 digits',field['title'])
