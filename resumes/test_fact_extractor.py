"""Isolation tests for resumes.services.fact_extractor.

LLM calls are mocked; the network is never touched. Tests target the
load-bearing properties:

    1. The anti-fabrication guard drops any fact whose evidence_quote
       is NOT a substring of the source.
    2. Repo classification's fail-safe coerces 'unsure' / unknown /
       malformed to TUTORIAL_DERIVED, never USER_ORIGINAL.
    3. The hedge detector trips on "~" / "about" / "approximately" /
       "aims to" patterns even when the LLM forgot to flag them.
    4. Extracted facts integrate with FactStore (dedup, metrics_for
       binding) end to end.
    5. The four stub source types raise NotImplementedError.
"""

from unittest.mock import patch

from django.test import SimpleTestCase

from resumes.services import fact_extractor as fx
from resumes.services.fact_extractor import (
    _ExtractedFactRaw,
    _ExtractionResult,
    _RepoClassification,
    _evidence_in_source,
    _looks_hedged,
    _reliability_from_classification,
    extract_facts,
    extract_from_github_readme,
    extract_into_store,
)
from resumes.services.fact_store import (
    FactStore,
    FactType,
    SourceReliability,
)


REPO_URL = "https://github.com/zeyad/healthcare-prediction-depi"
REPO_DISPLAY = "Healthcare Prediction (DEPI)"


# ---------------------------------------------------------------------------
# Helper functions (pure logic)
# ---------------------------------------------------------------------------


class EvidenceGuardHelperTests(SimpleTestCase):
    """The substring check is the structural anti-fabrication guarantee.
    It must be permissive on whitespace + casing but refuse paraphrase."""

    def test_exact_substring_passes(self):
        self.assertTrue(
            _evidence_in_source(
                "Achieved 0.89 ROC-AUC",
                "We achieved 0.89 ROC-AUC on the held-out set.",
            )
        )

    def test_case_insensitive(self):
        self.assertTrue(
            _evidence_in_source(
                "ACHIEVED 0.89 roc-auc",
                "We achieved 0.89 ROC-AUC on the held-out set.",
            )
        )

    def test_whitespace_normalized(self):
        self.assertTrue(
            _evidence_in_source(
                "Achieved   0.89\n ROC-AUC",
                "We achieved 0.89 ROC-AUC on the held-out set.",
            )
        )

    def test_paraphrase_fails(self):
        """A semantically-equivalent rewrite is NOT a substring — it
        gets dropped. This is what blocks the LLM from laundering an
        invented claim through a quote that "captures the spirit"."""
        self.assertFalse(
            _evidence_in_source(
                "Reached an ROC-AUC of 0.89",         # not in the source
                "We achieved 0.89 ROC-AUC on the held-out set.",
            )
        )

    def test_empty_quote_fails(self):
        self.assertFalse(_evidence_in_source("", "any source text"))
        self.assertFalse(_evidence_in_source("   ", "any source text"))


class HedgeDetectorTests(SimpleTestCase):
    """The regex catches hedge patterns the LLM may have missed flagging."""

    def test_tilde_number_is_hedged(self):
        self.assertTrue(_looks_hedged("Achieved ~89% accuracy"))

    def test_about_number_is_hedged(self):
        self.assertTrue(_looks_hedged("Trained for about 200 epochs"))

    def test_approximately_is_hedged(self):
        self.assertTrue(_looks_hedged("Approximately 1,000 users."))

    def test_aims_to_is_hedged(self):
        self.assertTrue(_looks_hedged("This model aims to predict churn."))

    def test_up_to_number_is_hedged(self):
        self.assertTrue(_looks_hedged("Serves up to 500 requests/min."))

    def test_concrete_number_is_not_hedged(self):
        self.assertFalse(_looks_hedged("Achieved 0.89 ROC-AUC on validation."))
        self.assertFalse(_looks_hedged("Served 4,200 users in production."))


class ReliabilityFromClassificationTests(SimpleTestCase):
    """Fail-safe → TUTORIAL_DERIVED on any non-'original' value."""

    def test_original_maps_to_user_original(self):
        cls = _RepoClassification(classification="original", reasoning="")
        self.assertEqual(
            _reliability_from_classification(cls), SourceReliability.USER_ORIGINAL,
        )

    def test_tutorial_maps_to_tutorial_derived(self):
        cls = _RepoClassification(classification="tutorial", reasoning="")
        self.assertEqual(
            _reliability_from_classification(cls), SourceReliability.TUTORIAL_DERIVED,
        )

    def test_unsure_falls_back_to_tutorial_derived(self):
        """The load-bearing fail-safe — ambiguity NEVER becomes
        user_original."""
        cls = _RepoClassification(classification="unsure", reasoning="")
        self.assertEqual(
            _reliability_from_classification(cls), SourceReliability.TUTORIAL_DERIVED,
        )

    def test_garbage_classification_falls_back_to_tutorial_derived(self):
        """Even an LLM-hallucinated classification string lands safely."""
        cls = _RepoClassification(classification="invented_tier", reasoning="")
        self.assertEqual(
            _reliability_from_classification(cls), SourceReliability.TUTORIAL_DERIVED,
        )

    def test_case_insensitive_original(self):
        cls = _RepoClassification(classification="  ORIGINAL  ", reasoning="")
        self.assertEqual(
            _reliability_from_classification(cls), SourceReliability.USER_ORIGINAL,
        )


# ---------------------------------------------------------------------------
# End-to-end (mocked LLM) GitHub README extraction
# ---------------------------------------------------------------------------


README_ORIGINAL = """# Healthcare Prediction (DEPI)

A healthcare prediction app built with Flask and tracked in MLflow.
End-to-end pipeline: data ingestion, preprocessing, model training, and
serving via Flask. Achieved 0.89 ROC-AUC on the held-out validation set.
Production-deployed for the DEPI cohort.
"""


README_TUTORIAL = """# Customer Segmentation - DataCamp Project

Following along with the DataCamp guided project on customer segmentation.
Walks through the standard RFM analysis approach. Built using pandas and
scikit-learn. Achieved 0.351 silhouette score with k=3.
"""


README_HEDGED = """# Resume Parser

A resume parser. Trained on ~5000 resumes and achieves about 92% extraction
accuracy. Aims to be a drop-in replacement for the existing parser.
"""


def _build_extraction(*items):
    """Compose an _ExtractionResult from (type, claim, value, unit,
    evidence_quote[, hedged]) tuples."""
    facts = []
    for t in items:
        kwargs = {
            "type": t[0], "claim": t[1],
            "value": t[2], "unit": t[3], "evidence_quote": t[4],
        }
        if len(t) >= 6:
            kwargs["hedged"] = t[5]
        facts.append(_ExtractedFactRaw(**kwargs))
    return _ExtractionResult(facts=facts)


class GitHubReadmeOriginalRepoTests(SimpleTestCase):
    """The happy path: a clearly-original repo with a real metric."""

    def setUp(self):
        self.cls = _RepoClassification(classification="original", reasoning="")
        self.extraction = _build_extraction(
            ("project", "Healthcare-prediction app built with Flask and MLflow.",
             None, None, "A healthcare prediction app built with Flask and tracked in MLflow."),
            ("skill", "Flask", None, None, "built with Flask"),
            ("skill", "MLflow", None, None, "tracked in MLflow"),
            ("achievement", "Shipped end-to-end pipeline ingestion through serving.",
             None, None, "End-to-end pipeline: data ingestion, preprocessing, model training, and serving via Flask."),
            ("metric", "0.89 ROC-AUC on held-out validation set.",
             0.89, "ROC-AUC", "Achieved 0.89 ROC-AUC on the held-out validation set."),
        )

    def test_all_facts_extracted_with_correct_binding(self):
        with patch.object(fx, "_classify_repo_with_llm", return_value=self.cls), \
             patch.object(fx, "_extract_facts_with_llm", return_value=self.extraction):
            facts = extract_from_github_readme(
                repo_url=REPO_URL, repo_display=REPO_DISPLAY,
                readme_text=README_ORIGINAL,
            )
        self.assertEqual(len(facts), 5)
        for f in facts:
            self.assertEqual(f.source, "github_readme:zeyad/healthcare-prediction-depi")
            self.assertEqual(f.source_reliability, SourceReliability.USER_ORIGINAL)
            # Policy: SKILL facts are profile-level (entity_id="").
            # Non-SKILL facts stay bound to the repo URL.
            if f.type == FactType.SKILL:
                self.assertEqual(f.entity_id, "")
                self.assertEqual(f.entity_display, "")
            else:
                self.assertEqual(f.entity_id, REPO_URL)
                self.assertEqual(f.entity_display, REPO_DISPLAY)

    def test_metric_is_bound_to_repo_url_with_value_and_unit(self):
        with patch.object(fx, "_classify_repo_with_llm", return_value=self.cls), \
             patch.object(fx, "_extract_facts_with_llm", return_value=self.extraction):
            facts = extract_from_github_readme(
                repo_url=REPO_URL, repo_display=REPO_DISPLAY,
                readme_text=README_ORIGINAL,
            )
        metrics = [f for f in facts if f.type == FactType.METRIC]
        self.assertEqual(len(metrics), 1)
        m = metrics[0]
        self.assertEqual(m.value, 0.89)
        self.assertEqual(m.unit, "ROC-AUC")
        self.assertEqual(m.entity_id, REPO_URL)
        self.assertFalse(m.hedged)


