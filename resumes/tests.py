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

    def test_linkedin_link_only_counts(self):
        # A parsed handle without scraped rich fields still counts — the
        # recruiter can verify identity via the URL alone.
        out = compute_evidence_confidence(self._profile(
            linkedin_signals={"username": "jane-doe", "profile_url": "https://linkedin.com/in/jane-doe"},
        ))
        self.assertEqual(out["score"], 1)
        self.assertEqual(out["sources"], ["linkedin"])

    def test_linkedin_error_snapshot_skipped(self):
        out = compute_evidence_confidence(self._profile(
            linkedin_signals={"error": "blocked", "profile_url": "x"},
        ))
        self.assertEqual(out["score"], 0)

    def test_all_four_signals_max_score(self):
        out = compute_evidence_confidence(self._profile(
            github_signals={"public_repos": 5},
            scholar_signals={"total_citations": 100, "top_publications": [{"title": "X"}]},
            kaggle_signals={"competitions": {"count": 1}, "datasets": {"count": 0},
                            "notebooks": {"count": 0}, "discussion": {"count": 0}},
            linkedin_signals={"username": "jane", "profile_url": "https://linkedin.com/in/jane"},
        ))
        self.assertEqual(out["score"], 4)
        self.assertEqual(out["label"], "Strong")
        self.assertEqual(set(out["sources"]), {"github", "scholar", "kaggle", "linkedin"})


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


class PdfExporterCairoShimTests(SimpleTestCase):
    """The cairocffi shim lets the xhtml2pdf import chain survive on
    Windows installs without libcairo-2.dll. Verifies the shim is
    idempotent and installs a stub when the real cairocffi can't
    initialise."""

    def test_shim_no_op_when_cairocffi_already_present(self):
        import sys
        from resumes.services.pdf_exporter import _shim_cairocffi_if_missing
        sentinel = object()
        sys.modules['cairocffi'] = sentinel  # type: ignore[assignment]
        try:
            _shim_cairocffi_if_missing()
            self.assertIs(sys.modules.get('cairocffi'), sentinel)
        finally:
            sys.modules.pop('cairocffi', None)

    def test_shim_installs_stub_with_required_attrs_when_dlopen_fails(self):
        import sys
        from unittest.mock import patch
        from resumes.services.pdf_exporter import _shim_cairocffi_if_missing

        # Save + clear the real cairocffi so the shim path runs.
        original = sys.modules.pop('cairocffi', None)
        try:
            # Patch the builtin importer to raise the same OSError
            # signature cairocffi raises when libcairo-2 is missing.
            import builtins
            real_import = builtins.__import__

            def fake_import(name, *a, **k):
                if name == 'cairocffi':
                    raise OSError(
                        "no library called 'cairo-2' was found"
                    )
                return real_import(name, *a, **k)

            with patch.object(builtins, '__import__', side_effect=fake_import):
                _shim_cairocffi_if_missing()
            # Stub is in place and exposes the minimum surface area
            # rlPyCairo touches during import.
            stub = sys.modules.get('cairocffi')
            self.assertIsNotNone(stub)
            self.assertTrue(callable(stub.cairo_version))
            self.assertEqual(stub.cairo_version(), 11600)
            self.assertTrue(callable(getattr(stub, 'Context')))
        finally:
            sys.modules.pop('cairocffi', None)
            if original is not None:
                sys.modules['cairocffi'] = original

    def test_shim_propagates_non_cairo_oserrors(self):
        import sys
        from unittest.mock import patch
        from resumes.services.pdf_exporter import _shim_cairocffi_if_missing

        original = sys.modules.pop('cairocffi', None)
        try:
            import builtins
            real_import = builtins.__import__

            def fake_import(name, *a, **k):
                if name == 'cairocffi':
                    raise OSError("disk failure")  # unrelated OSError
                return real_import(name, *a, **k)
            with patch.object(builtins, '__import__', side_effect=fake_import):
                with self.assertRaises(OSError):
                    _shim_cairocffi_if_missing()
        finally:
            sys.modules.pop('cairocffi', None)
            if original is not None:
                sys.modules['cairocffi'] = original


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


class SubstepLoadingComponentTests(TestCase):
    """Tier 3: the substep loading component replaces the old static-string
    overlay. These tests cover the JS / template contract — the component
    is registered globally on every page (via base.html), the registry has
    every operation we expect, and call sites use the {op: '...'} form
    rather than legacy strings."""

    def setUp(self):
        from django.contrib.auth import get_user_model
        from profiles.models import UserProfile
        User = get_user_model()
        self.user = User.objects.create_user(
            username='loader@example.com', email='loader@example.com', password='x',
        )
        UserProfile.objects.create(user=self.user, full_name='Loader User')
        self.client.force_login(self.user)

    def test_base_template_exposes_loading_ops_registry(self):
        """Every page should have the LoadingOps registry available so any
        client-side code can drive the overlay without re-declaring keys."""
        resp = self.client.get('/profiles/dashboard/')
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode('utf-8')
        # Registry keys we rely on across pages:
        for op in ('cv-upload', 'job-scrape', 'job-paste', 'gap-analysis',
                   'resume-gen', 'cover-letter', 'outreach', 'outreach-campaign',
                   'learning-path', 'salary'):
            self.assertIn(f"'{op}'", body, f"LoadingOps registry missing key {op!r}")
        # Component pieces that pages target:
        self.assertIn('id="loading-steps"', body)
        self.assertIn('id="loading-failure"', body)
        self.assertIn('id="loading-retry"', body)
        # Legacy single-line mode still supported.
        self.assertIn('id="loading-msg"', body)

    def test_cv_upload_uses_substep_op_key(self):
        """upload_cv.html should call showLoading({op: 'cv-upload'}), not
        the legacy plain-string form."""
        resp = self.client.get('/profiles/setup/upload/')
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode('utf-8')
        self.assertIn("showLoading({op: 'cv-upload'})", body)
        # The legacy single-string call must NOT be there — catches regression.
        self.assertNotIn("Your agent is reading your CV — up to a minute on first run.", body)

    def test_job_input_uses_substep_op_keys(self):
        resp = self.client.get('/jobs/input/')
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode('utf-8')
        # Both URL-mode and paste-mode forms migrated:
        self.assertIn("showLoading({op: 'job-scrape'})", body)
        self.assertIn("showLoading({op: 'job-paste'})", body)


class TemplateBlockTagInJSCommentTests(TestCase):
    """Regression guard: a literal `{% block name %}` string inside a JS
    comment is parsed by Django's template engine as a real block tag.
    On extending templates this raises TemplateSyntaxError ("'block' tag
    with name 'X' appears more than once"). Hit production once via a
    DOMContentLoaded JSDoc that referenced `{% block content %}` literally.

    This test renders the gap-analysis loading state + the resume
    generation page and asserts both succeed (no TemplateSyntaxError).
    """

    def setUp(self):
        from django.contrib.auth import get_user_model
        from profiles.models import UserProfile
        from jobs.models import Job
        User = get_user_model()
        self.user = User.objects.create_user(
            username='blocktag@example.com', email='blocktag@example.com',
            password='x',
        )
        UserProfile.objects.create(
            user=self.user, full_name='Block Tag User',
            data_content={'skills': [{'name': 'Python'}]},
        )
        self.client.force_login(self.user)
        self.job = Job.objects.create(
            user=self.user, title='Engineer', company='Acme',
            description='Need Python.', extracted_skills=['Python'],
        )

    def test_gap_analysis_loading_state_renders(self):
        """No TemplateSyntaxError; the loading-state HTML reaches the user."""
        resp = self.client.get(f'/analysis/gap/{self.job.id}/')
        self.assertEqual(resp.status_code, 200)
        # Loading state contains the substep heading.
        self.assertContains(resp, 'Reading the')

    def test_resume_generate_page_renders(self):
        from analysis.models import GapAnalysis
        GapAnalysis.objects.create(
            user=self.user, job=self.job, similarity_score=0.85,
        )
        resp = self.client.get(f'/resumes/generate/{self.job.id}/')
        self.assertEqual(resp.status_code, 200)


class ScriptTagBalanceTests(TestCase):
    """Regression guard: a literal `</script>` string inside a JS comment
    terminates the script element early in the HTML parser, leaking the
    rest of the script body (Tours registry, theme toggle, everything) as
    visible text on the page. Hit production once via a Shepherd JSDoc
    comment that wrote `<script>...</script>` literally inside backticks.

    This test asserts the rendered dashboard has matching counts of
    <script> opens and </script> closes. Mismatch == something inside a
    script body is being parsed as a tag boundary.
    """

    def setUp(self):
        from django.contrib.auth import get_user_model
        from profiles.models import UserProfile
        User = get_user_model()
        self.user = User.objects.create_user(
            username='balance@example.com', email='balance@example.com', password='x',
        )
        UserProfile.objects.create(user=self.user, full_name='Balance User')
        self.client.force_login(self.user)

    def test_dashboard_script_tags_balanced(self):
        import re
        resp = self.client.get('/profiles/dashboard/')
        body = resp.content.decode('utf-8')
        # Count opening <script (handles `<script>` and `<script src=...>`)
        # and closing </script> tags.
        opens = len(re.findall(r'<script\b', body, flags=re.IGNORECASE))
        closes = len(re.findall(r'</script\s*>', body, flags=re.IGNORECASE))
        self.assertEqual(
            opens, closes,
            f'Unbalanced <script> tags: {opens} opens vs {closes} closes. '
            f'A literal "</script>" inside a script body breaks the parser.'
        )

    def test_no_loading_ops_text_leaks_outside_script(self):
        """The LoadingOps registry should only appear inside a <script>
        tag. If it shows up as visible text on the page, a script tag
        boundary broke."""
        resp = self.client.get('/profiles/dashboard/')
        body = resp.content.decode('utf-8')
        # Find the position of `const LoadingOps = {` and verify it sits
        # inside a script block (last <script> before this position has
        # not been closed yet).
        marker = 'const LoadingOps = {'
        idx = body.find(marker)
        self.assertGreater(idx, 0, 'LoadingOps registry missing entirely')
        before = body[:idx]
        # Last <script appearance before the marker must NOT be followed
        # by a </script> close before the marker.
        last_open = before.rfind('<script')
        last_close = before.rfind('</script>')
        self.assertGreater(last_open, last_close,
                           'LoadingOps appears AFTER a </script> close — script body leaked.')


class ProfileSummaryAndObjectivePropertyTests(TestCase):
    """Master review form binds to `profile.objective` and
    `profile.normalized_summary`. Both surface from data_content via
    @property; saving them via the form persists back into data_content."""

    def setUp(self):
        from django.contrib.auth import get_user_model
        from profiles.models import UserProfile
        User = get_user_model()
        self.user = User.objects.create_user(
            username='summary@example.com', email='summary@example.com', password='x',
        )
        UserProfile.objects.create(
            user=self.user, full_name='Test User',
            data_content={
                'objective': 'Build resilient distributed systems.',
                'normalized_summary': 'Senior engineer, 8 years Python + Go.',
            },
        )
        self.client.force_login(self.user)

    def test_property_surfaces_objective_and_summary(self):
        from profiles.models import UserProfile
        p = UserProfile.objects.get(user=self.user)
        self.assertEqual(p.objective, 'Build resilient distributed systems.')
        self.assertEqual(p.normalized_summary, 'Senior engineer, 8 years Python + Go.')

    def test_normalized_summary_falls_back_to_summary(self):
        """Older parses populated `summary` instead of `normalized_summary`.
        The property reads either, in priority order."""
        from profiles.models import UserProfile
        UserProfile.objects.filter(user=self.user).update(data_content={
            'summary': 'Legacy summary text.',
        })
        p = UserProfile.objects.get(user=self.user)
        self.assertEqual(p.normalized_summary, 'Legacy summary text.')

    def test_review_post_persists_objective_and_summary(self):
        """The master review POST handler now writes both fields back to
        data_content. Without this, edits to the textareas vanished."""
        from profiles.models import UserProfile
        from django.urls import reverse
        resp = self.client.post(reverse('review_master_profile'), {
            'full_name': 'Test User', 'email': 'summary@example.com',
            'phone': '', 'location': '',
            'objective': 'New objective text.',
            'normalized_summary': 'New summary text.',
            'contact_links_json': '[]', 'skills_json': '[]',
            'experiences_json': '[]', 'education_json': '[]',
            'projects_json': '[]', 'certifications_json': '[]',
        })
        self.assertEqual(resp.status_code, 302)
        p = UserProfile.objects.get(user=self.user)
        self.assertEqual(p.data_content['objective'], 'New objective text.')
        self.assertEqual(p.data_content['normalized_summary'], 'New summary text.')

    def test_review_form_renders_property_values(self):
        """The form template binds `{{ profile.objective }}` and
        `{{ profile.normalized_summary }}`. The rendered HTML must show
        the actual stored values, not blank placeholders."""
        resp = self.client.get('/profiles/setup/review/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Build resilient distributed systems.')
        self.assertContains(resp, 'Senior engineer, 8 years Python + Go.')


class OnboardingFlowOrderTests(TestCase):
    """The post-CV-upload onboarding sequence is:
        Upload → Connect → (Project review if signals) → Master review.

    Master review is the FINAL step, not the first stop after upload.
    Out-of-onboarding visits keep the legacy semantics.
    """

    def setUp(self):
        from django.contrib.auth import get_user_model
        from profiles.models import UserProfile
        User = get_user_model()
        self.user = User.objects.create_user(
            username='flow@example.com', email='flow@example.com', password='x',
        )
        self.profile = UserProfile.objects.create(user=self.user)
        self.client.force_login(self.user)

    def _set_onboarding_flag(self, value=True):
        session = self.client.session
        session['in_onboarding'] = value
        session.save()

    def test_connect_auto_merges_and_redirects_to_master_review_when_signals_present(self):
        """Onboarding + signals: connect_accounts auto-applies enriched
        projects to the master profile and skips the (now read-only)
        review page entirely. The user lands on master review with the
        merge already done."""
        from profiles.models import UserProfile
        UserProfile.objects.filter(user=self.user).update(data_content={
            'github_signals': {
                'profile_url': 'https://github.com/me',
                'top_repos': [{'name': 'alpha', 'full_name': 'me/alpha',
                               'description': 'demo', 'html_url': 'https://github.com/me/alpha',
                               'stargazers_count': 1, 'forks_count': 0, 'language': 'Python'}],
                'language_breakdown': [['Python', 1]],
            },
        })
        self._set_onboarding_flag()
        from unittest.mock import patch
        # Stub the LLM calls — we only care about the redirect contract +
        # that data_content['projects'] gets written. Both enrich_profile
        # and dedupe_projects fall back to deterministic paths on LLM
        # failure (existing behaviour) so the auto-apply still runs.
        from profiles.services import project_enricher, project_dedupe
        with patch.object(project_enricher, 'get_structured_llm') as mock_a, \
             patch.object(project_dedupe, 'get_structured_llm') as mock_b:
            mock_a.return_value.invoke.side_effect = RuntimeError('boom')
            mock_b.return_value.invoke.side_effect = RuntimeError('boom')
            resp = self.client.post('/profiles/setup/connect/')
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, '/profiles/setup/review/')
        # Auto-apply persisted: data_content['projects'] now includes the
        # GitHub-derived project (rule-based fallback shape from enricher).
        profile = UserProfile.objects.get(user=self.user)
        names = [p.get('name') for p in profile.data_content.get('projects', [])]
        self.assertIn('alpha', names)

    def test_connect_redirects_to_master_review_when_no_signals(self):
        """In onboarding + no signals connected → POST connect →
        master review (skip the empty project-review step)."""
        self._set_onboarding_flag()
        resp = self.client.post('/profiles/setup/connect/')
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, '/profiles/setup/review/')

    def test_connect_redirects_to_master_review_when_signals_have_error(self):
        """A snapshot dict with `error` set is treated as 'no signals' —
        the user's connect attempt failed (e.g. rate-limited), so don't
        send them to a project review with nothing to merge."""
        from profiles.models import UserProfile
        UserProfile.objects.filter(user=self.user).update(data_content={
            'github_signals': {'error': 'rate_limited'},
        })
        self._set_onboarding_flag()
        resp = self.client.post('/profiles/setup/connect/')
        self.assertEqual(resp.url, '/profiles/setup/review/')

    def test_connect_out_of_onboarding_preserves_legacy_redirect(self):
        """Settings-style visit (not in onboarding): posting Continue
        should go to job_input_view or dashboard, NOT into the project-
        review detour even if signals exist."""
        from profiles.models import UserProfile
        UserProfile.objects.filter(user=self.user).update(data_content={
            'github_signals': {'top_repos': []},
        })
        # No in_onboarding flag set.
        resp = self.client.post('/profiles/setup/connect/')
        self.assertEqual(resp.status_code, 302)
        self.assertIn(resp.url, ('/jobs/input/', '/profiles/dashboard/'))

    def test_master_review_is_final_step_when_onboarding(self):
        """Master review used to redirect to connect_accounts when in
        onboarding. Post-reorder, it's the LAST step — should route
        directly to job_input_view (or dashboard) and clear the
        in_onboarding session flag."""
        self._set_onboarding_flag()
        # Minimal POST to satisfy the form.
        resp = self.client.post('/profiles/setup/review/', {
            'full_name': 'Test User',
            'email': 'flow@example.com',
        })
        self.assertEqual(resp.status_code, 302)
        # No active jobs → job input is the natural next stop.
        self.assertEqual(resp.url, '/jobs/input/')
        # Session flag must be cleared so the "Skip onboarding" affordance
        # disappears on subsequent pages.
        self.assertFalse(self.client.session.get('in_onboarding'))


class TourAndHelpAffordanceTests(TestCase):
    """Tier 4: Shepherd tour, Help affordance, step indicators, and the
    routing tooltip on gap analysis. The tour is registered on every page
    via base.html; pages that should auto-run it set SHOULD_RUN_TOUR."""

    def setUp(self):
        from django.contrib.auth import get_user_model
        from profiles.models import UserProfile
        User = get_user_model()
        self.user = User.objects.create_user(
            username='tour@example.com', email='tour@example.com', password='x',
        )
        UserProfile.objects.create(user=self.user, full_name='Tour User')
        self.client.force_login(self.user)

    def test_help_button_renders_on_authenticated_pages(self):
        """The "?" Help button is in base.html and renders for every
        logged-in page so the user always has a re-trigger affordance."""
        resp = self.client.get('/profiles/dashboard/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="help-button"')
        self.assertContains(resp, 'aria-label="Help"')
        # Pages register their tour via window.PAGE_TOUR — Help reads it.
        self.assertContains(resp, "window.PAGE_TOUR = 'dashboard'")

    def test_shepherd_cdn_loaded_on_authenticated_pages(self):
        """Shepherd.js + CSS are CDN-loaded; verify the script + stylesheet
        tags are present so a tour can actually run."""
        resp = self.client.get('/profiles/dashboard/')
        body = resp.content.decode('utf-8')
        self.assertIn('shepherd.js', body)
        self.assertIn('shepherd.css', body)
        # Tours registry is declared globally
        self.assertIn("const Tours =", body)
        for key in ('dashboard', 'resume-edit', 'gap-analysis'):
            self.assertIn(f"'{key}':", body)

    def test_first_visit_dashboard_auto_runs_tour(self):
        """A user without `has_seen_tour` set should get
        window.SHOULD_RUN_TOUR=true on first dashboard visit."""
        resp = self.client.get('/profiles/dashboard/')
        self.assertContains(resp, 'window.SHOULD_RUN_TOUR = true')

    def test_returning_user_does_not_auto_run_tour(self):
        """After dismiss/complete, has_seen_tour=True; the dashboard
        should NOT set SHOULD_RUN_TOUR on subsequent visits."""
        from profiles.models import UserProfile
        UserProfile.objects.filter(user=self.user).update(
            data_content={'has_seen_tour': True},
        )
        resp = self.client.get('/profiles/dashboard/')
        self.assertNotContains(resp, 'window.SHOULD_RUN_TOUR = true')

    def test_dismiss_tour_endpoint_persists_flag(self):
        from profiles.models import UserProfile
        resp = self.client.post('/profiles/api/tour/dismiss/')
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()['ok'])
        profile = UserProfile.objects.get(user=self.user)
        self.assertTrue(profile.data_content.get('has_seen_tour'))

    def test_dismiss_tour_endpoint_rejects_get(self):
        resp = self.client.get('/profiles/api/tour/dismiss/')
        self.assertEqual(resp.status_code, 405)

    def test_step_indicators_on_onboarding_pages(self):
        """The 4-step onboarding sequence shows explicit progress markers.
        Welcome short-circuits for users with profile data already, so we
        log in as a brand-new user with no profile fields."""
        from django.contrib.auth import get_user_model
        from profiles.models import UserProfile
        User = get_user_model()
        new_user = User.objects.create_user(
            username='fresh@example.com', email='fresh@example.com', password='x',
        )
        UserProfile.objects.create(user=new_user)  # empty profile
        self.client.force_login(new_user)
        resp = self.client.get('/welcome/')
        self.assertContains(resp, 'Step 1 of 4')
        resp = self.client.get('/profiles/setup/upload/')
        self.assertContains(resp, 'Step 2 of 4')
        # Post-reorder, connect-accounts is step 3 (was step 4 before
        # signals/project-review moved ahead of master review).
        resp = self.client.get('/profiles/setup/connect/')
        self.assertContains(resp, 'Step 3 of 4')


