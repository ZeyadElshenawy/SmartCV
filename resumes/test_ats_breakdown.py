"""Slice 1 — read-only ATS breakdown panel.

Covers the resume-aware helper (`breakdown_for_resume`), the idempotent
`refresh_ats_score`, the `score_reconciles` clamp flag, the read-only GET
endpoint, and the `base` key just added to the deterministic scorer.

Run: ``python manage.py test resumes`` (Django TestCase — these resolve
resume.gap_analysis.job over the ORM and exercise the view via the test
Client, so they belong in the app suite, not pytest tests/).
"""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from jobs.models import Job
from analysis.models import GapAnalysis
from resumes.models import GeneratedResume
from resumes.services.scoring import compute_ats_breakdown
from resumes.services.ats_breakdown import (
    breakdown_for_resume,
    refresh_ats_score,
    score_reconciles,
    apply_edit_to_content,
    score_with_edit,
)
from resumes.services.ats_cards import build_ats_cards, _ats_card_id

# Keys the panel/endpoint depend on. `base` is the Slice-1 addition.
ATS_BREAKDOWN_KEYS = {
    "score", "raw_score", "base", "matched_count", "total_count",
    "in_context_count", "in_context_bonus", "stuffed_skills",
    "stuffing_penalty", "keyword_counts",
    "must_have", "nice_to_have", "in_context", "stuffing",
}

# A general fixture (not tuned to any real profile): tiered job + a résumé that
# matches some must/nice skills, demonstrates two in bullets (in-context bonus),
# and stuffs one (Python) past the threshold.
RICH_SKILLS = ["Python", "Django", "PostgreSQL", "Docker", "Kubernetes", "GraphQL"]
RICH_TIERS = {
    "must_have": ["Python", "Django", "Docker"],
    "nice_to_have": ["PostgreSQL", "Kubernetes", "GraphQL"],
}
RICH_CONTENT = {
    "professional_summary": "Backend engineer who built REST APIs in Python.",
    "skills": ["Python", "Django", "PostgreSQL"],
    "experience": [{
        "title": "Engineer",
        "description": [
            "Built APIs in Python. Python Python Python Python Python services everywhere.",
            "Used Django and PostgreSQL daily.",
        ],
    }],
    "projects": [{
        "name": "x",
        "description": ["REST API integration with Python and Django"],
    }],
}


def _user(email):
    return get_user_model().objects.create_user(
        username=email, email=email, password="x",
    )


def _make_chain(user, *, skills=None, tiers=None, content=None, ats_score=0.0):
    job = Job.objects.create(
        user=user, title="Engineer", description="JD text.",
        extracted_skills=skills or [],
        extracted_skills_tiers=tiers or {},
    )
    gap = GapAnalysis.objects.create(user=user, job=job)
    resume = GeneratedResume.objects.create(
        gap_analysis=gap, content=content or {}, ats_score=ats_score,
    )
    return job, gap, resume


class BreakdownForResumeTests(TestCase):
    def setUp(self):
        self.user = _user("helper@example.com")
        self.job, self.gap, self.resume = _make_chain(
            self.user, skills=RICH_SKILLS, tiers=RICH_TIERS, content=RICH_CONTENT,
        )

    def test_determinism(self):
        a = breakdown_for_resume(self.resume)
        b = breakdown_for_resume(self.resume)
        self.assertEqual(a, b)

    def test_one_computation_matches_pure_scorer(self):
        # The helper must be a thin resolver over the pure scorer — same triple,
        # same number, no second path.
        direct = compute_ats_breakdown(
            self.resume.content, self.job.extracted_skills,
            self.job.extracted_skills_tiers,
        )
        self.assertEqual(breakdown_for_resume(self.resume), direct)

    def test_content_param_scores_the_hypothetical_not_resume_content(self):
        # Locks the Slice-2 reuse contract: an explicit content is scored as-is.
        hypo = breakdown_for_resume(self.resume, {"skills": []})
        self.assertEqual(hypo["matched_count"], 0)
        self.assertEqual(hypo["score"], 0.0)
        self.assertGreater(breakdown_for_resume(self.resume)["matched_count"], 0)

    def test_explicit_empty_content_is_honoured(self):
        # content is None selects resume.content; an explicit {} must NOT.
        self.assertEqual(breakdown_for_resume(self.resume, {})["matched_count"], 0)

    def test_base_key_present_and_reconciles_when_not_clamped(self):
        bd = breakdown_for_resume(self.resume)
        self.assertIn("base", bd)
        # base + in-context − stuffing == score (clamp didn't bind here).
        self.assertAlmostEqual(
            bd["base"] + bd["in_context_bonus"] - bd["stuffing_penalty"],
            bd["score"], delta=0.1,
        )
        self.assertTrue(score_reconciles(bd))