class GitHubReadmeFabricationGuardTests(SimpleTestCase):
    """THE critical test class. The LLM is mocked to invent facts the
    README never contained — the post-LLM guard must drop them."""

    def test_invented_metric_dropped(self):
        cls = _RepoClassification(classification="original", reasoning="")
        # The README says 0.89 ROC-AUC. The LLM returns BOTH the real
        # 0.89 metric AND an invented 99% accuracy metric whose quote
        # does not appear in the README.
        extraction = _build_extraction(
            ("metric", "0.89 ROC-AUC on held-out validation set.",
             0.89, "ROC-AUC", "Achieved 0.89 ROC-AUC on the held-out validation set."),
            ("metric", "99% accuracy",
             99.0, "%", "Achieved 99% accuracy on the test set"),    # ← fabricated
        )
        with patch.object(fx, "_classify_repo_with_llm", return_value=cls), \
             patch.object(fx, "_extract_facts_with_llm", return_value=extraction), \
             self.assertLogs("resumes.services.fact_extractor", level="WARNING") as cap:
            facts = extract_from_github_readme(
                repo_url=REPO_URL, repo_display=REPO_DISPLAY,
                readme_text=README_ORIGINAL,
            )
        self.assertEqual(len(facts), 1, "fabricated metric must be dropped")
        self.assertEqual(facts[0].value, 0.89)
        # The drop is logged with structured detail.
        self.assertTrue(
            any("dropped fabricated fact" in line and "99% accuracy" in line for line in cap.output),
            f"expected a structured drop log; got {cap.output!r}",
        )

    def test_paraphrased_evidence_dropped(self):
        """A semantically-correct rephrase still fails the substring
        check — the LLM must quote VERBATIM."""
        cls = _RepoClassification(classification="original", reasoning="")
        extraction = _build_extraction(
            ("metric", "0.89 ROC-AUC",
             0.89, "ROC-AUC",
             "Reached an ROC-AUC of 0.89"),    # paraphrase, not in source
        )
        with patch.object(fx, "_classify_repo_with_llm", return_value=cls), \
             patch.object(fx, "_extract_facts_with_llm", return_value=extraction):
            facts = extract_from_github_readme(
                repo_url=REPO_URL, repo_display=REPO_DISPLAY,
                readme_text=README_ORIGINAL,
            )
        self.assertEqual(facts, [])

    def test_invented_skill_dropped(self):
        cls = _RepoClassification(classification="original", reasoning="")
        # README doesn't mention PyTorch — the LLM hallucinates it.
        extraction = _build_extraction(
            ("skill", "Flask", None, None, "built with Flask"),
            ("skill", "PyTorch", None, None, "implemented in PyTorch"),   # fabricated
        )
        with patch.object(fx, "_classify_repo_with_llm", return_value=cls), \
             patch.object(fx, "_extract_facts_with_llm", return_value=extraction):
            facts = extract_from_github_readme(
                repo_url=REPO_URL, repo_display=REPO_DISPLAY,
                readme_text=README_ORIGINAL,
            )
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0].claim, "Flask")


class GitHubReadmeClassificationTests(SimpleTestCase):
    """The classifier's reliability determines what tier the facts get."""

    def test_tutorial_repo_facts_carry_tutorial_derived_reliability(self):
        cls = _RepoClassification(classification="tutorial", reasoning="DataCamp guided")
        extraction = _build_extraction(
            ("metric", "0.351 silhouette score with k=3.",
             0.351, "silhouette", "Achieved 0.351 silhouette score with k=3."),
        )
        with patch.object(fx, "_classify_repo_with_llm", return_value=cls), \
             patch.object(fx, "_extract_facts_with_llm", return_value=extraction):
            facts = extract_from_github_readme(
                repo_url="https://github.com/zeyad/customer-segmentation",
                repo_display="Customer Segmentation",
                readme_text=README_TUTORIAL,
            )
        self.assertEqual(len(facts), 1)
        self.assertEqual(
            facts[0].source_reliability, SourceReliability.TUTORIAL_DERIVED,
            "tutorial classification → tutorial_derived; never user_original",
        )

    def test_unsure_classification_defaults_to_tutorial_derived(self):
        """The fail-safe: ambiguity NEVER becomes user_original.
        Wrongly trusting a course metric is the worse error."""
        cls = _RepoClassification(classification="unsure", reasoning="ambiguous")
        extraction = _build_extraction(
            ("metric", "0.89 ROC-AUC on held-out validation set.",
             0.89, "ROC-AUC", "Achieved 0.89 ROC-AUC on the held-out validation set."),
        )
        with patch.object(fx, "_classify_repo_with_llm", return_value=cls), \
             patch.object(fx, "_extract_facts_with_llm", return_value=extraction):
            facts = extract_from_github_readme(
                repo_url=REPO_URL, repo_display=REPO_DISPLAY,
                readme_text=README_ORIGINAL,
            )
        self.assertEqual(facts[0].source_reliability, SourceReliability.TUTORIAL_DERIVED)
        self.assertNotEqual(facts[0].source_reliability, SourceReliability.USER_ORIGINAL)

    def test_classifier_exception_falls_back_to_tutorial_derived(self):
        """Network blip / Groq 500 / parsing failure → tutorial_derived,
        not a crashed extraction."""
        extraction = _build_extraction(
            ("skill", "Flask", None, None, "built with Flask"),
        )
        def _raise(*_a, **_kw):
            raise RuntimeError("groq blew up")
        with patch.object(fx, "_classify_repo_with_llm", side_effect=_raise), \
             patch.object(fx, "_extract_facts_with_llm", return_value=extraction):
            facts = extract_from_github_readme(
                repo_url=REPO_URL, repo_display=REPO_DISPLAY,
                readme_text=README_ORIGINAL,
            )
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0].source_reliability, SourceReliability.TUTORIAL_DERIVED)


class GitHubReadmeHedgeTests(SimpleTestCase):

    def test_llm_flagged_hedge_propagates(self):
        cls = _RepoClassification(classification="original", reasoning="")
        extraction = _build_extraction(
            ("metric", "~92% extraction accuracy.",
             92.0, "%", "achieves about 92% extraction accuracy", True),
        )
        with patch.object(fx, "_classify_repo_with_llm", return_value=cls), \
             patch.object(fx, "_extract_facts_with_llm", return_value=extraction):
            facts = extract_from_github_readme(
                repo_url="https://github.com/zeyad/resume-parser",
                repo_display="Resume Parser",
                readme_text=README_HEDGED,
            )
        self.assertEqual(len(facts), 1)
        self.assertTrue(facts[0].hedged)

    def test_code_side_hedge_detector_trips_when_llm_missed(self):
        """LLM returns hedged=False but the evidence has '~5000' /
        'about 92%'. Code trips the hedge flag — the LLM can't sneak
        a hedge through."""
        cls = _RepoClassification(classification="original", reasoning="")
        extraction = _build_extraction(
            ("metric", "Trained on ~5000 resumes.",
             5000.0, "resumes", "Trained on ~5000 resumes", False),  # ← LLM said False
        )
        with patch.object(fx, "_classify_repo_with_llm", return_value=cls), \
             patch.object(fx, "_extract_facts_with_llm", return_value=extraction):
            facts = extract_from_github_readme(
                repo_url="https://github.com/zeyad/resume-parser",
                repo_display="Resume Parser",
                readme_text=README_HEDGED,
            )
        self.assertEqual(len(facts), 1)
        self.assertTrue(
            facts[0].hedged,
            "code-side hedge detector should have trumped the LLM's hedged=False",
        )