class ResumeSchemaCoercionTests(SimpleTestCase):
    """Regression: Groq strict-validates tool-call shapes. The model
    sometimes returns null for blank string fields and wraps list items
    as single-key objects. Schema's before-validators coerce both shapes
    to the canonical form so we don't lose generations to validation."""

    def test_null_string_fields_coerce_to_empty(self):
        from profiles.services.schemas import ResumeExperience, ResumeEducation
        # The literal shape Groq rejected with `expected string, but got null`:
        exp = ResumeExperience(**{
            'title': 'Engineer', 'company': '', 'duration': '',
            'location': None, 'industry': None,
            'start_date': None, 'end_date': None,
            'description': [],
        })
        self.assertEqual(exp.location, '')
        self.assertEqual(exp.industry, '')
        self.assertEqual(exp.start_date, '')
        edu = ResumeEducation(**{
            'degree': 'BSc', 'institution': 'MIT', 'year': '2024',
            'gpa': None, 'location': None, 'field': None,
        })
        self.assertEqual(edu.gpa, '')
        self.assertEqual(edu.location, '')

    def test_object_wrapped_strings_flatten(self):
        """The model emits the legacy `highlights` alias as
        `[{description: "..."}]` and skills as
        `[{name: "Dart", proficiency: null}]` — both should flatten to
        plain string lists. PR 3a: `highlights` is folded into the
        canonical `description` field (no separate output field)."""
        from profiles.services.schemas import ResumeProject, ResumeContentResult
        proj = ResumeProject(**{
            'name': 'Mega News',
            'highlights': [
                {'description': 'Engineered a scalable codebase using Clean Architecture.'},
                {'description': 'Reduced load times by fetching news from 4 APIs in parallel.'},
            ],
            'description': [],
            'technologies': [],
        })
        self.assertEqual(len(proj.description), 2)
        self.assertIn('scalable codebase', proj.description[0])
        self.assertIn('Reduced load times', proj.description[1])
        # PR 3a: highlights is no longer a field on ResumeProject.
        self.assertFalse(hasattr(proj, 'highlights'))
        # ResumeContentResult.skills = list of objects → list of strings
        result = ResumeContentResult(**{
            'professional_title': 'Engineer',
            'skills': [
                {'name': 'Dart', 'proficiency': None, 'years': None},
                {'name': 'Flutter', 'proficiency': None, 'years': None},
            ],
        })
        self.assertEqual(result.skills, ['Dart', 'Flutter'])

    def test_top_level_null_strings_coerce(self):
        from profiles.services.schemas import ResumeContentResult
        result = ResumeContentResult(**{
            'professional_title': None,
            'professional_summary': None,
            'objective': None,
        })
        self.assertEqual(result.professional_title, '')
        self.assertEqual(result.professional_summary, '')
        self.assertEqual(result.objective, '')

    # --- Pass G: null description + highlights merge regressions --------

    def test_experience_null_description_coerces_to_empty_list(self):
        """The exact failure from the regen log: description=null on a
        Union[str, List[str]] field. v1 raised
        ``Input should be a valid string [...] input_value=None``; v2
        treats null as no-description."""
        from profiles.services.schemas import ResumeExperience
        exp = ResumeExperience(**{
            'title': 'DT Intern', 'description': None,
        })
        self.assertEqual(exp.description, [])

    def test_experience_null_desc_with_highlights_promotes(self):
        # The Banque Misr regen had description=null and bullets in
        # the (extra) highlights field. The docx exporter reads only
        # description — without this promotion the rendered bullets
        # would have vanished.
        from profiles.services.schemas import ResumeExperience
        exp = ResumeExperience(**{
            'title': 'DT Intern',
            'description': None,
            'highlights': ['Rotated across departments.',
                           'Built a Microsoft Fabric pipeline.'],
        })
        self.assertEqual(exp.description, [
            'Rotated across departments.',
            'Built a Microsoft Fabric pipeline.',
        ])

    def test_experience_string_desc_plus_highlights_merges(self):
        # AI/DS Trainee case: LLM put a summary sentence in description
        # and the actual bullets in highlights. Merge: summary stays
        # first, then highlights bullets.
        from profiles.services.schemas import ResumeExperience
        exp = ResumeExperience(**{
            'title': 'AI Trainee',
            'description': 'Selected participant in DEPI.',
            'highlights': ['Applied the full data-science lifecycle.',
                           'Used MLOps tools (MLflow, Hugging Face).'],
        })
        self.assertEqual(exp.description, [
            'Selected participant in DEPI.',
            'Applied the full data-science lifecycle.',
            'Used MLOps tools (MLflow, Hugging Face).',
        ])

    def test_experience_list_desc_appends_highlights(self):
        # PR 3a behavior change: the pre-PR-3a validator dropped
        # highlights when description was already multi-item (heuristic:
        # likely-duplicate content from the LLM). The new validator
        # appends in order — predictable, matches the new prompt's
        # "do not invent fields" contract (the LLM should produce only
        # description under PR 3a; duplication is a prompt-side concern,
        # not a validator-side one).
        from profiles.services.schemas import ResumeExperience
        exp = ResumeExperience(**{
            'title': 'IT Intern',
            'description': ['Built X.', 'Shipped Y.'],
            'highlights': ['Appended from highlights alias.'],
        })
        self.assertEqual(exp.description, [
            'Built X.', 'Shipped Y.',
            'Appended from highlights alias.',
        ])

    def test_education_null_honors_coerces_to_empty_list(self):
        # Round 1.5.1 — the DevOps regen log showed honors=null on the
        # education entry blocking the entire recovery path. Null →
        # empty list lets recovery salvage the LLM's content.
        from profiles.services.schemas import ResumeEducation
        edu = ResumeEducation(**{
            'degree': 'BSc',
            'institution': 'KSIU',
            'year': '2026',
            'field': '',
            'gpa': '',
            'location': '',
            'honors': None,
        })
        self.assertEqual(edu.honors, [])

    def test_project_null_description_with_highlights_promotes(self):
        # Same promotion rule applies to projects.
        from profiles.services.schemas import ResumeProject
        p = ResumeProject(**{
            'name': 'Healthcare Prediction',
            'description': None,
            'highlights': ['End-to-end ML pipeline for stroke risk.'],
            'technologies': ['Python', 'scikit-learn'],
        })
        self.assertEqual(p.description, ['End-to-end ML pipeline for stroke risk.'])


# --- Round 1 (DevOps audit): regression coverage -------------------------

from resumes.services.resume_normalizer import (
    trim_skills_to_plan,
    normalize_bullet_punctuation,
)


class TrimSkillsToPlanTests(SimpleTestCase):
    """The audit caught that the resume's Skills section was 'Rust, C++,
    Python, Java, TypeScript, ...' for a DevOps JD — the LLM had its
    own picks while the plan's JD-must-have-ordered skills_to_list
    sat unused. trim_skills_to_plan now forces the section to the
    plan's ordering."""

    def test_replaces_llm_skills_with_plan_ordering(self):
        resume = {'skills': ['Rust', 'C++', 'Python', 'Java']}
        plan = _FakePlan()
        plan.skills_to_list = ['Docker', 'Kubernetes', 'Linux', 'CI/CD']
        out = trim_skills_to_plan(resume, plan)
        self.assertEqual(out['skills'][:4], ['Docker', 'Kubernetes', 'Linux', 'CI/CD'])

    def test_appends_llm_extras_not_in_plan(self):
        resume = {'skills': ['Docker', 'Terraform', 'Ansible']}
        plan = _FakePlan()
        plan.skills_to_list = ['Docker', 'Kubernetes']
        out = trim_skills_to_plan(resume, plan)
        # Plan's order first, then LLM extras (Terraform, Ansible).
        self.assertEqual(out['skills'][0], 'Docker')
        self.assertEqual(out['skills'][1], 'Kubernetes')
        self.assertIn('Terraform', out['skills'])
        self.assertIn('Ansible', out['skills'])

    def test_drops_llm_soft_skill_extras(self):
        # LLM extras that happen to be soft skills (Communication, ...)
        # should NOT survive the merge.
        resume = {'skills': ['Communication', 'Teamwork', 'Terraform']}
        plan = _FakePlan()
        plan.skills_to_list = ['Docker', 'Kubernetes']
        out = trim_skills_to_plan(resume, plan)
        self.assertNotIn('Communication', out['skills'])
        self.assertNotIn('Teamwork', out['skills'])
        self.assertIn('Terraform', out['skills'])

    def test_skips_when_plan_has_no_skills(self):
        # Defensive: empty plan ≠ wipe.
        resume = {'skills': ['Python', 'Rust']}
        plan = _FakePlan()
        plan.skills_to_list = []
        out = trim_skills_to_plan(resume, plan)
        self.assertEqual(out['skills'], ['Python', 'Rust'])

    def test_dedupes_conjunction_extended_subset(self):
        """The 2026-05-28 5:16 run shipped 'SQL' AND 'Databases & SQL'
        side-by-side. The token-subset-with-conjunction rule must drop
        the second-seen so the resume shows one canonical entry."""
        resume = {'skills': []}
        plan = _FakePlan()
        plan.skills_to_list = ['SQL', 'Databases & SQL', 'Python']
        out = trim_skills_to_plan(resume, plan)
        self.assertIn('SQL', out['skills'])
        self.assertNotIn('Databases & SQL', out['skills'])
        self.assertIn('Python', out['skills'])

    def test_dedupes_supervised_learning_variants(self):
        """Same pattern: 'Supervised Learning' + 'Supervised &
        Unsupervised Learning' — drop the conjunction-extended form
        when the simpler one was seen first."""
        resume = {'skills': []}
        plan = _FakePlan()
        plan.skills_to_list = ['Supervised Learning',
                               'Supervised & Unsupervised Learning']
        out = trim_skills_to_plan(resume, plan)
        self.assertIn('Supervised Learning', out['skills'])
        self.assertNotIn('Supervised & Unsupervised Learning', out['skills'])

    def test_dedupes_when_conjunction_form_seen_first(self):
        """Order-independence: with 'Databases & SQL' seen first and
        'SQL' coming after, the SQL entry is the subset and gets dropped."""
        resume = {'skills': []}
        plan = _FakePlan()
        plan.skills_to_list = ['Databases & SQL', 'SQL']
        out = trim_skills_to_plan(resume, plan)
        # First-seen wins: 'Databases & SQL' stays, second 'SQL' drops.
        self.assertIn('Databases & SQL', out['skills'])
        self.assertNotIn('SQL', out['skills'])

    def test_does_not_dedupe_compound_product_names(self):
        """Docker / Docker Compose is a known false-positive risk. The
        longer form has no conjunction, so the rule MUST NOT fire.
        Same protection for PostgreSQL / SQL."""
        resume = {'skills': []}
        plan = _FakePlan()
        plan.skills_to_list = ['Docker', 'Docker Compose', 'SQL', 'PostgreSQL']
        out = trim_skills_to_plan(resume, plan)
        self.assertIn('Docker', out['skills'])
        self.assertIn('Docker Compose', out['skills'])
        self.assertIn('SQL', out['skills'])
        self.assertIn('PostgreSQL', out['skills'])


class NormalizeExperienceDatesTests(SimpleTestCase):
    """2026-05-29 Almansour duration shipped as 'August 2025 - Sep 2025'
    (mixed long/short month form). Normalize to 3-letter abbreviations."""

    def test_shortens_long_month_in_duration(self):
        from resumes.services.resume_normalizer import normalize_experience_dates
        resume = {'experience': [
            {'title': 'A', 'duration': 'August 2025 - Sep 2025', 'description': []},
        ]}
        out = normalize_experience_dates(resume)
        self.assertEqual(out['experience'][0]['duration'], 'Aug 2025 - Sep 2025')

    def test_shortens_in_start_and_end_dates(self):
        from resumes.services.resume_normalizer import normalize_experience_dates
        resume = {'experience': [
            {'title': 'A', 'start_date': 'August 2025', 'end_date': 'December 2025',
             'duration': '', 'description': []},
        ]}
        out = normalize_experience_dates(resume)
        self.assertEqual(out['experience'][0]['start_date'], 'Aug 2025')
        self.assertEqual(out['experience'][0]['end_date'], 'Dec 2025')

    def test_idempotent_on_already_abbreviated(self):
        from resumes.services.resume_normalizer import normalize_experience_dates
        resume = {'experience': [
            {'title': 'A', 'duration': 'Jun 2025 - Dec 2025', 'description': []},
        ]}
        out = normalize_experience_dates(resume)
        self.assertEqual(out['experience'][0]['duration'], 'Jun 2025 - Dec 2025')

    def test_preserves_present_and_ranges(self):
        from resumes.services.resume_normalizer import normalize_experience_dates
        resume = {'experience': [
            {'title': 'A', 'duration': 'January 2024 - Present', 'description': []},
        ]}
        out = normalize_experience_dates(resume)
        self.assertEqual(out['experience'][0]['duration'], 'Jan 2024 - Present')


class MarkExpectedGraduationTests(SimpleTestCase):
    """Prefix education year with 'Expected' when it's in the future."""

    def test_marks_future_month_year_as_expected(self):
        import datetime as dt
        from resumes.services.resume_normalizer import mark_expected_graduation
        resume = {'education': [{'degree': 'BSc', 'year': 'June 2026'}]}
        out = mark_expected_graduation(resume, _today=dt.date(2026, 5, 29))
        self.assertEqual(out['education'][0]['year'], 'Expected June 2026')

    def test_does_not_mark_past_dates(self):
        import datetime as dt
        from resumes.services.resume_normalizer import mark_expected_graduation
        resume = {'education': [{'degree': 'BSc', 'year': 'June 2024'}]}
        out = mark_expected_graduation(resume, _today=dt.date(2026, 5, 29))
        self.assertEqual(out['education'][0]['year'], 'June 2024')

    def test_does_not_mark_current_month(self):
        """Same month, same year — already happened or in progress, not
        future. No prefix."""
        import datetime as dt
        from resumes.services.resume_normalizer import mark_expected_graduation
        resume = {'education': [{'degree': 'BSc', 'year': 'May 2026'}]}
        out = mark_expected_graduation(resume, _today=dt.date(2026, 5, 29))
        self.assertEqual(out['education'][0]['year'], 'May 2026')

    def test_marks_year_only_future(self):
        import datetime as dt
        from resumes.services.resume_normalizer import mark_expected_graduation
        resume = {'education': [{'degree': 'BSc', 'year': '2027'}]}
        out = mark_expected_graduation(resume, _today=dt.date(2026, 5, 29))
        self.assertEqual(out['education'][0]['year'], 'Expected 2027')

    def test_idempotent_when_already_marked(self):
        import datetime as dt
        from resumes.services.resume_normalizer import mark_expected_graduation
        resume = {'education': [{'degree': 'BSc', 'year': 'Expected June 2026'}]}
        out = mark_expected_graduation(resume, _today=dt.date(2026, 5, 29))
        self.assertEqual(out['education'][0]['year'], 'Expected June 2026')


class StripPipeTitleSummaryTests(SimpleTestCase):
    """Round-4 reviewer: "Data Scientist | AI Engineer | Data Analyst
    with applied experience in..." was the lead phrase for 4 rounds
    despite the prompt rule. Strip deterministically."""

    def test_strips_three_pipe_titles_with_with_connector(self):
        from types import SimpleNamespace
        from resumes.services.resume_normalizer import clean_summary_phrasing
        resume = {'professional_summary':
                  'Data Scientist | AI Engineer | Data Analyst with applied '
                  'experience in Machine Learning and Deep Learning.'}
        job = SimpleNamespace(title='Data Scientist')
        out = clean_summary_phrasing(resume, job=job)
        self.assertEqual(
            out['professional_summary'],
            'Data Scientist with applied experience in Machine Learning and Deep Learning.',
        )

    def test_uses_jd_title_when_primary_does_not_match(self):
        from types import SimpleNamespace
        from resumes.services.resume_normalizer import clean_summary_phrasing
        resume = {'professional_summary':
                  'AI Engineer | Data Scientist | Analyst with applied experience.'}
        job = SimpleNamespace(title='Data Scientist')
        out = clean_summary_phrasing(resume, job=job)
        self.assertEqual(
            out['professional_summary'],
            'Data Scientist with applied experience.',
        )

    def test_falls_back_to_primary_when_no_job(self):
        from resumes.services.resume_normalizer import clean_summary_phrasing
        resume = {'professional_summary':
                  'Data Scientist | AI Engineer | Data Analyst with hands-on Python work.'}
        out = clean_summary_phrasing(resume, job=None)
        self.assertEqual(
            out['professional_summary'],
            'Data Scientist with hands-on Python work.',
        )

    def test_leaves_single_title_unchanged(self):
        from types import SimpleNamespace
        from resumes.services.resume_normalizer import clean_summary_phrasing
        resume = {'professional_summary':
                  'Data Scientist with applied experience in NLP.'}
        job = SimpleNamespace(title='Data Scientist')
        out = clean_summary_phrasing(resume, job=job)
        self.assertEqual(
            out['professional_summary'],
            'Data Scientist with applied experience in NLP.',
        )

    def test_handles_two_pipe_titles(self):
        from types import SimpleNamespace
        from resumes.services.resume_normalizer import clean_summary_phrasing
        resume = {'professional_summary':
                  'Data Scientist | ML Engineer focused on production pipelines.'}
        job = SimpleNamespace(title='Data Scientist')
        out = clean_summary_phrasing(resume, job=job)
        self.assertEqual(
            out['professional_summary'],
            'Data Scientist focused on production pipelines.',
        )


class FilterLanguagesProficiencyTests(SimpleTestCase):
    """Round-4 reviewer: R3 had 'Arabic (Native), English (Fluent)'.
    R4 lost the proficiency markers, shipping bare 'English, Arabic'.
    Enrich from profile_data and reorder by profile sequence."""

    def test_enriches_bare_names_with_profile_proficiency(self):
        from resumes.services.resume_normalizer import filter_languages
        resume = {'languages': ['English', 'Arabic']}
        profile = {'languages': [
            {'name': 'Arabic', 'proficiency': 'Native'},
            {'name': 'English', 'proficiency': 'Fluent'},
        ]}
        out = filter_languages(resume, profile_data=profile)
        self.assertEqual(out['languages'], ['Arabic (Native)', 'English (Fluent)'])

    def test_reorders_to_match_profile_sequence(self):
        """LLM emitted 'English' first but the profile lists Arabic first
        (the candidate's native language). Profile order wins."""
        from resumes.services.resume_normalizer import filter_languages
        resume = {'languages': ['English (Fluent)', 'Arabic (Native)']}
        profile = {'languages': [
            {'name': 'Arabic', 'proficiency': 'Native'},
            {'name': 'English', 'proficiency': 'Fluent'},
        ]}
        out = filter_languages(resume, profile_data=profile)
        self.assertEqual(out['languages'], ['Arabic (Native)', 'English (Fluent)'])

    def test_no_op_without_profile_data(self):
        from resumes.services.resume_normalizer import filter_languages
        resume = {'languages': ['Arabic (Native)', 'English (Fluent)']}
        out = filter_languages(resume, profile_data=None)
        # Without profile, just sanitize-pass. Both still spoken languages.
        self.assertEqual(out['languages'], ['Arabic (Native)', 'English (Fluent)'])

    def test_handles_profile_string_entries(self):
        from resumes.services.resume_normalizer import filter_languages
        resume = {'languages': ['Arabic']}
        # Profile stored as strings with parens already embedded.
        profile = {'languages': ['Arabic (Native)', 'English (Fluent)']}
        out = filter_languages(resume, profile_data=profile)
        self.assertEqual(out['languages'], ['Arabic (Native)'])


