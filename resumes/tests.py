"""Tests for resumes.views description helpers.

These helpers handle the bracket-corruption bug territory: the resume editor's
textarea stores multiline bullet descriptions, but the JSON schema stores them
as List[str]. A mistake in this conversion (or a round-trip that mutates the
data) is what caused the bug fixed in fd90299.
"""
from django.test import SimpleTestCase

from resumes.views import (
    _description_list_to_text,
    _description_text_to_list,
)


class TextareaToListTests(SimpleTestCase):
    def test_none_becomes_empty_list(self):
        self.assertEqual(_description_text_to_list(None), [])

    def test_empty_string_becomes_empty_list(self):
        self.assertEqual(_description_text_to_list(''), [])

    def test_single_line_becomes_single_element_list(self):
        self.assertEqual(_description_text_to_list('Shipped feature X'), ['Shipped feature X'])

    def test_newline_separated_bullets_become_list(self):
        raw = 'Shipped feature X\nOwned migration Y\nMentored 2 juniors'
        self.assertEqual(
            _description_text_to_list(raw),
            ['Shipped feature X', 'Owned migration Y', 'Mentored 2 juniors'],
        )

    def test_crlf_line_endings_are_handled(self):
        """Browsers POST textareas with \\r\\n; regression guard."""
        raw = 'Line one\r\nLine two\r\nLine three'
        self.assertEqual(
            _description_text_to_list(raw),
            ['Line one', 'Line two', 'Line three'],
        )

    def test_blank_lines_are_dropped(self):
        raw = 'First\n\n\nSecond\n   \nThird'
        self.assertEqual(
            _description_text_to_list(raw),
            ['First', 'Second', 'Third'],
        )

    def test_surrounding_whitespace_is_stripped(self):
        raw = '   padded bullet   \n\ttabbed bullet\t'
        self.assertEqual(
            _description_text_to_list(raw),
            ['padded bullet', 'tabbed bullet'],
        )


class ListToTextareaTests(SimpleTestCase):
    def test_none_becomes_empty_string(self):
        self.assertEqual(_description_list_to_text(None), '')

    def test_empty_list_becomes_empty_string(self):
        self.assertEqual(_description_list_to_text([]), '')

    def test_list_joins_with_newline(self):
        self.assertEqual(
            _description_list_to_text(['First bullet', 'Second bullet']),
            'First bullet\nSecond bullet',
        )

    def test_legacy_string_value_passes_through(self):
        """Older resumes may still have string-shaped descriptions; don't mangle them."""
        self.assertEqual(
            _description_list_to_text('already a string'),
            'already a string',
        )

    def test_falsy_list_items_are_skipped(self):
        self.assertEqual(
            _description_list_to_text(['real', '', None, 'also real']),
            'real\nalso real',
        )

    def test_non_string_items_are_coerced(self):
        self.assertEqual(_description_list_to_text([1, 2]), '1\n2')


class RoundTripTests(SimpleTestCase):
    """The view's lifecycle is: stored List[str] -> textarea string (GET) ->
    back to List[str] (POST save). This must be lossless for well-formed data,
    which is exactly what the bracket-corruption bug violated."""

    def test_list_roundtrips_losslessly(self):
        original = ['Led team of 5 engineers', 'Cut p95 latency by 40%', 'Shipped feature X']
        textarea = _description_list_to_text(original)
        roundtripped = _description_text_to_list(textarea)
        self.assertEqual(roundtripped, original)

    def test_empty_list_roundtrips(self):
        self.assertEqual(_description_text_to_list(_description_list_to_text([])), [])

    def test_user_editing_in_browser_preserves_bullets(self):
        """Simulate a user opening the editor (LF on server) and the browser
        resubmitting the same textarea with CRLF line endings."""
        original = ['Built A', 'Built B', 'Built C']
        textarea_server_sent = _description_list_to_text(original)
        textarea_browser_posted = textarea_server_sent.replace('\n', '\r\n')
        self.assertEqual(_description_text_to_list(textarea_browser_posted), original)



# ============================================================
# resumes.services.scoring — ATS breakdown + evidence confidence
# ============================================================

from types import SimpleNamespace
from resumes.services.scoring import (
    compute_ats_breakdown,
    compute_evidence_confidence,
    calculate_ats_score,
    STUFFING_THRESHOLD,
    STUFFING_PENALTY_PER_SKILL,
    IN_CONTEXT_BONUS_PER_SKILL,
)


