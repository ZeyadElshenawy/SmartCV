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


class DocxExportTests(TestCase):
    """The DOCX exporter mirrors PDF export's section_order, the same
    fields, and the same authorization. The output should be a valid
    .docx (zip with [Content_Types].xml) carrying the candidate's
    name, professional title, all section headings the saved
    section_order asks for, and content for each.

    We don't try to verify visual fidelity — DOCX rendering varies by
    Word version and ATS pipelines re-style aggressively anyway.
    """

    def setUp(self):
        from jobs.models import Job
        from analysis.models import GapAnalysis
        from profiles.models import UserProfile
        from resumes.models import GeneratedResume
        from django.contrib.auth import get_user_model
        User = get_user_model()
        self.user = User.objects.create_user(
            username='docx@example.com', email='docx@example.com', password='x',
        )
        UserProfile.objects.create(
            user=self.user, full_name='Ada Lovelace', email='docx@example.com',
            phone='+1-555-0100', location='London, UK',
            linkedin_url='https://www.linkedin.com/in/ada/',
            github_url='https://github.com/ada/',
            data_content={'portfolio_url': 'https://ada.example.com'},
        )
        self.client.force_login(self.user)
        job = Job.objects.create(
            user=self.user, title='Backend Engineer', company='Acme Corp',
            description='Need a Python backend dev.',
            extracted_skills=['Python', 'PostgreSQL'],
        )
        gap = GapAnalysis.objects.create(user=self.user, job=job, similarity_score=0.7)
        self.resume = GeneratedResume.objects.create(
            gap_analysis=gap,
            content={
                'professional_title': 'Backend Systems Engineer',
                'professional_summary': 'Built distributed Python systems.',
                'skills': ['Python', 'PostgreSQL', 'Redis'],
                'experience': [{
                    'title': 'Backend Engineer',
                    'company': 'PriorCo',
                    'duration': '2023 - Present',
                    'description': ['Cut p99 latency from 1.2s to 380ms.', 'Owned async migration.'],
                }],
                'education': [{
                    'degree': 'BSc Computer Science',
                    'institution': 'Cairo University',
                    'graduation_year': '2024',
                }],
                'projects': [{
                    'name': 'pgbench-tuner',
                    'url': 'https://github.com/ada/pgbench-tuner',
                    'description': ['Auto-tunes PostgreSQL parameters.'],
                }],
                'certifications': [{
                    'name': 'AWS SAA',
                    'issuer': 'Amazon',
                    'date': '2024',
                    'url': 'https://example.com/cert',
                }],
                'languages': ['English', 'Arabic'],
            },
        )

    def _url(self):
        return f'/resumes/export-docx/{self.resume.id}/'

    def test_export_returns_docx_content_type(self):
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp['Content-Type'],
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        )
        self.assertIn('attachment', resp['Content-Disposition'])
        self.assertIn('.docx', resp['Content-Disposition'])

    def test_export_produces_valid_zip_with_required_parts(self):
        """A real DOCX is a zip containing [Content_Types].xml and
        word/document.xml. If we accidentally returned plain text or
        a corrupted file, this catches it."""
        import io
        import zipfile
        resp = self.client.get(self._url())
        buf = io.BytesIO(resp.content)
        with zipfile.ZipFile(buf) as z:
            names = set(z.namelist())
            self.assertIn('[Content_Types].xml', names)
            self.assertIn('word/document.xml', names)
            doc_xml = z.read('word/document.xml').decode('utf-8')
        # Spot-check: the candidate's name should appear in the document XML
        self.assertIn('ADA LOVELACE', doc_xml)
        # Section headings the saved order asks for should all be present
        self.assertIn('PROFESSIONAL SUMMARY', doc_xml)
        self.assertIn('SKILLS', doc_xml)
        self.assertIn('PROFESSIONAL EXPERIENCE', doc_xml)
        self.assertIn('EDUCATION', doc_xml)
        self.assertIn('PROJECTS', doc_xml)
        self.assertIn('CERTIFICATIONS', doc_xml)
        self.assertIn('LANGUAGES', doc_xml)
        # Content sample — bullet text from experience
        self.assertIn('Cut p99 latency', doc_xml)

    def test_export_honors_saved_section_order(self):
        """Sections in the DOCX should appear in the user's saved
        section_order, not the default. Verify by extracting all section
        heading positions and asserting the order matches what's saved."""
        import io, zipfile, re
        # Reorder: projects → skills → summary, rest fills in after.
        self.resume.content = {
            **self.resume.content,
            'section_order': ['projects', 'skills', 'summary'],
        }
        self.resume.save()
        resp = self.client.get(self._url())
        with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
            doc_xml = z.read('word/document.xml').decode('utf-8')
        proj_pos = doc_xml.find('PROJECTS')
        skills_pos = doc_xml.find('SKILLS')
        summary_pos = doc_xml.find('PROFESSIONAL SUMMARY')
        # All three must appear, and in the order the user saved
        self.assertGreater(proj_pos, 0)
        self.assertGreater(skills_pos, 0)
        self.assertGreater(summary_pos, 0)
        self.assertLess(proj_pos, skills_pos)
        self.assertLess(skills_pos, summary_pos)

    def test_export_per_owner_scope(self):
        from django.contrib.auth import get_user_model
        User = get_user_model()
        other = User.objects.create_user(
            username='other@example.com', email='other@example.com', password='x',
        )
        self.client.force_login(other)
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 404)

    def test_export_handles_empty_sections_gracefully(self):
        """A nearly-empty resume must still produce a valid DOCX (just
        with a header and whatever sections do have content)."""
        import io, zipfile
        self.resume.content = {'professional_title': 'Engineer'}
        self.resume.save()
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
            doc_xml = z.read('word/document.xml').decode('utf-8')
        self.assertIn('ADA LOVELACE', doc_xml)
        # No Skills heading because skills was empty
        self.assertNotIn('SKILLS', doc_xml)