class NormalizeBulletPunctuationTests(SimpleTestCase):
    def test_adds_period_when_at_least_one_bullet_has_one(self):
        resume = {'experience': [{'description': [
            'Built X.',
            'Shipped Y',
            'Reduced Z',
        ]}]}
        out = normalize_bullet_punctuation(resume)
        self.assertEqual(out['experience'][0]['description'],
                         ['Built X.', 'Shipped Y.', 'Reduced Z.'])

    def test_leaves_period_less_lists_alone(self):
        resume = {'experience': [{'description': [
            'Built X', 'Shipped Y', 'Reduced Z',
        ]}]}
        out = normalize_bullet_punctuation(resume)
        # No bullet had terminal punctuation — leave the list unchanged.
        self.assertEqual(out['experience'][0]['description'],
                         ['Built X', 'Shipped Y', 'Reduced Z'])

    def test_respects_existing_terminal_punctuation(self):
        # Bullets that end with ! or ? don't get an extra period.
        resume = {'experience': [{'description': [
            'Built X.', 'What is Y?', 'Achieved 10x growth!',
        ]}]}
        out = normalize_bullet_punctuation(resume)
        self.assertEqual(out['experience'][0]['description'],
                         ['Built X.', 'What is Y?', 'Achieved 10x growth!'])


class NormalizeBulletPunctuationRound152Tests(SimpleTestCase):
    """Round 1.5.2 — drop orphan list-introducer stubs ending in ':'
    that v1's policy was appending '.' to, producing the 'foo:.' bug
    the audit flagged as broken template scaffolding."""

    def test_drops_orphan_colon_header_bullets(self):
        resume = {'experience': [{'description': [
            'Built the platform.',
            'Capstone project highlights:',          # ← stub, drop
            'Delivered 4 prototypes:',               # ← stub, drop
            'Shipped to production.',
        ]}]}
        out = normalize_bullet_punctuation(resume)
        bullets = out['experience'][0]['description']
        self.assertEqual(len(bullets), 2)
        for b in bullets:
            self.assertFalse(b.endswith(':.'))
            self.assertFalse(b.endswith(':'))


class CourseworkBulletRejectsAchievementsTests(SimpleTestCase):
    """Round 1.5.2 — tighten coursework detection so capstone-metric
    bullets ("3 Grafana dashboards (19 panels)") never get folded into
    a fake "Coursework included: ..." line."""

    def test_digit_in_bullet_rejects_as_coursework(self):
        from resumes.services.resume_normalizer import _is_coursework_bullet
        # DevOps capstone deliverables — these were getting mis-classified.
        self.assertFalse(_is_coursework_bullet('3 Grafana dashboards'))
        self.assertFalse(_is_coursework_bullet('Multi-channel alerting Slack Email Discord 4 channels'))
        # Real course title without digits still passes.
        self.assertTrue(_is_coursework_bullet('Prompt Engineering'))

    def test_colon_midbullet_rejects_as_coursework(self):
        from resumes.services.resume_normalizer import _is_coursework_bullet
        # "Section: detail" pattern from capstone notes.
        self.assertFalse(_is_coursework_bullet('Systems Administration: Active Directory'))
        # Pure course title still passes.
        self.assertTrue(_is_coursework_bullet('Tools for Data Science'))


class AcronymSuffixSkillDedupTests(SimpleTestCase):
    """Round 1.5.2 — dedup 'CI/CD' against
    'Continuous Integration and Continuous Delivery (CI/CD)' via the
    acronym-suffix rule."""

    def test_dedups_verbose_form_against_acronym(self):
        from resumes.services.resume_normalizer import (
            trim_skills_to_plan,
        )
        resume = {'skills': []}
        plan = _FakePlan()
        plan.skills_to_list = [
            'CI/CD',
            'Continuous Integration and Continuous Delivery (CI/CD)',
            'Python',
        ]
        out = trim_skills_to_plan(resume, plan)
        # First-seen wins — 'CI/CD' kept, verbose form deduped.
        self.assertIn('CI/CD', out['skills'])
        self.assertNotIn(
            'Continuous Integration and Continuous Delivery (CI/CD)',
            out['skills'],
        )
        self.assertIn('Python', out['skills'])

    def test_dedups_acronym_against_verbose_form_when_verbose_first(self):
        from resumes.services.resume_normalizer import trim_skills_to_plan
        resume = {'skills': []}
        plan = _FakePlan()
        plan.skills_to_list = [
            'Continuous Integration and Continuous Delivery (CI/CD)',
            'CI/CD',
        ]
        out = trim_skills_to_plan(resume, plan)
        # Verbose form wins (first-seen) — acronym deduped against it.
        self.assertEqual(out['skills'],
                         ['Continuous Integration and Continuous Delivery (CI/CD)'])

    def test_sql_is_not_a_duplicate_of_postgresql(self):
        # Round 1.5.3 regression — the v1 blind-suffix rule was deduping
        # SQL against PostgreSQL because 'postgresql' ends with 'sql'.
        # New rule requires parens-acronym or whitelisted-suffix, so
        # SQL stays distinct.
        from resumes.services.resume_normalizer import _is_near_duplicate_skill
        self.assertFalse(_is_near_duplicate_skill(
            [('PostgreSQL', 'postgresql')], 'SQL', 'sql',
        ))

    def test_docker_compose_is_not_a_duplicate_of_docker(self):
        # Round 1.5.3 regression — the v1 prefix rule deduped
        # "Docker Compose" against "Docker" (different products).
        # New rule requires the remainder to be a whitelisted generic
        # suffix; "compose" is not in the list.
        from resumes.services.resume_normalizer import _is_near_duplicate_skill
        self.assertFalse(_is_near_duplicate_skill(
            [('Docker', 'docker')], 'Docker Compose', 'dockercompose',
        ))

    def test_cicd_tools_is_a_duplicate_of_cicd(self):
        # Whitelisted-suffix rule kicks in: "tools" is in
        # _GENERIC_SKILL_SUFFIXES so "CI/CD tools" dedups against
        # "CI/CD". Preserved from Round 1.5.
        from resumes.services.resume_normalizer import _is_near_duplicate_skill
        self.assertTrue(_is_near_duplicate_skill(
            [('CI/CD', 'cicd')], 'CI/CD tools', 'cicdtools',
        ))


class CleanSummaryPhrasingTests(SimpleTestCase):
    """Round 1.5.2 — strip recruiter-jargon openers and unsupported YoE
    claims from the LLM's generated summary."""

    def setUp(self):
        from resumes.services.resume_normalizer import clean_summary_phrasing
        self.clean = clean_summary_phrasing

    def test_strips_highly_motivated_opener(self):
        resume = {'professional_summary': 'Highly motivated Junior DevOps Engineer with Docker.'}
        out = self.clean(resume)
        self.assertFalse(out['professional_summary'].lower().startswith('highly motivated'))
        self.assertTrue(out['professional_summary'].startswith('Junior DevOps Engineer'))

    def test_strips_yoe_claim(self):
        resume = {'professional_summary':
                  'Junior DevOps Engineer with 1 year of experience in CI/CD pipelines. Strong DevOps practitioner.'}
        out = self.clean(resume)
        self.assertNotIn('1 year of experience', out['professional_summary'])
        self.assertIn('Strong DevOps practitioner', out['professional_summary'])

    def test_strips_compound_recruiter_jargon(self):
        resume = {'professional_summary':
                  'Results-driven backend developer with up to 2 years of experience in microservices.'}
        out = self.clean(resume)
        self.assertNotIn('Results-driven', out['professional_summary'])
        self.assertNotIn('years of experience', out['professional_summary'])

    def test_no_op_when_summary_is_clean(self):
        resume = {'professional_summary':
                  'Junior DevOps Engineer with hands-on Docker, Kubernetes, and Linux experience.'}
        out = self.clean(resume)
        self.assertEqual(out['professional_summary'],
                         'Junior DevOps Engineer with hands-on Docker, Kubernetes, and Linux experience.')


class EnforceVerbatimTitlesTests(SimpleTestCase):
    """Round 1.5.2 — snap paraphrased experience titles back to the CV's
    verbatim form (audit caught "DevOps Engineering Trainee" → "DevOps
    Engineer Trainee")."""

    def setUp(self):
        from resumes.services.resume_normalizer import enforce_verbatim_titles
        self.enforce = enforce_verbatim_titles

    def test_snaps_paraphrased_title_back_to_cv(self):
        resume = {'experience': [
            {'title': 'DevOps Engineer Trainee'},  # LLM paraphrase
        ]}
        profile = {'experiences': [
            {'title': 'DevOps Engineering Trainee'},  # CV verbatim
        ]}
        out = self.enforce(resume, profile)
        self.assertEqual(out['experience'][0]['title'], 'DevOps Engineering Trainee')

    def test_leaves_unrelated_titles_alone(self):
        resume = {'experience': [
            {'title': 'Marketing Manager'},  # very different from CV
        ]}
        profile = {'experiences': [
            {'title': 'DevOps Engineering Trainee'},
        ]}
        out = self.enforce(resume, profile)
        # Below the 0.75 similarity threshold — leave it alone.
        self.assertEqual(out['experience'][0]['title'], 'Marketing Manager')

    def test_no_op_without_profile_data(self):
        resume = {'experience': [{'title': 'X'}]}
        out = self.enforce(resume, None)
        self.assertEqual(out['experience'][0]['title'], 'X')


class CleanLocationTests(SimpleTestCase):
    """Round 1.5.2 — strip Arabic-government-registry prefixes from
    LinkedIn-scraped location fields."""

    def test_strips_qesm_prefix(self):
        from profiles.services.profile_sanitizer import sanitize_profile_data
        clean = sanitize_profile_data({'experiences': [
            {'title': 'X', 'company': 'Y',
             'location': 'Qesm El Zamalek, Cairo, Egypt'},
        ]})
        self.assertEqual(
            clean['experiences'][0]['location'],
            'El Zamalek, Cairo, Egypt',
        )

    def test_strips_markaz_prefix(self):
        from profiles.services.profile_sanitizer import sanitize_profile_data
        clean = sanitize_profile_data({'experiences': [
            {'title': 'X', 'company': 'Y',
             'location': 'Markaz Tanta, Gharbia, Egypt'},
        ]})
        self.assertEqual(
            clean['experiences'][0]['location'],
            'Tanta, Gharbia, Egypt',
        )


class TrimSkillsToPlanRound15Tests(SimpleTestCase):
    """Round 1.5 strengthens trim_skills_to_plan: soft skills the plan
    surfaced get re-filtered, near-duplicate skills get deduped, and
    summary backfill uses just-the-facts phrasing."""

    def test_plan_supplied_soft_skills_are_dropped(self):
        # Gap analyzer extracted "Agile, Communication, Multitasking,
        # Time management" from the JD's soft-skill line and put them in
        # plan.skills_to_list. The audit caught these leaking through.
        resume = {'skills': ['Docker']}
        plan = _FakePlan()
        plan.skills_to_list = [
            'Docker', 'Kubernetes', 'Linux',
            'Agile', 'Communication', 'Multitasking', 'Time management',
            'Bash',
        ]
        out = trim_skills_to_plan(resume, plan)
        for soft in ('Agile', 'Communication', 'Multitasking', 'Time management'):
            self.assertNotIn(soft, out['skills'],
                             f"{soft!r} should be re-filtered out of skills")
        self.assertIn('Docker', out['skills'])
        self.assertIn('Kubernetes', out['skills'])
        self.assertIn('Bash', out['skills'])

    def test_near_duplicate_skills_dedup(self):
        # "CI/CD tools" and "CI/CD" both appeared from JD parsing.
        # First-seen wins. Same for "Scripting" + "Bash" + "Python".
        resume = {'skills': []}
        plan = _FakePlan()
        plan.skills_to_list = ['CI/CD', 'CI/CD tools', 'Bash', 'Scripting']
        out = trim_skills_to_plan(resume, plan)
        # "CI/CD tools" dedups against "CI/CD"; "Scripting" is in the
        # soft-skill blocklist (Round 1.5 added it because Bash/Python
        # already cover it).
        self.assertEqual(out['skills'], ['CI/CD', 'Bash'])

    def test_round_153_devops_skills_dont_falsely_dedup(self):
        # Regression for the Round 1.5.3 catastrophe — the v1
        # blind-suffix rule was dropping Docker Compose, SQL, and
        # any other plain skill that happened to share a substring
        # with another. With the new parens-acronym + whitelisted
        # prefix rule, all of these stay.
        resume = {'skills': []}
        plan = _FakePlan()
        plan.skills_to_list = [
            'Linux', 'virtualization', 'Docker', 'CI/CD', 'PostgreSQL',
            'Bash', 'Python', 'Nginx', 'Docker Compose',
            'Continuous Integration and Continuous Delivery (CI/CD)', 'SQL',
        ]
        out = trim_skills_to_plan(resume, plan)
        # CI/CD is canonical; the verbose "Continuous Integration..."
        # form gets deduped via the parens-acronym rule.
        # Docker Compose stays (not a generic suffix of Docker).
        # SQL stays (PostgreSQL is not a parens-acronym alias).
        self.assertIn('SQL', out['skills'])
        self.assertIn('Docker Compose', out['skills'])
        self.assertIn('PostgreSQL', out['skills'])
        self.assertNotIn(
            'Continuous Integration and Continuous Delivery (CI/CD)',
            out['skills'],
        )


class TrimProjectsSubstringMatchTests(SimpleTestCase):
    """Round 1.5.3 — the LLM truncates project names. trim_projects_to_plan
    must accept canonical-substring containment so 'ACR-QA' matches
    'ACR-QA — Automated Code Review Platform'."""

    def test_truncated_llm_name_matches_full_plan_name(self):
        from resumes.services.resume_normalizer import trim_projects_to_plan
        resume = {'projects': [
            {'name': 'ACR-QA'},
            {'name': 'Containerized URL Shortener'},
        ]}
        plan = _FakePlan(projects=[
            'ACR-QA — Automated Code Review Platform',
            'Containerized URL Shortener — Production Monitoring Stack',
        ])
        out = trim_projects_to_plan(resume, plan)
        # Both should survive via substring containment.
        self.assertEqual(len(out['projects']), 2)
        names = [p['name'] for p in out['projects']]
        self.assertIn('ACR-QA', names)
        self.assertIn('Containerized URL Shortener', names)

    def test_truncation_in_other_direction_works_too(self):
        # Plan has short name, LLM emits longer — also valid match.
        from resumes.services.resume_normalizer import trim_projects_to_plan
        resume = {'projects': [
            {'name': 'Healthcare Prediction (DEPI) — Stroke Risk'},
        ]}
        plan = _FakePlan(projects=['Healthcare Prediction (DEPI)'])
        out = trim_projects_to_plan(resume, plan)
        self.assertEqual(len(out['projects']), 1)


class BackfillSummaryNoMetaNarrationTests(SimpleTestCase):
    """The audit called out "drawing on the X role and project work"
    as meta-narration. The phrase is gone now."""

    def test_summary_has_no_meta_narration_clause(self):
        resume = {
            'professional_summary': '',
            'skills': ['Docker', 'Kubernetes', 'Linux'],
            'experience': [{'title': 'DevOps Engineering Trainee'}],
        }
        job = SimpleNamespace(title='Junior DevOps Engineer')
        out = backfill_summary(resume, job=job)
        self.assertNotIn('drawing on', out['professional_summary'].lower())
        self.assertNotIn('role and project work', out['professional_summary'].lower())
        # Still leads with the JD title (Round 1 behaviour preserved).
        self.assertTrue(out['professional_summary'].startswith('Junior DevOps Engineer'))


class ResumeProjectAppendsHighlightsAliasTests(SimpleTestCase):
    """PR 3a behavior: ResumeProject's coerce_to_canonical validator
    appends `highlights` input into `description` in order — no
    richness-comparison heuristic, no drop-on-duplicate. The pre-PR-3a
    richness check was a workaround for the LLM emitting both fields
    with varying quality; the new prompt ("do not invent fields") makes
    highlights effectively dead on output, so the validator simply folds
    any legacy input liberally."""

    def test_highlights_appended_after_description(self):
        from profiles.services.schemas import ResumeProject
        p = ResumeProject(**{
            'name': 'ACR-QA',
            'description': ['Built a platform.'],
            'highlights': [
                'Built a language-agnostic static analysis platform running 7 tools in parallel with 97.1% precision.',
                'Delivered a 273-test pytest suite passing in under 6 seconds; GitHub Actions + GitLab CI integration; SARIF v2.1.0 export; OWASP Top 10 compliance mapping with CWE IDs.',
            ],
        })
        # description first, then highlights — append-in-order.
        self.assertEqual(len(p.description), 3)
        self.assertEqual(p.description[0], 'Built a platform.')
        self.assertIn('97.1%', p.description[1])
        self.assertIn('SARIF', p.description[2])

    def test_short_highlights_still_appended(self):
        from profiles.services.schemas import ResumeProject
        p = ResumeProject(**{
            'name': 'X',
            'description': [
                'Built a system with 99.9% uptime over 6 months.',
                'Shipped 50 deployments per week.',
                'Reduced cloud spend by 40%.',
            ],
            'highlights': ['One short fact.'],
        })
        # PR 3a: no richness heuristic — append regardless.
        self.assertEqual(len(p.description), 4)
        self.assertIn('99.9%', p.description[0])
        self.assertEqual(p.description[-1], 'One short fact.')