class ComputeAtsBreakdownTests(SimpleTestCase):
    def test_no_job_skills_returns_zero(self):
        out = compute_ats_breakdown({"skills": ["Python"]}, [])
        self.assertEqual(out["score"], 0.0)
        self.assertEqual(out["matched_count"], 0)
        self.assertEqual(out["total_count"], 0)

    def test_basic_match_score(self):
        # 2 of 4 keywords present anywhere in the resume JSON
        content = {"skills": ["Python", "SQL"], "experience": []}
        out = compute_ats_breakdown(content, ["Python", "SQL", "Rust", "Go"])
        self.assertEqual(out["matched_count"], 2)
        self.assertEqual(out["total_count"], 4)
        self.assertEqual(out["raw_score"], 50.0)
        # No in-context bonus (no experience), no stuffing — final = raw
        self.assertEqual(out["score"], 50.0)

    def test_in_context_bonus_for_keywords_in_experience(self):
        # Same matched count, but the keyword also appears in experience
        # bullets — should get the in-context bonus.
        content = {
            "skills": ["Python"],
            "experience": [
                {"description": ["Built distributed Python pipelines"]},
            ],
        }
        out = compute_ats_breakdown(content, ["Python"])
        self.assertEqual(out["matched_count"], 1)
        self.assertEqual(out["in_context_count"], 1)
        # raw 100 + bonus 2, capped at 100
        self.assertEqual(out["score"], 100.0)
        self.assertEqual(out["in_context_bonus"], IN_CONTEXT_BONUS_PER_SKILL)

    def test_in_context_bonus_is_capped(self):
        # 6 in-context skills × 2 = 12 raw bonus, capped at 10
        content = {
            "skills": ["Python", "SQL", "Java", "Rust", "Go", "Ruby"],
            "experience": [{
                "description": [
                    "Used Python and SQL daily",
                    "Migrated services to Java and Go",
                    "Wrote internal tooling in Rust and Ruby",
                ],
            }],
        }
        out = compute_ats_breakdown(content, ["Python", "SQL", "Java", "Rust", "Go", "Ruby"])
        self.assertEqual(out["in_context_count"], 6)
        self.assertEqual(out["in_context_bonus"], 10.0)  # capped

    def test_keyword_stuffing_is_penalized(self):
        # A skill that appears > STUFFING_THRESHOLD times across the resume
        # gets penalized 5 points per stuffed keyword.
        stuffed = " python " * (STUFFING_THRESHOLD + 1)
        content = {
            "skills": ["Python"],
            "experience": [{"description": [stuffed]}],
        }
        out = compute_ats_breakdown(content, ["Python"])
        self.assertIn("Python", out["stuffed_skills"])
        self.assertEqual(out["stuffing_penalty"], STUFFING_PENALTY_PER_SKILL)
        # raw 100 + 2 bonus - 5 penalty = 97
        self.assertEqual(out["score"], 97.0)

    def test_score_is_clamped_to_zero_when_penalties_exceed(self):
        # Engineer 5 stuffed skills (5 × 5pt = 25 pt penalty) on a resume
        # with raw_score 0 (no skills match). Final must clamp to 0, not negative.
        stuffed_text = " ".join(["python sql java rust go"] * 6)
        content = {"experience": [{"description": [stuffed_text]}]}
        out = compute_ats_breakdown(content, ["Python", "SQL", "Java", "Rust", "Go"])
        self.assertGreater(out["stuffing_penalty"], 0)
        self.assertGreaterEqual(out["score"], 0.0)

    def test_keyword_counts_per_skill_are_returned(self):
        content = {"skills": ["Python", "SQL"]}
        out = compute_ats_breakdown(content, ["Python", "SQL", "Rust"])
        self.assertEqual(out["keyword_counts"]["Python"], 1)
        self.assertEqual(out["keyword_counts"]["SQL"], 1)
        self.assertEqual(out["keyword_counts"]["Rust"], 0)

    def test_legacy_calculate_ats_score_returns_just_the_float(self):
        content = {"skills": ["Python"]}
        score = calculate_ats_score(content, ["Python"])
        self.assertIsInstance(score, float)
        self.assertEqual(score, 100.0)


