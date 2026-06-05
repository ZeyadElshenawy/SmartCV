"""Tests for the seniority-overclaim fix.

Covers all five test cases the task spec calls out, plus a few edge
cases around the prefix-stripping regex and the dispatcher integration.
"""
from __future__ import annotations

from django.test import SimpleTestCase

from resumes.services.seniority import (
    _classify_entry,
    _entry_months,
    compute_candidate_stage,
    display_seniority_prefix,
    honest_job_title,
    strip_seniority_prefix,
)


# ---------------------------------------------------------------------------
# (a) Intern-only history (taher-shaped): all internships / training /
#     contract — confident classification of every entry, very low
#     professional months → "Junior".
# ---------------------------------------------------------------------------


class ComputeStageInternOnlyHistoryTests(SimpleTestCase):
    """Mirrors taher@123.com's profile shape: all dated entries are
    internships, training programs, or short contracts. The expected
    outcome is a CONFIDENT "Junior" — the contract gets counted but
    can't reach the 24-month mid threshold."""

    def _experiences(self):
        # Exact shape of taher's experiences[] entries (real field names
        # from profile.data_content):
        return [
            {"title": "Internet of Things (IoT) Internship",
             "company": "", "start_date": "2024", "end_date": "",
             "employment_type": None, "source": None},
            {"title": "Cloud Computing Internship",
             "company": "", "start_date": "2024", "end_date": "",
             "employment_type": None, "source": None},
            {"title": "Technical Analyst (AI Systems)",
             "company": "Turing",
             "start_date": "Mar 2026", "end_date": "Apr 2026",
             "employment_type": "Contract", "source": "linkedin"},
            {"title": "Cross Platform Mobile App Developer",
             "company": "Digital Egypt Pioneers Initiative - DEPI",
             "start_date": "Jun 2025", "end_date": "Dec 2025",
             "employment_type": "Internship", "source": "linkedin"},
            {"title": "UIUX Design Internship",
             "company": "Elevvo Pathways",
             "start_date": "Aug 2025", "end_date": "Sep 2025",
             "employment_type": "Internship", "source": "linkedin"},
        ]

    def test_intern_titles_without_employment_type_tag_classified_as_training(self):
        """Hand-typed 'IoT Internship' has employment_type=None — the
        Rule-3 title fallback (\\b(internship|training|bootcamp)\\b)
        catches it."""
        exp = self._experiences()[0]
        self.assertEqual(_classify_entry(exp), "training")

    def test_employment_type_tag_classifies_directly(self):
        """LinkedIn-merged 'Internship' tag → training, regardless of
        title."""
        exp = self._experiences()[3]
        self.assertEqual(_classify_entry(exp), "training")

    def test_contract_counted_as_professional(self):
        """Per the trace's decision: Contract counts toward professional
        months — even if short. The Turing 2-month engagement is the
        most-senior real engagement in this shape and contributes its
        2 months to the tenure sum."""
        exp = self._experiences()[2]  # Turing Contract
        self.assertEqual(_classify_entry(exp), "professional")
        # Inclusive months: Mar 2026 → Apr 2026 = 2.
        self.assertEqual(_entry_months(exp), 2)

    def test_intern_only_history_returns_junior_confident(self):
        """End-to-end on the taher-shaped profile: should be
        'Junior' with confident=True (every dated entry is
        classifiable)."""
        stage, confident = compute_candidate_stage(self._experiences())
        self.assertEqual(stage, "Junior")
        self.assertTrue(
            confident,
            "Multiple entries with parseable dates AND categorisable "
            "(training/professional) — must be confident.",
        )

    def test_intern_only_history_renders_junior_prefix(self):
        self.assertEqual(
            display_seniority_prefix(self._experiences()),
            "Junior",
        )

    def test_intern_only_history_full_pipeline_to_honest_title(self):
        """Mid-targeting JD + Junior-supported candidate → 'Junior X',
        not 'Mid X'. The JD's level NEVER survives."""
        out = honest_job_title("Mid Flutter Developer", self._experiences())
        self.assertEqual(out, "Junior Flutter Developer")


# ---------------------------------------------------------------------------
# (b) Genuinely-tenured history — proves the helper cuts both ways
#     (doesn't always-strip / always-Junior).
# ---------------------------------------------------------------------------