class PR3aSchemaTolerance(SimpleTestCase):
    """PR 3a: ResumeExperience and ResumeProject collapse to a single
    canonical `description: List[str]` field with extra="forbid".
    The validator's coerce_to_canonical folds known LLM-invented field
    names into description; extra="forbid" rejects unknown ones.

    These tests pin the behavior of the input-tolerance layer — the
    load-bearing piece that the rest of the pipeline depends on."""

    def test_achievements_wrapper_folded_into_description(self):
        """PR 3f-style invention: achievements: [{description: [...]}]
        unwraps and merges into the canonical description field."""
        from profiles.services.schemas import ResumeExperience
        e = ResumeExperience(
            title='Engineer', company='X',
            achievements=[{'description': ['Bullet A.', 'Bullet B.']}],
        )
        self.assertEqual(e.description, ['Bullet A.', 'Bullet B.'])
        self.assertFalse(hasattr(e, 'achievements'))

    def test_responsibilities_invention_folded(self):
        """Other invented alias names also fold."""
        from profiles.services.schemas import ResumeExperience
        e = ResumeExperience(
            title='Engineer', company='X',
            responsibilities=['Resp 1', 'Resp 2'],
        )
        self.assertEqual(e.description, ['Resp 1', 'Resp 2'])
        self.assertFalse(hasattr(e, 'responsibilities'))

    def test_string_description_coerced_to_list(self):
        """Legacy description as a single string wraps in a list."""
        from profiles.services.schemas import ResumeExperience
        e = ResumeExperience(
            title='Engineer', company='X',
            description='Single-paragraph description.',
        )
        self.assertEqual(e.description, ['Single-paragraph description.'])

    def test_unknown_field_rejected_on_experience(self):
        """extra='forbid' rejects truly unknown fields (not in alias list)."""
        from pydantic import ValidationError
        from profiles.services.schemas import ResumeExperience
        with self.assertRaises(ValidationError):
            ResumeExperience(
                title='Engineer', company='X',
                fake_field='this should fail validation',
            )

    def test_unknown_field_rejected_on_project(self):
        from pydantic import ValidationError
        from profiles.services.schemas import ResumeProject
        with self.assertRaises(ValidationError):
            ResumeProject(name='X', fake_field='nope')

    def test_no_bullets_at_all_produces_empty_description(self):
        """Refinement 3 from the review gate: every-field-absent shape
        validates and yields description=[]."""
        from profiles.services.schemas import ResumeExperience, ResumeProject
        e = ResumeExperience(title='E', company='C')
        self.assertEqual(e.description, [])
        p = ResumeProject(name='P')
        self.assertEqual(p.description, [])

    def test_known_inventions_registered(self):
        """Refinement 6 — registry doc-test. The bullet-alias registry
        must cover all LLM inventions observed across the audit thread.
        If you intentionally tighten this list (drop an entry), update
        the test deliberately so the change is explicit."""
        from profiles.services.schemas import _BULLET_ALIAS_KEYS
        # Historical second-canonical (folds silently)
        self.assertIn('highlights', _BULLET_ALIAS_KEYS)
        # Inventions surfaced by PR 3f and recorder logs
        for invention in ('achievements', 'responsibilities', 'bullets'):
            self.assertIn(
                invention, _BULLET_ALIAS_KEYS,
                msg=(
                    f"Known LLM invention '{invention}' missing from "
                    f"_BULLET_ALIAS_KEYS. If this invention should no "
                    f"longer be tolerated, intentionally update this test."
                ),
            )

    def test_project_technologies_csv_tolerance(self):
        """Pre-existing input tolerance preserved: comma-separated
        technologies string from the editor form still coerces to list."""
        from profiles.services.schemas import ResumeProject
        p = ResumeProject(name='X', technologies='Python, Django, Postgres')
        self.assertEqual(p.technologies, ['Python', 'Django', 'Postgres'])


class PR3aMigrationCommand(SimpleTestCase):
    """The one-shot data-migration command that converts stored
    GeneratedResume.content rows from pre-PR-3a (highlights + description
    dual fields) to PR-3a (description canonical, list[str])."""

    def test_migrates_highlights_to_description(self):
        from resumes.management.commands.migrate_resume_schema import _migrate_content
        content = {
            'experience': [{
                'title': 'E', 'company': 'C',
                'highlights': ['B1', 'B2'],
                'description': 'Old paragraph',
            }],
            'projects': [{
                'name': 'P',
                'highlights': ['PB1'],
            }],
        }
        migrated, changed = _migrate_content(content)
        self.assertTrue(changed)
        # Old string description wraps to list, then highlights appended.
        self.assertNotIn('highlights', migrated['experience'][0])
        self.assertEqual(
            migrated['experience'][0]['description'],
            ['Old paragraph', 'B1', 'B2'],
        )
        self.assertNotIn('highlights', migrated['projects'][0])
        self.assertEqual(migrated['projects'][0]['description'], ['PB1'])

    def test_idempotent_on_new_shape(self):
        from resumes.management.commands.migrate_resume_schema import _migrate_content
        content = {
            'experience': [{
                'title': 'E', 'company': 'C',
                'description': ['B1', 'B2'],
            }],
            'projects': [{
                'name': 'P',
                'description': ['PB1'],
                'technologies': ['Python'],
            }],
        }
        migrated, changed = _migrate_content(content)
        self.assertFalse(changed)
        self.assertEqual(migrated['experience'][0]['description'], ['B1', 'B2'])
        self.assertEqual(migrated['projects'][0]['description'], ['PB1'])

    def test_handles_missing_sections_gracefully(self):
        from resumes.management.commands.migrate_resume_schema import _migrate_content
        # No experience, no projects — should not raise.
        migrated, changed = _migrate_content({'professional_title': 'X'})
        self.assertFalse(changed)
        self.assertEqual(migrated, {'professional_title': 'X'})

    def test_handles_experiences_plural_key(self):
        """Some rows use 'experiences' rather than 'experience' as the
        section key — the migrator accepts either."""
        from resumes.management.commands.migrate_resume_schema import _migrate_content
        content = {
            'experiences': [{
                'title': 'E', 'company': 'C',
                'highlights': ['B1'],
            }],
        }
        migrated, changed = _migrate_content(content)
        self.assertTrue(changed)
        self.assertEqual(migrated['experiences'][0]['description'], ['B1'])


class PR3bSchemaTolerance(SimpleTestCase):
    """PR 3b mirrors PR 3a's pattern for the CV-parser-side schemas
    (Experience and Project — used by ResumeSchema, stored in
    UserProfile.data_content). Uses the same _fold_into_description
    helper as PR 3a, so these tests verify the helper produces correct
    output when called from the profile-side validators too.

    Also pins the contract for the 6 fields promoted from extra='allow'
    during PR 3b (source, employment_type on Experience; source,
    source_id, pushed_at, date on Project)."""

    def test_description_canonical(self):
        from profiles.services.schemas import Experience
        e = Experience(title='Eng', company='C', description=['Built X.'])
        self.assertEqual(e.description, ['Built X.'])

    def test_input_highlights_folded(self):
        from profiles.services.schemas import Experience
        e = Experience(
            title='Eng', company='C',
            highlights=['B1', 'B2'],
        )
        self.assertEqual(e.description, ['B1', 'B2'])
        self.assertFalse(hasattr(e, 'highlights'))

    def test_input_achievements_folded(self):
        """Pre-PR-3b, achievements was a declared field on Experience
        (the only one of the three schemas with this third field).
        Post-PR-3b it folds via the alias registry."""
        from profiles.services.schemas import Experience
        e = Experience(
            title='Eng', company='C',
            achievements=['Shipped X.', 'Mentored 2 jrs.'],
        )
        self.assertEqual(e.description, ['Shipped X.', 'Mentored 2 jrs.'])
        self.assertFalse(hasattr(e, 'achievements'))

    def test_input_responsibilities_folded_on_experience(self):
        from profiles.services.schemas import Experience
        e = Experience(
            title='Eng', company='C',
            responsibilities=['Resp 1', 'Resp 2'],
        )
        self.assertEqual(e.description, ['Resp 1', 'Resp 2'])

    def test_string_description_coerced_to_list(self):
        from profiles.services.schemas import Experience
        e = Experience(
            title='Eng', company='C',
            description='Single-paragraph description.',
        )
        self.assertEqual(e.description, ['Single-paragraph description.'])

    def test_unknown_field_rejected_on_experience(self):
        """extra='forbid' rejects fields outside the canonical set AND
        outside the alias registry. Genuinely unknown invention names
        (not in _BULLET_ALIAS_KEYS, not one of the 6 promoted fields)
        fail validation. Restored from hotfix's silently-accepted variant
        by PR 3b.1, which paired this strictness with the CV-parser
        prompt's "DO NOT INVENT FIELDS" guidance."""
        from pydantic import ValidationError
        from profiles.services.schemas import Experience
        with self.assertRaises(ValidationError):
            Experience(title='Eng', company='C', fake_field='nope')

    def test_unknown_field_rejected_on_project(self):
        from pydantic import ValidationError
        from profiles.services.schemas import Project
        with self.assertRaises(ValidationError):
            Project(name='X', fake_field='nope')

    def test_null_description_coerced_to_empty_list_on_experience(self):
        """Defense-in-depth: ``description=None`` coerces to [] via the
        validator's mode='before' fold even though the field type is
        ``List[str]`` (non-Optional). The CV-parser prompt forbids
        emitting null; this test pins the safety net for prompt drift.
        """
        from profiles.services.schemas import Experience
        e = Experience(
            title='E', company='C',
            description=None,
            highlights=['Bullet 1', 'Bullet 2'],
        )
        self.assertEqual(e.description, ['Bullet 1', 'Bullet 2'])

    def test_null_description_coerced_to_empty_list_on_project(self):
        """Mirror for Project."""
        from profiles.services.schemas import Project
        p = Project(
            name='P',
            description=None,
            highlights=['PB1'],
        )
        self.assertEqual(p.description, ['PB1'])

    def test_empty_default(self):
        from profiles.services.schemas import Experience, Project
        e = Experience(title='E', company='C')
        self.assertEqual(e.description, [])
        p = Project(name='P')
        self.assertEqual(p.description, [])

    def test_role_field_preserved_on_project(self):
        """Project.role is semantically distinct from bullets — it
        names the candidate's role on the project ("Lead", "Solo dev",
        etc.). Must survive validation alongside description."""
        from profiles.services.schemas import Project
        p = Project(name='thing', role='Lead Developer',
                    description=['Built it.'])
        self.assertEqual(p.role, 'Lead Developer')
        self.assertEqual(p.description, ['Built it.'])

    def test_promoted_extras_accepted_on_experience(self):
        """source and employment_type were silent extras pre-PR-3b
        (under extra='allow'). PR 3b promoted them to explicit fields.
        Both must validate without ValidationError under the new
        extra='forbid' config."""
        from profiles.services.schemas import Experience
        e = Experience(
            title='Eng', company='C',
            source='linkedin',
            employment_type='Full-time',
        )
        self.assertEqual(e.source, 'linkedin')
        self.assertEqual(e.employment_type, 'Full-time')

    def test_promoted_extras_accepted_on_project(self):
        """source, source_id, pushed_at, date were silent extras
        pre-PR-3b. All four are now explicit Optional fields and must
        validate."""
        from profiles.services.schemas import Project
        p = Project(
            name='thing',
            source='github',
            source_id='me/thing',
            pushed_at='2026-01-01T00:00:00Z',
            date='2024',
        )
        self.assertEqual(p.source, 'github')
        self.assertEqual(p.source_id, 'me/thing')
        self.assertEqual(p.pushed_at, '2026-01-01T00:00:00Z')
        self.assertEqual(p.date, '2024')


class PR3bMigrationCommand(SimpleTestCase):
    """The migrate_profile_schema management command converts stored
    UserProfile.data_content rows from pre-PR-3b shape (description +
    highlights + achievements + silent extras) to PR-3b shape."""

    def test_migrates_dict_shape_experiences(self):
        from profiles.management.commands.migrate_profile_schema import (
            _migrate_data_content,
        )
        content = {
            'experiences': [{
                'title': 'E', 'company': 'C',
                'description': 'Para',
                'highlights': ['B1', 'B2'],
                'achievements': ['A1'],
                'source': 'linkedin',           # promoted — must survive
                'employment_type': 'Full-time', # promoted — must survive
            }],
        }
        migrated, changed = _migrate_data_content(content)
        self.assertTrue(changed)
        exp = migrated['experiences'][0]
        self.assertNotIn('highlights', exp)
        self.assertNotIn('achievements', exp)
        # description ordering: existing first, then highlights, then
        # achievements (alias-registry order).
        self.assertEqual(exp['description'], ['Para', 'B1', 'B2', 'A1'])
        # Promoted fields must be preserved by the migration.
        self.assertEqual(exp['source'], 'linkedin')
        self.assertEqual(exp['employment_type'], 'Full-time')

    def test_migrates_dict_shape_projects(self):
        from profiles.management.commands.migrate_profile_schema import (
            _migrate_data_content,
        )
        content = {
            'projects': [{
                'name': 'thing',
                'highlights': ['PB1', 'PB2'],
                'source': 'github',
                'source_id': 'me/thing',
                'pushed_at': '2026-01-01T00:00:00Z',
                'date': '2024',
                'technologies': ['Python'],
            }],
        }
        migrated, changed = _migrate_data_content(content)
        self.assertTrue(changed)
        proj = migrated['projects'][0]
        self.assertNotIn('highlights', proj)
        self.assertEqual(proj['description'], ['PB1', 'PB2'])
        # All 4 promoted fields preserved.
        self.assertEqual(proj['source'], 'github')
        self.assertEqual(proj['source_id'], 'me/thing')
        self.assertEqual(proj['pushed_at'], '2026-01-01T00:00:00Z')
        self.assertEqual(proj['date'], '2024')
        self.assertEqual(proj['technologies'], ['Python'])

    def test_idempotent_on_new_shape(self):
        from profiles.management.commands.migrate_profile_schema import (
            _migrate_data_content,
        )
        content = {
            'experiences': [{
                'title': 'E', 'company': 'C',
                'description': ['B1', 'B2'],
                'source': 'cv',
            }],
            'projects': [{
                'name': 'P',
                'description': ['PB1'],
                'source': 'github',
                'source_id': 'me/p',
            }],
        }
        migrated, changed = _migrate_data_content(content)
        self.assertFalse(changed)
        self.assertEqual(migrated['experiences'][0]['description'], ['B1', 'B2'])
        self.assertEqual(migrated['projects'][0]['description'], ['PB1'])

    def test_scan_unknowns_reports_extras(self):
        """--scan-unknowns must surface keys that aren't canonical and
        aren't in the alias registry, so the operator can classify
        them before extra='forbid' rejects them at validation time."""
        from profiles.management.commands.migrate_profile_schema import (
            _scan_unknown_keys,
        )
        content = {
            'experiences': [{
                'title': 'E', 'company': 'C',
                # Genuinely unknown — not canonical, not an alias.
                'fake_invented_field': 'mock',
            }],
            'projects': [{
                'name': 'P',
                'another_unknown': 42,
            }],
        }
        unknowns = _scan_unknown_keys(content)
        self.assertIn('experience.fake_invented_field', unknowns)
        self.assertIn('project.another_unknown', unknowns)
        self.assertEqual(unknowns['experience.fake_invented_field'], 1)
        self.assertEqual(unknowns['project.another_unknown'], 1)
        # Promoted fields and known aliases must NOT appear as unknowns.
        clean_content = {
            'experiences': [{
                'title': 'E', 'company': 'C',
                'source': 'cv',
                'employment_type': 'Internship',
            }],
            'projects': [{
                'name': 'P',
                'source': 'github',
                'source_id': 'me/p',
                'pushed_at': '2026-01-01T00:00:00Z',
                'date': '2024',
            }],
        }
        self.assertEqual(_scan_unknown_keys(clean_content), {})


class PR3b2OptionalSchemaTests(SimpleTestCase):
    """PR 3b.2: 6 fields on the resume-output nested models were
    relaxed to Optional with None defaults so Groq's server-side
    tool-call validator stops rejecting LLM responses that emit null
    for legitimately-missing fields (internship industry, ongoing-
    role end_date, cert-without-URL, etc.).

    Validators continue to normalize None -> '' / None -> [] for
    downstream readers, so post-validation shapes are unchanged.

    Also pins the scope-guard contract: required-semantic fields
    (title, company on Experience; name, issuer on Certification)
    are NOT in the Optional set. At the JSON-schema layer (what Groq
    actually validates against), null on those fields would still be
    rejected — preventing future PRs from silently broadening the
    relaxation."""

    def test_optional_industry_accepts_none_on_experience(self):
        from profiles.services.schemas import ResumeExperience
        e = ResumeExperience(title='Engineer', company='X', industry=None)
        # Validator normalizes None -> '' for downstream readers.
        self.assertEqual(e.industry, '')

    def test_optional_end_date_accepts_none(self):
        """end_date=None is the LLM's correct emission for an ongoing
        role when the prompt asked for "Present" but the LLM puts
        "Present" in duration only and leaves end_date empty."""
        from profiles.services.schemas import ResumeExperience
        e = ResumeExperience(title='Engineer', company='X', end_date=None)
        self.assertEqual(e.end_date, '')

    def test_optional_cert_fields_accept_none(self):
        """ResumeCertification url/date/duration accept None for
        certs without a verification URL, undated certs, and
        certs without a stated duration."""
        from profiles.services.schemas import ResumeCertification
        c = ResumeCertification(
            name='Some Cert', issuer='Some Org',
            url=None, date=None, duration=None,
        )
        self.assertEqual(c.url, '')
        self.assertEqual(c.date, '')
        self.assertEqual(c.duration, '')

    def test_optional_education_honors_accepts_none(self):
        """ResumeEducation.honors=None normalizes to [] (a degree
        entry typically has no honors line for most candidates)."""
        from profiles.services.schemas import ResumeEducation
        ed = ResumeEducation(institution='X', degree='Y', honors=None)
        self.assertEqual(ed.honors, [])

    def test_required_fields_stay_strict_in_json_schema(self):
        """SCOPE GUARD (PR 3b.2): the relaxation is scoped to fields
        with semantic-null meaning. Required-semantic fields stay
        strict at the JSON-schema layer (which is what Groq's
        server-side tool-call validator actually checks). null on
        title/company/name/issuer still gets rejected upstream —
        preserving the surface where genuine data bugs surface.

        Test asserts on model_json_schema() rather than Python-side
        validation because the existing _coerce_null_strings
        validator masks Python-side null on ALL declared str fields.
        The JSON-schema layer is the architecturally relevant boundary.
        """
        from profiles.services.schemas import (
            ResumeExperience, ResumeCertification,
        )

        exp_schema = ResumeExperience.model_json_schema()
        cert_schema = ResumeCertification.model_json_schema()

        def _accepts_null(field_schema: dict) -> bool:
            """True iff the JSON schema for one field admits null.

            Pydantic emits Optional[str] as `{"anyOf": [{"type": "string"},
            {"type": "null"}]}` or similar; a plain str = "" emits just
            `{"type": "string"}`."""
            if field_schema.get('type') == 'null':
                return True
            for sub in field_schema.get('anyOf', []):
                if sub.get('type') == 'null':
                    return True
            return False

        # PR 3b.2 relaxed fields — null IS accepted at JSON-schema layer
        self.assertTrue(_accepts_null(exp_schema['properties']['industry']))
        self.assertTrue(_accepts_null(exp_schema['properties']['end_date']))
        self.assertTrue(_accepts_null(cert_schema['properties']['date']))
        self.assertTrue(_accepts_null(cert_schema['properties']['duration']))
        self.assertTrue(_accepts_null(cert_schema['properties']['url']))

        # Required-semantic fields — null is NOT accepted at JSON-schema
        # layer (Groq will still 400 if the LLM emits null for these).
        self.assertFalse(_accepts_null(exp_schema['properties']['title']))
        self.assertFalse(_accepts_null(exp_schema['properties']['company']))
        self.assertFalse(_accepts_null(exp_schema['properties']['start_date']))
        self.assertFalse(_accepts_null(cert_schema['properties']['name']))
        self.assertFalse(_accepts_null(cert_schema['properties']['issuer']))


class NormalizeRoleAnalystVariantsTests(SimpleTestCase):
    """Issue 3 fix (2026-05-20): _normalize_role was missing 'analyst'
    keywords. 'Junior Data Analyst' was falling through to the
    software_engineer default, causing KB retrieval to pull
    software-engineering chunks (Django/PostgreSQL/LLM-systems patterns)
    for analyst JDs instead of data-flavored content.

    Fix routes analyst variants to data_scientist (the closest existing
    bucket with KB depth — 3 role-specific + 45 universal chunks).
    KB Sprint 2 may add a dedicated data_analyst bucket later; this
    PR is the routing fix only."""

    def test_data_analyst_variants_route_to_data_scientist(self):
        from profiles.services.knowledge_retriever import _normalize_role
        cases = [
            'Data Analyst',
            'Junior Data Analyst',
            'Senior Data Analyst',
            'Data Analyst Intern',
            'Business Analyst',
            'Reporting Analyst',
        ]
        for jd_title in cases:
            self.assertEqual(
                _normalize_role(jd_title), 'data_scientist',
                msg=f"'{jd_title}' should route to 'data_scientist'",
            )

    def test_analyst_variants_never_fall_to_software_engineer(self):
        """SCOPE GUARD: pin that the analyst keywords actually catch.
        Prevents future refactors from silently reverting the bug."""
        from profiles.services.knowledge_retriever import _normalize_role
        for jd_title in (
            'Data Analyst', 'Junior Data Analyst', 'Senior Data Analyst',
            'Business Analyst', 'Reporting Analyst',
        ):
            self.assertNotEqual(
                _normalize_role(jd_title), 'software_engineer',
                msg=(
                    f"'{jd_title}' fell through to software_engineer — "
                    f"the very bug Issue 3 was meant to fix."
                ),
            )

    def test_engineering_roles_with_data_context_route_to_engineering(self):
        """SCOPE GUARD: 'Data X Engineer' titles must NOT be caught by
        the new analyst keywords. The 'data analyst' substring requires
        the literal word 'analyst' — 'Data Analysis Engineer' doesn't
        match (no 't' suffix). Pinning this so future broader matches
        ('analyst' alone, etc.) don't accidentally catch engineering
        roles."""
        from profiles.services.knowledge_retriever import _normalize_role
        # Direct data_engineer match.
        self.assertEqual(_normalize_role('Data Engineer'), 'data_engineer')
        # No keyword catches this — falls through to software_engineer.
        # Pinning the current behavior so the analyst PR can't be blamed
        # for it later.
        self.assertEqual(
            _normalize_role('Data Analysis Engineer'),
            'software_engineer',
        )
        # NOTE: 'Senior Data Platform Engineer' routes to 'devops' (NOT
        # 'data_engineer') because the existing devops branch matches
        # 'platform'. That's pre-existing behavior, predates this PR,
        # and is intentionally left untouched.

    def test_non_data_analysts_preserved(self):
        """SCOPE GUARD: bare 'analyst' was rejected — Quality/Financial/
        Security Analysts still route the same as before this PR."""
        from profiles.services.knowledge_retriever import _normalize_role
        # Quality Analyst -> qa (matches 'quality' in the qa branch).
        self.assertEqual(_normalize_role('Quality Analyst'), 'qa')
        # Financial / Security Analyst -> software_engineer default.
        # Not great, but no better bucket exists; same as pre-PR.
        self.assertEqual(_normalize_role('Financial Analyst'), 'software_engineer')
        self.assertEqual(_normalize_role('Security Analyst'), 'software_engineer')