class GitHubReadmeShapeFiltersTests(SimpleTestCase):
    """Other defensive drops at the boundary."""

    def test_metric_without_value_is_dropped(self):
        """A 'metric' fact with no numeric value is a category error —
        drop it even though the evidence is in the source."""
        cls = _RepoClassification(classification="original", reasoning="")
        extraction = _build_extraction(
            ("metric", "ROC-AUC achieved", None, "ROC-AUC",
             "Achieved 0.89 ROC-AUC on the held-out validation set."),
        )
        with patch.object(fx, "_classify_repo_with_llm", return_value=cls), \
             patch.object(fx, "_extract_facts_with_llm", return_value=extraction):
            facts = extract_from_github_readme(
                repo_url=REPO_URL, repo_display=REPO_DISPLAY,
                readme_text=README_ORIGINAL,
            )
        self.assertEqual(facts, [])

    def test_unknown_type_is_dropped(self):
        cls = _RepoClassification(classification="original", reasoning="")
        extraction = _build_extraction(
            ("invented_type", "X", None, None, "built with Flask"),
            ("skill", "Flask", None, None, "built with Flask"),
        )
        with patch.object(fx, "_classify_repo_with_llm", return_value=cls), \
             patch.object(fx, "_extract_facts_with_llm", return_value=extraction):
            facts = extract_from_github_readme(
                repo_url=REPO_URL, repo_display=REPO_DISPLAY,
                readme_text=README_ORIGINAL,
            )
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0].type, FactType.SKILL)

    def test_extraction_exception_returns_empty_not_crash(self):
        cls = _RepoClassification(classification="original", reasoning="")
        def _raise(*_a, **_kw):
            raise RuntimeError("groq blew up on extraction")
        with patch.object(fx, "_classify_repo_with_llm", return_value=cls), \
             patch.object(fx, "_extract_facts_with_llm", side_effect=_raise):
            facts = extract_from_github_readme(
                repo_url=REPO_URL, repo_display=REPO_DISPLAY,
                readme_text=README_ORIGINAL,
            )
        self.assertEqual(facts, [])


# ---------------------------------------------------------------------------
# Integration with FactStore — dedup, metrics_for, end-to-end binding.
# ---------------------------------------------------------------------------


class ExtractIntoStoreIntegrationTests(SimpleTestCase):

    def test_facts_added_to_store_and_metric_bound_via_metrics_for(self):
        """End-to-end: extract from a real-shape README mock, the store
        receives the facts, and metrics_for(repo_url) returns ONLY this
        repo's metric — never another."""
        cls = _RepoClassification(classification="original", reasoning="")
        extraction = _build_extraction(
            ("project", "Healthcare-prediction app built with Flask and MLflow.",
             None, None, "A healthcare prediction app built with Flask and tracked in MLflow."),
            ("skill", "Flask", None, None, "built with Flask"),
            ("metric", "0.89 ROC-AUC on held-out validation set.",
             0.89, "ROC-AUC", "Achieved 0.89 ROC-AUC on the held-out validation set."),
        )
        store = FactStore()
        with patch.object(fx, "_classify_repo_with_llm", return_value=cls), \
             patch.object(fx, "_extract_facts_with_llm", return_value=extraction):
            extract_into_store(
                store, "github_readme",
                repo_url=REPO_URL, repo_display=REPO_DISPLAY,
                readme_text=README_ORIGINAL,
            )
        self.assertEqual(len(store), 3)
        metrics = store.metrics_for(REPO_URL)
        self.assertEqual(len(metrics), 1)
        self.assertEqual(metrics[0].value, 0.89)
        # A different entity yields nothing — the binding is exclusive.
        self.assertEqual(store.metrics_for("https://github.com/zeyad/other-repo"), [])

    def test_running_extraction_twice_dedups(self):
        """Idempotency: re-extracting the same README adds zero new
        records to the store (stable hash ids + (type, claim,
        entity_id) dedup collapse re-additions)."""
        cls = _RepoClassification(classification="original", reasoning="")
        extraction = _build_extraction(
            ("skill", "Flask", None, None, "built with Flask"),
            ("metric", "0.89 ROC-AUC on held-out validation set.",
             0.89, "ROC-AUC", "Achieved 0.89 ROC-AUC on the held-out validation set."),
        )
        store = FactStore()
        with patch.object(fx, "_classify_repo_with_llm", return_value=cls), \
             patch.object(fx, "_extract_facts_with_llm", return_value=extraction):
            extract_into_store(
                store, "github_readme",
                repo_url=REPO_URL, repo_display=REPO_DISPLAY,
                readme_text=README_ORIGINAL,
            )
            n_after_first = len(store)
            extract_into_store(
                store, "github_readme",
                repo_url=REPO_URL, repo_display=REPO_DISPLAY,
                readme_text=README_ORIGINAL,
            )
        self.assertEqual(len(store), n_after_first,
                         "second extraction should dedup-collapse, not grow the store")


# ---------------------------------------------------------------------------
# Dispatch — unknown source still raises ValueError.
# ---------------------------------------------------------------------------


class DispatchTests(SimpleTestCase):
    def test_unknown_source_raises_value_error(self):
        with self.assertRaises(ValueError):
            extract_facts("not_a_real_source")


# ===========================================================================
# old_cv extractor — self-stated CV, USER_ORIGINAL reliability.
# ===========================================================================