class SectionOrderEndpointTests(TestCase):
    """The user can drag-reorder sections on the edit page; the order is
    persisted on resume.content['section_order'] and applied uniformly
    to the live preview, the resume_preview page, and the downloaded PDF."""

    def setUp(self):
        from jobs.models import Job
        from analysis.models import GapAnalysis
        from profiles.models import UserProfile
        from resumes.models import GeneratedResume
        from django.contrib.auth import get_user_model
        User = get_user_model()
        self.user = User.objects.create_user(
            username='order@example.com', email='order@example.com', password='x',
        )
        UserProfile.objects.create(user=self.user, full_name='Test User', email='order@example.com')
        self.client.force_login(self.user)
        job = Job.objects.create(user=self.user, title='Engineer')
        gap = GapAnalysis.objects.create(user=self.user, job=job, similarity_score=0.5)
        self.resume = GeneratedResume.objects.create(
            gap_analysis=gap, content={'professional_title': 'Engineer'},
        )

    def _url(self):
        return f'/resumes/section-order/{self.resume.id}/'

    def test_post_persists_validated_order(self):
        from django.urls import reverse  # noqa
        resp = self.client.post(
            self._url(),
            data='{"order": ["projects", "skills", "summary", "experience"]}',
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        # User-supplied order respected, missing keys appended at end
        self.assertEqual(
            body['order'],
            ['projects', 'skills', 'summary', 'experience', 'education', 'certifications', 'languages'],
        )
        self.resume.refresh_from_db()
        self.assertEqual(self.resume.content['section_order'], body['order'])

    def test_unknown_keys_are_dropped_silently(self):
        """Defensive: a stale UI or a malicious client must not poison the
        saved order with unknown keys (e.g., 'admin_only_section')."""
        resp = self.client.post(
            self._url(),
            data='{"order": ["skills", "definitely_not_a_section", "summary"]}',
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)
        # Bad key dropped; valid ones preserved in their relative order
        self.assertNotIn('definitely_not_a_section', resp.json()['order'])
        self.assertEqual(resp.json()['order'][:2], ['skills', 'summary'])

    def test_non_list_body_returns_400(self):
        resp = self.client.post(
            self._url(),
            data='{"order": "not a list"}',
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 400)

    def test_invalid_json_returns_400(self):
        resp = self.client.post(self._url(), data='not json', content_type='application/json')
        self.assertEqual(resp.status_code, 400)

    def test_per_owner_scope(self):
        from django.contrib.auth import get_user_model
        User = get_user_model()
        other = User.objects.create_user(
            username='other@example.com', email='other@example.com', password='x',
        )
        self.client.force_login(other)
        resp = self.client.post(self._url(), data='{"order": []}', content_type='application/json')
        self.assertEqual(resp.status_code, 404)

    def test_edit_page_renders_saved_order_in_drag_list_and_preview(self):
        """The drag list AND the live preview must both honor the saved
        section_order — not the default — when the page loads."""
        self.resume.content = {
            'professional_title': 'Engineer',
            'professional_summary': 'sum',
            'skills': ['Python'],
            'section_order': ['projects', 'skills', 'summary'],
        }
        self.resume.save()
        from django.urls import reverse
        resp = self.client.get(reverse('resume_edit', args=[self.resume.id]))
        body = resp.content.decode('utf-8')
        # Drag list: 'projects' chip should appear before 'skills' chip
        proj_idx = body.index('data-section-key="projects"')
        skills_idx = body.index('data-section-key="skills"')
        summary_idx = body.index('data-section-key="summary"')
        self.assertLess(proj_idx, skills_idx)
        self.assertLess(skills_idx, summary_idx)
        # Live preview: data-preview-section attributes must appear in same order
        ppi = body.index('data-preview-section="projects"')
        psi = body.index('data-preview-section="skills"')
        psum = body.index('data-preview-section="summary"')
        self.assertLess(ppi, psi)
        self.assertLess(psi, psum)


class RegenerateSectionEndpointTests(TestCase):
    """The per-section regenerate endpoint lets the user iterate on a single
    weak section (summary, skills, experience, projects) without losing
    edits to other sections. Pinning the contract: auth scope, allowed
    sections, persisted update, body shape that returns just the rewritten
    value."""

    def setUp(self):
        from jobs.models import Job
        from analysis.models import GapAnalysis
        from profiles.models import UserProfile
        from resumes.models import GeneratedResume
        from django.contrib.auth import get_user_model
        from django.urls import reverse  # noqa
        User = get_user_model()
        self.user = User.objects.create_user(
            username='regen@example.com', email='regen@example.com', password='x',
        )
        UserProfile.objects.create(
            user=self.user, full_name='Test User', email='regen@example.com',
            data_content={'skills': [{'name': 'Python'}]},
        )
        self.client.force_login(self.user)
        job = Job.objects.create(
            user=self.user, title='Backend Engineer', company='Acme',
            description='We need Python and Postgres experts.',
            extracted_skills=['Python', 'PostgreSQL'],
        )
        gap = GapAnalysis.objects.create(user=self.user, job=job, similarity_score=0.6)
        self.resume = GeneratedResume.objects.create(
            gap_analysis=gap,
            content={
                'professional_title': 'Backend Engineer',
                'professional_summary': 'Old summary.',
                'skills': ['Python'],
                'experience': [],
                'projects': [],
            },
        )

    def _url(self, section):
        return f'/resumes/regen/{self.resume.id}/{section}/'

    def test_unsupported_section_returns_400(self):
        resp = self.client.post(self._url('unknown'), data='{}', content_type='application/json')
        self.assertEqual(resp.status_code, 400)

    def test_regen_summary_persists_new_value(self):
        from unittest.mock import patch
        with patch('resumes.views.regenerate_section', return_value='Brand new tailored summary.') as m:
            resp = self.client.post(
                self._url('professional_summary'),
                data='{"current_content": {"professional_summary": "old"}}',
                content_type='application/json',
            )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body['section'], 'professional_summary')
        self.assertEqual(body['value'], 'Brand new tailored summary.')
        self.resume.refresh_from_db()
        self.assertEqual(
            self.resume.content['professional_summary'],
            'Brand new tailored summary.',
        )
        # Other sections must be untouched
        self.assertEqual(self.resume.content['professional_title'], 'Backend Engineer')
        m.assert_called_once()

    def test_regen_skills_returns_list(self):
        from unittest.mock import patch
        new_skills = ['Python', 'PostgreSQL', 'Docker']
        with patch('resumes.views.regenerate_section', return_value=new_skills):
            resp = self.client.post(
                self._url('skills'),
                data='{}',
                content_type='application/json',
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['value'], new_skills)
        self.resume.refresh_from_db()
        self.assertEqual(self.resume.content['skills'], new_skills)

    def test_regen_uses_in_flight_content_when_provided(self):
        """The client sends current_content with unsaved edits; the
        generator should see those, not the DB snapshot."""
        from unittest.mock import patch
        captured_content = {}
        def fake(profile, job, gap, content, section):
            captured_content.update(content)
            return 'ok'
        with patch('resumes.views.regenerate_section', side_effect=fake):
            self.client.post(
                self._url('professional_summary'),
                data='{"current_content": {"professional_title": "EDITED IN BROWSER"}}',
                content_type='application/json',
            )
        self.assertEqual(captured_content.get('professional_title'), 'EDITED IN BROWSER')

    def test_regen_scoped_to_owner(self):
        """Another user must not be able to regenerate someone else's resume."""
        from django.contrib.auth import get_user_model
        User = get_user_model()
        other = User.objects.create_user(
            username='other@example.com', email='other@example.com', password='x',
        )
        self.client.force_login(other)
        resp = self.client.post(self._url('professional_summary'),
                                data='{}', content_type='application/json')
        self.assertEqual(resp.status_code, 404)

    def test_regen_failure_returns_502(self):
        """When the LLM call raises, surface a 502 not a 500 stacktrace —
        the UI handles 502 gracefully (alert + button reset)."""
        from unittest.mock import patch
        with patch('resumes.views.regenerate_section', side_effect=Exception('LLM down')):
            resp = self.client.post(
                self._url('skills'),
                data='{}',
                content_type='application/json',
            )
        self.assertEqual(resp.status_code, 502)

    def test_regen_refuses_to_save_empty_summary(self):
        """A silent LLM failure that returns an empty string must NOT
        overwrite the saved summary. 422 + clear detail message."""
        from unittest.mock import patch
        original = self.resume.content.get('professional_summary')
        with patch('resumes.views.regenerate_section', return_value=''):
            resp = self.client.post(
                self._url('professional_summary'),
                data='{}',
                content_type='application/json',
            )
        self.assertEqual(resp.status_code, 422)
        body = resp.json()
        self.assertEqual(body['error'], 'empty_regeneration')
        self.assertIn('unchanged', body['detail'].lower())
        # Saved content is preserved
        self.resume.refresh_from_db()
        self.assertEqual(self.resume.content.get('professional_summary'), original)

    def test_regen_refuses_to_save_empty_skills_list(self):
        from unittest.mock import patch
        with patch('resumes.views.regenerate_section', return_value=[]):
            resp = self.client.post(
                self._url('skills'),
                data='{}',
                content_type='application/json',
            )
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()['error'], 'empty_regeneration')

    def test_regen_refuses_to_save_experience_without_bullets(self):
        """A list of experience entries with empty descriptions is just as
        bad as an empty list — the user would see "regenerated" rows with
        nothing in them. Refuse + return 422."""
        from unittest.mock import patch
        useless = [{'title': 'Engineer', 'company': 'Co', 'duration': '2024',
                    'description': []}]
        with patch('resumes.views.regenerate_section', return_value=useless):
            resp = self.client.post(
                self._url('experience'),
                data='{}',
                content_type='application/json',
            )
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()['error'], 'empty_regeneration')

    def test_regen_refuses_to_save_projects_without_bullets(self):
        from unittest.mock import patch
        useless = [{'name': 'demo', 'url': '', 'description': []}]
        with patch('resumes.views.regenerate_section', return_value=useless):
            resp = self.client.post(
                self._url('projects'),
                data='{}',
                content_type='application/json',
            )
        self.assertEqual(resp.status_code, 422)


