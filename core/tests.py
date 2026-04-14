"""Tests for core services — career stage detection.

The stage drives the dashboard hero (label, copy, primary CTA), so the
priority order matters: an offer always beats interviews, interviews beat
applying, applying beats just-looking, etc.
"""
from django.test import SimpleTestCase

from core.services.career_stage import detect_career_stage


class DetectCareerStageTests(SimpleTestCase):
    def test_no_profile_returns_getting_started(self):
        s = detect_career_stage(has_profile=False, status_counts={})
        self.assertEqual(s['key'], 'getting_started')
        self.assertEqual(s['primary_route'], 'upload_master_profile')

    def test_profile_no_jobs_returns_ready_to_look(self):
        s = detect_career_stage(has_profile=True, status_counts={})
        self.assertEqual(s['key'], 'ready_to_look')
        self.assertEqual(s['primary_route'], 'job_input_view')

    def test_only_saved_returns_actively_applying(self):
        s = detect_career_stage(has_profile=True, status_counts={'saved': 3})
        self.assertEqual(s['key'], 'actively_applying')

    def test_only_applied_returns_actively_applying(self):
        s = detect_career_stage(has_profile=True, status_counts={'applied': 5})
        self.assertEqual(s['key'], 'actively_applying')

    def test_interviewing_overrides_applying(self):
        s = detect_career_stage(has_profile=True, status_counts={
            'saved': 5, 'applied': 8, 'interviewing': 1,
        })
        self.assertEqual(s['key'], 'interviewing')
        self.assertEqual(s['tone'], 'accent')

    def test_offer_is_top_priority(self):
        # Even with active interviews and applications, an offer wins.
        s = detect_career_stage(has_profile=True, status_counts={
            'saved': 5, 'applied': 8, 'interviewing': 2, 'offer': 1,
        })
        self.assertEqual(s['key'], 'offer_in_hand')
        self.assertEqual(s['tone'], 'success')

    def test_only_rejected_returns_reflecting(self):
        s = detect_career_stage(has_profile=True, status_counts={'rejected': 3})
        self.assertEqual(s['key'], 'reflecting')

    def test_rejected_loses_to_interviewing(self):
        s = detect_career_stage(has_profile=True, status_counts={
            'rejected': 3, 'interviewing': 1,
        })
        self.assertEqual(s['key'], 'interviewing')

    def test_status_counts_are_case_insensitive(self):
        s = detect_career_stage(has_profile=True, status_counts={'OFFER': 1})
        self.assertEqual(s['key'], 'offer_in_hand')

    def test_missing_keys_treated_as_zero(self):
        s = detect_career_stage(has_profile=True, status_counts={'applied': 0})
        self.assertEqual(s['key'], 'ready_to_look')

    def test_each_stage_has_required_fields(self):
        # Every returned stage must have the fields the template relies on.
        for ctx in [
            (False, {}),
            (True, {}),
            (True, {'saved': 1}),
            (True, {'interviewing': 1}),
            (True, {'offer': 1}),
            (True, {'rejected': 1}),
        ]:
            with self.subTest(ctx=ctx):
                s = detect_career_stage(has_profile=ctx[0], status_counts=ctx[1])
                for required in ('key', 'label', 'detail', 'primary_label', 'primary_href', 'tone'):
                    self.assertIn(required, s)
                    self.assertTrue(s[required], f"empty value for {required}")