class ScoreReconcilesTests(TestCase):
    def test_true_when_clamp_did_not_bind(self):
        bd = {"base": 58.3, "in_context_bonus": 6.0, "stuffing_penalty": 5.0, "score": 59.3}
        self.assertTrue(score_reconciles(bd))

    def test_false_when_clamp_bound_high(self):
        # Unclamped 104 → score clamped to 100 → equation must NOT be asserted.
        bd = {"base": 100.0, "in_context_bonus": 4.0, "stuffing_penalty": 0.0, "score": 100.0}
        self.assertFalse(score_reconciles(bd))

    def test_real_over_hundred_case_flags_not_reconciling(self):
        user = _user("clamp@example.com")
        _job, _gap, resume = _make_chain(
            user,
            skills=["Python", "Django"],
            tiers={"must_have": ["Python", "Django"], "nice_to_have": []},
            content={
                "skills": ["Python", "Django"],
                "experience": [{"title": "Eng",
                                "description": ["Used Python and Django heavily."]}],
            },
        )
        bd = breakdown_for_resume(resume)
        self.assertEqual(bd["score"], 100.0)        # clamped
        self.assertGreater(
            bd["base"] + bd["in_context_bonus"] - bd["stuffing_penalty"], 100.0,
        )
        self.assertFalse(score_reconciles(bd))


class LegacyAndEmptyEdgeTests(TestCase):
    def test_no_tiers_collapses_to_must_have(self):
        user = _user("notiers@example.com")
        _job, _gap, resume = _make_chain(
            user,
            skills=["Python", "SQL"],
            tiers=None,                       # → extracted_skills_tiers == {} → None path
            content={"skills": ["Python"]},   # Python in skills only: no bonus/penalty
        )
        bd = breakdown_for_resume(resume)
        self.assertEqual(bd["nice_to_have"], {"matched": [], "missed": [], "coverage": 0.0})
        self.assertEqual(bd["base"], bd["raw_score"])   # tier-weighting didn't apply
        self.assertEqual(bd["score"], bd["raw_score"])  # no in-context/stuffing here

    def test_empty_job_skills_yields_zeroed_breakdown(self):
        user = _user("noskills@example.com")
        _job, _gap, resume = _make_chain(user, skills=[], content={"skills": ["Python"]})
        bd = breakdown_for_resume(resume)
        self.assertEqual(bd["score"], 0.0)
        self.assertEqual(bd["total_count"], 0)
        self.assertEqual(bd["must_have"], {"matched": [], "missed": [], "coverage": 0.0})