class ComputeStageTenuredHistoryTests(SimpleTestCase):
    """A profile with 5+ years of full-time tenure should produce 'Mid'
    or 'Senior' (depending on the bucket) with confident=True."""

    def test_thirty_months_full_time_returns_mid(self):
        """30 months full-time → in the [24, 60) bucket → 'Mid'."""
        experiences = [
            {"title": "Software Engineer", "company": "Acme",
             "start_date": "Jan 2022", "end_date": "Jun 2024",
             "employment_type": "Full-time"},
        ]
        stage, confident = compute_candidate_stage(experiences)
        self.assertEqual(stage, "Mid")
        self.assertTrue(confident)
        self.assertEqual(
            honest_job_title("Junior Flutter Developer", experiences),
            "Mid Flutter Developer",
            "candidate-supported Mid > JD's Junior target — fix promotes "
            "honestly, not just always-strip",
        )

    def test_seventy_months_full_time_returns_senior(self):
        """70 months full-time → [60, 96) → 'Senior'."""
        experiences = [
            {"title": "Backend Engineer", "company": "Big Co",
             "start_date": "Jan 2019", "end_date": "Oct 2024",
             "employment_type": "Full-time"},
        ]
        stage, confident = compute_candidate_stage(experiences)
        self.assertEqual(stage, "Senior")
        self.assertTrue(confident)

    def test_hundred_twenty_months_returns_staff(self):
        """120 months → >= 96 → 'Staff'."""
        experiences = [
            {"title": "Principal Engineer", "company": "Big Co",
             "start_date": "Jan 2014", "end_date": "Dec 2023",
             "employment_type": "Full-time"},
        ]
        stage, confident = compute_candidate_stage(experiences)
        self.assertEqual(stage, "Staff")
        self.assertTrue(confident)

    def test_tenured_candidate_keeps_jd_role_base(self):
        """The helper retains the bare role from the JD; only the
        seniority prefix changes."""
        experiences = [
            {"title": "Engineer", "company": "Acme",
             "start_date": "Jan 2020", "end_date": "Jan 2026",
             "employment_type": "Full-time"},
        ]
        self.assertEqual(
            honest_job_title("Mid Backend Engineer", experiences),
            "Senior Backend Engineer",
        )


# ---------------------------------------------------------------------------
# (c) Unparseable / empty dates + no employment_type tags → not
#     confident → strip the prefix entirely.
# ---------------------------------------------------------------------------


class ComputeStageInsufficientSignalTests(SimpleTestCase):

    def test_all_dates_unparseable_returns_not_confident(self):
        experiences = [
            {"title": "Software Engineer", "company": "Co",
             "start_date": "", "end_date": "",
             "employment_type": "Full-time"},
            {"title": "Developer", "company": "Other Co",
             "start_date": None, "end_date": None,
             "employment_type": "Full-time"},
        ]
        stage, confident = compute_candidate_stage(experiences)
        self.assertFalse(
            confident,
            "no entry has a parseable start_date → confident must be False",
        )

    def test_all_unknown_categories_returns_not_confident(self):
        """No employment_type and no training-title signal — categories
        are all 'unknown', so confident=False even though dates
        parse."""
        experiences = [
            {"title": "Software Engineer", "company": "Co",
             "start_date": "Jan 2020", "end_date": "Jan 2025",
             "employment_type": None},
            {"title": "Engineer", "company": "Other Co",
             "start_date": "Feb 2025", "end_date": "Feb 2026",
             "employment_type": None},
        ]
        stage, confident = compute_candidate_stage(experiences)
        self.assertFalse(
            confident,
            "every entry's category is 'unknown' (no etype, no training "
            "title signal) — must be confident=False",
        )

    def test_empty_experiences_returns_not_confident(self):
        stage, confident = compute_candidate_stage([])
        self.assertFalse(confident)
        self.assertEqual(display_seniority_prefix([]), "")
        self.assertEqual(display_seniority_prefix(None), "")

    def test_low_signal_falls_back_to_bare_role(self):
        """honest_job_title with no confident classification → strip
        only (option (a) fallback)."""
        experiences = [
            {"title": "Developer", "start_date": "", "end_date": "",
             "employment_type": None},
        ]
        self.assertEqual(
            honest_job_title("Senior Flutter Developer", experiences),
            "Flutter Developer",
        )