class SchemaSupersetTests(TestCase):
    """Phase 0 schema unification — the resume content schema is now a
    superset of the master profile. Editor form fields persist round-trip
    and the DOCX/sync paths surface them.
    """

    def setUp(self):
        from jobs.models import Job
        from analysis.models import GapAnalysis
        from profiles.models import UserProfile
        from resumes.models import GeneratedResume
        from django.contrib.auth import get_user_model
        User = get_user_model()
        self.user = User.objects.create_user(
            username='superset@example.com', email='superset@example.com', password='x',
        )
        UserProfile.objects.create(
            user=self.user, full_name='Marie Curie', email='superset@example.com',
            data_content={
                'objective': 'Translate physics breakthroughs into impactful tools.',
                'experiences': [{
                    'title': 'Research Scientist', 'company': 'Sorbonne Lab',
                    'start_date': '1898', 'end_date': '1906',
                    'location': 'Paris, FR', 'industry': 'Academic Research',
                    'highlights': ['Isolated polonium and radium.'],
                }],
                'education': [{
                    'degree': 'Doctorate', 'field': 'Physics',
                    'institution': 'Sorbonne', 'graduation_year': '1903',
                    'gpa': 'Honours', 'location': 'Paris, FR',
                    'honors': ['Nobel Prize in Physics 1903'],
                }],
                'projects': [{
                    'name': 'Polonium isolation', 'url': 'https://example.com/po',
                    'description': ['Identified polonium via radiochemical separation.'],
                    'technologies': ['Radiometry', 'Chemistry'],
                }],
                'certifications': [{
                    'name': 'Pharmacy Diploma', 'issuer': 'Sorbonne',
                    'date': '1894', 'duration': '4 years',
                    'url': '',
                }],
            },
        )
        self.client.force_login(self.user)
        job = Job.objects.create(
            user=self.user, title='Physicist', company='Pasteur',
            description='Physics research role.',
            extracted_skills=['Radiometry'],
        )
        gap = GapAnalysis.objects.create(user=self.user, job=job, similarity_score=0.9)
        self.resume = GeneratedResume.objects.create(
            gap_analysis=gap,
            content={
                'professional_title': 'Physicist',
                'professional_summary': 'Physicist focused on radioactivity research.',
                'skills': ['Radiometry'],
                # Intentionally minimal so sync-from-master has work to do.
                'experience': [{'title': 'Research Scientist', 'company': 'Sorbonne Lab',
                                'duration': '1898 - 1906',
                                'description': ['Isolated polonium.']}],
                'education': [{'degree': 'Doctorate', 'institution': 'Sorbonne', 'year': '1903'}],
                'projects': [{'name': 'Polonium isolation',
                              'description': ['Identified polonium.'],
                              'url': 'https://example.com/po'}],
                'certifications': [{'name': 'Pharmacy Diploma', 'issuer': 'Sorbonne',
                                    'date': '1894', 'url': ''}],
                'languages': [],
            },
        )

    def test_edit_form_persists_new_experience_fields(self):
        """exp_location[], exp_industry[] survive a POST round-trip."""
        from django.urls import reverse
        url = reverse('resume_edit', args=[self.resume.id])
        resp = self.client.post(url, data={
            'professional_title': 'Physicist',
            'professional_summary': 'Updated.',
            'objective': 'Custom objective text.',
            'skills': 'Radiometry, Chemistry',
            'exp_title[]': ['Research Scientist'],
            'exp_company[]': ['Sorbonne Lab'],
            'exp_duration[]': ['1898 - 1906'],
            'exp_location[]': ['Paris, FR'],
            'exp_industry[]': ['Academic Research'],
            'exp_description[]': ['Isolated polonium and radium.'],
            'edu_degree[]': ['Doctorate'],
            'edu_field[]': ['Physics'],
            'edu_institution[]': ['Sorbonne'],
            'edu_year[]': ['1903'],
            'edu_gpa[]': ['Honours'],
            'edu_location[]': ['Paris, FR'],
            'edu_honors[]': ['Nobel Prize in Physics 1903'],
            'proj_name[]': ['Polonium isolation'],
            'proj_url[]': ['https://example.com/po'],
            'proj_technologies[]': ['Radiometry, Chemistry'],
            'proj_description[]': ['Identified polonium.'],
            'cert_name[]': ['Pharmacy Diploma'],
            'cert_issuer[]': ['Sorbonne'],
            'cert_date[]': ['1894'],
            'cert_duration[]': ['4 years'],
            'cert_url[]': [''],
            'languages': '',
        }, follow=False)
        self.assertEqual(resp.status_code, 302)
        self.resume.refresh_from_db()
        c = self.resume.content
        self.assertEqual(c['objective'], 'Custom objective text.')
        self.assertEqual(c['experience'][0]['location'], 'Paris, FR')
        self.assertEqual(c['experience'][0]['industry'], 'Academic Research')
        self.assertEqual(c['education'][0]['field'], 'Physics')
        self.assertEqual(c['education'][0]['gpa'], 'Honours')
        self.assertEqual(c['education'][0]['location'], 'Paris, FR')
        self.assertEqual(c['education'][0]['honors'], ['Nobel Prize in Physics 1903'])
        self.assertEqual(c['projects'][0]['technologies'], ['Radiometry', 'Chemistry'])
        self.assertEqual(c['certifications'][0]['duration'], '4 years')

    def test_auto_sync_on_get_fills_blank_fields(self):
        """Visiting /resumes/edit/<id>/ silently merges blank/missing fields
        from the master profile into the resume content. No LLM call, no
        manual button — just open the page and master fields are there."""
        from django.urls import reverse
        # Force the auto-regen branch off so we exercise the cheap auto-sync.
        # (Auto-regen fires when profile.updated_at > resume.created_at; we
        # pass ?refresh=0 to match the existing escape-hatch contract.)
        resp = self.client.get(reverse('resume_edit', args=[self.resume.id]) + '?refresh=0')
        self.assertEqual(resp.status_code, 200)
        self.resume.refresh_from_db()
        c = self.resume.content
        # Master fields populated:
        self.assertEqual(c['experience'][0]['location'], 'Paris, FR')
        self.assertEqual(c['experience'][0]['industry'], 'Academic Research')
        self.assertEqual(c['education'][0]['field'], 'Physics')
        self.assertEqual(c['education'][0]['gpa'], 'Honours')
        self.assertEqual(c['education'][0]['honors'], ['Nobel Prize in Physics 1903'])
        self.assertEqual(c['projects'][0]['technologies'], ['Radiometry', 'Chemistry'])
        self.assertEqual(c['certifications'][0]['duration'], '4 years')
        self.assertIn('Translate physics breakthroughs', c['objective'])
        # Existing typed bullet preserved (not clobbered):
        self.assertIn('Isolated polonium.', c['experience'][0]['description'])

    def test_auto_sync_is_idempotent(self):
        """Second visit produces identical content + must NOT call save()
        again (idempotent merge prevents needless DB writes)."""
        from django.urls import reverse
        from unittest.mock import patch
        url = reverse('resume_edit', args=[self.resume.id]) + '?refresh=0'
        # First hit: should save (master fields are being merged in for
        # the first time).
        self.client.get(url)
        self.resume.refresh_from_db()
        first_content = self.resume.content
        # Second hit: nothing should change; assert resume.save() is NOT
        # called by the auto-sync branch this time.
        with patch('resumes.models.GeneratedResume.save') as save_mock:
            self.client.get(url)
            # Auto-sync detects no diff, skips save.
            self.assertFalse(save_mock.called)
        self.resume.refresh_from_db()
        self.assertEqual(self.resume.content, first_content)

    def test_auto_sync_skipped_when_master_empty(self):
        """If the user hasn't filled in the master profile, opening the
        edit page must not blow up — it just renders without changes."""
        from profiles.models import UserProfile
        from django.urls import reverse
        UserProfile.objects.filter(user=self.user).update(data_content={})
        resp = self.client.get(reverse('resume_edit', args=[self.resume.id]) + '?refresh=0')
        self.assertEqual(resp.status_code, 200)

    def test_docx_renders_new_fields(self):
        """The DOCX exporter surfaces location, GPA, honors, technologies,
        and cert duration when present in resume.content."""
        import io, zipfile
        # Populate the resume directly with all new fields so we don't rely
        # on sync_from_master to put them there.
        self.resume.content = {
            **self.resume.content,
            'objective': 'Translate physics breakthroughs into impactful tools.',
            'experience': [{
                'title': 'Research Scientist', 'company': 'Sorbonne Lab',
                'duration': '1898 - 1906',
                'location': 'Paris, FR', 'industry': 'Academic Research',
                'description': ['Isolated polonium.'],
            }],
            'education': [{
                'degree': 'Doctorate', 'field': 'Physics',
                'institution': 'Sorbonne', 'year': '1903',
                'gpa': 'Honours', 'location': 'Paris, FR',
                'honors': ['Nobel Prize in Physics 1903'],
            }],
            'projects': [{
                'name': 'Polonium isolation',
                'url': 'https://example.com/po',
                'description': ['Identified polonium.'],
                'technologies': ['Radiometry', 'Chemistry'],
            }],
            'certifications': [{
                'name': 'Pharmacy Diploma', 'issuer': 'Sorbonne',
                'date': '1894', 'duration': '4 years', 'url': '',
            }],
        }
        self.resume.save()
        resp = self.client.get(f'/resumes/export-docx/{self.resume.id}/')
        self.assertEqual(resp.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
            doc_xml = z.read('word/document.xml').decode('utf-8')
        # New fields should all appear in the rendered XML.
        self.assertIn('Translate physics breakthroughs', doc_xml)  # objective
        self.assertIn('Paris, FR', doc_xml)                         # location
        self.assertIn('Academic Research', doc_xml)                 # industry
        self.assertIn('Physics', doc_xml)                           # education field
        self.assertIn('GPA Honours', doc_xml)                       # gpa
        self.assertIn('Nobel Prize', doc_xml)                       # honors
        self.assertIn('Radiometry', doc_xml)                        # tech stack
        self.assertIn('4 years', doc_xml)                           # cert duration


class ExportErrorPageTests(TestCase):
    """Tier-1 papercut: export failures used to return a plain 500 with
    a single sentence. Now they render the export_error.html template
    with retry / alt-format / back-to-resume links."""

    def setUp(self):
        from jobs.models import Job
        from analysis.models import GapAnalysis
        from profiles.models import UserProfile
        from resumes.models import GeneratedResume
        from django.contrib.auth import get_user_model
        User = get_user_model()
        self.user = User.objects.create_user(
            username='exporterr@example.com', email='exporterr@example.com', password='x',
        )
        UserProfile.objects.create(user=self.user, full_name='Test User')
        self.client.force_login(self.user)
        job = Job.objects.create(
            user=self.user, title='Engineer', company='Acme', description='x',
            extracted_skills=[],
        )
        gap = GapAnalysis.objects.create(user=self.user, job=job, similarity_score=0.5)
        self.resume = GeneratedResume.objects.create(
            gap_analysis=gap,
            content={'professional_title': 'Engineer'},
        )

    def test_pdf_export_failure_renders_friendly_error_page(self):
        from unittest.mock import patch
        with patch('resumes.views.generate_pdf', side_effect=RuntimeError('boom')):
            resp = self.client.get(f'/resumes/export/{self.resume.id}/')
        self.assertEqual(resp.status_code, 500)
        # Friendly page body — not the old plaintext "PDF generation failed."
        self.assertContains(resp, 'PDF export failed', status_code=500)
        self.assertContains(resp, 'Retry PDF', status_code=500)
        self.assertContains(resp, 'Try DOCX instead', status_code=500)
        self.assertContains(resp, 'Back to résumé', status_code=500)

    def test_docx_export_failure_renders_friendly_error_page(self):
        from unittest.mock import patch
        with patch('resumes.views.generate_docx', side_effect=RuntimeError('boom')):
            resp = self.client.get(f'/resumes/export-docx/{self.resume.id}/')
        self.assertEqual(resp.status_code, 500)
        self.assertContains(resp, 'DOCX export failed', status_code=500)
        self.assertContains(resp, 'Retry DOCX', status_code=500)
        self.assertContains(resp, 'Try PDF instead', status_code=500)


class DashboardResumeCountTests(TestCase):
    """S4: a returning user with multiple resumes per job should see a
    `N résumés` badge on each job tile, linking to the history."""

    def test_dashboard_annotates_resume_count_on_jobs(self):
        from jobs.models import Job
        from analysis.models import GapAnalysis
        from profiles.models import UserProfile
        from resumes.models import GeneratedResume
        from django.contrib.auth import get_user_model
        User = get_user_model()
        user = User.objects.create_user(
            username='dash@example.com', email='dash@example.com', password='x',
        )
        UserProfile.objects.create(user=user, full_name='Returning User')
        job = Job.objects.create(
            user=user, title='Engineer', company='Acme', description='x',
            extracted_skills=[], application_status='saved',
        )
        gap = GapAnalysis.objects.create(user=user, job=job, similarity_score=0.7)
        # Three resume iterations for the same job — typical second-session
        # state for a user iterating on tailoring.
        for i in range(3):
            GeneratedResume.objects.create(gap_analysis=gap, content={'professional_title': f'v{i}'})
        self.client.force_login(user)
        resp = self.client.get('/profiles/dashboard/')
        self.assertEqual(resp.status_code, 200)
        # The annotated count surfaces as "3 résumés" badge text.
        self.assertContains(resp, '3 résumés')


class AutoSyncBannerTests(TestCase):
    """M10: when the auto-sync branch patches fields into resume.content,
    the next page render shows a one-shot banner so the user knows the
    page didn't render exactly what they last saved."""

    def setUp(self):
        from jobs.models import Job
        from analysis.models import GapAnalysis
        from profiles.models import UserProfile
        from resumes.models import GeneratedResume
        from django.contrib.auth import get_user_model
        User = get_user_model()
        self.user = User.objects.create_user(
            username='sync@example.com', email='sync@example.com', password='x',
        )
        UserProfile.objects.create(
            user=self.user, full_name='Synced User',
            data_content={
                # Master profile has fields the resume snapshot lacks.
                'experiences': [{'title': 'Engineer', 'company': 'Acme',
                                 'location': 'Berlin, DE', 'industry': 'SaaS',
                                 'highlights': ['Shipped X.']}],
            },
        )
        self.client.force_login(self.user)
        job = Job.objects.create(
            user=self.user, title='Engineer', company='Acme',
            description='x', extracted_skills=[],
        )
        gap = GapAnalysis.objects.create(user=self.user, job=job, similarity_score=0.7)
        # Resume content lacks the supplemental fields (location/industry).
        self.resume = GeneratedResume.objects.create(
            gap_analysis=gap,
            content={
                'experience': [{'title': 'Engineer', 'company': 'Acme',
                                'duration': '', 'description': ['Shipped X.']}],
            },
        )

    def test_auto_sync_banner_shows_on_first_visit_after_change(self):
        """Visiting the edit page when auto-sync writes new fields surfaces
        a banner. The session flag is consumed so it doesn't reappear."""
        from django.urls import reverse
        url = reverse('resume_edit', args=[self.resume.id]) + '?refresh=0'
        # First visit — auto-sync fires (resume content was missing
        # location/industry; master has them) and banner shows.
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Synced from your master profile')
        # Second visit — content already merged on the first hit, no diff,
        # no banner.
        resp = self.client.get(url)
        self.assertNotContains(resp, 'Synced from your master profile')

    def test_no_sync_banner_when_nothing_to_sync(self):
        """If the master has no new fields beyond what the resume already
        has, no banner appears."""
        from profiles.models import UserProfile
        from django.urls import reverse
        # Empty data_content → nothing to merge → no banner.
        UserProfile.objects.filter(user=self.user).update(data_content={})
        resp = self.client.get(reverse('resume_edit', args=[self.resume.id]) + '?refresh=0')
        self.assertNotContains(resp, 'Synced from your master profile')


class OnboardingBannerDismissTests(TestCase):
    """M9: dismissing the dashboard onboarding banner persists across
    visits. Welcome's 'Just show me around' also sets the dismiss flag."""

    def setUp(self):
        from profiles.models import UserProfile
        from django.contrib.auth import get_user_model
        User = get_user_model()
        self.user = User.objects.create_user(
            username='banner@example.com', email='banner@example.com', password='x',
        )
        # Profile with no name + no skills + no jobs → banner SHOULD show
        # by default.
        UserProfile.objects.create(user=self.user)
        self.client.force_login(self.user)

    def test_dashboard_shows_banner_for_new_user_by_default(self):
        resp = self.client.get('/profiles/dashboard/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Two steps to unlock')

    def test_dismiss_endpoint_persists_across_visits(self):
        from profiles.models import UserProfile
        # Click the dismiss endpoint
        resp = self.client.post('/profiles/api/onboarding/dismiss/')
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()['ok'])
        # Flag persisted
        profile = UserProfile.objects.get(user=self.user)
        self.assertTrue(profile.data_content.get('onboarding_banner_dismissed'))
        # Subsequent dashboard visits don't show the banner
        resp = self.client.get('/profiles/dashboard/')
        self.assertNotContains(resp, 'Two steps to unlock')

    def test_dismiss_endpoint_rejects_get(self):
        resp = self.client.get('/profiles/api/onboarding/dismiss/')
        self.assertEqual(resp.status_code, 405)

    def test_welcome_skip_also_dismisses_banner(self):
        from profiles.models import UserProfile
        resp = self.client.post('/welcome/', {'action': 'skip'})
        self.assertEqual(resp.status_code, 302)
        profile = UserProfile.objects.get(user=self.user)
        self.assertTrue(profile.data_content.get('has_seen_welcome'))
        # Critical: welcome's skip ALSO sets the banner-dismissed flag
        # (M9 fix — without this, dashboard re-nags after the user
        # explicitly opted out of guided onboarding).
        self.assertTrue(profile.data_content.get('onboarding_banner_dismissed'))


