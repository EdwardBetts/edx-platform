# -*- coding: utf-8 -*-
"""
Tests for the service classes in verify_student.
"""

from datetime import datetime

import ddt
from django.conf import settings
from mock import patch
from freezegun import freeze_time
from pytz import utc

from lms.djangoapps.verify_student.models import ManualVerification, SoftwareSecurePhotoVerification, SSOVerification
from lms.djangoapps.verify_student.services import IDVerificationService
from student.tests.factories import UserFactory
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory

FAKE_SETTINGS = {
    "DAYS_GOOD_FOR": 10,
}


@patch.dict(settings.VERIFY_STUDENT, FAKE_SETTINGS)
@ddt.ddt
class TestIDVerificationService(ModuleStoreTestCase):
    """
    Tests for IDVerificationService.
    """

    def test_user_is_verified(self):
        """
        Test to make sure we correctly answer whether a user has been verified.
        """
        user = UserFactory.create()
        attempt = SoftwareSecurePhotoVerification(user=user)
        attempt.save()

        # If it's any of these, they're not verified...
        for status in ["created", "ready", "denied", "submitted", "must_retry"]:
            attempt.status = status
            attempt.save()
            self.assertFalse(IDVerificationService.user_is_verified(user), status)

        attempt.status = "approved"
        attempt.save()
        self.assertTrue(IDVerificationService.user_is_verified(user), attempt.status)

    def test_user_has_valid_or_pending(self):
        """
        Determine whether we have to prompt this user to verify, or if they've
        already at least initiated a verification submission.
        """
        user = UserFactory.create()
        attempt = SoftwareSecurePhotoVerification(user=user)

        # If it's any of these statuses, they don't have anything outstanding
        for status in ["created", "ready", "denied"]:
            attempt.status = status
            attempt.save()
            self.assertFalse(IDVerificationService.user_has_valid_or_pending(user), status)

        # Any of these, and we are. Note the benefit of the doubt we're giving
        # -- must_retry, and submitted both count until we hear otherwise
        for status in ["submitted", "must_retry", "approved"]:
            attempt.status = status
            attempt.save()
            self.assertTrue(IDVerificationService.user_has_valid_or_pending(user), status)

    def test_user_status(self):
        # each part of the test is in it's own 'frozen time' block because the status is dependent on recency of
        # verifications and in order to control the recency, we just put everything inside of a frozen time

        # test for correct status when no error returned
        user = UserFactory.create()
        with freeze_time('2014-12-12'):
            status = IDVerificationService.user_status(user)
            expected_status = {'status': 'none', 'error': '', 'should_display': True, 'verification_expiry': '',
                               'status_date': ''}
            self.assertDictEqual(status, expected_status)

        with freeze_time('2015-01-02'):
            # test for when photo verification has been created
            SoftwareSecurePhotoVerification.objects.create(user=user, status='approved')
            status = IDVerificationService.user_status(user)
            expected_status = {'status': 'approved', 'error': '', 'should_display': True, 'verification_expiry': '',
                               'status_date': datetime.now(utc)}
            self.assertDictEqual(status, expected_status)

        with freeze_time('2015-02-02'):
            # create another photo verification for the same user, make sure the denial
            # is handled properly
            SoftwareSecurePhotoVerification.objects.create(
                user=user, status='denied', error_msg='[{"photoIdReasons": ["Not provided"]}]'
            )
            status = IDVerificationService.user_status(user)
            expected_status = {
                'status': 'must_reverify', 'error': ['id_image_missing'], 'should_display': True, 'verification_expiry': '',
                'status_date': '',
            }
            self.assertDictEqual(status, expected_status)

        with freeze_time('2015-03-02'):
            # test for when sso verification has been created
            SSOVerification.objects.create(user=user, status='approved')
            status = IDVerificationService.user_status(user)
            expected_status = {'status': 'approved', 'error': '', 'should_display': False, 'verification_expiry': '',
                               'status_date': datetime.now(utc)}
            self.assertDictEqual(status, expected_status)

        with freeze_time('2015-04-02'):
            # create another sso verification for the same user, make sure the denial
            # is handled properly
            SSOVerification.objects.create(user=user, status='denied')
            status = IDVerificationService.user_status(user)
            expected_status = {'status': 'must_reverify', 'error': '', 'should_display': False, 'verification_expiry': '',
                               'status_date': ''}
            self.assertDictEqual(status, expected_status)

        with freeze_time('2015-05-02'):
            # test for when manual verification has been created
            ManualVerification.objects.create(user=user, status='approved')
            status = IDVerificationService.user_status(user)
            expected_status = {'status': 'approved', 'error': '', 'should_display': False, 'verification_expiry': '',
                               'status_date': datetime.now(utc)}
            self.assertDictEqual(status, expected_status)

    @ddt.unpack
    @ddt.data(
        {'enrollment_mode': 'honor', 'status': None, 'output': 'N/A'},
        {'enrollment_mode': 'audit', 'status': None, 'output': 'N/A'},
        {'enrollment_mode': 'verified', 'status': False, 'output': 'Not ID Verified'},
        {'enrollment_mode': 'verified', 'status': True, 'output': 'ID Verified'},
    )
    def test_verification_status_for_user(self, enrollment_mode, status, output):
        """
        Verify verification_status_for_user returns correct status.
        """
        user = UserFactory.create()
        CourseFactory.create()

        with patch(
            'lms.djangoapps.verify_student.services.IDVerificationService.user_is_verified'
        ) as mock_verification:
            mock_verification.return_value = status

            status = IDVerificationService.verification_status_for_user(user, enrollment_mode)
            self.assertEqual(status, output)

    def test_get_verified_user_ids(self):
        """
        Tests for getting users that are verified.
        """
        user_a = UserFactory.create()
        user_b = UserFactory.create()
        user_c = UserFactory.create()
        user_unverified = UserFactory.create()
        user_denied = UserFactory.create()

        SoftwareSecurePhotoVerification.objects.create(user=user_a, status='approved')
        ManualVerification.objects.create(user=user_b, status='approved')
        SSOVerification.objects.create(user=user_c, status='approved')
        SSOVerification.objects.create(user=user_denied, status='denied')

        verified_user_ids = set(IDVerificationService.get_verified_user_ids([
            user_a, user_b, user_c, user_unverified, user_denied
        ]))
        expected_user_ids = {user_a.id, user_b.id, user_c.id}

        self.assertEqual(expected_user_ids, verified_user_ids)