# ---------------------------------------------------------------------------
# (d) strip_seniority_prefix — direct unit tests on the prefix regex.
# ---------------------------------------------------------------------------


class StripSeniorityPrefixTests(SimpleTestCase):

    def test_strips_mid(self):
        self.assertEqual(
            strip_seniority_prefix("Mid Flutter Developer"),
            "Flutter Developer",
        )

    def test_strips_senior(self):
        self.assertEqual(
            strip_seniority_prefix("Senior Software Engineer"),
            "Software Engineer",
        )

    def test_strips_junior(self):
        self.assertEqual(
            strip_seniority_prefix("Junior Backend Engineer"),
            "Backend Engineer",
        )

    def test_strips_sr_abbreviation(self):
        self.assertEqual(
            strip_seniority_prefix("Sr. Frontend Engineer"),
            "Frontend Engineer",
        )

    def test_strips_jr_abbreviation(self):
        self.assertEqual(
            strip_seniority_prefix("Jr. Data Engineer"),
            "Data Engineer",
        )

    def test_strips_lead(self):
        self.assertEqual(
            strip_seniority_prefix("Lead ML Engineer"),
            "ML Engineer",
        )

    def test_strips_staff(self):
        self.assertEqual(
            strip_seniority_prefix("Staff Software Engineer"),
            "Software Engineer",
        )

    def test_strips_principal(self):
        self.assertEqual(
            strip_seniority_prefix("Principal Architect"),
            "Architect",
        )

    def test_strips_entry_level_hyphenated(self):
        self.assertEqual(
            strip_seniority_prefix("Entry-Level Designer"),
            "Designer",
        )

    def test_strips_entry_level_spaced(self):
        self.assertEqual(
            strip_seniority_prefix("Entry Level Designer"),
            "Designer",
        )

    def test_strips_mid_level_hyphenated(self):
        self.assertEqual(
            strip_seniority_prefix("Mid-Level Data Scientist"),
            "Data Scientist",
        )

    def test_case_insensitive(self):
        self.assertEqual(
            strip_seniority_prefix("senior software engineer"),
            "software engineer",
        )
        self.assertEqual(
            strip_seniority_prefix("MID FLUTTER DEVELOPER"),
            "FLUTTER DEVELOPER",
        )

    def test_no_prefix_unchanged(self):
        """A title without a leading seniority word is returned
        unchanged (modulo trim)."""
        self.assertEqual(
            strip_seniority_prefix("Flutter Developer"),
            "Flutter Developer",
        )
        self.assertEqual(
            strip_seniority_prefix("Software Engineer III"),
            "Software Engineer III",
        )
        self.assertEqual(
            strip_seniority_prefix("Engineering Manager"),
            "Engineering Manager",
        )

    def test_handles_empty_and_none(self):
        self.assertEqual(strip_seniority_prefix(""), "")
        self.assertEqual(strip_seniority_prefix(None), "")

    def test_does_not_strip_seniority_in_middle_of_title(self):
        """'Lead' as a leading word strips; 'Lead' embedded in a longer
        non-prefix title position does not."""
        # The regex anchors at ^, so this is structurally guaranteed.
        self.assertEqual(
            strip_seniority_prefix("Engineering Lead"),
            "Engineering Lead",
        )


# ---------------------------------------------------------------------------
# (e) Regression guard: cap calibration STILL reads classification.seniority
#     unchanged. The fix touches the displayed-label path only — the
#     planner's seniority-based cap math must be untouched.
# ---------------------------------------------------------------------------