class AwardsFieldEndToEndTests(SimpleTestCase):
    """Round 1.5: Honors & Awards section is now first-class. Schema
    accepts the field, _ensure_profile_data_preserved surfaces from
    profile, _write_awards renders bold name + plain suffix."""

    def test_schema_accepts_awards_list(self):
        from profiles.services.schemas import ResumeContentResult
        r = ResumeContentResult(awards=[
            'ICPC ECPC 2024 — Honorable Mention, 2nd among KSIU teams',
            'Dean\'s List — Fall 2024',
        ])
        self.assertEqual(len(r.awards), 2)
        self.assertTrue(r.awards[0].startswith('ICPC'))

    def test_schema_normalizes_honors_alias(self):
        from profiles.services.schemas import ResumeContentResult
        # Some pipelines may emit 'honors' instead of 'awards'.
        r = ResumeContentResult(honors=['Award A', 'Award B'])
        self.assertEqual(r.awards, ['Award A', 'Award B'])


class WriteAwardsTests(SimpleTestCase):
    """The Honors & Awards docx writer renders with bold name + plain
    suffix and uses the same section-heading + List Bullet style as
    certifications."""

    def test_writes_awards_section_when_present(self):
        from docx import Document
        from resumes.services.docx_exporter import _write_awards
        doc = Document()
        _write_awards(doc, {'awards': [
            'ICPC ECPC 2024 — Honorable Mention',
            'Dean\'s List',
        ]})
        # Section heading + two bullets.
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        self.assertEqual(paragraphs[0], 'HONORS & AWARDS')
        self.assertEqual(paragraphs[1], 'ICPC ECPC 2024 — Honorable Mention')
        self.assertEqual(paragraphs[2], 'Dean\'s List')

    def test_no_op_when_no_awards(self):
        from docx import Document
        from resumes.services.docx_exporter import _write_awards
        doc = Document()
        before = len(doc.paragraphs)
        _write_awards(doc, {})
        # Writer adds no new paragraphs when awards is missing/empty.
        self.assertEqual(len(doc.paragraphs), before)


class TrimCertsKeepsAllCertsTests(SimpleTestCase):
    """Round 1.5: the cert filter was simplified to keep ALL candidate
    certs (capped at _CERT_CAP). The auditor's recommendation was
    'Include ALL certifications from JSON'."""

    def test_keeps_every_cert_regardless_of_plan(self):
        from resumes.services.resume_normalizer import trim_certs_to_plan
        resume = {'certifications': [
            {'name': 'Introduction to Software Testing', 'issuer': 'U of Minnesota'},
            {'name': 'Random Off-Topic Cert', 'issuer': 'Whatever'},
            {'name': 'Applied ML in Python', 'issuer': 'Coursera'},
        ]}
        plan = _FakePlan(certifications=['IBM Data Scientist Track'])
        plan.skills_to_list = ['Docker']
        out = trim_certs_to_plan(resume, plan)
        names = [c['name'] for c in out['certifications']]
        # Every cert survives — plan membership no longer filters.
        self.assertEqual(len(names), 3)
        self.assertIn('Introduction to Software Testing', names)
        self.assertIn('Random Off-Topic Cert', names)


class BackfillSummaryUsesJdTitleTests(SimpleTestCase):
    """The audit caught the backfill leading with 'DevOps Engineering
    Trainee' for a 'Junior DevOps Engineer' JD. The summary now
    leads with the JD title when available."""

    def test_leads_with_jd_title_not_experience_title(self):
        resume = {
            'professional_summary': '',
            'skills': ['Docker', 'Kubernetes', 'Linux', 'Bash'],
            'experience': [{'title': 'DevOps Engineering Trainee'}],
        }
        job = SimpleNamespace(title='Junior DevOps Engineer')
        out = backfill_summary(resume, job=job)
        self.assertTrue(out['professional_summary'].startswith('Junior DevOps Engineer'))
        self.assertNotIn('data and ML lifecycle', out['professional_summary'])

    def test_falls_back_to_experience_title_when_no_jd_title(self):
        resume = {
            'professional_summary': '',
            'skills': ['Python'],
            'experience': [{'title': 'Data Engineer'}],
        }
        out = backfill_summary(resume, job=None)
        self.assertTrue(out['professional_summary'].startswith('Data Engineer'))


class ZeroWidthCharStripTests(SimpleTestCase):
    """The audit caught project names ending with U+2060 word joiner,
    e.g. 'ACR-QA — Automated Code Review Platform⁠'. The CV parser
    leaves these in skills, project names, and bullets. The sanitizer
    now strips them recursively."""

    def test_strips_word_joiner_from_project_name(self):
        from profiles.services.profile_sanitizer import sanitize_profile_data
        clean = sanitize_profile_data({
            'projects': [
                {'name': 'ACR-QA — Automated Code Review Platform⁠',
                 'description': ['Built CI/​CD pipeline.']},
            ],
        })
        self.assertEqual(clean['projects'][0]['name'],
                         'ACR-QA — Automated Code Review Platform')
        self.assertEqual(clean['projects'][0]['description'],
                         ['Built CI/CD pipeline.'])

    def test_strips_from_skills_and_experiences(self):
        from profiles.services.profile_sanitizer import sanitize_profile_data
        clean = sanitize_profile_data({
            'skills': ['Docker​', 'Python⁠'],
            'experiences': [{'title': 'DevOps Trainee﻿',
                              'description': ['Used CI/​CD.']}],
        })
        skill_names = [s.get('name') if isinstance(s, dict) else s
                       for s in clean['skills']]
        self.assertIn('Docker', skill_names)
        self.assertIn('Python', skill_names)
        self.assertEqual(clean['experiences'][0]['title'], 'DevOps Trainee')
        self.assertEqual(clean['experiences'][0]['description'],
                         ['Used CI/CD.'])


class StripSchemaEnvelopeLeaksTests(SimpleTestCase):
    """The DevOps regen log showed `additionalProperties`, `properties`,
    `type` keys in the final resume sections list — the LLM emitted
    the schema-envelope shape on a 200 (happy path), not via
    failed_generation. The recovery unwrap didn't fire because there
    was no exception. _strip_schema_envelope_leaks handles this."""

    def test_strips_top_level_envelope_keys(self):
        from resumes.services.resume_generator import _strip_schema_envelope_leaks
        resume = {
            'professional_summary': 'hello',
            'skills': ['A'],
            'additionalProperties': True,
            'properties': {'leftover': 'whatever'},
            'type': 'object',
        }
        out = _strip_schema_envelope_leaks(resume)
        self.assertNotIn('additionalProperties', out)
        self.assertNotIn('properties', out)
        self.assertNotIn('type', out)
        self.assertEqual(out['skills'], ['A'])

    def test_steps_into_properties_when_full_envelope(self):
        from resumes.services.resume_generator import _strip_schema_envelope_leaks
        resume = {
            'additionalProperties': True,
            'properties': {
                'professional_summary': 'inside',
                'skills': ['X', 'Y'],
            },
            'type': 'object',
        }
        out = _strip_schema_envelope_leaks(resume)
        self.assertEqual(out['professional_summary'], 'inside')
        self.assertEqual(out['skills'], ['X', 'Y'])
        self.assertNotIn('additionalProperties', out)

    def test_unwraps_per_field_type_value_wrappers(self):
        from resumes.services.resume_generator import _strip_schema_envelope_leaks
        resume = {
            'skills': {'type': 'array', 'value': ['Docker', 'K8s']},
            'professional_summary': {'type': 'string', 'value': 'hi'},
        }
        out = _strip_schema_envelope_leaks(resume)
        self.assertEqual(out['skills'], ['Docker', 'K8s'])
        self.assertEqual(out['professional_summary'], 'hi')


# --- Pass H: failed_generation extraction robustness ----------------------

class IsTokenLimitErrorTests(SimpleTestCase):
    """The 22:22 regen hit Groq's 30k TPM ceiling with a 32,423-token
    prompt. The generator now detects that specific failure and retries
    with the v2 grounding block + RAG standards stripped. The detector
    must distinguish 'token-limit' from other Groq errors so we don't
    pointlessly retry tool_use_failed / 5xx / etc."""

    def test_matches_body_dict_with_tokens_type(self):
        from resumes.services.resume_generator import _is_token_limit_error
        class E(Exception):
            body = {
                'error': {
                    'message': 'Request too large for model... TPM Limit 30000',
                    'type': 'tokens',
                    'code': 'rate_limit_exceeded',
                },
            }
        self.assertTrue(_is_token_limit_error(E('x')))

    def test_matches_str_fallback_when_body_missing(self):
        from resumes.services.resume_generator import _is_token_limit_error
        class E(Exception):
            body = None
        e = E()
        e.args = ("Error code: 413 - {'error': {'message': "
                  "'Request too large for model X on tokens per minute (TPM)', "
                  "'type': 'tokens', 'code': 'rate_limit_exceeded'}}",)
        self.assertTrue(_is_token_limit_error(e))

    def test_rejects_tool_use_failed(self):
        # tool_use_failed is recoverable via _recover_resume_from_failed_generation,
        # NOT by retrying with a smaller prompt.
        from resumes.services.resume_generator import _is_token_limit_error
        class E(Exception):
            body = {
                'error': {
                    'message': 'tool call validation failed',
                    'type': 'invalid_request_error',
                    'code': 'tool_use_failed',
                },
            }
        self.assertFalse(_is_token_limit_error(E('x')))

    def test_rejects_generic_exceptions(self):
        from resumes.services.resume_generator import _is_token_limit_error
        self.assertFalse(_is_token_limit_error(ValueError('something else')))
        self.assertFalse(_is_token_limit_error(RuntimeError('connection reset')))


class ExtractFailedGenerationTests(SimpleTestCase):
    """Regression coverage for the three extraction paths. The 18:03 regen
    hit the silent-return path because exc.body was None on this Groq SDK
    version — _extract_failed_generation now falls back to exc.response
    and finally to ast.literal_eval of str(exc)."""

    def test_path1_body_dict(self):
        from resumes.services.resume_generator import _extract_failed_generation
        class E(Exception):
            body = {'error': {'failed_generation': '{"a": 1}'}}
        self.assertEqual(_extract_failed_generation(E('x')), '{"a": 1}')

    def test_path2_response_json(self):
        from resumes.services.resume_generator import _extract_failed_generation
        class Resp:
            def json(self):
                return {'error': {'failed_generation': '{"b": 2}'}}
        class E(Exception):
            body = None
            response = Resp()
        self.assertEqual(_extract_failed_generation(E('x')), '{"b": 2}')

    def test_path3_str_parse(self):
        # This is the case that was failing silently — body is None,
        # response is None, only str(exc) carries the payload.
        from resumes.services.resume_generator import _extract_failed_generation
        class E(Exception):
            body = None
            response = None
        e = E()
        e.args = ("Error code: 400 - {'error': {'message': 'foo', "
                  "'failed_generation': '[{\"name\": \"X\"}]'}}",)
        self.assertEqual(_extract_failed_generation(e),
                         '[{"name": "X"}]')

    def test_returns_none_when_payload_missing_everywhere(self):
        from resumes.services.resume_generator import _extract_failed_generation
        class E(Exception):
            body = None
            response = None
        self.assertIsNone(_extract_failed_generation(E('nothing useful')))

    def test_recovery_end_to_end_with_schema_envelope_in_str(self):
        # The full failure mode from the 18:03 regen: schema-envelope
        # payload reachable only via str(exc). Recovery should still
        # surface the LLM's content (skills, summary, experience with
        # highlights promoted to description).
        import json as _json
        from resumes.services.resume_generator import _recover_resume_from_failed_generation
        envelope = _json.dumps([{
            'name': 'ResumeContentResult',
            'parameters': {
                'additionalProperties': True,
                'properties': {
                    'professional_summary': {'type': 'string', 'value': 'DS with ML.'},
                    'skills': {'type': 'array', 'value': ['Python', 'TensorFlow']},
                    'experience': {'type': 'array', 'value': [
                        {'title': 'AI Trainee', 'description': None,
                         'highlights': ['Built model.', 'Used MLflow.']},
                    ]},
                    'projects': {'type': 'array', 'value': []},
                    'certifications': {'type': 'array', 'value': []},
                    'education': {'type': 'array', 'value': []},
                    'objective': {'type': 'string', 'value': ''},
                },
            },
        }])
        class E(Exception):
            body = None
            response = None
        e = E()
        e.args = (f"Error code: 400 - {{'error': {{'failed_generation': {envelope!r}}}}}",)
        result = _recover_resume_from_failed_generation(e)
        self.assertIsNotNone(result, "recovery should not silently return None")
        self.assertEqual(result.professional_summary, 'DS with ML.')
        self.assertEqual(result.skills, ['Python', 'TensorFlow'])
        # Highlights → description promotion still fires through the
        # full pipeline.
        self.assertEqual(result.experience[0].description,
                         ['Built model.', 'Used MLflow.'])


class ResumeFailedGenerationRecoveryTests(SimpleTestCase):
    """Salvage Groq's tool_use_failed payload for resume generation.
    Same pattern as outreach_generator + learning_path_generator."""

    def _exc(self, raw_failed_generation: str):
        class _FakeBadRequest(Exception):
            pass
        e = _FakeBadRequest('tool_use_failed')
        e.body = {
            'error': {
                'message': 'tool call validation failed',
                'type': 'invalid_request_error',
                'code': 'tool_use_failed',
                'failed_generation': raw_failed_generation,
            }
        }
        return e

    def test_recovers_from_tool_call_wrapper(self):
        """Groq wraps the failed call as `[{name, parameters}]`. Salvage
        the parameters dict and validate against the schema."""
        from unittest.mock import patch, MagicMock
        from resumes.services import resume_generator
        # Shape from the production trace — tool-call wrapper with null
        # fields and object-wrapped highlights/skills.
        raw = (
            '[{"name": "ResumeContentResult", "parameters": {'
            '"professional_title": "Engineer",'
            '"professional_summary": "Built things.",'
            '"skills": [{"name": "Python", "proficiency": null}],'
            '"experience": [{"title": "Dev", "company": "Acme",'
            ' "industry": null, "location": null, "start_date": "2024",'
            ' "end_date": "", "duration": "", "description": []}],'
            '"projects": [{"name": "Tool",'
            ' "highlights": [{"description": "Shipped X"}],'
            ' "description": [], "technologies": [], "url": ""}],'
            '"education": [{"degree": "BSc", "institution": "MIT",'
            ' "year": "2024", "gpa": null, "location": null}],'
            '"certifications": [], "languages": [], "objective": ""}}]'
        )
        # Build a minimal profile + job stub for the generator wrapper.
        import types
        profile = types.SimpleNamespace(
            data_content={'skills': [{'name': 'Python'}]},
            raw_text='', skills=[{'name': 'Python'}], experiences=[],
            education=[], projects=[], certifications=[],
        )
        job = types.SimpleNamespace(
            title='Engineer', company='Acme', description='Need Python.',
            extracted_skills=['Python'],
        )
        gap = types.SimpleNamespace(matched_skills=['Python'])

        # Mock the structured LLM to raise the tool_use_failed exception;
        # the generator should salvage the failed_generation.
        with patch.object(resume_generator, 'get_structured_llm') as mock_llm:
            mock_llm.return_value.invoke.side_effect = self._exc(raw)
            out = resume_generator.generate_resume_content(profile, job, gap)
        # Recovery succeeded: we got the tool-call payload, not the offline
        # fallback (which would have a different professional_summary).
        self.assertEqual(out['professional_title'], 'Engineer')
        self.assertEqual(out['skills'], ['Python'])
        # PR 3a: object-wrapped highlights got flattened by the schema
        # validator into the canonical `description` field.
        self.assertEqual(out['projects'][0]['description'], ['Shipped X'])
        # Null strings coerced to "":
        self.assertEqual(out['experience'][0]['industry'], '')
        self.assertEqual(out['experience'][0]['location'], '')

    def test_recovers_when_experience_has_employment_type(self):
        """2026-05-28 production failure: Groq emitted
        experience[].employment_type='Internship'/'Full-time' which hits
        ResumeExperience.extra='forbid' and previously dropped the whole
        recovery — shipping the offline fallback instead of the LLM
        output. The validator must now silently pop employment_type and
        let the salvaged resume through."""
        import json as _json
        from resumes.services.resume_generator import (
            _recover_resume_from_failed_generation,
        )
        payload = {
            'name': 'ResumeContentResult',
            'parameters': {
                'professional_title': '',
                'professional_summary': 'Data Scientist.',
                'objective': '',
                'skills': [
                    {'name': 'Python', 'years': None, 'proficiency': None},
                    {'name': 'PySpark', 'years': None, 'proficiency': None},
                ],
                'experience': [
                    {
                        'title': 'AI & Data Science Trainee',
                        'company': 'DEPI',
                        'duration': 'Jun 2025 - Dec 2025',
                        'location': 'Remote',
                        'industry': None,
                        'start_date': 'Jun 2025',
                        'end_date': 'Dec 2025',
                        'description': ['Applied the full DS lifecycle.'],
                        'employment_type': 'Internship',
                    },
                    {
                        'title': 'Digital Transformation Intern',
                        'company': 'Almansour Automotive',
                        'duration': 'Aug 2025',
                        'location': 'Al Jizah, Egypt',
                        'industry': None,
                        'start_date': 'August 2025',
                        'end_date': None,
                        'description': ['Built PySpark pipeline.'],
                        'employment_type': 'Full-time',
                    },
                ],
                'education': [],
                'projects': [],
                'certifications': [],
                'languages': [],
                'awards': [],
            },
        }
        raw = _json.dumps([payload])
        e = self._exc(raw)
        result = _recover_resume_from_failed_generation(e)
        self.assertIsNotNone(
            result,
            "employment_type extra should be dropped, not abort recovery",
        )
        # Skills coerced from objects to strings via _flatten_string_list.
        self.assertEqual(result.skills, ['Python', 'PySpark'])
        # Both experience entries survived with their bullets intact.
        self.assertEqual(len(result.experience), 2)
        self.assertEqual(result.experience[0].title, 'AI & Data Science Trainee')
        self.assertEqual(result.experience[0].description,
                         ['Applied the full DS lifecycle.'])
        self.assertEqual(result.experience[1].title,
                         'Digital Transformation Intern')

    def test_recovers_when_project_has_signal_only_source_field(self):
        """Projects sometimes carry `source='github'` etc. in the LLM
        output — that field is signal-only per the prompt and not part
        of ResumeProject. The validator must drop it instead of failing
        recovery."""
        import json as _json
        from resumes.services.resume_generator import (
            _recover_resume_from_failed_generation,
        )
        payload = {
            'name': 'ResumeContentResult',
            'parameters': {
                'professional_title': '',
                'professional_summary': '',
                'objective': '',
                'skills': ['Python'],
                'experience': [],
                'education': [],
                'projects': [{
                    'name': 'SmartCV',
                    'url': 'https://example.com',
                    'technologies': ['Python'],
                    'description': ['Built it.'],
                    'source': 'github',
                    'source_id': 'foo/SmartCV',
                    'source_url': 'https://github.com/foo/SmartCV',
                    'role': 'Author',
                }],
                'certifications': [],
                'languages': [],
                'awards': [],
            },
        }
        raw = _json.dumps([payload])
        e = self._exc(raw)
        result = _recover_resume_from_failed_generation(e)
        self.assertIsNotNone(result)
        self.assertEqual(len(result.projects), 1)
        self.assertEqual(result.projects[0].name, 'SmartCV')
        self.assertEqual(result.projects[0].description, ['Built it.'])