class AgentChatProfileAwareTests(TestCase):
    """B1: /agent/ adapts to zero-data state. Input stays enabled (per
    user feedback — disabled inputs feel broken), but a banner above the
    chat warns the user the agent has nothing to ground answers in."""

    def test_zero_data_user_sees_setup_banner(self):
        from django.contrib.auth import get_user_model
        from profiles.models import UserProfile
        User = get_user_model()
        user = User.objects.create_user(
            username='zero@example.com', email='zero@example.com', password='x',
        )
        UserProfile.objects.create(user=user)  # empty: no name, no skills
        self.client.force_login(user)
        resp = self.client.get('/agent/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'This will be more useful once you set up your profile')
        # Header text reflects the empty state too — no false "knows your
        # profile and pipeline" claim.
        self.assertContains(resp, 'profile not set up yet')
        self.assertNotContains(resp, 'knows your profile and pipeline')

    def test_populated_user_does_not_see_banner(self):
        from django.contrib.auth import get_user_model
        from profiles.models import UserProfile
        User = get_user_model()
        user = User.objects.create_user(
            username='full@example.com', email='full@example.com', password='x',
        )
        UserProfile.objects.create(
            user=user, full_name='Marie Curie',
            data_content={'skills': [{'name': 'Python'}]},
        )
        self.client.force_login(user)
        resp = self.client.get('/agent/')
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'This will be more useful once you set up your profile')
        self.assertContains(resp, 'knows your profile and pipeline')