class ComputeEvidenceConfidenceTests(SimpleTestCase):
    def _profile(self, **signals):
        return SimpleNamespace(data_content=signals)

    def test_no_signals_returns_zero(self):
        out = compute_evidence_confidence(self._profile())
        self.assertEqual(out["score"], 0)
        self.assertEqual(out["label"], "Untested")
        self.assertEqual(out["sources"], [])

    def test_only_github_with_repos_counts(self):
        out = compute_evidence_confidence(self._profile(
            github_signals={"public_repos": 5},
        ))
        self.assertEqual(out["score"], 1)
        self.assertEqual(out["label"], "Limited")
        self.assertEqual(out["sources"], ["github"])

    def test_github_with_zero_repos_does_not_count(self):
        out = compute_evidence_confidence(self._profile(
            github_signals={"public_repos": 0},
        ))
        self.assertEqual(out["score"], 0)

    def test_error_snapshot_is_skipped(self):
        out = compute_evidence_confidence(self._profile(
            github_signals={"error": "rate-limited", "public_repos": 99},
        ))
        self.assertEqual(out["score"], 0)

    def test_scholar_needs_pubs_or_citations(self):
        # Empty top_publications + 0 citations → does not count
        out = compute_evidence_confidence(self._profile(
            scholar_signals={"top_publications": [], "total_citations": 0},
        ))
        self.assertEqual(out["score"], 0)
        # With citations
        out = compute_evidence_confidence(self._profile(
            scholar_signals={"top_publications": [], "total_citations": 100},
        ))
        self.assertEqual(out["score"], 1)
        # With publications
        out = compute_evidence_confidence(self._profile(
            scholar_signals={"top_publications": [{"title": "A"}], "total_citations": 0},
        ))
        self.assertEqual(out["score"], 1)

    def test_kaggle_any_category_activity_counts(self):
        out = compute_evidence_confidence(self._profile(
            kaggle_signals={
                "competitions": {"count": 0},
                "datasets": {"count": 0},
                "notebooks": {"count": 3},
                "discussion": {"count": 0},
            },
        ))
        self.assertEqual(out["score"], 1)
        self.assertEqual(out["sources"], ["kaggle"])

    def test_all_three_signals_max_score(self):
        out = compute_evidence_confidence(self._profile(
            github_signals={"public_repos": 5},
            scholar_signals={"total_citations": 100, "top_publications": [{"title": "X"}]},
            kaggle_signals={"competitions": {"count": 1}, "datasets": {"count": 0},
                            "notebooks": {"count": 0}, "discussion": {"count": 0}},
        ))
        self.assertEqual(out["score"], 3)
        self.assertEqual(out["label"], "Strong")
        self.assertEqual(set(out["sources"]), {"github", "scholar", "kaggle"})


from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse


class ResumeEditPreviewTemplateClassTests(TestCase):
    """Live preview on /resumes/edit/<id>/ must carry a pdf-preview--<template>
    modifier class so each template choice shifts the preview's CSS vars.
    If it doesn't render server-side, the page opens mismatched with the
    saved template, and the first radio click would be the first thing to
    shape the preview (jarring UX).
    """

    def setUp(self):
        from jobs.models import Job
        from analysis.models import GapAnalysis
        from resumes.models import GeneratedResume
        User = get_user_model()
        self.user = User.objects.create_user(
            username='rt@example.com', email='rt@example.com', password='x',
        )
        self.client.force_login(self.user)
        job = Job.objects.create(user=self.user, title='Data Scientist')
        self.gap = GapAnalysis.objects.create(
            user=self.user, job=job, similarity_score=0.5,
        )
        self.resume = GeneratedResume.objects.create(
            gap_analysis=self.gap,
            content={
                'professional_title': 'Data Scientist',
                'professional_summary': 'Lorem ipsum.',
                'template_name': 'executive',
            },
        )

    def test_preview_carries_saved_template_modifier(self):
        resp = self.client.get(reverse('resume_edit', args=[self.resume.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'pdf-preview pdf-preview--executive')

    def test_preview_falls_back_to_standard_when_template_missing(self):
        self.resume.content = {'professional_title': 'X', 'professional_summary': 'Y'}
        self.resume.save()
        resp = self.client.get(reverse('resume_edit', args=[self.resume.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'pdf-preview pdf-preview--standard')

    def test_every_card_has_a_thumbnail_preview(self):
        """Each template radio card must render a .template-thumb miniature
        styled with the same pdf-preview--<value> modifier the big right-side
        preview uses, so users can eyeball the style before picking."""
        import re
        resp = self.client.get(reverse('resume_edit', args=[self.resume.id]))
        body = resp.content.decode('utf-8')
        values = re.findall(r'value="([^"]+)"\s+class="sr-only"', body)
        self.assertTrue(values, 'Template radio values not found in page.')
        for v in values:
            needle = f'class="template-thumb pdf-preview pdf-preview--{v}"'
            self.assertIn(
                needle, body,
                f'Template "{v}" radio card is missing its thumbnail div.',
            )
        # And the thumbnail stylesheet must exist.
        self.assertIn('.template-thumb {', body)

    def test_every_template_choice_has_matching_css_modifier(self):
        """Regression: if a new template is added to template_choices in the
        view but the CSS block is forgotten, the preview silently falls back
        to the default. Compare the view's choices against CSS selectors in
        the rendered page and fail if any are missing."""
        import re
        resp = self.client.get(reverse('resume_edit', args=[self.resume.id]))
        body = resp.content.decode('utf-8')
        values = re.findall(r'value="([^"]+)"\s+class="sr-only"', body)
        self.assertTrue(values, 'Template radio values not found in page.')
        for v in values:
            self.assertIn(
                f'.pdf-preview--{v}', body,
                f'No .pdf-preview--{v} rule in the page for template "{v}".',
            )