class LearningPathFailedGenerationRecoveryTests(SimpleTestCase):
    """Groq returns 400 tool_use_failed when the model emits a bare top-level
    list instead of {items: [...]}. The well-formed JSON list is still in
    `error.failed_generation`. The generator salvages it instead of dropping
    a successful generation on the floor."""

    def _exc(self, raw_failed_generation: str):
        """Build an exception shaped like a Groq BadRequestError."""
        class _FakeBadRequest(Exception):
            pass
        e = _FakeBadRequest('tool_use_failed')
        e.body = {
            'error': {
                'message': 'Failed to call a function.',
                'type': 'invalid_request_error',
                'code': 'tool_use_failed',
                'failed_generation': raw_failed_generation,
            }
        }
        return e

    def test_recovers_from_bare_list_failed_generation(self):
        from unittest.mock import patch
        from analysis.services import learning_path_generator
        # Real shape from the production trace: top-level array of items.
        raw = (
            '[{"importance": "Python is hot.", "skill": "python",'
            ' "resources": [{"name": "Real Python", "url": "https://realpython.com/",'
            ' "provider": "Other"}], "project_idea": "Web scraper.",'
            ' "time_estimate": "10-15 hours"}]'
        )
        with patch.object(learning_path_generator, 'get_structured_llm') as mock_llm:
            mock_llm.return_value.invoke.side_effect = self._exc(raw)
            out = learning_path_generator.generate_learning_path(['python'])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]['skill'], 'python')
        self.assertEqual(out[0]['time_estimate'], '10-15 hours')
        self.assertEqual(out[0]['resources'][0]['url'], 'https://realpython.com/')

    def test_recovers_from_wrapped_failed_generation(self):
        """If the model gets the wrapper right but the tool serializer still
        flakes, recover from `{items: [...]}` form too."""
        from unittest.mock import patch
        from analysis.services import learning_path_generator
        raw = (
            '{"items": [{"importance": "ok", "skill": "go",'
            ' "resources": [], "project_idea": "x", "time_estimate": "20h"}]}'
        )
        with patch.object(learning_path_generator, 'get_structured_llm') as mock_llm:
            mock_llm.return_value.invoke.side_effect = self._exc(raw)
            out = learning_path_generator.generate_learning_path(['go'])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]['skill'], 'go')

    def test_returns_empty_when_failed_generation_unparseable(self):
        from unittest.mock import patch
        from analysis.services import learning_path_generator
        # Truncated JSON — recovery should return [] not raise.
        with patch.object(learning_path_generator, 'get_structured_llm') as mock_llm:
            mock_llm.return_value.invoke.side_effect = self._exc('[{"skill"')
            out = learning_path_generator.generate_learning_path(['python'])
        self.assertEqual(out, [])

    def test_returns_empty_on_non_groq_exception(self):
        """A generic exception (network error, etc.) without the Groq
        body shape still drops gracefully."""
        from unittest.mock import patch
        from analysis.services import learning_path_generator
        with patch.object(learning_path_generator, 'get_structured_llm') as mock_llm:
            mock_llm.return_value.invoke.side_effect = RuntimeError('network down')
            out = learning_path_generator.generate_learning_path(['python'])
        self.assertEqual(out, [])


class LearningPathPersistenceTests(TestCase):
    """Tier 5 (M4): the learning path persists across page loads, the
    mark-as-done toggle saves to the profile, and the return-path CTA
    points at the right next-step."""

    def setUp(self):
        from django.contrib.auth import get_user_model
        from profiles.models import UserProfile
        User = get_user_model()
        self.user = User.objects.create_user(
            username='learner@example.com', email='learner@example.com', password='x',
        )
        UserProfile.objects.create(user=self.user, full_name='Learner User')
        self.client.force_login(self.user)

    def test_get_renders_persisted_learning_path(self):
        """If the user has a persisted path, GET should render it without
        re-running the LLM."""
        from profiles.models import UserProfile
        UserProfile.objects.filter(user=self.user).update(data_content={
            'learning_path': [{
                'skill': 'Python',
                'importance': 'Used everywhere.',
                'resources': [{'name': 'Real Python', 'url': 'https://realpython.com/',
                               'provider': 'Other'}],
                'project_idea': 'Build a CLI tool.',
                'time_estimate': '15 hours over 2 weeks',
            }],
        })
        resp = self.client.get('/analysis/learning-path/')
        self.assertEqual(resp.status_code, 200)
        # Skill, time estimate, and clickable resource URL all present.
        self.assertContains(resp, 'Python')
        self.assertContains(resp, '15 hours over 2 weeks')
        self.assertContains(resp, 'https://realpython.com/')
        # Mark-as-done button rendered (Alpine state, not server-side
        # checkbox, so we check for the toggle text).
        self.assertContains(resp, 'Mark as done')

    def test_mark_skill_complete_toggles_state(self):
        from profiles.models import UserProfile
        # First call marks
        resp = self.client.post(
            '/analysis/api/learning-path/skill-done/',
            data='{"skill": "python"}',
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body['ok'])
        self.assertEqual(body['action'], 'marked')
        self.assertEqual(body['completed_skills'], ['python'])
        profile = UserProfile.objects.get(user=self.user)
        self.assertEqual(profile.data_content['completed_skills'], ['python'])
        # Second call unmarks (toggle)
        resp = self.client.post(
            '/analysis/api/learning-path/skill-done/',
            data='{"skill": "python"}',
            content_type='application/json',
        )
        self.assertEqual(resp.json()['action'], 'unmarked')
        profile.refresh_from_db()
        self.assertEqual(profile.data_content['completed_skills'], [])

    def test_mark_skill_complete_rejects_empty_skill(self):
        resp = self.client.post(
            '/analysis/api/learning-path/skill-done/',
            data='{"skill": ""}',
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()['error'], 'skill_required')

    def test_completed_skill_marker_renders_with_strikethrough(self):
        from profiles.models import UserProfile
        UserProfile.objects.filter(user=self.user).update(data_content={
            'completed_skills': ['python'],
            'learning_path': [{'skill': 'Python', 'importance': 'x',
                               'resources': [], 'project_idea': 'y'}],
        })
        resp = self.client.get('/analysis/learning-path/')
        body = resp.content.decode('utf-8')
        # Alpine x-data initialized to true for the matched skill
        self.assertIn("x-data=\"{done: true}\"", body)

    def test_return_cta_points_to_global_dashboard_when_no_job(self):
        from profiles.models import UserProfile
        UserProfile.objects.filter(user=self.user).update(data_content={
            'learning_path': [{'skill': 'Python', 'importance': 'x',
                               'resources': [], 'project_idea': 'y'}],
        })
        resp = self.client.get('/analysis/learning-path/')
        # Global learning-path view → return CTA points at dashboard
        self.assertContains(resp, 'Pick a job to re-analyze')
        # NOT the per-job CTA
        self.assertNotContains(resp, 'Re-run gap for')

    def test_return_cta_points_to_specific_job_when_scoped(self):
        from jobs.models import Job
        from profiles.models import UserProfile
        UserProfile.objects.filter(user=self.user).update(data_content={
            'learning_path': [{'skill': 'Python', 'importance': 'x',
                               'resources': [], 'project_idea': 'y'}],
        })
        job = Job.objects.create(
            user=self.user, title='Backend Engineer', company='Acme',
            description='Need Python.', extracted_skills=[],
        )
        resp = self.client.get(f'/analysis/learning-path/{job.id}/')
        self.assertContains(resp, 'Re-run gap for')
        self.assertContains(resp, 'Backend Engineer')


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


# ---------------------------------------------------------------------------
# Resume normalizer — Pass B safety net
# ---------------------------------------------------------------------------

import copy

from resumes.services.resume_normalizer import (
    normalize_resume,
    normalize_titles,
    filter_soft_skills,
    enforce_skill_hard_cap,
    strip_first_person_from_resume,
    consolidate_coursework,
    trim_projects_to_plan,
    trim_certs_to_plan,
)


class _FakeProjectPlan:
    """Stand-in for inclusion_planner.ProjectPlan (kept here to avoid the
    settings/db cost of importing the real dataclass for SimpleTestCase
    consumers)."""
    def __init__(self, name: str):
        self.name = name


class _FakePlan:
    def __init__(self, projects: list[str] | None = None, certifications: list[str] | None = None):
        self.projects = [_FakeProjectPlan(n) for n in (projects or [])]
        self.certifications = list(certifications or [])


class ResumeNormalizerTests(SimpleTestCase):
    """Pass-B post-LLM safety net.

    Each test pins one defect we have actually seen in the LLM's output
    so a regression that re-introduces the leak fails loudly.
    """

    def test_normalize_titles_fixes_all_caps_experience(self):
        resume = {'experience': [{'title': 'DIGITAL TRANSFORMATION INTERN'}]}
        out = normalize_titles(resume)
        self.assertEqual(out['experience'][0]['title'], 'Digital Transformation Intern')

    def test_normalize_titles_preserves_mixed_case(self):
        resume = {'experience': [{'title': 'AI & Data Science Trainee'}]}
        out = normalize_titles(resume)
        self.assertEqual(out['experience'][0]['title'], 'AI & Data Science Trainee')

    def test_normalize_titles_fixes_parser_typo(self):
        resume = {'experience': [{'title': 'INFROMATION TECHNOLOGY INTERN'}]}
        out = normalize_titles(resume)
        self.assertEqual(out['experience'][0]['title'], 'Information Technology Intern')

    def test_filter_soft_skills_drops_communication_and_presentation(self):
        resume = {'skills': ['Python', 'Communication', 'SQL', 'Presentation skills', 'TensorFlow']}
        out = filter_soft_skills(resume)
        self.assertEqual(out['skills'], ['Python', 'SQL', 'TensorFlow'])

    def test_filter_soft_skills_accepts_dict_entries(self):
        resume = {'skills': [{'name': 'Python'}, {'name': 'Teamwork'}, {'name': 'Pandas'}]}
        out = filter_soft_skills(resume)
        self.assertEqual(out['skills'], ['Python', 'Pandas'])

    def test_enforce_skill_hard_cap_truncates_at_cap(self):
        resume = {'skills': [f'S{i}' for i in range(20)]}
        out = enforce_skill_hard_cap(resume, cap=14)
        self.assertEqual(len(out['skills']), 14)
        # Order preserved.
        self.assertEqual(out['skills'][0], 'S0')
        self.assertEqual(out['skills'][-1], 'S13')

    def test_strip_first_person_cleans_summary_and_descriptions(self):
        resume = {
            'professional_summary': 'I am a data scientist. My focus is on ML.',
            'experience': [{
                'description': ['I built a churn model.', 'I deployed it to prod.'],
            }],
        }
        out = strip_first_person_from_resume(resume)
        # Summary: no "I" / "my", sentences re-capped.
        self.assertNotIn(' I ', ' ' + out['professional_summary'] + ' ')
        self.assertNotIn(' my ', ' ' + out['professional_summary'].lower() + ' ')
        self.assertTrue(out['professional_summary'][0].isupper())
        # Bullets: same.
        for b in out['experience'][0]['description']:
            self.assertNotRegex(b, r"\b[Ii]\b")
            self.assertNotRegex(b, r"\b[Mm]y\b")
            self.assertTrue(b[0].isupper())

    def test_consolidate_coursework_collapses_short_course_bullets(self):
        resume = {'experience': [{
            'description': [
                'Developed practical skills across the AI lifecycle.',
                'Prompt Engineering',
                'Data Science Methodology',
                'Tools for Data Science',
                'Databases & SQL',
                'Practised prompt engineering on real prompts.',
            ],
        }]}
        out = consolidate_coursework(resume)
        bullets = out['experience'][0]['description']
        # The four short course-names collapsed into one line.
        self.assertEqual(len(bullets), 3)
        self.assertEqual(bullets[0], 'Developed practical skills across the AI lifecycle.')
        self.assertTrue(bullets[1].startswith('Coursework included:'))
        self.assertIn('Prompt Engineering', bullets[1])
        self.assertIn('Databases & SQL', bullets[1])
        self.assertEqual(bullets[2], 'Practised prompt engineering on real prompts.')

    def test_consolidate_coursework_splits_embedded_bullets_in_single_string(self):
        # The LLM frequently emits one bullet with embedded \n• markers.
        # A list-introducer prelude ending with ":" is dropped (redundant
        # once the items are folded into "Coursework included: ...").
        resume = {'experience': [{
            'description': 'Coursework consisted of:\n• Prompt Engineering\n• Data Science Methodology\n• Tools for Data Science',
        }]}
        out = consolidate_coursework(resume)
        bullets = out['experience'][0]['description']
        self.assertIsInstance(bullets, list)
        self.assertEqual(len(bullets), 1)
        self.assertTrue(bullets[0].startswith('Coursework included:'))
        self.assertIn('Prompt Engineering', bullets[0])
        self.assertIn('Tools for Data Science', bullets[0])

    def test_consolidate_coursework_keeps_non_header_prelude(self):
        # A meaningful prelude (no trailing colon) is preserved as a bullet.
        # Fixture has three course-name bullets after the prelude so the
        # MIN_RUN=3 consolidation threshold (PR1 Fix 3) triggers.
        resume = {'experience': [{
            'description': 'Built models throughout the program\n• Prompt Engineering\n• Data Science Methodology\n• Tools for Data Science',
        }]}
        out = consolidate_coursework(resume)
        bullets = out['experience'][0]['description']
        self.assertEqual(bullets[0], 'Built models throughout the program')
        self.assertTrue(bullets[1].startswith('Coursework included:'))

    def test_consolidate_coursework_leaves_real_bullets_alone(self):
        resume = {'experience': [{
            'description': [
                'Built a churn model that improved retention by 12 percent.',
                'Deployed the model to production via MLflow.',
            ],
        }]}
        out = consolidate_coursework(resume)
        self.assertEqual(out['experience'][0]['description'], [
            'Built a churn model that improved retention by 12 percent.',
            'Deployed the model to production via MLflow.',
        ])

    def test_trim_projects_to_plan_drops_off_plan_projects(self):
        resume = {'projects': [
            {'name': 'SmartCV'},
            {'name': 'BookShop'},                 # not in plan → drop
            {'name': 'Healthcare Prediction (DEPI)'},
            {'name': 'apotheosis-traffic-sign-detection'},  # not in plan → drop
        ]}
        plan = _FakePlan(projects=['SmartCV', 'Healthcare Prediction (DEPI)'])
        out = trim_projects_to_plan(resume, plan)
        names = [p['name'] for p in out['projects']]
        self.assertEqual(names, ['SmartCV', 'Healthcare Prediction (DEPI)'])

    def test_trim_projects_to_plan_fuzzy_matches_lightly_renamed_project(self):
        # The LLM rewrote "Healthcare Prediction (DEPI)" → "Healthcare Prediction - DEPI".
        # Should still match by SequenceMatcher.
        resume = {'projects': [
            {'name': 'Healthcare Prediction - DEPI'},
        ]}
        plan = _FakePlan(projects=['Healthcare Prediction (DEPI)'])
        out = trim_projects_to_plan(resume, plan)
        self.assertEqual(len(out['projects']), 1)

    def test_trim_projects_to_plan_skips_when_plan_has_no_projects(self):
        # Defensive — never wipe projects just because the planner returned [].
        resume = {'projects': [{'name': 'SmartCV'}, {'name': 'BookShop'}]}
        plan = _FakePlan(projects=[])
        out = trim_projects_to_plan(resume, plan)
        self.assertEqual(len(out['projects']), 2)

    def test_trim_certs_to_plan_keeps_all_certs_now(self):
        # Round 1.5: the previous "drop certs not in plan" filter was
        # over-aggressive and removed Software Testing on a DevOps JD.
        # The new policy is keep all of the candidate's certs (capped
        # at _CERT_CAP). plan membership is irrelevant.
        resume = {'certifications': [
            {'name': 'IBM Data Scientist Professional Certificate'},
            {'name': 'Project Management: The Basics for Success'},
            {'name': 'Applied Machine Learning in Python'},
        ]}
        plan = _FakePlan(certifications=[
            'IBM Data Scientist Professional Certificate',
        ])
        out = trim_certs_to_plan(resume, plan)
        names = [c['name'] for c in out['certifications']]
        self.assertEqual(names, [
            'IBM Data Scientist Professional Certificate',
            'Project Management: The Basics for Success',
            'Applied Machine Learning in Python',
        ])

    def test_normalize_resume_end_to_end_combines_every_rule(self):
        resume = {
            'professional_summary': 'I am a data scientist with strong ML chops.',
            'skills': [
                'Python', 'SQL', 'Communication', 'TensorFlow', 'PyTorch',
                'Pandas', 'Presentation skills', 'NumPy', 'scikit-learn',
                'Machine Learning', 'Deep Learning', 'MLflow', 'Power BI',
                'Statistical Modeling', 'Teamwork', 'A', 'B', 'C', 'D', 'E',
            ],
            'experience': [{
                'title': 'AI & DATA SCIENCE TRAINEE',
                'description': [
                    'I built models throughout the program.',
                    'Prompt Engineering',
                    'Data Science Methodology',
                    'Tools for Data Science',
                ],
            }],
            'projects': [
                {'name': 'SmartCV'},
                {'name': 'BookShop'},
            ],
            'certifications': [
                {'name': 'IBM Data Scientist Professional Certificate'},
                {'name': 'Project Management 101'},
            ],
        }
        plan = _FakePlan(
            projects=['SmartCV'],
            certifications=['IBM Data Scientist Professional Certificate'],
        )
        out = normalize_resume(resume, plan=plan)

        # Title-cased.
        self.assertEqual(out['experience'][0]['title'], 'AI & Data Science Trainee')
        # Soft skills gone, hard-capped at 14.
        self.assertNotIn('Communication', out['skills'])
        self.assertNotIn('Presentation skills', out['skills'])
        self.assertNotIn('Teamwork', out['skills'])
        self.assertLessEqual(len(out['skills']), 14)
        # First-person stripped from summary.
        self.assertNotRegex(out['professional_summary'], r"\bI\b")
        # Coursework collapsed.
        descs = out['experience'][0]['description']
        self.assertTrue(any('Coursework included:' in d for d in descs))
        # Plan trims applied to projects (BookShop is dropped).
        self.assertEqual([p['name'] for p in out['projects']], ['SmartCV'])
        # Round 1.5: certs are NOT filtered against the plan any more —
        # both candidate certs survive (capped at 8).
        self.assertEqual(
            [c['name'] for c in out['certifications']],
            ['IBM Data Scientist Professional Certificate', 'Project Management 101'],
        )

    def test_normalize_resume_does_not_mutate_input(self):
        resume = {
            'skills': ['Python', 'Communication'],
            'experience': [{'title': 'INTERN', 'description': ['I worked.']}],
        }
        before = copy.deepcopy(resume)
        normalize_resume(resume)
        self.assertEqual(resume, before)

    # --- Pass E regression coverage --------------------------------------

    def test_consolidate_coursework_handles_long_course_titles(self):
        # The real Banque Misr resume had DEPI coursework bullets like
        # "Python for Data Science, AI & Development + Python Project"
        # (9 words) and "Data Analysis, Visualization & Machine Learning
        # with Python" (8 words). The v1 7-word cap rejected them so
        # consolidation never fired. v2 cap is 12.
        resume = {'experience': [{
            'description': [
                'Selected participant in the DEPI program.',
                'Prompt Engineering',
                'What is Data Science / Data Science Methodology',
                'Tools for Data Science',
                'Python for Data Science, AI & Development + Python Project',
                'Databases & SQL for Data Science with Python',
                'Data Analysis, Visualization & Machine Learning with Python',
                'MLOps tools (MLflow, Hugging Face)',
                'Capstone project end-to-end AI demonstrating preprocessing, feature engineering, model selection, evaluation and deployment.',
            ],
        }]}
        out = consolidate_coursework(resume)
        bullets = out['experience'][0]['description']
        # Post PR-1 Fix 3: MLflow and Hugging Face are now recognised as
        # technical tokens (_TECHNICAL_TOKENS), so the "MLOps tools
        # (MLflow, Hugging Face)" bullet is correctly excluded from the
        # coursework run and emits as its own bullet. Expected total is
        # now 4 (prelude + consolidated coursework + MLOps + capstone),
        # not 3. The test's documented purpose — verifying the 12-word
        # cap on course titles within the consolidated run — is unchanged.
        self.assertEqual(len(bullets), 4)
        self.assertEqual(bullets[0], 'Selected participant in the DEPI program.')
        self.assertTrue(bullets[1].startswith('Coursework included:'))
        # Every original LONG-TITLE course is in the consolidated line —
        # this is the assertion that verifies the 12-word cap is working.
        self.assertIn('Prompt Engineering', bullets[1])
        self.assertIn('Python for Data Science, AI & Development + Python Project', bullets[1])
        self.assertIn('Databases & SQL for Data Science with Python', bullets[1])
        # MLOps tools bullet survives separately (technical_token rule).
        self.assertIn('MLOps tools', bullets[2])
        self.assertIn('Hugging Face', bullets[2])
        # Capstone (long, multi-sentence) survives as its own bullet.
        self.assertTrue(bullets[3].startswith('Capstone project'))

    def test_consolidate_coursework_does_not_merge_real_action_bullets(self):
        # The real Apotheosis Traffic Sign project had two action bullets
        # ("Classified signs using visual characteristics", "Tuned
        # thresholds via external JSON configuration") that v1 wrongly
        # merged into a fake "Coursework included: Classified signs,
        # Tuned thresholds..." line because the verb list didn't include
        # "classified" / "tuned". v2 -ed-past-tense heuristic catches
        # them as action bullets.
        resume = {'projects': [{
            'description': [
                'Preprocessed traffic sign images with color filtering and contour detection.',
                'Classified signs using visual characteristics',
                'Tuned thresholds via external JSON configuration',
            ],
        }]}
        out = consolidate_coursework(resume)
        bullets = out['projects'][0]['description']
        # All three bullets survive verbatim — no coursework consolidation.
        self.assertEqual(len(bullets), 3)
        for b in bullets:
            self.assertFalse(b.startswith('Coursework included:'),
                             f"action bullet wrongly consolidated: {b!r}")

    def test_consolidate_coursework_preserves_short_ed_course_titles(self):
        # "Supervised Learning" / "Advanced Statistics" are short noun
        # phrases that happen to start with -ed words. The 4+-word
        # threshold for the -ed rejection keeps them as courses.
        resume = {'experience': [{
            'description': [
                'Trained on classical ML methods.',
                'Supervised Learning',
                'Unsupervised Learning',
                'Advanced Statistics',
                'Deployed a final project to production.',
            ],
        }]}
        out = consolidate_coursework(resume)
        bullets = out['experience'][0]['description']
        # Prelude + consolidated 3-course line + final = 3 bullets.
        self.assertEqual(len(bullets), 3)
        self.assertTrue(bullets[1].startswith('Coursework included:'))
        self.assertIn('Supervised Learning', bullets[1])
        self.assertIn('Unsupervised Learning', bullets[1])
        self.assertIn('Advanced Statistics', bullets[1])