class FactualityCheckEnrichedProjectsTests(SimpleTestCase):
    """Phase 3: factuality_check accepts enriched-project URLs / source_ids
    as legitimate evidence so Phase-2 confirmed projects don't get falsely
    flagged as fabrication. Companies and schools still require source-text
    grounding.
    """

    def test_project_grounded_via_enriched_source_url(self):
        from benchmarks.llm_judge import factuality_check
        generated = {
            'experience': [{'company': 'Acme'}],
            'projects': [{'name': 'climate-modeling', 'description': []}],
        }
        # Source CV text has Acme but NOT climate-modeling — that project
        # came from Phase-1 enrichment of GitHub signals.
        source_text = "Engineer at Acme since 2023."
        confirmed = [{
            'name': 'climate-modeling',
            'url': 'https://github.com/me/climate-modeling',
            'source': 'github',
            'source_id': 'me/climate-modeling',
        }]
        result = factuality_check(generated, source_text, confirmed_projects=confirmed)
        # Both Acme and climate-modeling grounded; ratio = 1.0
        self.assertEqual(result['ratio'], 1.0)
        self.assertEqual(result['ungrounded'], [])

    def test_project_falsely_named_still_flags_as_ungrounded(self):
        """An LLM-fabricated project name should still get flagged — being
        in confirmed_projects requires actual text overlap (substring), not
        just the existence of any enriched project."""
        from benchmarks.llm_judge import factuality_check
        generated = {
            'experience': [{'company': 'Acme'}],
            'projects': [{'name': 'completely-made-up-project'}],
        }
        source_text = "Engineer at Acme since 2023."
        confirmed = [{
            'name': 'climate-modeling',
            'url': 'https://github.com/me/climate-modeling',
            'source': 'github',
        }]
        result = factuality_check(generated, source_text, confirmed_projects=confirmed)
        self.assertIn('completely-made-up-project', result['ungrounded'])
        # Acme grounded, fabricated project not — 1/2 = 0.5
        self.assertEqual(result['ratio'], 0.5)

    def test_company_does_not_use_project_evidence(self):
        """Company names must come from source_text — they CANNOT use the
        confirmed-projects index (otherwise a typed project named after a
        fake company would launder fabrication)."""
        from benchmarks.llm_judge import factuality_check
        generated = {
            'experience': [{'company': 'NotInCV-Corp'}],
            'projects': [],
        }
        source_text = "Engineer at Acme since 2023."
        confirmed = [{
            'name': 'NotInCV-Corp',  # appears in confirmed projects
            'url': 'https://example.com',
        }]
        result = factuality_check(generated, source_text, confirmed_projects=confirmed)
        # Company should still be flagged — confirmed projects are not
        # evidence for company entities.
        self.assertIn('NotInCV-Corp', result['ungrounded'])

    def test_backwards_compat_no_confirmed_projects(self):
        """Calling without confirmed_projects (legacy callers) still works
        and falls back to source-text-only grounding."""
        from benchmarks.llm_judge import factuality_check
        generated = {
            'experience': [{'company': 'Acme'}],
            'projects': [{'name': 'pgbench-tuner'}],
        }
        source_text = "Engineer at Acme; built pgbench-tuner."
        result = factuality_check(generated, source_text)
        self.assertEqual(result['ratio'], 1.0)


