"""Tests for ``resumes.services.pipeline_dispatch``.

Verifies the v1/v2 pipeline selector:
  - default flag is ``'v1'`` (so adding the flag changes nothing)
  - ``'v1'`` routes to ``generate_resume_content_supervised`` unchanged
  - ``'v2'`` routes through the v2 pipeline and produces a v1-shaped
    template dict with a v1-shaped ``validation_report``
  - the two production trigger sites (tasks + in-place regen view)
    actually call the dispatcher rather than the legacy entry point

Tests mock the leaf functions, not the entire helper, so the
dispatcher's orchestration is exercised end-to-end.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.conf import settings
from django.test import SimpleTestCase, override_settings


def _fake_resume_v2():
    """Minimal stand-in for a ``GeneratedResumeV2``.

    Only the attributes the dispatcher touches are populated; the
    adapter and the validation-report shim are mocked separately, so
    deep-shape correctness is not asserted here.
    """
    return SimpleNamespace(sections={}, fabrication_events=[])


def _fake_profile():
    return SimpleNamespace(data_content={
        "professional_summary": "Existing summary.",
        "skills": ["Python"],
        "experiences": [
            {"title": "Engineer", "company": "Acme",
             "start_date": "2024-01", "end_date": "2024-12",
             "description": ["Built a thing."]},
        ],
        "projects": [],
        "education": [],
    })


def _fake_job():
    return SimpleNamespace(
        title="ML Engineer",
        company="Target Co",
        description="We want Python and ML.",
        extracted_skills=["Python", "ML"],
        extracted_skills_tiers={"must_have": ["Python"], "nice_to_have": ["ML"]},
    )


def _fake_gap():
    return SimpleNamespace()


class PipelineDispatchSettingTests(SimpleTestCase):
    """The setting itself — default must be ``'v1'`` for reversibility."""

    def test_default_setting_is_v1(self):
        """No env override → ``RESUME_GENERATOR_PIPELINE == 'v1'``."""
        self.assertEqual(getattr(settings, "RESUME_GENERATOR_PIPELINE", None), "v1")


class PipelineDispatchV1Tests(SimpleTestCase):
    """With the default ``'v1'`` flag, the dispatcher must delegate to
    ``generate_resume_content_supervised`` byte-for-byte and not touch
    any v2 entry point."""

    @patch("resumes.services.resume_generator.generate_resume_content_supervised")
    @patch("resumes.services.resume_generator_v2.generate_resume_v2")
    def test_v1_default_calls_supervised(self, mock_v2_gen, mock_v1_sup):
        """Default flag → v1 supervised entry point fires, v2 never imported."""
        from resumes.services.pipeline_dispatch import (
            generate_resume_content_dispatched,
        )
        mock_v1_sup.return_value = {"professional_summary": "v1 output"}

        result = generate_resume_content_dispatched(
            _fake_profile(), _fake_job(), _fake_gap(), previous_best=None,
        )

        mock_v1_sup.assert_called_once()
        mock_v2_gen.assert_not_called()
        self.assertEqual(result, {"professional_summary": "v1 output"})

    @patch("resumes.services.resume_generator.generate_resume_content_supervised")
    def test_v1_passes_previous_best_through(self, mock_v1_sup):
        """``previous_best`` must reach the supervised entry point unchanged."""
        from resumes.services.pipeline_dispatch import (
            generate_resume_content_dispatched,
        )
        mock_v1_sup.return_value = {}
        prev_best = {"content": {"professional_summary": "prior"}}

        generate_resume_content_dispatched(
            _fake_profile(), _fake_job(), _fake_gap(), previous_best=prev_best,
        )

        kwargs = mock_v1_sup.call_args.kwargs
        self.assertEqual(kwargs.get("previous_best"), prev_best)

    @patch("resumes.services.resume_generator.generate_resume_content_supervised")
    @patch("resumes.services.resume_generator_v2.generate_resume_v2")
    def test_unknown_pipeline_falls_back_to_v1(self, mock_v2_gen, mock_v1_sup):
        """Defensive: an unrecognised flag value must not break production —
        fall back to v1 rather than raise."""
        from resumes.services.pipeline_dispatch import (
            generate_resume_content_dispatched,
        )
        mock_v1_sup.return_value = {}

        generate_resume_content_dispatched(
            _fake_profile(), _fake_job(), _fake_gap(), pipeline="garbage",
        )

        mock_v1_sup.assert_called_once()
        mock_v2_gen.assert_not_called()


class PipelineDispatchV2Tests(SimpleTestCase):
    """With ``pipeline='v2'``, the dispatcher must run the v2 pipeline,
    NEVER call v1 supervised, and emit a v1-dict-shaped result with a
    v1-shaped ``validation_report``."""

    def _patch_v2_leaves(self):
        """Patch every v2 entry point the dispatcher touches.

        Returns a tuple of MagicMocks so individual tests can assert
        call counts / argument shapes. Patches are stacked so the
        caller is responsible for stopping each ``patch`` via the
        returned ``patchers`` list (``addCleanup``)."""
        leaves = {
            "classify": patch("profiles.services.role_classifier.classify_for_jd"),
            "extract": patch("resumes.services.fact_extractor.extract_into_store"),
            "store_cls": patch("resumes.services.fact_extractor.FactStore"),
            "kb_prefetch": patch(
                "resumes.services.kb_integration.prefetch_kb_for_pipeline"),
            "kb_split": patch("resumes.services.kb_integration.split_kb_chunks"),
            "kb_rules": patch(
                "resumes.services.kb_integration.format_writing_rules_block"),
            "planner": patch("resumes.services.resume_planner_v2.build_plan"),
            "generator": patch(
                "resumes.services.resume_generator_v2.generate_resume_v2"),
            "reviewer": patch(
                "resumes.services.resume_reviewer_v2.review_and_regenerate"),
            "vr_shim": patch(
                "resumes.services.resume_reviewer_v2.build_v2_validation_report"),
            "adapter": patch(
                "resumes.services.resume_v2_adapter.resume_v2_to_template_dict"),
            "v1_sup": patch(
                "resumes.services.resume_generator.generate_resume_content_supervised"),
        }
        mocks = {name: p.start() for name, p in leaves.items()}
        for p in leaves.values():
            self.addCleanup(p.stop)
        return mocks

    def test_v2_flag_calls_v2_pipeline_not_v1(self):
        """``pipeline='v2'`` → ``generate_resume_v2`` fires; v1
        supervised entry point never touched."""
        from resumes.services.pipeline_dispatch import (
            generate_resume_content_dispatched,
        )
        m = self._patch_v2_leaves()
        m["kb_split"].return_value = ([], [])
        m["kb_rules"].return_value = "RULES"
        m["planner"].return_value = MagicMock(name="PlanResult")
        m["generator"].return_value = _fake_resume_v2()
        m["reviewer"].return_value = (_fake_resume_v2(), {"rounds_run": 0})
        m["vr_shim"].return_value = {
            "findings": [], "grounding_findings": [],
            "supervisor_findings": [], "regression_findings": [],
        }
        m["adapter"].return_value = {
            "professional_summary": "v2 synth",
            "skills": ["Python"],
            "experience": [{"title": "Engineer", "description": ["bullet"]}],
            "projects": [],
        }

        generate_resume_content_dispatched(
            _fake_profile(), _fake_job(), _fake_gap(), pipeline="v2",
        )

        m["generator"].assert_called_once()
        m["reviewer"].assert_called_once()
        m["adapter"].assert_called_once()
        m["v1_sup"].assert_not_called()

    def test_v2_output_has_v1_dict_shape(self):
        """The dispatcher must return what the adapter produced PLUS a
        ``validation_report`` key — same shape v1 emits."""
        from resumes.services.pipeline_dispatch import (
            generate_resume_content_dispatched,
        )
        m = self._patch_v2_leaves()
        m["kb_split"].return_value = ([], [])
        m["kb_rules"].return_value = ""
        m["planner"].return_value = MagicMock()
        m["generator"].return_value = _fake_resume_v2()
        m["reviewer"].return_value = (_fake_resume_v2(), {})
        m["vr_shim"].return_value = {
            "findings": [], "grounding_findings": [],
            "supervisor_findings": [], "regression_findings": [],
        }
        m["adapter"].return_value = {
            "professional_summary": "Engineer with five years experience.",
            "skills": ["Python", "ML"],
            "experience": [{"title": "Engineer", "company": "Acme",
                            "description": ["Built things."]}],
            "projects": [{"name": "Proj", "description": ["x"]}],
            "education": [],
            "certifications": [],
            "languages": [],
        }

        result = generate_resume_content_dispatched(
            _fake_profile(), _fake_job(), _fake_gap(), pipeline="v2",
        )

        for key in ("professional_summary", "skills", "experience",
                    "projects", "education", "validation_report"):
            self.assertIn(key, result, f"v2 output missing v1-shaped key: {key!r}")
        self.assertEqual(result["professional_summary"],
                         "Engineer with five years experience.")
        self.assertIsInstance(result["experience"], list)
        self.assertIsInstance(result["skills"], list)

    def test_v2_validation_report_uses_v1_shim_keys(self):
        """``validation_report`` must carry the v1-shaped keys the
        existing findings_classifier + findings_presenter consume."""
        from resumes.services.pipeline_dispatch import (
            generate_resume_content_dispatched,
        )
        m = self._patch_v2_leaves()
        m["kb_split"].return_value = ([], [])
        m["kb_rules"].return_value = ""
        m["planner"].return_value = MagicMock()
        m["generator"].return_value = _fake_resume_v2()
        m["reviewer"].return_value = (_fake_resume_v2(), {})
        m["vr_shim"].return_value = {
            "findings": [{"rule_id": "ban-opener", "where": "experience/x[0]"}],
            "grounding_findings": [],
            "supervisor_findings": [],
            "regression_findings": [],
        }
        m["adapter"].return_value = {"professional_summary": "ok"}

        result = generate_resume_content_dispatched(
            _fake_profile(), _fake_job(), _fake_gap(), pipeline="v2",
        )

        vr = result.get("validation_report")
        self.assertIsInstance(vr, dict)
        # Keys consumed by findings_presenter / findings_classifier:
        for key in ("findings", "grounding_findings",
                    "supervisor_findings", "regression_findings"):
            self.assertIn(key, vr, f"validation_report missing v1-shaped key: {key!r}")
        m["vr_shim"].assert_called_once()


@override_settings(RESUME_GENERATOR_PIPELINE="v2")
class PipelineDispatchSettingHonoredTests(SimpleTestCase):
    """When the env-driven setting is flipped to ``'v2'`` (no per-call
    override), the dispatcher must read it and route accordingly."""

    @patch("resumes.services.resume_v2_adapter.resume_v2_to_template_dict",
           return_value={"professional_summary": "s"})
    @patch("resumes.services.resume_reviewer_v2.build_v2_validation_report",
           return_value={"findings": [], "grounding_findings": [],
                         "supervisor_findings": [], "regression_findings": []})
    @patch("resumes.services.resume_reviewer_v2.review_and_regenerate")
    @patch("resumes.services.resume_generator_v2.generate_resume_v2")
    @patch("resumes.services.resume_planner_v2.build_plan")
    @patch("resumes.services.kb_integration.format_writing_rules_block",
           return_value="")
    @patch("resumes.services.kb_integration.split_kb_chunks",
           return_value=([], []))
    @patch("resumes.services.kb_integration.prefetch_kb_for_pipeline",
           return_value=[])
    @patch("resumes.services.fact_extractor.extract_into_store")
    @patch("resumes.services.fact_extractor.FactStore")
    @patch("profiles.services.role_classifier.classify_for_jd")
    @patch("resumes.services.resume_generator.generate_resume_content_supervised")
    def test_setting_v2_routes_to_v2(self, mock_v1_sup, mock_classify,
                                     mock_store_cls, mock_extract,
                                     mock_kb_prefetch, mock_kb_split,
                                     mock_kb_rules, mock_plan, mock_v2_gen,
                                     mock_review, mock_vr, mock_adapter):
        mock_v2_gen.return_value = _fake_resume_v2()
        mock_review.return_value = (_fake_resume_v2(), {})

        from resumes.services.pipeline_dispatch import (
            generate_resume_content_dispatched,
        )
        generate_resume_content_dispatched(
            _fake_profile(), _fake_job(), _fake_gap(),
        )

        mock_v2_gen.assert_called_once()
        mock_v1_sup.assert_not_called()


class ProductionTriggerSitesUseDispatcherTests(SimpleTestCase):
    """Both production full-resume trigger sites must call the
    dispatcher, not ``generate_resume_content_supervised`` directly."""

    def test_tasks_module_imports_dispatcher(self):
        """``resumes.tasks`` must import + reference the dispatcher.

        Catches a future refactor accidentally restoring the direct v1
        import on the background-task path."""
        from resumes import tasks
        import inspect
        source = inspect.getsource(tasks)
        self.assertIn("generate_resume_content_dispatched", source,
                      "resumes/tasks.py no longer calls the dispatcher — "
                      "the v1/v2 flag would have no effect on Path A.")
        self.assertNotIn("generate_resume_content_supervised(", source,
                         "resumes/tasks.py still has a direct call to "
                         "generate_resume_content_supervised — should "
                         "go through the dispatcher.")

    def test_views_module_uses_dispatcher_for_in_place_regen(self):
        """``trigger_resume_regeneration_api`` (Path B in-place regen)
        must use the dispatcher too — otherwise flipping the flag would
        only affect Path A and produce inconsistent v1/v2 mixing."""
        from resumes import views
        import inspect
        source = inspect.getsource(views.trigger_resume_regeneration_api)
        self.assertIn("generate_resume_content_dispatched", source,
                      "trigger_resume_regeneration_api no longer goes "
                      "through the dispatcher — flipping the flag would "
                      "skip Path B.")