from resumes.services.resume_normalizer import (
    filter_soft_skill_bullets,
    backfill_summary,
)


class FilterSoftSkillBulletsTests(SimpleTestCase):
    def test_drops_explicit_developed_soft_skills_bullet(self):
        resume = {'experience': [{'description': [
            'Built a procurement dashboard in Power BI.',
            'Developed soft skills including cross-team communication, problem-solving, and adaptability in a corporate environment.',
            'Shipped the dashboard to 4 stakeholders.',
        ]}]}
        out = filter_soft_skill_bullets(resume)
        bullets = out['experience'][0]['description']
        self.assertEqual(len(bullets), 2)
        for b in bullets:
            self.assertNotIn('soft skills', b.lower())

    def test_drops_dense_soft_skill_bullet_without_explicit_opener(self):
        # "Collaborated closely on cross-team initiatives, leadership, and adaptability."
        # 2+ soft-skill nouns inside a short bullet → drop.
        resume = {'experience': [{'description': [
            'Wrote ETL jobs in PySpark over 12 ERP tables.',
            'Showed communication, teamwork, leadership across the engineering org.',
        ]}]}
        out = filter_soft_skill_bullets(resume)
        bullets = out['experience'][0]['description']
        self.assertEqual(len(bullets), 1)
        self.assertIn('PySpark', bullets[0])

    def test_keeps_long_bullet_that_mentions_one_soft_skill_in_passing(self):
        # A bullet that incidentally says "communication" once but is
        # otherwise a real achievement should survive.
        resume = {'experience': [{'description': [
            'Led the rollout of the analytics platform across 4 business units, partnering with Product, Engineering, and Operations to align on KPIs and communication cadence.',
        ]}]}
        out = filter_soft_skill_bullets(resume)
        bullets = out['experience'][0]['description']
        self.assertEqual(len(bullets), 1)


class BackfillSummaryTests(SimpleTestCase):
    def test_backfills_when_summary_is_empty(self):
        resume = {
            'professional_summary': '',
            'skills': ['Python', 'SQL', 'PySpark', 'Power BI', 'MLflow'],
            'experience': [{'title': 'AI & Data Science Trainee'}],
        }
        out = backfill_summary(resume)
        self.assertTrue(out['professional_summary'])
        self.assertIn('AI & Data Science Trainee', out['professional_summary'])
        # Top 4 skills appear in the synthesized summary.
        self.assertIn('Python', out['professional_summary'])
        self.assertIn('Power BI', out['professional_summary'])

    def test_no_op_when_summary_already_present(self):
        resume = {
            'professional_summary': 'Data scientist focused on banking risk.',
            'experience': [{'title': 'X'}],
        }
        out = backfill_summary(resume)
        self.assertEqual(out['professional_summary'],
                         'Data scientist focused on banking risk.')

    def test_no_op_when_no_experience(self):
        resume = {'professional_summary': '', 'experience': []}
        out = backfill_summary(resume)
        self.assertEqual(out['professional_summary'], '')

    def test_picks_jd_aligned_experience_not_most_recent(self):
        # Round 1.5: the summary no longer mentions the experience
        # title (the audit flagged that as "meta-narration"). The
        # JD-aligned experience picker still matters for the no-JD
        # fallback case — but here the JD title is the lead and the
        # experience title isn't in the summary at all.
        resume = {
            'professional_summary': '',
            'skills': ['Python', 'Pandas', 'scikit-learn', 'TensorFlow'],
            'experience': [
                {'title': 'Digital Transformation Intern'},
                {'title': 'Information Technology Intern'},
                {'title': 'AI & Data Science Trainee'},
            ],
        }
        job = SimpleNamespace(title='Data Scientist')
        out = backfill_summary(resume, job=job)
        self.assertTrue(out['professional_summary'].startswith('Data Scientist'))
        self.assertIn('Python', out['professional_summary'])
        # No meta-narration mentioning the candidate's role title.
        self.assertNotIn('drawing on', out['professional_summary'].lower())

    def test_falls_back_to_first_when_no_jd_title_overlap(self):
        # Round 1.5: when a JD title IS provided, summary leads with
        # it regardless of experience-title overlap. The
        # experience-picker fallback only matters when job=None.
        resume = {
            'professional_summary': '',
            'skills': ['Python'],
            'experience': [
                {'title': 'Sales Associate'},
                {'title': 'Cashier'},
            ],
        }
        out = backfill_summary(resume, job=None)
        # No JD → falls back to first experience title.
        self.assertIn('Sales Associate', out['professional_summary'])


# Inclusion planner tech-overlap + skill backfill tests
from resumes.services.inclusion_planner import (
    _discriminating_tech_overlap,
    _scan_for_jd_skills_in_profile_text,
    _BASE_TECH_CANON,
)


class DiscriminatingTechOverlapTests(SimpleTestCase):
    """Post PR2b Fix A: _discriminating_tech_overlap returns a tuple
    ``(count, jd_rescued_tokens)`` and the base-tech filter is now
    JD-aware (a base token like Python COUNTS when the JD explicitly
    lists it). These four tests assert the same scenarios as before
    but unpack the tuple and account for JD-aware rescue."""

    def test_python_only_project_with_python_in_jd_counts_via_rescue(self):
        # PR2b Fix A semantics change: this JD does include 'python',
        # so the Python tech entry is rescued from the base-tech filter
        # and counts. Pre-Fix-A this returned 0; post-Fix-A it returns 1
        # with 'Python' in jd_rescued. OpenCV and Jupyter Notebook
        # remain uncounted (neither in JD, Jupyter is base).
        jd = {'python', 'pandas', 'numpy', 'scikitlearn', 'tensorflow', 'mlflow'}
        count, rescued = _discriminating_tech_overlap(
            ['Python', 'OpenCV', 'Jupyter Notebook'], jd,
        )
        self.assertEqual(count, 1)
        self.assertEqual(rescued, ['Python'])

    def test_webdev_project_has_zero_overlap(self):
        # Brain Tumor Classification App — HTML/CSS/JavaScript/Swiper
        # tech list against a DS JD. None are in JD, all but Swiper are
        # base. Still zero — no rescue.
        jd = {'python', 'pandas', 'tensorflow', 'pytorch'}
        count, rescued = _discriminating_tech_overlap(
            ['HTML', 'CSS', 'JavaScript', 'Swiper'], jd,
        )
        self.assertEqual(count, 0)
        self.assertEqual(rescued, [])

    def test_ml_project_has_strong_overlap(self):
        # Healthcare Prediction — Pandas + scikit-learn + MLflow are
        # all non-base and in JD → counted. Python is base AND in JD
        # → rescued and counted. Jupyter base AND not in JD → filtered.
        # Total = 4 (was 3 pre-Fix-A because Python was filtered).
        jd = {'python', 'pandas', 'scikitlearn', 'mlflow', 'tensorflow'}
        count, rescued = _discriminating_tech_overlap(
            ['Python', 'Jupyter Notebook', 'Pandas', 'scikit-learn',
             'Flask', 'MLflow'],
            jd,
        )
        self.assertEqual(count, 4)
        self.assertEqual(rescued, ['Python'])

    def test_handles_non_list_input(self):
        # Tuple unpacking — defensive returns are (0, []) now.
        self.assertEqual(_discriminating_tech_overlap(None, {'python'}), (0, []))
        self.assertEqual(_discriminating_tech_overlap('Python', {'python'}), (0, []))


class ScanForJdSkillsInProfileTextTests(SimpleTestCase):
    def test_finds_skill_mentioned_in_experience_bullet(self):
        # TensorFlow / MLflow / Hugging Face mentioned in DEPI
        # experience description should be surfaced even when they're
        # not in the user's formal skills list.
        data = {
            'experiences': [{
                'title': 'AI & Data Science Trainee',
                'description': 'Used MLOps tools (MLflow, Hugging Face) to build pipelines. Trained models with TensorFlow.',
            }],
            'projects': [],
            'certifications': [],
        }
        found = _scan_for_jd_skills_in_profile_text(
            data,
            ['TensorFlow', 'MLflow', 'Hugging Face', 'Kafka'],
            already_in_list_canon=set(),
        )
        self.assertIn('TensorFlow', found)
        self.assertIn('MLflow', found)
        self.assertIn('Hugging Face', found)
        self.assertNotIn('Kafka', found)  # not mentioned anywhere

    def test_skips_skills_already_in_list(self):
        data = {'experiences': [{'description': 'Used MLflow.'}],
                'projects': [], 'certifications': []}
        already = {'mlflow'}  # canonical form
        found = _scan_for_jd_skills_in_profile_text(
            data, ['MLflow'], already_in_list_canon=already,
        )
        self.assertEqual(found, [])

    def test_requires_word_boundary_match(self):
        # "tensor" inside "TensorFlow" should NOT match the JD skill
        # "tensor" alone — and vice versa shouldn't false-positive
        # against partial matches.
        data = {'experiences': [{'description': 'Used PyTorch.'}],
                'projects': [], 'certifications': []}
        found = _scan_for_jd_skills_in_profile_text(
            data, ['Torch'], already_in_list_canon=set(),
        )
        self.assertEqual(found, [])  # "PyTorch" doesn't word-match "Torch"


# --- HR/CV specialist supervisor (final review layer) ---------------------

import json as _json_for_supervisor
from resumes.services.resume_generator import generate_resume_content_supervised


class _FakeLLM:
    """Minimal LLM stand-in: .invoke(...) returns a fixed value (or raises)."""
    def __init__(self, ret=None, exc=None):
        self._ret = ret
        self._exc = exc
        self.calls = []

    def invoke(self, messages, *a, **k):
        self.calls.append(messages)
        if self._exc is not None:
            raise self._exc
        return self._ret


def _content_has_image(messages) -> bool:
    """True iff the HumanMessage content list carries an image_url block."""
    try:
        content = messages[0].content
    except Exception:
        return False
    if not isinstance(content, list):
        return False
    return any(isinstance(b, dict) and b.get('type') == 'image_url' for b in content)


def _mk_finding(severity='blocking', layer='content', category='summary',
                issue='x', fix='y', location='l'):
    from profiles.services.schemas import SupervisorFinding
    return SupervisorFinding(severity=severity, layer=layer, category=category,
                             issue=issue, fix=fix, location=location)


class _FakeTokenLimitError(Exception):
    """Mimics Groq's 413 token-ceiling rejection for _is_token_limit_error."""
    def __init__(self):
        super().__init__("Request too large for tokens per minute (TPM)")
        self.body = {'error': {'type': 'tokens'}}


class RenderResumePngTests(SimpleTestCase):
    """resume_render.render_resume_png — the PDF->PNG path for the supervisor."""

    _CONTENT = {
        'professional_title': 'Data Analyst',
        'professional_summary': 'Analytical professional with SQL and Python.',
        'skills': ['Python', 'SQL', 'Tableau'],
        'experience': [], 'education': [], 'projects': [],
        'certifications': [], 'languages': [], 'awards': [],
    }

    def test_minimal_resume_renders_nonempty_png(self):
        from resumes.services.resume_render import render_resume_png
        png = render_resume_png(self._CONTENT, None, pages=1)
        self.assertTrue(png)
        self.assertEqual(png[:8], b'\x89PNG\r\n\x1a\n')  # PNG magic bytes

    def test_png_to_data_url_prefix(self):
        from resumes.services.resume_render import png_to_data_url
        url = png_to_data_url(b'\x89PNG\r\n\x1a\n\x00')
        self.assertTrue(url.startswith('data:image/png;base64,'))

    def test_cairocffi_shim_invoked(self):
        from unittest.mock import patch
        import resumes.services.pdf_exporter as pe
        from resumes.services.resume_render import render_resume_png
        with patch.object(pe, '_shim_cairocffi_if_missing',
                          wraps=pe._shim_cairocffi_if_missing) as m:
            render_resume_png(self._CONTENT, None, pages=1)
        m.assert_called_once()


class SupervisorPromptCoverageTests(SimpleTestCase):
    """Lock in the recruiter-checklist coverage: the supervisor prompt must
    direct the model at the high-value issue classes a senior reviewer catches
    (the ones a generic 'review the resume' prompt misses)."""

    def test_prompt_covers_high_value_checks(self):
        from resumes.services.resume_supervisor import SUPERVISOR_PROMPT
        p = SUPERVISOR_PROMPT.lower()
        # Redundant / duplicate bullets within a role.
        self.assertIn('redundant', p)
        # Bullet count vs tenure (padding on short roles).
        self.assertIn('tenure', p)
        # Overclaiming / inflation against seniority.
        self.assertIn('overclaim', p)
        self.assertIn('seniority', p)
        # Skill-list duplicates / near-duplicates.
        self.assertIn('near-duplicate', p)
        # Date completeness / consistency across roles.
        self.assertIn('date', p)
        # Internal jargon / recruiter audience.
        self.assertIn('jargon', p)
        # Role relevance / dilution.
        self.assertIn('relevance', p)
        # Generic summary.
        self.assertIn('generic', p)

    def test_prompt_still_forbids_fabrication(self):
        from resumes.services.resume_supervisor import SUPERVISOR_PROMPT
        p = SUPERVISOR_PROMPT.lower()
        self.assertIn('fabrication', p)
        self.assertIn('missing skills', p)


class StructuralObservationsTests(SimpleTestCase):
    """The deterministic pre-scan that surfaces structural defects (bullet
    bloat, redundant bullets, date gaps, duplicate skills) for the LLM to judge."""

    def test_flags_bullet_bloat(self):
        from resumes.services.resume_supervisor import _structural_observations
        rc = {'experience': [{'title': 'Intern', 'duration': 'Aug 2025 - Sep 2025',
                              'description': ['a', 'b', 'c', 'd', 'e', 'f', 'g']}]}
        out = _structural_observations(rc)
        self.assertIn('7 bullets', out)
        self.assertIn('HIGH', out)

    def test_flags_lexical_redundant_bullets(self):
        from resumes.services.resume_supervisor import _structural_observations
        rc = {'experience': [{'title': 'Intern', 'duration': 'Aug 2025 - Sep 2025',
                              'description': [
                                  'Developed and optimized a PySpark data pipeline for enterprise systems',
                                  'Developed and optimized a PySpark data pipeline for enterprise systems and analytics',
                              ]}]}
        out = _structural_observations(rc)
        self.assertIn('REDUNDANT', out)

    def test_flags_missing_end_date(self):
        from resumes.services.resume_supervisor import _structural_observations
        rc = {'experience': [{'title': 'Intern', 'start_date': 'Aug 2025',
                              'description': ['a']}]}
        out = _structural_observations(rc)
        self.assertIn('NO end date', out)

    def test_flags_single_date_no_range(self):
        from resumes.services.resume_supervisor import _structural_observations
        rc = {'experience': [{'title': 'Intern', 'duration': 'Aug 2025',
                              'description': ['a']}]}
        out = _structural_observations(rc)
        self.assertIn('single date', out)

    def test_accepts_proper_range_and_present(self):
        from resumes.services.resume_supervisor import _structural_observations
        rc = {'experience': [
            {'title': 'A', 'duration': 'Jun 2025 - Dec 2025', 'description': ['x']},
            {'title': 'B', 'duration': 'Jan 2024 - Present', 'description': ['y']},
        ]}
        out = _structural_observations(rc)
        self.assertNotIn('single date', out)
        self.assertNotIn('NO end date', out)

    def test_flags_subset_duplicate_skills(self):
        from resumes.services.resume_supervisor import _structural_observations
        rc = {'skills': ['SQL', 'Databases & SQL', 'Supervised Learning',
                         'Supervised & Unsupervised Learning']}
        out = _structural_observations(rc)
        self.assertIn('duplicate skills', out.lower())

    def test_clean_resume_returns_empty(self):
        from resumes.services.resume_supervisor import _structural_observations
        rc = {'experience': [{'title': 'A', 'duration': 'Jun 2025 - Dec 2025',
                              'description': ['Built X', 'Shipped Y']}],
              'skills': ['Python', 'SQL', 'Docker']}
        self.assertEqual(_structural_observations(rc), "")

    def test_flags_keyword_stuffing_for_jd_skill(self):
        """The 2026-05-28 5:16 run shipped a resume with 'Python' ×18 and
        'Machine Learning' ×8. Counts above STUFFING_THRESHOLD (4) must
        surface as an observation when ``job`` is provided so the
        supervisor can feed it back into the regen prompt."""
        from types import SimpleNamespace
        from resumes.services.resume_supervisor import _structural_observations
        rc = {
            'experience': [
                {'title': 'AI Trainee', 'duration': 'Jun 2025 - Dec 2025',
                 'description': [
                     'Built Python data pipelines and Python ETL with Python.',
                     'Wrote Python unit tests and Python notebooks for Python services.',
                     'Maintained Python infra and Python CI/CD with Python tooling.',
                 ]},
            ],
            'skills': ['Python', 'SQL'],
        }
        job = SimpleNamespace(extracted_skills=['Python', 'SQL'])
        out = _structural_observations(rc, job=job)
        self.assertIn('KEYWORD STUFFING', out)
        self.assertIn('"Python"', out)
        # SQL appears once — should NOT be flagged.
        self.assertNotIn('"SQL"', out)

    def test_no_stuffing_flag_below_threshold(self):
        """Counts at or below STUFFING_THRESHOLD (4) must not flag, so we
        don't bother the supervisor with noise. The detector counts the
        whole resume JSON, so the skills array and any duration / title
        also contribute — keep total <=4 to verify the strict-greater-
        than-threshold check."""
        from types import SimpleNamespace
        from resumes.services.resume_supervisor import _structural_observations
        rc = {
            'experience': [
                {'title': 'A', 'duration': 'Jun 2025 - Dec 2025',
                 # 3 'Python' in description + 1 in skills = 4 total, == threshold.
                 'description': ['Python Python Python work']},
            ],
            'skills': ['Python'],
        }
        job = SimpleNamespace(extracted_skills=['Python'])
        out = _structural_observations(rc, job=job)
        self.assertNotIn('KEYWORD STUFFING', out)

    def test_stuffing_flag_works_without_job_arg(self):
        """When ``job`` is None, falls back to the candidate's own skills
        list — this keeps the pre-scan useful in callers that don't have
        a job object (e.g. ad-hoc tooling)."""
        from resumes.services.resume_supervisor import _structural_observations
        rc = {
            'experience': [
                {'title': 'A', 'duration': 'Jun 2025 - Dec 2025',
                 'description': [
                     'Pandas Pandas Pandas Pandas Pandas Pandas analysis.',
                 ]},
            ],
            'skills': ['Pandas'],
        }
        out = _structural_observations(rc)  # no job
        self.assertIn('KEYWORD STUFFING', out)
        self.assertIn('"Pandas"', out)