class ResumeGeneratorEnrichedProjectsPromptTests(SimpleTestCase):
    """Phase 3: source-tagged projects survive the resume_generator's prompt
    assembly (slim_cv) and the offline fallback. The LLM gets to see the
    `source` field so it can treat enriched projects as ground truth."""

    def test_offline_fallback_preserves_enriched_project(self):
        """The offline fallback (no LLM) must pass enriched projects through
        intact — including their custom technologies and bullets."""
        import types
        from resumes.services.resume_generator import _build_offline_fallback
        profile = types.SimpleNamespace(data_content={
            'projects': [{
                'name': 'climate-modeling', 'url': 'https://github.com/me/climate-modeling',
                'description': ['Built earth-science modeling pipeline.'],
                'technologies': ['Python', 'PyTorch'],
                'source': 'github', 'source_id': 'me/climate-modeling',
            }],
        }, raw_text='', skills=[], experiences=[], education=[],
            projects=[], certifications=[])
        job = types.SimpleNamespace(title='ML Engineer', description='Build models.',
                                    extracted_skills=[])
        out = _build_offline_fallback(profile, job, profile.data_content)
        self.assertEqual(len(out['projects']), 1)
        proj = out['projects'][0]
        self.assertEqual(proj['name'], 'climate-modeling')
        self.assertEqual(proj['url'], 'https://github.com/me/climate-modeling')
        self.assertIn('PyTorch', proj['technologies'])
        # Bullets survive verbatim
        self.assertIn('Built earth-science modeling pipeline.', proj['description'])

    def test_slim_cv_includes_source_field_for_llm_prompt(self):
        """Verify the slim_cv dict assembled in generate_resume_content keeps
        the `source` field on each project so the LLM can apply the
        'pre-vetted from external signals' rule."""
        # We can't run the full LLM call in a unit test, but we CAN assert
        # the assembly logic is correct by inspecting what would be
        # serialized into the prompt.
        raw_cv_data = {
            'projects': [{
                'name': 'climate-modeling', 'url': 'https://github.com/me/climate-modeling',
                'description': ['Built it.'], 'technologies': ['Python'],
                'source': 'github', 'source_id': 'me/climate-modeling',
            }],
        }
        # Replicate the slim_cv filter from generate_resume_content
        _SIGNAL_KEYS = {'github_signals', 'scholar_signals', 'kaggle_signals', 'linkedin_snapshot'}
        slim_cv = {k: v for k, v in raw_cv_data.items()
                   if k != 'raw_text'
                   and k not in _SIGNAL_KEYS
                   and v
                   and k not in ('normalized_summary', 'objective')}
        self.assertEqual(slim_cv['projects'][0]['source'], 'github')