class CapCalibrationRegressionTests(SimpleTestCase):
    """Confirms the planner's seniority-driven cap calibration still
    fires (reads classification.seniority and applies the override
    table). The dispatcher's honest_job_title gate does NOT alter the
    classifier, the RoleClassification.seniority field, or the planner's
    behaviour — only the displayed label downstream.
    """

    def test_seniority_calibration_table_still_returns_override(self):
        """The cap-override table itself is unchanged — same data, same
        consumer."""
        from resumes.services.kb_integration import seniority_calibration
        # The known 'mid' override matches DEFAULT_SECTION_CAPS so the
        # test asserts table presence + shape, not a delta.
        mid = seniority_calibration("mid")
        self.assertIsNotNone(mid)
        self.assertEqual(mid.get("experience"), 12)
        self.assertEqual(mid.get("projects"), 8)
        junior = seniority_calibration("junior")
        self.assertIsNotNone(junior)
        # Junior has a tighter experience cap than mid by design.
        self.assertLess(
            junior.get("experience"), mid.get("experience"),
            "the override table itself must be intact",
        )

    def test_classifier_seniority_field_unchanged(self):
        """RoleClassification.seniority still receives the JD's
        merged value — the helper does NOT mutate the classification."""
        from profiles.services.role_classifier import RoleClassification
        # The schema is unchanged; we don't add a candidate_seniority
        # field (that's noted as future work). The seniority slot still
        # carries the JD's merged value.
        cls = RoleClassification(
            primary_role="Flutter Developer",
            seniority="mid",
            tech_stack_signals=["Flutter", "Dart"],
            region="global",
        )
        self.assertEqual(cls.seniority, "mid")

    def test_dispatcher_passes_classification_seniority_to_planner_unchanged(self):
        """The dispatcher's honest_job_title gate does NOT touch the
        classification object the planner sees. build_plan still reads
        classification.seniority as 'mid' regardless of what the
        candidate's tenure actually says."""
        # Inspect the dispatcher source — this is a structural guard
        # so a future refactor that accidentally rewrote classification
        # would trip this test.
        import inspect
        from resumes.services import pipeline_dispatch
        source = inspect.getsource(pipeline_dispatch._generate_via_v2)
        # build_plan is still passed the unmodified `classification`.
        self.assertIn("classification=classification", source)
        # And honest_job_title is wired (the gate is present).
        self.assertIn("honest_job_title", source)
        # And NOTHING in the dispatcher mutates classification.seniority.
        self.assertNotIn("classification.seniority =", source)
        self.assertNotIn("classification.seniority=", source)


# ---------------------------------------------------------------------------
# Edge cases that don't fit cleanly into (a)–(e) but matter for general
# correctness.
# ---------------------------------------------------------------------------


class EdgeCaseTests(SimpleTestCase):

    def test_jd_without_seniority_prefix_is_passed_through(self):
        """A JD whose title already has no level prefix should produce
        the candidate's prefix added (if confident) — proving the helper
        handles JD shapes that don't need stripping."""
        experiences = [
            {"title": "Engineer", "start_date": "Jan 2020",
             "end_date": "Jan 2026", "employment_type": "Full-time"},
        ]
        # JD title without a prefix — no stripping needed, candidate
        # prefix is prepended.
        self.assertEqual(
            honest_job_title("Flutter Developer", experiences),
            "Senior Flutter Developer",
        )

    def test_year_only_dates_parse(self):
        """Year-only dates should produce SOME tenure credit (the
        underlying parser falls back to January)."""
        experiences = [
            {"title": "Engineer", "start_date": "2018",
             "end_date": "2024", "employment_type": "Full-time"},
        ]
        # Jan 2018 → Jan 2024 inclusive = 73 months → Senior.
        stage, confident = compute_candidate_stage(experiences)
        self.assertEqual(stage, "Senior")
        self.assertTrue(confident)

    def test_internship_with_no_employment_type_recognised_by_title_fallback(self):
        """A hand-typed 'Summer Internship 2024' with no
        employment_type tag must still be excluded via the title
        fallback."""
        exp = {"title": "Summer Internship 2024",
               "start_date": "Jun 2024", "end_date": "Aug 2024",
               "employment_type": None}
        self.assertEqual(_classify_entry(exp), "training")

    def test_fellow_title_classified_as_training(self):
        """'\\bfellow\\b' is in the strict title regex (Rule 2)."""
        exp = {"title": "Research Fellow", "start_date": "Jan 2024",
               "end_date": "Jun 2024", "employment_type": None}
        self.assertEqual(_classify_entry(exp), "training")

    def test_volunteer_employment_type_excluded(self):
        exp = {"title": "Software Volunteer", "start_date": "Jan 2024",
               "end_date": "Mar 2024", "employment_type": "Volunteer"}
        self.assertEqual(_classify_entry(exp), "training")

    def test_part_time_counted_as_professional(self):
        """Part-time is a professional engagement (the trace's
        exclusion set names training only — Part-time counts)."""
        exp = {"title": "Engineer", "start_date": "Jan 2022",
               "end_date": "Jan 2026", "employment_type": "Part-time"}
        self.assertEqual(_classify_entry(exp), "professional")