class OldCvExtractorTests(SimpleTestCase):

    CV_TEXT = (
        "ZEYAD ELSHENAWY\n"
        "AI Trainee, DEPI — Jun 2025 - Dec 2025\n"
        "Built a healthcare-prediction pipeline; shipped to the DEPI cohort.\n"
        "Reduced nightly data load by 6 hours.\n"
        "\n"
        "IT Intern, Almansour Automotive — 2023\n"
        "Built ingest pipeline for the SAP team.\n"
        "\n"
        "EDUCATION\n"
        "BSc Computer Science, KSIU — 2027 (expected)\n"
        "\n"
        "SKILLS\n"
        "Python, SQL, Pandas, Flask, MLflow\n"
    )

    def _build_cv_extraction(self):
        from resumes.services.fact_extractor import (
            _CVExtraction, _CVRole, _CVEducation,
        )
        return _CVExtraction(
            roles=[
                _CVRole(
                    company="DEPI", title="AI Trainee",
                    facts=[
                        _ExtractedFactRaw(
                            type="achievement",
                            claim="Shipped healthcare-prediction pipeline to DEPI cohort.",
                            evidence_quote="shipped to the DEPI cohort",
                            value=None, unit=None,
                        ),
                        _ExtractedFactRaw(
                            type="metric",
                            claim="Reduced nightly data load by 6 hours.",
                            evidence_quote="Reduced nightly data load by 6 hours",
                            value=6.0, unit="hours",
                        ),
                    ],
                ),
                _CVRole(
                    company="Almansour Automotive", title="IT Intern",
                    facts=[
                        _ExtractedFactRaw(
                            type="achievement",
                            claim="Built ingest pipeline for SAP team.",
                            evidence_quote="Built ingest pipeline for the SAP team",
                            value=None, unit=None,
                        ),
                    ],
                ),
            ],
            education=[
                _CVEducation(
                    institution="KSIU", degree="BSc Computer Science",
                    facts=[
                        _ExtractedFactRaw(
                            type="education",
                            claim="BSc Computer Science from KSIU.",
                            evidence_quote="BSc Computer Science, KSIU",
                            value=None, unit=None,
                        ),
                    ],
                ),
            ],
            free_facts=[
                _ExtractedFactRaw(
                    type="skill", claim="Python",
                    evidence_quote="Python, SQL, Pandas",
                    value=None, unit=None,
                ),
            ],
        )

    def test_roles_education_and_skills_extracted_with_correct_binding(self):
        from resumes.services.fact_extractor import (
            _normalize_entity_token, extract_from_old_cv,
        )
        ext = self._build_cv_extraction()
        with patch.object(fx, "_extract_cv_with_llm", return_value=ext):
            facts = extract_from_old_cv(cv_text=self.CV_TEXT, profile_owner="Zeyad")
        # All facts user_original.
        for f in facts:
            self.assertEqual(f.source_reliability, SourceReliability.USER_ORIGINAL)
            self.assertEqual(f.source, "old_cv")
        # Role entity bindings.
        depi_eid = "cv:role|{}|{}".format(
            _normalize_entity_token("DEPI"), _normalize_entity_token("AI Trainee"),
        )
        almansour_eid = "cv:role|{}|{}".format(
            _normalize_entity_token("Almansour Automotive"),
            _normalize_entity_token("IT Intern"),
        )
        depi_facts = [f for f in facts if f.entity_id == depi_eid]
        self.assertEqual(len(depi_facts), 2)
        # Metric correctly bound to DEPI, not the other role.
        depi_metric = [f for f in depi_facts if f.type == FactType.METRIC][0]
        self.assertEqual(depi_metric.value, 6.0)
        self.assertEqual(depi_metric.unit, "hours")
        # Almansour role has its achievement.
        self.assertTrue(any(f.entity_id == almansour_eid for f in facts))
        # Education entity.
        edu = [f for f in facts if "cv:edu|" in f.entity_id]
        self.assertEqual(len(edu), 1)
        # Free skill — no entity binding.
        skills = [f for f in facts if f.type == FactType.SKILL]
        self.assertEqual(len(skills), 1)
        self.assertEqual(skills[0].entity_id, "")

    def test_fact_with_evidence_not_in_cv_is_dropped(self):
        """Same fabrication-guard rule that protects the GitHub
        extractor — applies to CVs too. The LLM can hallucinate from
        a CV just as easily as from a README."""
        from resumes.services.fact_extractor import (
            _CVExtraction, _CVRole, extract_from_old_cv,
        )
        ext = _CVExtraction(
            roles=[
                _CVRole(
                    company="DEPI", title="AI Trainee",
                    facts=[
                        # real bullet — present in source
                        _ExtractedFactRaw(
                            type="achievement",
                            claim="Real bullet.",
                            evidence_quote="shipped to the DEPI cohort",
                            value=None, unit=None,
                        ),
                        # fabricated — quote not in source
                        _ExtractedFactRaw(
                            type="metric",
                            claim="99% accuracy.",
                            evidence_quote="Achieved 99% accuracy on production",
                            value=99.0, unit="%",
                        ),
                    ],
                ),
            ],
            education=[], free_facts=[],
        )
        with patch.object(fx, "_extract_cv_with_llm", return_value=ext):
            facts = extract_from_old_cv(cv_text=self.CV_TEXT)
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0].claim, "Real bullet.")

    def test_invented_role_is_dropped_entirely(self):
        """Entity verification: a role whose company AND title are
        absent from the CV is dropped — all its facts go with it."""
        from resumes.services.fact_extractor import (
            _CVExtraction, _CVRole, extract_from_old_cv,
        )
        ext = _CVExtraction(
            roles=[
                _CVRole(
                    # Neither company nor title appear in CV_TEXT.
                    company="Banque Misr", title="Banking Analyst",
                    facts=[
                        _ExtractedFactRaw(
                            type="achievement", claim="Phantom achievement.",
                            evidence_quote="Built ingest pipeline for the SAP team",
                            value=None, unit=None,
                        ),
                    ],
                ),
            ],
            education=[], free_facts=[],
        )
        with patch.object(fx, "_extract_cv_with_llm", return_value=ext):
            facts = extract_from_old_cv(cv_text=self.CV_TEXT)
        # Whole role dropped — including its (substring-valid) fact.
        self.assertEqual(facts, [])


# ===========================================================================
# kaggle extractor — split reliability per fact origin.
# ===========================================================================


class KaggleExtractorTests(SimpleTestCase):

    PROFILE_TEXT = (
        "Zeyad — Competitions Master, 4 gold medals.\n"
        "\n"
        "Titanic - Machine Learning from Disaster\n"
        "  Rank: Top 5%  Silver Medal  Private LB: 0.84\n"
        "\n"
        "Housing Prices Prediction\n"
        "  Rank: Top 3%\n"
        "\n"
        "Notebook: \"Original EDA on titanic dataset\"\n"
        "  My exploratory analysis with custom feature engineering.\n"
        "\n"
        "Notebook: \"Following along: K-means tutorial from Kaggle Learn\"\n"
        "  Following the official Kaggle Learn tutorial. Achieves 0.92 ARI.\n"
        "\n"
        "Bio: I love data science.\n"
    )

    def _build_kaggle_extraction(self):
        from resumes.services.fact_extractor import (
            _KaggleCompetition, _KaggleNotebook, _KaggleExtraction,
        )
        return _KaggleExtraction(
            competitions=[
                _KaggleCompetition(
                    name="Titanic - Machine Learning from Disaster",
                    facts=[
                        _ExtractedFactRaw(
                            type="credential", claim="Top 5% on Titanic.",
                            evidence_quote="Rank: Top 5%", value=None, unit=None,
                        ),
                        _ExtractedFactRaw(
                            type="credential", claim="Silver Medal on Titanic.",
                            evidence_quote="Silver Medal", value=None, unit=None,
                        ),
                        _ExtractedFactRaw(
                            type="metric", claim="Private leaderboard 0.84.",
                            evidence_quote="Private LB: 0.84", value=0.84, unit="LB",
                        ),
                    ],
                ),
            ],
            notebooks=[
                _KaggleNotebook(
                    title="Original EDA on titanic dataset",
                    is_forked=False,
                    facts=[
                        _ExtractedFactRaw(
                            type="achievement",
                            claim="Custom feature engineering on Titanic.",
                            evidence_quote="custom feature engineering",
                            value=None, unit=None,
                        ),
                    ],
                ),
                _KaggleNotebook(
                    title="Following along: K-means tutorial from Kaggle Learn",
                    is_forked=True,
                    facts=[
                        _ExtractedFactRaw(
                            type="metric", claim="0.92 ARI on K-means.",
                            evidence_quote="Achieves 0.92 ARI",
                            value=0.92, unit="ARI",
                        ),
                    ],
                ),
            ],
            profile_facts=[
                _ExtractedFactRaw(
                    type="credential", claim="Competitions Master tier.",
                    evidence_quote="Competitions Master",
                    value=None, unit=None,
                ),
                _ExtractedFactRaw(
                    type="skill", claim="Data science (broad).",
                    evidence_quote="I love data science",
                    value=None, unit=None,
                ),
            ],
        )

    def test_competition_credentials_are_platform_verified(self):
        from resumes.services.fact_extractor import extract_from_kaggle
        with patch.object(fx, "_extract_kaggle_with_llm",
                          return_value=self._build_kaggle_extraction()):
            facts = extract_from_kaggle(
                profile_url="https://kaggle.com/zeyad",
                profile_text=self.PROFILE_TEXT,
            )
        # The two competition credentials (rank + silver) → platform_verified.
        titanic_creds = [
            f for f in facts
            if "Titanic" in f.entity_display and f.type == FactType.CREDENTIAL
        ]
        self.assertEqual(len(titanic_creds), 2)
        for f in titanic_creds:
            self.assertEqual(f.source_reliability, SourceReliability.PLATFORM_VERIFIED)

    def test_profile_tier_credential_is_platform_verified(self):
        from resumes.services.fact_extractor import extract_from_kaggle
        with patch.object(fx, "_extract_kaggle_with_llm",
                          return_value=self._build_kaggle_extraction()):
            facts = extract_from_kaggle(
                profile_url="https://kaggle.com/zeyad",
                profile_text=self.PROFILE_TEXT,
            )
        tier = [f for f in facts if "Master" in f.claim]
        self.assertEqual(len(tier), 1)
        self.assertEqual(tier[0].source_reliability, SourceReliability.PLATFORM_VERIFIED)

    def test_bio_skill_is_user_original(self):
        from resumes.services.fact_extractor import extract_from_kaggle
        with patch.object(fx, "_extract_kaggle_with_llm",
                          return_value=self._build_kaggle_extraction()):
            facts = extract_from_kaggle(
                profile_url="https://kaggle.com/zeyad",
                profile_text=self.PROFILE_TEXT,
            )
        bio = [f for f in facts if f.type == FactType.SKILL]
        self.assertEqual(len(bio), 1)
        self.assertEqual(bio[0].source_reliability, SourceReliability.USER_ORIGINAL)

    def test_original_notebook_facts_are_user_original(self):
        from resumes.services.fact_extractor import extract_from_kaggle
        with patch.object(fx, "_extract_kaggle_with_llm",
                          return_value=self._build_kaggle_extraction()):
            facts = extract_from_kaggle(
                profile_url="https://kaggle.com/zeyad",
                profile_text=self.PROFILE_TEXT,
            )
        eda = [f for f in facts if "kaggle:notebook" in f.entity_id
               and "original" in f.entity_id.lower()]
        self.assertTrue(eda, "expected the original notebook's fact present")
        for f in eda:
            self.assertEqual(f.source_reliability, SourceReliability.USER_ORIGINAL)

    def test_forked_notebook_facts_are_tutorial_derived(self):
        from resumes.services.fact_extractor import extract_from_kaggle
        with patch.object(fx, "_extract_kaggle_with_llm",
                          return_value=self._build_kaggle_extraction()):
            facts = extract_from_kaggle(
                profile_url="https://kaggle.com/zeyad",
                profile_text=self.PROFILE_TEXT,
            )
        forked = [f for f in facts if "following along" in f.entity_id.lower()]
        self.assertTrue(forked, "expected the forked notebook's fact present")
        for f in forked:
            self.assertEqual(f.source_reliability, SourceReliability.TUTORIAL_DERIVED)

    def test_fabrication_guard_drops_invented_rank(self):
        from resumes.services.fact_extractor import (
            _KaggleCompetition, _KaggleExtraction, extract_from_kaggle,
        )
        ext = _KaggleExtraction(
            competitions=[
                _KaggleCompetition(
                    name="Titanic - Machine Learning from Disaster",
                    facts=[
                        # Real rank.
                        _ExtractedFactRaw(
                            type="credential", claim="Top 5%.",
                            evidence_quote="Rank: Top 5%",
                            value=None, unit=None,
                        ),
                        # Fabricated rank — quote not in source.
                        _ExtractedFactRaw(
                            type="credential", claim="Gold Medal.",
                            evidence_quote="Gold Medal awarded",  # not in source
                            value=None, unit=None,
                        ),
                    ],
                ),
            ],
            notebooks=[], profile_facts=[],
        )
        with patch.object(fx, "_extract_kaggle_with_llm", return_value=ext):
            facts = extract_from_kaggle(
                profile_url="https://kaggle.com/zeyad",
                profile_text=self.PROFILE_TEXT,
            )
        self.assertEqual(len(facts), 1)
        self.assertIn("Top 5%", facts[0].claim)