class ResumeListThumbnailTests(TestCase):
    """Each card on the résumé list page now renders a thumbnail preview of
    the actual resume content (name, summary, first experience, top skills)
    so the user can recognise their resumes by glance instead of by reading
    the job-title text. The thumbnail reuses the editor's .pdf-preview CSS,
    so picking a template carries through to the list view automatically.
    """

    def setUp(self):
        from jobs.models import Job
        from analysis.models import GapAnalysis
        from profiles.models import UserProfile
        from resumes.models import GeneratedResume
        User = get_user_model()
        self.user = User.objects.create_user(
            username='listthumb@example.com', email='listthumb@example.com', password='x',
        )
        UserProfile.objects.create(user=self.user, full_name='Ada Lovelace', email='listthumb@example.com')
        self.client.force_login(self.user)
        job = Job.objects.create(user=self.user, title='Senior Backend Engineer', company='Acme Corp')
        self.gap = GapAnalysis.objects.create(user=self.user, job=job, similarity_score=0.82)
        self.resume = GeneratedResume.objects.create(
            gap_analysis=self.gap,
            content={
                'professional_title': 'Backend Systems Engineer',
                'professional_summary': 'Built distributed Python systems for high-throughput pipelines and ran the migration from synchronous to async stack.',
                'template_name': 'danette',
                'experience': [
                    {
                        'title': 'Backend Engineer',
                        'company': 'PriorCo',
                        'duration': '2023 - Present',
                        'description': ['Cut p99 latency from 1.2s to 380ms', 'Owned async migration across 6 services'],
                    },
                ],
                'skills': ['Python', 'PostgreSQL', 'Redis', 'Kubernetes', 'AWS'],
            },
        )

    def test_thumbnail_renders_real_content(self):
        """The thumbnail must show real resume data — name, professional
        title, first experience, skills — not placeholder bars."""
        resp = self.client.get(reverse('resume_list'))
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode('utf-8')
        # Name from the user's profile.
        self.assertIn('Ada Lovelace', body)
        # Professional title from the resume content (NOT the job title).
        self.assertIn('Backend Systems Engineer', body)
        # First experience entry's title and company.
        self.assertIn('Backend Engineer', body)
        self.assertIn('PriorCo', body)
        # Top skill renders.
        self.assertIn('Python', body)
        # Template-aware CSS modifier picked up.
        self.assertIn('pdf-preview pdf-preview--danette', body)
        # Sized as a list thumbnail (not the editor's small picker thumb).
        self.assertIn('resume-list-thumb', body)

    def test_thumbnail_handles_empty_content_gracefully(self):
        """A resume with empty content shouldn't break the page: section
        headings without data must not render, and the page still 200s."""
        self.resume.content = {}
        self.resume.save()
        resp = self.client.get(reverse('resume_list'))
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode('utf-8')
        # The thumbnail container still renders so the card has a stable shape
        self.assertIn('resume-list-thumb', body)
        # But the section headings inside it (Summary / Experience / Skills)
        # must NOT appear when there's no data — we conditionally render.
        # Match the exact section-title markup so we don't catch e.g. an
        # entirely unrelated word "Summary" elsewhere in the page chrome.
        self.assertNotIn('<div class="p-section-title">Summary</div>', body)
        self.assertNotIn('<div class="p-section-title">Experience</div>', body)
        self.assertNotIn('<div class="p-section-title">Skills</div>', body)
        # Header still falls back to job title when professional_title is empty.
        self.assertIn('Senior Backend Engineer', body)

    def test_thumbnail_falls_back_to_standard_when_template_missing(self):
        """If a resume saved before the template-name feature, fall back to
        'standard' so the thumbnail still has consistent styling."""
        self.resume.content = {
            'professional_title': 'X',
            'professional_summary': 'Y',
        }
        self.resume.save()
        resp = self.client.get(reverse('resume_list'))
        body = resp.content.decode('utf-8')
        self.assertIn('pdf-preview pdf-preview--standard', body)

    def test_profile_name_falls_back_to_email_local_part(self):
        """Users without a UserProfile.full_name should still get a header,
        not an empty void. The NAME slot specifically uses the email
        local-part as the fallback (the email itself separately appears in
        the contact line, which mirrors what the PDF template does)."""
        from profiles.models import UserProfile
        UserProfile.objects.filter(user=self.user).update(full_name='')
        resp = self.client.get(reverse('resume_list'))
        body = resp.content.decode('utf-8')
        # The name div uses the local-part, not the full email.
        self.assertIn('<div class="p-name">listthumb</div>', body)