class AtsBreakdownEndpointTests(TestCase):
    def setUp(self):
        self.user = _user("owner@example.com")
        self.job, self.gap, self.resume = _make_chain(
            self.user, skills=RICH_SKILLS, tiers=RICH_TIERS, content=RICH_CONTENT,
        )
        self.url = reverse("resume_ats_breakdown_api", args=[self.resume.id])

    def test_get_returns_full_breakdown_contract(self):
        self.client.force_login(self.user)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(ATS_BREAKDOWN_KEYS.issubset(data.keys()))
        self.assertIsInstance(data["base"], (int, float))
        self.assertIsInstance(data["must_have"], dict)
        # One computation across the HTTP boundary too.
        self.assertEqual(data["score"], breakdown_for_resume(self.resume)["score"])

    def test_empty_skills_endpoint_200_zeroed(self):
        user = _user("noskills2@example.com")
        _job, _gap, resume = _make_chain(user, skills=[], content={"skills": ["Python"]})
        self.client.force_login(user)
        resp = self.client.get(reverse("resume_ats_breakdown_api", args=[resume.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["score"], 0.0)

    def test_ownership_guard_rejects_other_user(self):
        other = _user("intruder@example.com")
        self.client.force_login(other)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 404)

    def test_edit_view_injects_breakdown_into_context(self):
        self.client.force_login(self.user)
        resp = self.client.get(reverse("resume_edit", args=[self.resume.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertIn("ats_breakdown", resp.context)
        self.assertEqual(
            resp.context["ats_breakdown"]["score"],
            breakdown_for_resume(self.resume)["score"],
        )

    def test_rendered_panel_has_no_leaked_template_syntax(self):
        # Regression guard for the multi-line {# #} leak: raw template tokens
        # must never reach the rendered panel. (The bug was render-only, so the
        # data tests above could not catch it — this one renders the HTML.)
        self.client.force_login(self.user)
        html = self.client.get(reverse("resume_edit", args=[self.resume.id])).content.decode()
        for token in ("{#", "#}", "{% comment %}", "{% endcomment %}"):
            self.assertNotIn(token, html, f"leaked template token {token!r} in rendered panel")

    def test_reconciliation_line_renders_and_sums(self):
        # RICH_CONTENT scores 59.3 with the [0,100] clamp NOT bound, so the
        # equation form must render and reconcile (58.3 + 6.0 − 5.0 = 59.3).
        self.client.force_login(self.user)
        html = self.client.get(reverse("resume_edit", args=[self.resume.id])).content.decode()
        bd = breakdown_for_resume(self.resume)
        self.assertTrue(score_reconciles(bd))
        self.assertIn("Weighted base", html)
        self.assertIn(f"= {bd['score']:.1f}", html)   # "= 59.3"


class RefreshAtsScoreTests(TestCase):
    def setUp(self):
        self.user = _user("refresh@example.com")
        self.job, self.gap, self.resume = _make_chain(
            self.user, skills=RICH_SKILLS, tiers=RICH_TIERS, content=RICH_CONTENT,
            ats_score=0.0,
        )

    def test_persists_only_ats_score_when_changed(self):
        before_content = dict(self.resume.content)
        before_report = dict(self.resume.validation_report)
        refresh_ats_score(self.resume)
        self.resume.refresh_from_db()
        self.assertGreater(self.resume.ats_score, 0.0)              # synced up
        self.assertEqual(self.resume.content, before_content)       # content untouched
        self.assertEqual(self.resume.validation_report, before_report)

    def test_noop_when_already_in_sync(self):
        refresh_ats_score(self.resume)          # sync once
        self.resume.refresh_from_db()
        with patch.object(self.resume, "save") as mock_save:
            refresh_ats_score(self.resume)      # already correct → no write
        mock_save.assert_not_called()


class NiceTierRenderTests(TestCase):
    """The absent nice-tier and the populated-but-zero-matched nice-tier are two
    distinct states that must not collapse into each other in the render."""

    def _edit_html(self, user, resume):
        self.client.force_login(user)
        return self.client.get(reverse("resume_edit", args=[resume.id])).content.decode()

    def test_absent_nice_tier_renders_none_specified(self):
        user = _user("absentnice@example.com")
        _job, _gap, resume = _make_chain(
            user,
            skills=["Python"],
            tiers={"must_have": ["Python"], "nice_to_have": []},
            content={"skills": ["Python"]},
        )
        bd = breakdown_for_resume(resume)
        self.assertEqual(bd["nice_to_have"], {"matched": [], "missed": [], "coverage": 0.0})
        self.assertIn("none specified", self._edit_html(user, resume))

    def test_populated_zero_nice_tier_renders_bar_and_missed_not_none(self):
        user = _user("zeronice@example.com")
        _job, _gap, resume = _make_chain(
            user,
            skills=["Python", "Kubernetes"],
            tiers={"must_have": ["Python"], "nice_to_have": ["Kubernetes"]},
            content={"skills": ["Python"]},   # Kubernetes absent → 0 matched / 1 missed
        )
        bd = breakdown_for_resume(resume)
        self.assertEqual(
            bd["nice_to_have"], {"matched": [], "missed": ["Kubernetes"], "coverage": 0.0},
        )
        html = self._edit_html(user, resume)
        self.assertNotIn("none specified", html)   # NOT the absent branch
        self.assertIn("0 matched", html)            # zero-coverage count row
        self.assertIn("Kubernetes", html)           # missed skill in the details


# ===========================================================================
# Slice 2 — Category-1 cards (read-only, real deltas)
# ===========================================================================

class CandidateBuilderTests(TestCase):
    """The pure hypothetical-edit constructor reused unchanged by Slice 3."""

    def test_apply_edit_is_pure_and_deterministic(self):
        content = {"skills": ["Python"]}
        edit = {"op": "add_skill", "skill": "Docker"}
        out1 = apply_edit_to_content(content, edit)
        out2 = apply_edit_to_content(content, edit)
        self.assertEqual(out1, out2)                       # deterministic
        self.assertEqual(content, {"skills": ["Python"]})  # input NOT mutated
        self.assertIn("Docker", out1["skills"])

    def test_apply_edit_is_variant_idempotent(self):
        # skills_match dedupe: a case/variant of an existing skill is not re-added.
        out = apply_edit_to_content({"skills": ["Python"]}, {"op": "add_skill", "skill": "python"})
        self.assertEqual(out["skills"], ["Python"])

    def test_card_id_is_stable_and_type_scoped(self):
        self.assertEqual(_ats_card_id("add_skill", "Docker"), _ats_card_id("add_skill", "docker"))
        self.assertNotEqual(_ats_card_id("add_skill", "Docker"), _ats_card_id("stuffing", "Docker"))


# A general fixture: 4 must-have skills; the candidate demonstrably HAS Docker
# (evidence-backed gap match) but it's not in the résumé; Python is matched AND
# present (so no card) and is also stuffed; Kubernetes is user-asserted and SQL
# is unevidenced (both must be filtered out).
def _producer_chain(user):
    job = Job.objects.create(
        user=user, title="Engineer", description="JD",
        extracted_skills=["Python", "SQL", "Docker", "Kubernetes"],
        extracted_skills_tiers={},   # no tiers → all must-have
    )
    gap = GapAnalysis.objects.create(
        user=user, job=job,
        matched_must_have=[
            {"name": "Docker", "evidence_source": "projects",
             "evidence_quote": "built CI/CD with Docker"},
            {"name": "Python", "evidence_source": "experience",
             "evidence_quote": "Used Python daily."},
            {"name": "Kubernetes", "evidence_source": "user",
             "evidence_quote": "", "user_asserted": True},
            {"name": "SQL", "evidence_source": "", "evidence_quote": ""},
        ],
    )
    resume = GeneratedResume.objects.create(
        gap_analysis=gap,
        content={
            "skills": ["Python"],
            "experience": [{"title": "E",
                            "description": ["Used Python Python Python Python Python daily."]}],
        },
    )
    return job, gap, resume


class AtsCardProducerTests(TestCase):
    def setUp(self):
        self.user = _user("producer@example.com")
        self.job, self.gap, self.resume = _producer_chain(self.user)
        self.cards = build_ats_cards(self.resume)
        self.actionable = [c for c in self.cards if c["kind"] == "actionable"]
        self.advisory = [c for c in self.cards if c["kind"] == "advisory"]

    def _docker(self):
        return next(c for c in self.actionable if c["skill"] == "Docker")

    def test_only_evidence_backed_missing_skill_is_carded(self):
        # Docker is the ONLY actionable card: it's evidence-backed AND missing.
        self.assertEqual([c["skill"] for c in self.actionable], ["Docker"])

    def test_provenance_filter_excludes_user_asserted_and_unevidenced(self):
        skills = [c["skill"] for c in self.actionable]
        self.assertNotIn("Kubernetes", skills)   # user_asserted → dropped
        self.assertNotIn("SQL", skills)          # no evidence_source/quote → dropped

    def test_skill_already_in_resume_is_not_carded(self):
        # Python is matched-with-evidence but PRESENT in the résumé (not missed).
        self.assertNotIn("Python", [c["skill"] for c in self.actionable])

    def test_delta_is_real_recompute(self):
        card = self._docker()
        current = breakdown_for_resume(self.resume)["score"]
        hypo = apply_edit_to_content(self.resume.content, card["edit"])
        expected = round(breakdown_for_resume(self.resume, hypo)["score"] - current, 1)
        self.assertEqual(card["delta"], expected)
        self.assertGreater(card["delta"], 0.0)
        self.assertIn("Docker", hypo["skills"])

    def test_previewed_equals_realized(self):
        # The card's projected_score == scoring the content Slice 3 will persist.
        card = self._docker()
        applied = apply_edit_to_content(self.resume.content, card["edit"])
        self.assertEqual(breakdown_for_resume(self.resume, applied)["score"],
                         card["projected_score"])

    def test_card_a_is_coverage_only(self):
        # Adding to the skills line must NOT earn the in-context bonus.
        current = breakdown_for_resume(self.resume)
        hypo = apply_edit_to_content(self.resume.content, self._docker()["edit"])
        new_bd = breakdown_for_resume(self.resume, hypo)
        self.assertEqual(new_bd["in_context_count"], current["in_context_count"])

    def test_card_a_copy_says_ats_score_not_match(self):
        msg = self._docker()["message"]
        self.assertIn("ATS score", msg)
        self.assertNotIn("raises your match", msg)
        self.assertIn("coverage", msg)
        self.assertIn("built CI/CD with Docker", msg)   # grounded in real evidence

    def test_stuffing_card_is_advisory_only(self):
        py = next(c for c in self.advisory if c["skill"] == "Python")
        self.assertEqual(py["recoverable"], 5.0)
        self.assertNotIn("delta", py)
        self.assertNotIn("edit", py)

    def test_cards_render_distinct_with_no_leaked_syntax(self):
        self.client.force_login(self.user)
        resp = self.client.get(reverse("resume_edit", args=[self.resume.id]))
        self.assertIn("ats_cards", resp.context)
        html = resp.content.decode()
        for token in ("{#", "#}", "{% comment %}", "{% endcomment %}"):
            self.assertNotIn(token, html)
        self.assertIn("Docker", html)            # actionable card
        self.assertIn("ATS score", html)         # honesty wording
        self.assertNotIn("raises your match", html)
        self.assertIn("advisory", html)          # advisory card distinguishable


class ZeroDeltaCardTests(TestCase):
    def test_zero_delta_card_is_filtered(self):
        # Construct a clamp at 100 with one missed, evidence-backed skill:
        # 9 of 10 skills matched AND in a bullet (in-context bonus maxes at +10),
        # base = 90 → score 100. Adding the 10th keeps it clamped → delta 0.
        user = _user("zerodelta@example.com")
        matched = [f"S{i}" for i in range(9)]
        all_skills = matched + ["Sgap"]
        job = Job.objects.create(
            user=user, title="E", description="JD",
            extracted_skills=all_skills, extracted_skills_tiers={},
        )
        gap = GapAnalysis.objects.create(
            user=user, job=job,
            matched_must_have=[{"name": "Sgap", "evidence_source": "projects",
                                "evidence_quote": "used Sgap in a project"}],
        )
        resume = GeneratedResume.objects.create(
            gap_analysis=gap,
            content={"skills": matched,
                     "experience": [{"title": "E", "description": [" ".join(matched)]}]},
        )
        self.assertEqual(breakdown_for_resume(resume)["score"], 100.0)   # clamped
        _bd, delta = score_with_edit(resume, {"op": "add_skill", "skill": "Sgap"})
        self.assertEqual(delta, 0.0)
        actionable = [c for c in build_ats_cards(resume) if c["kind"] == "actionable"]
        self.assertNotIn("Sgap", [c["skill"] for c in actionable])