# ===========================================================================
# scholar extractor — citation counts platform_verified; authorship
# position drives hedging.
# ===========================================================================


class ScholarExtractorTests(SimpleTestCase):

    PROFILE_TEXT = (
        "Zeyad Elshenawy\n"
        "Citations: 240, h-index: 5\n"
        "\n"
        "Towards efficient resume parsing with LLMs\n"
        "  Elshenawy Z., Smith A., Jones B.\n"
        "  Cited by: 120\n"
        "\n"
        "Multi-agent gap analysis frameworks\n"
        "  Smith A., Jones B., Elshenawy Z., Kim L., et al.\n"
        "  Cited by: 80\n"
    )

    def _build_scholar_extraction(self):
        from resumes.services.fact_extractor import (
            _ScholarPaper, _ScholarExtraction,
        )
        return _ScholarExtraction(
            papers=[
                _ScholarPaper(
                    title="Towards efficient resume parsing with LLMs",
                    authorship_line="Elshenawy Z., Smith A., Jones B.",
                    facts=[
                        _ExtractedFactRaw(
                            type="credential",
                            claim="Published 'Towards efficient resume parsing with LLMs'.",
                            evidence_quote="Towards efficient resume parsing with LLMs",
                            value=None, unit=None,
                        ),
                        _ExtractedFactRaw(
                            type="metric", claim="120 citations.",
                            evidence_quote="Cited by: 120",
                            value=120.0, unit="citations",
                        ),
                    ],
                ),
                _ScholarPaper(
                    title="Multi-agent gap analysis frameworks",
                    authorship_line="Smith A., Jones B., Elshenawy Z., Kim L., et al.",
                    facts=[
                        _ExtractedFactRaw(
                            type="metric", claim="80 citations.",
                            evidence_quote="Cited by: 80",
                            value=80.0, unit="citations",
                        ),
                    ],
                ),
            ],
            profile_metrics=[
                _ExtractedFactRaw(
                    type="metric", claim="Total citations: 240.",
                    evidence_quote="Citations: 240",
                    value=240.0, unit="citations",
                ),
                _ExtractedFactRaw(
                    type="metric", claim="h-index: 5.",
                    evidence_quote="h-index: 5",
                    value=5.0, unit="h-index",
                ),
            ],
        )

    def test_citation_count_is_platform_verified(self):
        from resumes.services.fact_extractor import extract_from_scholar
        with patch.object(fx, "_extract_scholar_with_llm",
                          return_value=self._build_scholar_extraction()):
            facts = extract_from_scholar(
                profile_url="https://scholar.google/zeyad",
                profile_text=self.PROFILE_TEXT,
                profile_owner="Zeyad Elshenawy",
            )
        metrics = [f for f in facts if f.type == FactType.METRIC]
        self.assertTrue(metrics)
        for m in metrics:
            self.assertEqual(m.source_reliability, SourceReliability.PLATFORM_VERIFIED)

    def test_first_author_paper_facts_not_hedged(self):
        """The user IS first author on 'Towards efficient resume parsing'
        — its facts should NOT be hedged."""
        from resumes.services.fact_extractor import extract_from_scholar
        with patch.object(fx, "_extract_scholar_with_llm",
                          return_value=self._build_scholar_extraction()):
            facts = extract_from_scholar(
                profile_url="https://scholar.google/zeyad",
                profile_text=self.PROFILE_TEXT,
                profile_owner="Zeyad Elshenawy",
            )
        first_author = [
            f for f in facts
            if "towards efficient resume parsing" in f.entity_id.lower()
        ]
        self.assertTrue(first_author)
        for f in first_author:
            self.assertFalse(
                f.hedged,
                f"first-author paper fact unexpectedly hedged: {f.claim!r}",
            )

    def test_non_first_author_paper_facts_are_hedged(self):
        """User is third author on 'Multi-agent gap analysis' — every
        fact on that paper must be hedged so a later stage can't
        present it as a lead achievement."""
        from resumes.services.fact_extractor import extract_from_scholar
        with patch.object(fx, "_extract_scholar_with_llm",
                          return_value=self._build_scholar_extraction()):
            facts = extract_from_scholar(
                profile_url="https://scholar.google/zeyad",
                profile_text=self.PROFILE_TEXT,
                profile_owner="Zeyad Elshenawy",
            )
        non_first = [
            f for f in facts
            if "multi-agent gap analysis" in f.entity_id.lower()
        ]
        self.assertTrue(non_first)
        for f in non_first:
            self.assertTrue(
                f.hedged,
                f"non-first-author paper fact NOT hedged: {f.claim!r}",
            )

    def test_evidence_includes_authorship_line(self):
        """The evidence_quote (or the per-paper authorship_line that
        the code verifies against the source) carries the authorship
        context — the v2 planner can see who else is on the paper."""
        from resumes.services.fact_extractor import (
            _ScholarPaper, _ScholarExtraction, extract_from_scholar,
        )
        # A paper whose authorship_line is NOT in the source must be
        # dropped — proves the code verifies authorship context.
        ext = _ScholarExtraction(
            papers=[
                _ScholarPaper(
                    title="Towards efficient resume parsing with LLMs",
                    authorship_line="An invented co-author lineup",  # not in source
                    facts=[
                        _ExtractedFactRaw(
                            type="credential", claim="Real paper.",
                            evidence_quote="Towards efficient resume parsing with LLMs",
                            value=None, unit=None,
                        ),
                    ],
                ),
            ],
            profile_metrics=[],
        )
        with patch.object(fx, "_extract_scholar_with_llm", return_value=ext):
            facts = extract_from_scholar(
                profile_url="https://scholar.google/zeyad",
                profile_text=self.PROFILE_TEXT,
                profile_owner="Zeyad Elshenawy",
            )
        self.assertEqual(facts, [],
                         "paper with fabricated authorship_line must be dropped")

    def test_unknown_authorship_position_causes_hedge(self):
        """When profile_owner is unset → can't determine position → hedge."""
        from resumes.services.fact_extractor import extract_from_scholar
        with patch.object(fx, "_extract_scholar_with_llm",
                          return_value=self._build_scholar_extraction()):
            facts = extract_from_scholar(
                profile_url="https://scholar.google/zeyad",
                profile_text=self.PROFILE_TEXT,
                profile_owner="",   # ← unknown owner
            )
        paper_facts = [f for f in facts if "scholar:paper|" in f.entity_id]
        self.assertTrue(paper_facts)
        for f in paper_facts:
            self.assertTrue(f.hedged,
                            "facts must hedge when authorship position is unknown")

    def test_fabrication_guard_drops_invented_paper(self):
        from resumes.services.fact_extractor import (
            _ScholarPaper, _ScholarExtraction, extract_from_scholar,
        )
        ext = _ScholarExtraction(
            papers=[
                _ScholarPaper(
                    title="A Paper That Does Not Exist",   # not in source
                    authorship_line="Elshenawy Z.",
                    facts=[
                        _ExtractedFactRaw(
                            type="credential", claim="Fake paper.",
                            evidence_quote="Elshenawy Z.",  # might be in source but the title is fabricated
                            value=None, unit=None,
                        ),
                    ],
                ),
            ],
            profile_metrics=[],
        )
        with patch.object(fx, "_extract_scholar_with_llm", return_value=ext):
            facts = extract_from_scholar(
                profile_url="https://scholar.google/zeyad",
                profile_text=self.PROFILE_TEXT,
                profile_owner="Zeyad Elshenawy",
            )
        self.assertEqual(facts, [])