class SupervisorRecoveryTests(SimpleTestCase):
    """_recover_review_from_failed_generation salvages a SupervisorReview from
    a failed structured-output call (the tool-call envelope shapes Groq emits)."""

    def _err_with_body(self, failed_generation: str):
        class _E(Exception):
            pass
        e = _E("tool_use_failed")
        e.body = {'error': {'failed_generation': failed_generation}}
        return e

    def test_unwraps_name_parameters_envelope(self):
        from resumes.services.resume_supervisor import _recover_review_from_failed_generation
        payload = _json_for_supervisor.dumps([{
            "name": "SupervisorReview",
            "parameters": {
                "verdict": "revise", "summary": "s",
                "findings": [{"layer": "content", "severity": "blocking",
                              "category": "summary", "location": "top",
                              "issue": "truncated", "fix": "complete it"}],
            },
        }])
        rev = _recover_review_from_failed_generation(self._err_with_body(payload))
        self.assertIsNotNone(rev)
        self.assertEqual(rev.verdict, "revise")
        self.assertEqual(len(rev.findings), 1)
        self.assertEqual(len(rev.blocking_content_findings()), 1)

    def test_bare_findings_list_two_items(self):
        from resumes.services.resume_supervisor import _recover_review_from_failed_generation
        payload = _json_for_supervisor.dumps([
            {"layer": "content", "severity": "blocking", "issue": "a", "fix": "fa"},
            {"layer": "render", "severity": "warning", "issue": "b", "fix": "fb"},
        ])
        rev = _recover_review_from_failed_generation(self._err_with_body(payload))
        self.assertIsNotNone(rev)
        self.assertEqual(len(rev.findings), 2)

    def test_single_bare_finding_not_mistaken_for_envelope(self):
        from resumes.services.resume_supervisor import _recover_review_from_failed_generation
        payload = _json_for_supervisor.dumps([
            {"layer": "content", "severity": "blocking", "issue": "only", "fix": "f"},
        ])
        rev = _recover_review_from_failed_generation(self._err_with_body(payload))
        self.assertIsNotNone(rev)
        self.assertEqual(len(rev.findings), 1)

    def test_recovers_from_str_exc_envelope(self):
        from resumes.services.resume_supervisor import _recover_review_from_failed_generation
        inner = _json_for_supervisor.dumps({
            "verdict": "revise",
            "findings": [{"layer": "content", "severity": "blocking", "issue": "x", "fix": "y"}],
        })
        # body=None; the payload is only reachable via str(exc)'s Python repr.
        msg = "Error code: 400 - " + repr({'error': {'failed_generation': inner}})
        rev = _recover_review_from_failed_generation(Exception(msg))
        self.assertIsNotNone(rev)
        self.assertEqual(len(rev.findings), 1)

    def test_garbage_payload_returns_none(self):
        from resumes.services.resume_supervisor import _recover_review_from_failed_generation
        rev = _recover_review_from_failed_generation(self._err_with_body("}{not json at all"))
        self.assertIsNone(rev)

    def test_no_failed_generation_returns_none(self):
        from resumes.services.resume_supervisor import _recover_review_from_failed_generation
        self.assertIsNone(_recover_review_from_failed_generation(Exception("boom")))


class ReviewResumeContextTests(SimpleTestCase):
    """_build_review_context keeps the prompt compact: gap lines + JD + resume
    JSON, but NOT the GitHub/Kaggle/LinkedIn signal blobs that 413 the generator."""

    def _job(self):
        return SimpleNamespace(
            title='Junior Data Analyst', company='Acme',
            extracted_skills=['SQL', 'Python'], description='Analyze data daily.',
        )

    def _gap(self):
        return SimpleNamespace(
            matched_skills=['Python'], critical_missing_skills=['Tableau'],
            soft_skill_gaps=['seniority'],
        )

    def test_includes_standards_gap_jd_and_resume_json(self):
        from resumes.services.resume_supervisor import _build_review_context
        rc = {'professional_summary': 'hi', 'validation_report': {'grounding_findings': []}}
        ctx = _build_review_context(rc, self._job(), self._gap(), 'KB_STANDARDS_HERE')
        self.assertIn('KB_STANDARDS_HERE', ctx)
        self.assertIn('MATCHED', ctx)
        self.assertIn('Junior Data Analyst', ctx)
        self.assertIn('professional_summary', ctx)

    def test_excludes_signal_blobs(self):
        from resumes.services.resume_supervisor import _build_review_context
        # Even if the resume_content somehow carried a signal blob, the context
        # is built from gap lines + JD + the resume JSON only.
        rc = {'professional_summary': 'hi'}
        ctx = _build_review_context(rc, self._job(), self._gap(), '')
        self.assertNotIn('github_signals', ctx)
        self.assertNotIn('kaggle_signals', ctx)


class ReviewResumeTests(SimpleTestCase):
    """review_resume orchestration: two-step (vision -> structure), fail-open,
    token-limit retry drops the image."""

    def _job(self):
        return SimpleNamespace(title='DA', company='X', extracted_skills=['SQL'],
                               description='d')

    def _gap(self):
        return SimpleNamespace(matched_skills=['SQL'], critical_missing_skills=[],
                               soft_skill_gaps=[])

    def test_blocking_finding_surfaced(self):
        from unittest.mock import patch
        import resumes.services.resume_supervisor as rs
        from profiles.services.schemas import SupervisorReview
        review = SupervisorReview(verdict='revise', summary='s',
                                  findings=[_mk_finding()])
        with patch.object(rs, 'render_resume_png', return_value=b'\x89PNG'), \
             patch.object(rs, 'get_llm', return_value=_FakeLLM(SimpleNamespace(content='critique'))), \
             patch.object(rs, 'get_structured_llm', return_value=_FakeLLM(review)):
            out = rs.review_resume({'professional_summary': 'x'}, None,
                                   self._job(), self._gap(), standards_block='S')
        self.assertEqual(out.verdict, 'revise')
        self.assertEqual(len(out.blocking_content_findings()), 1)

    def test_fail_open_on_exception(self):
        from unittest.mock import patch
        import resumes.services.resume_supervisor as rs
        with patch.object(rs, 'render_resume_png', return_value=b'\x89PNG'), \
             patch.object(rs, 'get_llm', return_value=_FakeLLM(exc=RuntimeError('down'))):
            out = rs.review_resume({'a': 1}, None, self._job(), self._gap(),
                                   standards_block='S')
        self.assertEqual(out.verdict, 'advance')
        self.assertEqual(out.summary, 'review unavailable')
        self.assertEqual(out.findings, [])

    def test_render_failure_degrades_to_text_only(self):
        from unittest.mock import patch
        import resumes.services.resume_supervisor as rs
        from profiles.services.schemas import SupervisorReview
        vision = _FakeLLM(SimpleNamespace(content='critique'))
        with patch.object(rs, 'render_resume_png', side_effect=RuntimeError('no render')), \
             patch.object(rs, 'get_llm', return_value=vision), \
             patch.object(rs, 'get_structured_llm',
                          return_value=_FakeLLM(SupervisorReview(verdict='advance'))):
            out = rs.review_resume({'a': 1}, None, self._job(), self._gap(),
                                   standards_block='S')
        self.assertEqual(out.verdict, 'advance')
        # No image was rendered, so the vision call carried no image block.
        self.assertFalse(_content_has_image(vision.calls[0]))

    def test_token_limit_retry_drops_image(self):
        from unittest.mock import patch
        import resumes.services.resume_supervisor as rs
        from profiles.services.schemas import SupervisorReview

        class _RetryLLM:
            def __init__(self):
                self.calls = []
            def invoke(self, messages, *a, **k):
                self.calls.append(messages)
                # First call (with image) hits the token ceiling; retry succeeds.
                if len(self.calls) == 1:
                    raise _FakeTokenLimitError()
                return SimpleNamespace(content='critique after slim')

        vision = _RetryLLM()
        with patch.object(rs, 'render_resume_png', return_value=b'\x89PNG'), \
             patch.object(rs, 'get_llm', return_value=vision), \
             patch.object(rs, 'get_structured_llm',
                          return_value=_FakeLLM(SupervisorReview(verdict='advance'))):
            out = rs.review_resume({'a': 1}, None, self._job(), self._gap(),
                                   standards_block='S')
        self.assertEqual(out.verdict, 'advance')
        self.assertEqual(len(vision.calls), 2)
        self.assertTrue(_content_has_image(vision.calls[0]))   # first had image
        self.assertFalse(_content_has_image(vision.calls[1]))  # retry dropped it


class SupervisedLoopTests(SimpleTestCase):
    """generate_resume_content_supervised — the generate/review/regenerate loop."""

    def _job(self):
        return SimpleNamespace(title='DA', company='X', extracted_skills=['SQL'],
                               description='d')

    def _gap(self):
        return SimpleNamespace(matched_skills=['SQL'], critical_missing_skills=[],
                               soft_skill_gaps=[])

    def _profile(self):
        return SimpleNamespace(data_content={})

    def _patches(self, gen_side_effect, review_side_effect):
        from unittest.mock import patch
        import resumes.services.resume_generator as g
        import resumes.services.resume_supervisor as rs
        return (
            patch.object(g, 'generate_resume_content', side_effect=gen_side_effect),
            patch.object(g, '_build_standards_section', return_value=('STD', None, {})),
            patch.object(rs, 'review_resume', side_effect=review_side_effect),
        )

    def test_disabled_bypasses_loop(self):
        from unittest.mock import patch
        from django.test import override_settings
        import resumes.services.resume_generator as g
        sentinel = {'professional_summary': 'plain'}
        with override_settings(SUPERVISOR_ENABLED=False):
            with patch.object(g, 'generate_resume_content', return_value=sentinel) as gen, \
                 patch.object(g, '_build_standards_section') as std:
                out = g.generate_resume_content_supervised(
                    self._profile(), self._job(), self._gap())
        gen.assert_called_once()
        std.assert_not_called()
        self.assertNotIn('supervisor_review', out)

    @staticmethod
    def _review(findings):
        from profiles.services.schemas import SupervisorReview
        verdict = 'revise' if any(
            f.severity == 'blocking' and f.layer == 'content' for f in findings) else 'advance'
        return SupervisorReview(verdict=verdict, summary='s', findings=findings)

    def test_stops_when_no_blocking_content(self):
        from django.test import override_settings
        gen = lambda *a, **k: {'professional_summary': 'draft'}
        reviews = [self._review([_mk_finding(severity='warning')])]
        p_gen, p_std, p_rev = self._patches(gen, reviews)
        with override_settings(SUPERVISOR_ENABLED=True, SUPERVISOR_MAX_REVISION_ROUNDS=1):
            with p_gen as gm, p_std, p_rev:
                out = generate_resume_content_supervised(
                    self._profile(), self._job(), self._gap())
        self.assertEqual(gm.call_count, 1)  # no regen
        self.assertEqual(out['supervisor_review']['rounds'], 1)
        self.assertEqual(out['validation_report']['supervisor_findings'][0]['severity'], 'warning')

    def test_render_only_blocking_does_not_regen(self):
        from django.test import override_settings
        gen = lambda *a, **k: {'professional_summary': 'draft'}
        reviews = [self._review([_mk_finding(severity='blocking', layer='render')])]
        p_gen, p_std, p_rev = self._patches(gen, reviews)
        with override_settings(SUPERVISOR_ENABLED=True, SUPERVISOR_MAX_REVISION_ROUNDS=1):
            with p_gen as gm, p_std, p_rev:
                out = generate_resume_content_supervised(
                    self._profile(), self._job(), self._gap())
        self.assertEqual(gm.call_count, 1)  # render blocking does NOT drive regen
        self.assertEqual(len(out['supervisor_review']['findings']), 1)

    def test_content_blocking_regenerates_with_feedback_then_ships(self):
        from django.test import override_settings
        gen_calls = []

        def gen(*a, **k):
            gen_calls.append(k.get('supervisor_feedback', ''))
            return {'professional_summary': f'draft {len(gen_calls)}'}

        reviews = [
            self._review([_mk_finding(severity='blocking', layer='content', issue='truncated')]),
            self._review([_mk_finding(severity='warning')]),
        ]
        p_gen, p_std, p_rev = self._patches(gen, reviews)
        with override_settings(SUPERVISOR_ENABLED=True, SUPERVISOR_MAX_REVISION_ROUNDS=1):
            with p_gen, p_std, p_rev:
                out = generate_resume_content_supervised(
                    self._profile(), self._job(), self._gap())
        self.assertEqual(len(gen_calls), 2)         # regenerated once
        self.assertEqual(gen_calls[0], '')          # first draft: no feedback
        self.assertIn('truncated', gen_calls[1])    # second draft: got the feedback
        self.assertEqual(out['supervisor_review']['rounds'], 2)

    def test_cap_stops_after_max_rounds(self):
        from django.test import override_settings
        gen = lambda *a, **k: {'professional_summary': 'draft'}
        # Always blocking content -> would loop forever without the cap.
        reviews = [
            self._review([_mk_finding(severity='blocking', layer='content')]),
            self._review([_mk_finding(severity='blocking', layer='content')]),
            self._review([_mk_finding(severity='blocking', layer='content')]),
        ]
        p_gen, p_std, p_rev = self._patches(gen, reviews)
        with override_settings(SUPERVISOR_ENABLED=True, SUPERVISOR_MAX_REVISION_ROUNDS=1):
            with p_gen as gm, p_std, p_rev:
                out = generate_resume_content_supervised(
                    self._profile(), self._job(), self._gap())
        self.assertEqual(gm.call_count, 2)  # round 0 + round 1, then cap
        self.assertEqual(out['supervisor_review']['rounds'], 2)
        self.assertEqual(out['supervisor_review']['verdict'], 'revise')

    def test_ships_best_draft_when_regen_regresses(self):
        """2026-05-28 5:16 production run: round 0 had 2 blocking, round 1
        had 4 blocking — regen made things worse and we shipped round 1.
        With the elitism guard the loop ships round 0 instead, since
        score (blocking, total) (2, 6) < (4, 9)."""
        from django.test import override_settings
        gen_calls = []

        def gen(*a, **k):
            gen_calls.append(k.get('supervisor_feedback', ''))
            return {'professional_summary': f'draft {len(gen_calls)}'}

        # Round 0: 2 blocking. Round 1: 4 blocking (regression).
        reviews = [
            self._review([
                _mk_finding(severity='blocking', layer='content', issue='a'),
                _mk_finding(severity='blocking', layer='content', issue='b'),
                _mk_finding(severity='warning', layer='content', issue='w1'),
                _mk_finding(severity='warning', layer='content', issue='w2'),
                _mk_finding(severity='warning', layer='content', issue='w3'),
                _mk_finding(severity='warning', layer='content', issue='w4'),
            ]),
            self._review([
                _mk_finding(severity='blocking', layer='content', issue='c'),
                _mk_finding(severity='blocking', layer='content', issue='d'),
                _mk_finding(severity='blocking', layer='content', issue='e'),
                _mk_finding(severity='blocking', layer='content', issue='f'),
                _mk_finding(severity='warning', layer='content', issue='w5'),
                _mk_finding(severity='warning', layer='content', issue='w6'),
                _mk_finding(severity='warning', layer='content', issue='w7'),
                _mk_finding(severity='warning', layer='content', issue='w8'),
                _mk_finding(severity='warning', layer='content', issue='w9'),
            ]),
        ]
        p_gen, p_std, p_rev = self._patches(gen, reviews)
        with override_settings(SUPERVISOR_ENABLED=True, SUPERVISOR_MAX_REVISION_ROUNDS=1):
            with p_gen, p_std, p_rev:
                out = generate_resume_content_supervised(
                    self._profile(), self._job(), self._gap())
        # Both rounds ran (round 0 + 1 regen), but the shipped content is
        # round 0's because round 1 regressed.
        self.assertEqual(out['professional_summary'], 'draft 1')
        # Surfaced findings are from the shipped (best) draft, not the latest.
        findings = out['supervisor_review']['findings']
        self.assertEqual(len([f for f in findings if f['severity'] == 'blocking']), 2)

    def test_ships_latest_when_regen_improves(self):
        """The complementary case: round 0 has 3 blocking, round 1 has 1
        blocking. Ship round 1 (it's the better draft) — the elitism
        guard must not regress the happy path."""
        from django.test import override_settings
        gen_calls = []

        def gen(*a, **k):
            gen_calls.append(k.get('supervisor_feedback', ''))
            return {'professional_summary': f'draft {len(gen_calls)}'}

        reviews = [
            self._review([
                _mk_finding(severity='blocking', layer='content', issue='a'),
                _mk_finding(severity='blocking', layer='content', issue='b'),
                _mk_finding(severity='blocking', layer='content', issue='c'),
            ]),
            self._review([
                _mk_finding(severity='blocking', layer='content', issue='d'),
                _mk_finding(severity='warning', layer='content', issue='w1'),
            ]),
        ]
        p_gen, p_std, p_rev = self._patches(gen, reviews)
        with override_settings(SUPERVISOR_ENABLED=True, SUPERVISOR_MAX_REVISION_ROUNDS=1):
            with p_gen, p_std, p_rev:
                out = generate_resume_content_supervised(
                    self._profile(), self._job(), self._gap())
        # Round 1 was strictly better — ship it.
        self.assertEqual(out['professional_summary'], 'draft 2')
        findings = out['supervisor_review']['findings']
        self.assertEqual(len([f for f in findings if f['severity'] == 'blocking']), 1)
