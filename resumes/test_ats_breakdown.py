"""Slice 1 — read-only ATS breakdown panel.

Covers the resume-aware helper (`breakdown_for_resume`), the idempotent
`refresh_ats_score`, the `score_reconciles` clamp flag, the read-only GET
endpoint, and the `base` key just added to the deterministic scorer.

Run: ``python manage.py test resumes`` (Django TestCase — these resolve
resume.gap_analysis.job over the ORM and exercise the view via the test
Client, so they belong in the app suite, not pytest tests/).
"""
import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from jobs.models import Job
from analysis.models import GapAnalysis
from profiles.models import UserProfile
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

    def test_get_returns_breakdown_and_cards_contract(self):
        # Slice 3 extends the endpoint shape to {breakdown, cards}.
        self.client.force_login(self.user)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("breakdown", data)
        self.assertIn("cards", data)
        self.assertIsInstance(data["cards"], list)
        bd = data["breakdown"]
        self.assertTrue(ATS_BREAKDOWN_KEYS.issubset(bd.keys()))
        self.assertIsInstance(bd["base"], (int, float))
        # One computation across the HTTP boundary too.
        self.assertEqual(bd["score"], breakdown_for_resume(self.resume)["score"])

    def test_empty_skills_endpoint_200_zeroed(self):
        user = _user("noskills2@example.com")
        _job, _gap, resume = _make_chain(user, skills=[], content={"skills": ["Python"]})
        self.client.force_login(user)
        resp = self.client.get(reverse("resume_ats_breakdown_api", args=[resume.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["breakdown"]["score"], 0.0)

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


# ===========================================================================
# Slice 3 — apply + persist + rescore
# ===========================================================================

class AtsApplyTests(TestCase):
    def setUp(self):
        self.user = _user("apply@example.com")
        self.job, self.gap, self.resume = _producer_chain(self.user)
        self.client.force_login(self.user)
        self.url = reverse("resume_ats_apply_api", args=[self.resume.id])

    def _docker_card(self):
        return next(c for c in build_ats_cards(self.resume) if c["skill"] == "Docker")

    def _post(self, body, url=None):
        return self.client.post(url or self.url, data=json.dumps(body),
                                content_type="application/json")

    def test_apply_persists_previewed_content(self):
        card = self._docker_card()
        original = dict(self.resume.content)
        resp = self._post({"card_id": card["id"]})
        self.assertEqual(resp.status_code, 200)
        self.resume.refresh_from_db()
        self.assertEqual(self.resume.content,
                         apply_edit_to_content(original, card["edit"]))

    def test_apply_persists_projected_score(self):
        card = self._docker_card()
        self._post({"card_id": card["id"]})
        self.resume.refresh_from_db()
        self.assertEqual(self.resume.ats_score, card["projected_score"])

    def test_apply_ignores_client_sent_payload(self):
        # THE boundary test — only card_id is trusted; content/edit/skill ignored.
        card = self._docker_card()
        original = dict(self.resume.content)
        resp = self._post({
            "card_id": card["id"],
            "content": {"skills": ["TOTALLY_FAKE"], "professional_summary": "HACKED"},
            "edit": {"op": "add_skill", "skill": "HACKED"},
            "skill": "HACKED",
        })
        self.assertEqual(resp.status_code, 200)
        self.resume.refresh_from_db()
        self.assertEqual(self.resume.content,
                         apply_edit_to_content(original, card["edit"]))
        blob = json.dumps(self.resume.content)
        self.assertNotIn("HACKED", blob)
        self.assertNotIn("TOTALLY_FAKE", blob)

    def test_apply_stale_card_id_409_no_write(self):
        before = dict(self.resume.content)
        resp = self._post({"card_id": "deadbeefdeadbeef"})
        self.assertEqual(resp.status_code, 409)
        self.resume.refresh_from_db()
        self.assertEqual(self.resume.content, before)

    def test_apply_advisory_card_id_409(self):
        stuffing = next(c for c in build_ats_cards(self.resume) if c["kind"] == "advisory")
        before = dict(self.resume.content)
        resp = self._post({"card_id": stuffing["id"]})
        self.assertEqual(resp.status_code, 409)
        self.resume.refresh_from_db()
        self.assertEqual(self.resume.content, before)

    def test_apply_is_idempotent_on_reapply(self):
        card = self._docker_card()
        self.assertEqual(self._post({"card_id": card["id"]}).status_code, 200)
        # Docker now present → no longer missed → no Docker card → second apply 409.
        resp2 = self._post({"card_id": card["id"]})
        self.assertEqual(resp2.status_code, 409)
        self.resume.refresh_from_db()
        dockers = [s for s in self.resume.content["skills"] if s.lower() == "docker"]
        self.assertEqual(len(dockers), 1)

    def test_apply_bad_request_without_card_id(self):
        resp = self._post({})
        self.assertEqual(resp.status_code, 400)

    def test_apply_ownership_guard(self):
        other = _user("intruder3@example.com")
        self.client.force_login(other)
        resp = self._post({"card_id": "x"})
        self.assertEqual(resp.status_code, 404)

    def test_coverage_only_survives_apply(self):
        before_ic = breakdown_for_resume(self.resume)["in_context_count"]
        self._post({"card_id": self._docker_card()["id"]})
        self.resume.refresh_from_db()
        self.assertEqual(breakdown_for_resume(self.resume)["in_context_count"], before_ic)

    def test_apply_response_panel_html_is_clean(self):
        resp = self._post({"card_id": self._docker_card()["id"]})
        data = resp.json()
        self.assertEqual(data["applied_skill"], "Docker")
        html = data["panel_html"]
        for token in ("{#", "#}", "{% comment %}", "{% endcomment %}"):
            self.assertNotIn(token, html)
        self.assertIn("ATS score", html)

    def test_remaining_cards_rebaseline_after_apply(self):
        # Two actionable cards (Docker, Redis); apply Docker; Redis re-baselines.
        user = _user("rebaseline@example.com")
        job = Job.objects.create(
            user=user, title="E", description="JD",
            extracted_skills=["Python", "Docker", "Redis", "SQL"],
            extracted_skills_tiers={},
        )
        gap = GapAnalysis.objects.create(
            user=user, job=job,
            matched_must_have=[
                {"name": "Docker", "evidence_source": "projects",
                 "evidence_quote": "built CI/CD with Docker"},
                {"name": "Redis", "evidence_source": "projects",
                 "evidence_quote": "used Redis for caching"},
            ],
        )
        resume = GeneratedResume.objects.create(
            gap_analysis=gap,
            content={"skills": ["Python"],
                     "experience": [{"title": "E", "description": ["Used Python."]}]},
        )
        self.client.force_login(user)
        docker = next(c for c in build_ats_cards(resume) if c["skill"] == "Docker")
        self._post({"card_id": docker["id"]},
                   url=reverse("resume_ats_apply_api", args=[resume.id]))
        resume.refresh_from_db()
        after = build_ats_cards(resume)
        # Docker card gone (now present); Redis card's baseline == the new score.
        self.assertNotIn("Docker", [c["skill"] for c in after if c["kind"] == "actionable"])
        redis = next(c for c in after if c["skill"] == "Redis")
        self.assertEqual(redis["current_score"], breakdown_for_resume(resume)["score"])


# ===========================================================================
# Slice 4 — Category-2 quantification (the anti-fabrication backbone)
# ===========================================================================

_DIGITLESS_BULLET = "Led the migration of the billing service to a new datastore."


def _quantify_chain(user, *, profile_experiences=None, resume_exp_desc=None):
    """A chain with a profile whose experiences[] matches the résumé experience,
    so a quantify card's profile-write has a target. Profile is created BEFORE
    the résumé (older) so the editor doesn't auto-regen."""
    profile = UserProfile.objects.create(
        user=user,
        data_content={
            "experiences": profile_experiences if profile_experiences is not None else [
                {"title": "Backend Engineer", "company": "PriorCo",
                 "description": ["Maintained backend services."]},
            ],
        },
    )
    job = Job.objects.create(
        user=user, title="E", description="JD",
        extracted_skills=["Python"], extracted_skills_tiers={},
    )
    gap = GapAnalysis.objects.create(user=user, job=job)
    resume = GeneratedResume.objects.create(
        gap_analysis=gap,
        content={
            "skills": ["Python"],
            "experience": [{
                "title": "Backend Engineer", "company": "PriorCo",
                "description": resume_exp_desc if resume_exp_desc is not None else [_DIGITLESS_BULLET],
            }],
        },
    )
    return profile, job, gap, resume


class QuantifyCardProducerTests(TestCase):
    def setUp(self):
        self.user = _user("qcard@example.com")
        self.profile, self.job, self.gap, self.resume = _quantify_chain(self.user)
        self.qcards = [c for c in build_ats_cards(self.resume) if c["kind"] == "quantify"]

    def test_quantify_card_carries_no_number(self):
        # THE anti-fabrication invariant — nothing numeric on the card.
        self.assertTrue(self.qcards)
        for c in self.qcards:
            for forbidden in ("delta", "projected_score", "number", "suggested", "edit", "current_score"):
                self.assertNotIn(forbidden, c)

    def test_per_bullet_selection(self):
        # Digit-less achievement bullet → exactly one quantify card at (exp, 0, 0).
        self.assertEqual(len(self.qcards), 1)
        c = self.qcards[0]
        self.assertEqual((c["section"], c["item_idx"], c["bullet_idx"]), ("experience", 0, 0))
        # A bullet that already has a number → no quantify card.
        _p, _j, _g, r2 = _quantify_chain(
            _user("q2@example.com"),
            resume_exp_desc=["Cut deploy time from 40 minutes to 6 minutes last quarter."],
        )
        self.assertEqual([c for c in build_ats_cards(r2) if c["kind"] == "quantify"], [])

    def test_building_cards_writes_nothing(self):
        # Declining / merely viewing must not mutate anything (no submit).
        before_p = dict(self.profile.data_content)
        before_r = dict(self.resume.content)
        build_ats_cards(self.resume)
        self.profile.refresh_from_db(); self.resume.refresh_from_db()
        self.assertEqual(self.profile.data_content, before_p)
        self.assertEqual(self.resume.content, before_r)


class QuantifySubmitTests(TestCase):
    def setUp(self):
        self.user = _user("qsubmit@example.com")
        self.profile, self.job, self.gap, self.resume = _quantify_chain(self.user)
        self.client.force_login(self.user)
        self.url = reverse("resume_ats_quantify_api", args=[self.resume.id])
        self.card = next(c for c in build_ats_cards(self.resume) if c["kind"] == "quantify")

    def _post(self, body, url=None):
        return self.client.post(url or self.url, data=json.dumps(body),
                                content_type="application/json")

    def test_verbatim_capture_becomes_a_fact(self):
        text = "cut deploy time from 40 to 6 min"
        resp = self._post({"card_id": self.card["id"], "text": text})
        self.assertEqual(resp.status_code, 200)
        self.profile.refresh_from_db()
        # Stored VERBATIM on the matched profile experience.
        descs = self.profile.data_content["experiences"][0]["description"]
        self.assertIn(text, descs)
        # → an ACHIEVEMENT fact → the user's digits enter the number-lock allow-set.
        from resumes.services.fact_extractor import extract_from_structured_profile
        from resumes.services.resume_generator_v2 import _allowed_numbers_from_facts
        facts = extract_from_structured_profile(data_content=self.profile.data_content)
        allowed = _allowed_numbers_from_facts(facts)
        self.assertIn(40.0, allowed)
        self.assertIn(6.0, allowed)

    def test_write_targets_profile_not_resume(self):
        before_resume = dict(self.resume.content)
        self._post({"card_id": self.card["id"], "text": "reduced errors by 30%"})
        self.profile.refresh_from_db(); self.resume.refresh_from_db()
        self.assertIn("reduced errors by 30%",
                      self.profile.data_content["experiences"][0]["description"])
        self.assertEqual(self.resume.content, before_resume)   # résumé untouched

    def test_declining_is_a_noop(self):
        before_p = dict(self.profile.data_content)
        # No POST at all (the user dismissed client-side).
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.data_content, before_p)

    def test_forged_card_id_409_no_write(self):
        before = dict(self.profile.data_content)
        resp = self._post({"card_id": "deadbeefdeadbeef", "text": "40%"})
        self.assertEqual(resp.status_code, 409)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.data_content, before)

    def test_no_entity_match_no_write(self):
        self.profile.data_content = {"experiences": [
            {"title": "Different Role", "company": "Other", "description": []}]}
        self.profile.save()
        before = dict(self.profile.data_content)
        resp = self._post({"card_id": self.card["id"], "text": "did 40%"})
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json().get("error"), "no_match")
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.data_content, before)

    def test_ambiguous_match_no_write(self):
        self.profile.data_content = {"experiences": [
            {"title": "Backend Engineer", "company": "PriorCo", "description": []},
            {"title": "Backend Engineer", "company": "PriorCo", "description": []}]}
        self.profile.save()
        before = dict(self.profile.data_content)
        resp = self._post({"card_id": self.card["id"], "text": "did 40%"})
        self.assertEqual(resp.status_code, 409)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.data_content, before)

    def test_idempotent_no_duplicate(self):
        text = "improved throughput by 25%"
        self._post({"card_id": self.card["id"], "text": text})
        self._post({"card_id": self.card["id"], "text": text})
        self.profile.refresh_from_db()
        descs = self.profile.data_content["experiences"][0]["description"]
        self.assertEqual(descs.count(text), 1)

    def test_empty_text_rejected_no_write(self):
        before = dict(self.profile.data_content)
        resp = self._post({"card_id": self.card["id"], "text": "   "})
        self.assertEqual(resp.status_code, 400)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.data_content, before)

    def test_ownership_guard(self):
        other = _user("qintruder@example.com")
        self.client.force_login(other)
        resp = self._post({"card_id": self.card["id"], "text": "40%"})
        self.assertEqual(resp.status_code, 404)

    def test_pipeline_honest_copy(self):
        with override_settings(RESUME_GENERATOR_PIPELINE="v1"):
            c = next(x for x in build_ats_cards(self.resume) if x["kind"] == "quantify")
            self.assertFalse(c["grounds_on_regen"])
            msg = self._post({"card_id": c["id"], "text": "x up by 11%"}).json()["message"]
            self.assertNotIn("ground this bullet when you regenerate", msg)
        with override_settings(RESUME_GENERATOR_PIPELINE="v2"):
            c = next(x for x in build_ats_cards(self.resume) if x["kind"] == "quantify")
            self.assertTrue(c["grounds_on_regen"])
            msg = self._post({"card_id": c["id"], "text": "y up by 22%"}).json()["message"]
            self.assertIn("ground this bullet when you regenerate", msg)

    def test_rendered_input_is_empty(self):
        resp = self.client.get(reverse("resume_edit", args=[self.resume.id]),
                               HTTP_HOST="localhost")
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn("data-quantify-card", html)   # the quantify card rendered
        self.assertIn("data-quantify-input", html)
        # The textarea has no pre-filled content: between the opening tag's '>'
        # and </textarea> there is nothing but whitespace (no suggested number).
        i = html.find("data-quantify-input")
        j = html.find("</textarea>", i)
        self.assertNotEqual(j, -1)
        after_open_tag = html[i:j].split(">", 1)[1]   # content after the opening tag closes
        self.assertEqual(after_open_tag.strip(), "")