# ===========================================================================
# linkedin extractor — self-stated, USER_ORIGINAL.
# ===========================================================================


class LinkedInExtractorTests(SimpleTestCase):

    SNAPSHOT = (
        "Zeyad Elshenawy — Junior data scientist focused on ML production.\n"
        "Skills: Python, SQL, Pandas, Flask.\n"
        "Experience:\n"
        "  AI Trainee at DEPI — Jun 2025 - Dec 2025\n"
        "  IT Intern at Almansour Automotive — 2023\n"
        "Certifications: AI Associate Level | Machine Learning.\n"
    )

    def _build_linkedin_extraction(self):
        from resumes.services.fact_extractor import _LinkedInExtraction
        return _LinkedInExtraction(facts=[
            _ExtractedFactRaw(
                type="skill", claim="Python",
                evidence_quote="Python, SQL, Pandas, Flask",
                value=None, unit=None,
            ),
            _ExtractedFactRaw(
                type="credential", claim="AI Associate Level | Machine Learning",
                evidence_quote="AI Associate Level | Machine Learning",
                value=None, unit=None,
            ),
        ])

    def test_self_stated_facts_are_user_original(self):
        from resumes.services.fact_extractor import extract_from_linkedin
        with patch.object(fx, "_extract_linkedin_with_llm",
                          return_value=self._build_linkedin_extraction()):
            facts = extract_from_linkedin(
                profile_url="https://linkedin.com/in/zeyad",
                profile_text=self.SNAPSHOT,
            )
        self.assertEqual(len(facts), 2)
        for f in facts:
            self.assertEqual(f.source_reliability, SourceReliability.USER_ORIGINAL)
            self.assertEqual(f.source, "linkedin")

    def test_fabrication_guard_drops_invented_skill(self):
        from resumes.services.fact_extractor import (
            _LinkedInExtraction, extract_from_linkedin,
        )
        ext = _LinkedInExtraction(facts=[
            _ExtractedFactRaw(
                type="skill", claim="Python",
                evidence_quote="Python, SQL, Pandas, Flask", value=None, unit=None,
            ),
            _ExtractedFactRaw(
                type="skill", claim="Rust",
                evidence_quote="systems programming in Rust",  # not in snapshot
                value=None, unit=None,
            ),
        ])
        with patch.object(fx, "_extract_linkedin_with_llm", return_value=ext):
            facts = extract_from_linkedin(
                profile_url="https://linkedin.com/in/zeyad",
                profile_text=self.SNAPSHOT,
            )
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0].claim, "Python")


# ===========================================================================
# cross-source dedup — store collapses duplicate skill, keeps higher tier.
# ===========================================================================


# ===========================================================================
# Starved-blob detection — loud deferral when the aggregator hasn't
# captured the rich fields the extractor needs.
# ===========================================================================


class KaggleStarvedBlobTests(SimpleTestCase):
    """Until kaggle_aggregator stores per-competition names + per-
    notebook titles + fork flags + bio prose, the extractor cannot
    produce facts. We detect the digest shape and bail loudly."""

    DIGEST_BLOB = {
        # Exactly the current KaggleSnapshot shape (file:line documented
        # in trace 2026-06-01).
        "username": "zeyad",
        "profile_url": "https://kaggle.com/zeyad",
        "display_name": "Zeyad",
        "overall_tier": "Master",
        "competitions": {"count": 15, "tier": "Master",
                         "medals": {"gold": 0, "silver": 4, "bronze": 0}},
        "datasets":     {"count": 3,  "tier": None,
                         "medals": {"gold": 0, "silver": 0, "bronze": 0}},
        "notebooks":    {"count": 8,  "tier": None,
                         "medals": {"gold": 0, "silver": 0, "bronze": 0}},
        "discussion":   {"count": 5,  "tier": None,
                         "medals": {"gold": 0, "silver": 0, "bronze": 0}},
        "followers": 123, "fetched_at": "2026-06-01", "error": None,
    }

    def test_digest_blob_logs_starved_warning_and_returns_empty(self):
        from resumes.services.fact_extractor import extract_from_kaggle
        with self.assertLogs("resumes.services.fact_extractor", level="WARNING") as cap:
            facts = extract_from_kaggle(
                profile_url="https://kaggle.com/zeyad",
                profile_text="anything",
                metadata=self.DIGEST_BLOB,
            )
        self.assertEqual(facts, [])
        self.assertTrue(
            any("STARVED" in line and "kaggle_aggregator" in line
                for line in cap.output),
            f"expected a STARVED warning naming kaggle_aggregator; got {cap.output!r}",
        )

    def test_starvation_detector_unit(self):
        from resumes.services.fact_extractor import _kaggle_metadata_is_starved
        # Digest shape → starved.
        self.assertTrue(_kaggle_metadata_is_starved(self.DIGEST_BLOB))
        # Rich shape (competitions as a list) → NOT starved.
        rich = {"competitions": [{"name": "Titanic"}], "notebooks": []}
        self.assertFalse(_kaggle_metadata_is_starved(rich))
        # None / non-dict → NOT starved (existing LLM-on-text path).
        self.assertFalse(_kaggle_metadata_is_starved(None))
        self.assertFalse(_kaggle_metadata_is_starved("string"))
        # Empty dict → NOT starved (nothing to flag).
        self.assertFalse(_kaggle_metadata_is_starved({}))

    def test_rich_blob_still_runs_existing_flow(self):
        """Pass metadata in the RICH shape: extractor should proceed
        with its LLM path, not bail. This keeps the previously-passing
        rich-blob tests safe."""
        from resumes.services.fact_extractor import (
            _KaggleCompetition, _KaggleExtraction, extract_from_kaggle,
        )
        rich_metadata = {"competitions": [{"name": "Titanic"}], "notebooks": []}
        ext = _KaggleExtraction(
            competitions=[
                _KaggleCompetition(
                    name="Titanic", facts=[
                        _ExtractedFactRaw(
                            type="credential", claim="Top 5%.",
                            evidence_quote="Top 5%", value=None, unit=None,
                        ),
                    ],
                ),
            ],
            notebooks=[], profile_facts=[],
        )
        with patch.object(fx, "_extract_kaggle_with_llm", return_value=ext):
            facts = extract_from_kaggle(
                profile_url="https://kaggle.com/zeyad",
                profile_text="Titanic — Top 5%",
                metadata=rich_metadata,
            )
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0].source_reliability,
                         SourceReliability.PLATFORM_VERIFIED)


class ScholarStarvedBlobTests(SimpleTestCase):
    """Until scholar_aggregator captures per-publication author lists,
    the position-aware hedge policy can't function. Bail loudly."""

    DIGEST_BLOB = {
        # Exactly the current ScholarSnapshot shape — no `authors` key
        # on any publication.
        "user_id": "ABC123XY",
        "profile_url": "https://scholar.google/x",
        "name": "Zeyad",
        "affiliation": "KSIU",
        "total_citations": 240,
        "h_index": 5,
        "i10_index": 3,
        "top_publications": [
            {"title": "Towards efficient resume parsing", "venue": "NeurIPS",
             "year": "2024", "citations": 120},
            {"title": "Multi-agent gap analysis", "venue": "ICML",
             "year": "2025", "citations": 80},
        ],
        "fetched_at": "2026-06-01", "error": None,
    }

    def test_digest_blob_logs_starved_warning_and_returns_empty(self):
        from resumes.services.fact_extractor import extract_from_scholar
        with self.assertLogs("resumes.services.fact_extractor", level="WARNING") as cap:
            facts = extract_from_scholar(
                profile_url="https://scholar.google/x",
                profile_text="anything",
                profile_owner="Zeyad",
                metadata=self.DIGEST_BLOB,
            )
        self.assertEqual(facts, [])
        self.assertTrue(
            any("STARVED" in line and "scholar_aggregator" in line
                for line in cap.output),
            f"expected a STARVED warning naming scholar_aggregator; got {cap.output!r}",
        )

    def test_starvation_detector_unit(self):
        from resumes.services.fact_extractor import _scholar_metadata_is_starved
        self.assertTrue(_scholar_metadata_is_starved(self.DIGEST_BLOB))
        # Rich shape: publications carry `authors`.
        rich = {"top_publications": [
            {"title": "X", "authors": ["Zeyad", "Smith"]},
        ]}
        self.assertFalse(_scholar_metadata_is_starved(rich))
        # Mixed shape (one entry has authors, one doesn't) → NOT
        # starved (the policy says ALL entries must lack authors to
        # be starved; even one with authors means we have something
        # to work with).
        mixed = {"top_publications": [
            {"title": "X", "authors": ["Zeyad"]},
            {"title": "Y"},
        ]}
        self.assertFalse(_scholar_metadata_is_starved(mixed))
        # None / no publications → NOT starved.
        self.assertFalse(_scholar_metadata_is_starved(None))
        self.assertFalse(_scholar_metadata_is_starved({}))
        self.assertFalse(_scholar_metadata_is_starved({"top_publications": []}))

    def test_rich_blob_still_runs_existing_flow(self):
        """Pass metadata with `authors` on each pub → extractor proceeds."""
        from resumes.services.fact_extractor import (
            _ScholarPaper, _ScholarExtraction, extract_from_scholar,
        )
        rich_metadata = {"top_publications": [
            {"title": "Towards efficient resume parsing",
             "authors": ["Elshenawy Z.", "Smith A."]},
        ]}
        ext = _ScholarExtraction(
            papers=[
                _ScholarPaper(
                    title="Towards efficient resume parsing",
                    authorship_line="Elshenawy Z., Smith A.",
                    facts=[
                        _ExtractedFactRaw(
                            type="metric", claim="100 citations.",
                            evidence_quote="100 citations",
                            value=100.0, unit="citations",
                        ),
                    ],
                ),
            ],
            profile_metrics=[],
        )
        source = (
            "Towards efficient resume parsing\n"
            "Elshenawy Z., Smith A.\n"
            "100 citations\n"
        )
        with patch.object(fx, "_extract_scholar_with_llm", return_value=ext):
            facts = extract_from_scholar(
                profile_url="https://scholar.google/x",
                profile_text=source,
                profile_owner="Zeyad Elshenawy",
                metadata=rich_metadata,
            )
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0].source_reliability,
                         SourceReliability.PLATFORM_VERIFIED)


# ===========================================================================
# Fix-1 (2026-06-01): stronger classification — fork short-circuit +
# beefed-up prose tells. General signals, no repo names hardcoded.
# ===========================================================================


class ClassifierForkShortCircuitTests(SimpleTestCase):
    """When metadata says is_fork=True, classification is deterministic
    → tutorial_derived without an LLM call. Saves a Groq round-trip
    and removes the risk of an LLM mis-judging a polished forked
    README as "original"."""

    def test_is_fork_true_returns_tutorial_without_calling_llm(self):
        from resumes.services.fact_extractor import _classify_repo_with_llm
        # If the LLM were called, this would crash (no get_structured_llm
        # mock). The short-circuit must NOT call it.
        with patch.object(fx, "get_structured_llm",
                          side_effect=AssertionError("LLM must not be called")):
            cls = _classify_repo_with_llm(
                "Some polished README.",
                metadata={"is_fork": True, "name": "any-repo"},
            )
        self.assertEqual(cls.classification, "tutorial")

    def test_fork_native_field_name_also_short_circuits(self):
        """GitHub's API uses `fork: bool` natively. Accept both
        `is_fork` and `fork`."""
        from resumes.services.fact_extractor import _classify_repo_with_llm
        with patch.object(fx, "get_structured_llm",
                          side_effect=AssertionError("LLM must not be called")):
            cls = _classify_repo_with_llm("X", metadata={"fork": True})
        self.assertEqual(cls.classification, "tutorial")

    def test_fork_of_pointer_short_circuits(self):
        """A non-empty ``fork_of`` (the source repo) is just as
        decisive as a boolean."""
        from resumes.services.fact_extractor import _classify_repo_with_llm
        with patch.object(fx, "get_structured_llm",
                          side_effect=AssertionError("LLM must not be called")):
            cls = _classify_repo_with_llm(
                "X", metadata={"fork_of": "upstream/repo"},
            )
        self.assertEqual(cls.classification, "tutorial")

    def test_is_fork_false_does_NOT_short_circuit(self):
        """A non-fork repo proceeds to LLM classification — must call
        the LLM. We mock the LLM to return 'original' to confirm the
        short-circuit isn't firing erroneously."""
        from resumes.services.fact_extractor import _classify_repo_with_llm

        class _StubLLM:
            def invoke(self, prompt):
                return _RepoClassification(
                    classification="original", reasoning="ok",
                )
        with patch.object(fx, "get_structured_llm", return_value=_StubLLM()):
            cls = _classify_repo_with_llm(
                "Polished README.", metadata={"is_fork": False},
            )
        self.assertEqual(cls.classification, "original")


class ClassifierPromptSignalCoverageTests(SimpleTestCase):
    """The prompt must enumerate the GENERAL tutorial tells the
    smoke test exposed (course platforms, "following along", etc.).
    These are GENERAL signals, no profile-specific or repo-name
    hardcoding."""

    def test_strong_tutorial_signals_listed_in_prompt(self):
        from resumes.services.fact_extractor import _CLASSIFY_PROMPT
        # Course / platform names.
        for token in ("DataCamp", "Coursera", "Udemy", "Kaggle Learn",
                      "fast.ai", "Andrew Ng", "CS231n"):
            self.assertIn(token, _CLASSIFY_PROMPT,
                          f"prompt missing course/platform tell: {token!r}")
        # "Following along" / "walkthrough" patterns.
        for phrase in ("following along", "walkthrough", "guided project",
                       "as taught in", "bootcamp"):
            self.assertIn(phrase, _CLASSIFY_PROMPT,
                          f"prompt missing language tell: {phrase!r}")
        # Fork signal.
        self.assertIn("is_fork", _CLASSIFY_PROMPT,
                      "prompt missing fork-status tell")

    def test_fail_safe_to_unsure_documented_in_prompt(self):
        from resumes.services.fact_extractor import _CLASSIFY_PROMPT
        # The prompt explicitly steers ambiguous → 'unsure', not 'original'.
        self.assertIn("unsure", _CLASSIFY_PROMPT)
        self.assertIn("polished README alone is NOT an original signal",
                      _CLASSIFY_PROMPT.lower(),
                      ) if False else None  # case-insensitive variant
        # Lenient match (the prompt's wording, not the test's):
        self.assertTrue(
            "polished README alone is NOT an original signal" in _CLASSIFY_PROMPT
            or "polished readme alone is not an original signal" in _CLASSIFY_PROMPT.lower(),
            "prompt should warn against 'polished README → original'",
        )


# ===========================================================================
# Fix-2 (2026-06-01): profile-README rebinding.
# ===========================================================================


class RebindProfileReadmeFactsTests(SimpleTestCase):
    """A fact extracted from {user}/{user}'s profile README that
    unambiguously references a specific repo should bind to THAT
    repo's entity. Ambiguous / missing references stay on the
    profile-README entity — never guess."""

    KNOWN_REPOS = [
        {"name": "SmartCV",
         "url": "https://github.com/zeyad/smartcv",
         "display_name": "SmartCV"},
        {"name": "healthcare-prediction-depi",
         "url": "https://github.com/zeyad/healthcare-prediction-depi",
         "display_name": "Healthcare Prediction (DEPI)"},
        {"name": "BookShop",
         "url": "https://github.com/zeyad/BookShop",
         "display_name": "BookShop"},
    ]
    PROFILE_URL = "https://github.com/zeyad/zeyad"

    def _profile_fact(self, **kw):
        defaults = {
            "id": "pf1",
            "type": FactType.METRIC,
            "claim": "337 passing tests",
            "value": 337.0, "unit": "tests",
            "entity_id": self.PROFILE_URL,
            "entity_display": "zeyad (profile README)",
            "source": "github_readme:zeyad/zeyad",
            "source_reliability": SourceReliability.USER_ORIGINAL,
            "evidence_quote": "tests-337%20passing",
        }
        defaults.update(kw)
        from resumes.services.fact_store import FactRecord
        return FactRecord(**defaults)

    def test_unambiguous_url_match_rebinds_to_repo(self):
        from resumes.services.fact_extractor import rebind_profile_readme_facts
        # Badge URL points unambiguously at SmartCV.
        fact = self._profile_fact(
            evidence_quote="[![Tests](badge.svg)](https://github.com/zeyad/smartcv#tests)",
        )
        out = rebind_profile_readme_facts([fact], self.KNOWN_REPOS)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].entity_id, "https://github.com/zeyad/smartcv")
        self.assertEqual(out[0].entity_display, "SmartCV")
        # The metric value/unit/claim survives the rebind.
        self.assertEqual(out[0].value, 337.0)
        self.assertEqual(out[0].unit, "tests")
        self.assertEqual(out[0].claim, "337 passing tests")

    def test_unambiguous_name_word_boundary_rebinds(self):
        """Bare repo name in the evidence (no URL) — word-boundary match
        rebinds. 'SmartCV' appearing as a standalone token is enough."""
        from resumes.services.fact_extractor import rebind_profile_readme_facts
        fact = self._profile_fact(
            claim="SmartCV passes 337 tests",
            evidence_quote="My project SmartCV: 337 passing tests",
        )
        out = rebind_profile_readme_facts([fact], self.KNOWN_REPOS)
        self.assertEqual(out[0].entity_id, "https://github.com/zeyad/smartcv")

    def test_no_match_leaves_fact_on_profile_entity(self):
        """A metric with no clear repo reference STAYS on the
        profile-README entity. Never guess."""
        from resumes.services.fact_extractor import rebind_profile_readme_facts
        fact = self._profile_fact(
            claim="50+ open-source contributions",
            evidence_quote="Made 50+ contributions across the OSS ecosystem.",
        )
        out = rebind_profile_readme_facts([fact], self.KNOWN_REPOS)
        # Entity unchanged.
        self.assertEqual(out[0].entity_id, self.PROFILE_URL)
        self.assertEqual(out[0].entity_display, "zeyad (profile README)")

    def test_multiple_matches_leaves_fact_on_profile_entity(self):
        """Two known repos referenced in the same evidence → ambiguous
        → DON'T rebind. The unambiguous-match guard is the safety
        principle ("never guess onto an entity")."""
        from resumes.services.fact_extractor import rebind_profile_readme_facts
        fact = self._profile_fact(
            evidence_quote=(
                "Worked on SmartCV and BookShop in the same week."
            ),
        )
        out = rebind_profile_readme_facts([fact], self.KNOWN_REPOS)
        self.assertEqual(out[0].entity_id, self.PROFILE_URL,
                         "ambiguous match must NOT rebind")

    def test_substring_inside_longer_identifier_does_not_match(self):
        """Word-boundary check: 'SmartCV' inside 'MySmartCVDemo' must
        NOT count as a match — that's a different identifier."""
        from resumes.services.fact_extractor import rebind_profile_readme_facts
        fact = self._profile_fact(
            evidence_quote="My latest project MySmartCVDemoApp has 100 stars.",
        )
        out = rebind_profile_readme_facts([fact], self.KNOWN_REPOS)
        self.assertEqual(out[0].entity_id, self.PROFILE_URL)

    def test_skill_facts_are_never_rebound(self):
        """SKILL facts are entity-less by policy (cross-source dedup).
        Rebinding would break that — verify they pass through unchanged."""
        from resumes.services.fact_extractor import rebind_profile_readme_facts
        from resumes.services.fact_store import FactRecord
        fact = FactRecord(
            id="skill1", type=FactType.SKILL, claim="Python",
            evidence_quote="Built SmartCV in Python",
            source="github_readme:zeyad/zeyad",
            source_reliability=SourceReliability.USER_ORIGINAL,
            entity_id="",   # already empty (SKILL policy)
        )
        out = rebind_profile_readme_facts([fact], self.KNOWN_REPOS)
        self.assertEqual(out[0].entity_id, "",
                         "SKILL facts are never bound to an entity")

    def test_empty_known_repos_is_a_noop(self):
        """No catalogue → no rebinds. Idempotent fast path."""
        from resumes.services.fact_extractor import rebind_profile_readme_facts
        fact = self._profile_fact()
        out = rebind_profile_readme_facts([fact], [])
        self.assertEqual(out[0].entity_id, self.PROFILE_URL)

    def test_unambiguous_match_helper_unit(self):
        from resumes.services.fact_extractor import _unambiguous_repo_match
        # URL match — single repo.
        repo = _unambiguous_repo_match(
            "see https://github.com/zeyad/smartcv#tests",
            self.KNOWN_REPOS,
        )
        self.assertEqual(repo["name"], "SmartCV")
        # Word-boundary name match.
        repo = _unambiguous_repo_match(
            "BookShop is my e-commerce demo", self.KNOWN_REPOS,
        )
        self.assertEqual(repo["name"], "BookShop")
        # Multiple matches → None.
        repo = _unambiguous_repo_match(
            "Both SmartCV and BookShop matter", self.KNOWN_REPOS,
        )
        self.assertIsNone(repo)
        # No match → None.
        repo = _unambiguous_repo_match(
            "Some unrelated text", self.KNOWN_REPOS,
        )
        self.assertIsNone(repo)


class CrossSourceDedupTests(SimpleTestCase):
    """Same skill from CV (user_original) and from a tutorial GitHub repo
    (tutorial_derived) collapses to ONE fact in the store. Higher
    reliability (user_original) wins — already proven at the store
    level, this test wires the extractors into it end to end."""

    CV_TEXT = "SKILLS\nPython, SQL, Pandas\n"
    README = "Following along with the DataCamp Python tutorial. Built with Python."

    def test_cv_user_original_wins_over_tutorial_github(self):
        from resumes.services.fact_extractor import (
            _CVExtraction, _ExtractionResult, _RepoClassification,
        )
        cv_ext = _CVExtraction(roles=[], education=[], free_facts=[
            _ExtractedFactRaw(
                type="skill", claim="Python",
                evidence_quote="Python, SQL, Pandas", value=None, unit=None,
            ),
        ])
        github_ext = _ExtractionResult(facts=[
            _ExtractedFactRaw(
                type="skill", claim="Python",
                evidence_quote="Built with Python", value=None, unit=None,
            ),
        ])
        store = FactStore()
        # Tutorial GitHub repo first — tutorial_derived enters.
        with patch.object(fx, "_classify_repo_with_llm",
                          return_value=_RepoClassification(classification="tutorial")), \
             patch.object(fx, "_extract_facts_with_llm", return_value=github_ext):
            extract_into_store(
                store, "github_readme",
                repo_url="https://github.com/zeyad/datacamp-python",
                repo_display="DataCamp Python", readme_text=self.README,
            )
        # Then the CV — user_original should upgrade the existing entry.
        with patch.object(fx, "_extract_cv_with_llm", return_value=cv_ext):
            extract_into_store(store, "old_cv", cv_text=self.CV_TEXT)
        self.assertEqual(len(store), 1,
                         "same skill from two sources should dedup-collapse")
        survivor = store.all()[0]
        self.assertEqual(survivor.source_reliability,
                         SourceReliability.USER_ORIGINAL,
                         "higher-reliability source must win the dedup tiebreak")
