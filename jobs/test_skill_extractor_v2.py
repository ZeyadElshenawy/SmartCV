"""Unit tests for skill_extractor v2 — tier inference, soft-skill survival,
domain canonicalization, backward-compat shim.

The LLM call itself is mocked. We exercise the post-LLM pipeline:
  - _filter_skills' denylist + JD-anchoring + canonicalization gate
  - cross-tier dedupe (must wins)
  - _canonicalize_domain alias map + free-text passthrough
  - extract_skills shim returns the deduped union, must-have first
"""
from __future__ import annotations

from unittest.mock import patch

from django.test import SimpleTestCase

from profiles.services.schemas import JobExtractionResult
from jobs.services.skill_extractor import (
    _canonicalize_domain,
    _filter_skills,
    extract_job_info,
    extract_skills,
)


JD_BANQUE_MISR = """
Shape the Future of Banking with AI at Banque Misr!
We're not just talking about AI; we're deploying it.

Required Skills:
- 5+ years of experience in Data Science with 2+ years of leadership experience
- Extensive experience in developing and implementing complex AI applications
- Proven leadership skills, with the ability to build and motivate high-performing teams
- Deep expertise in statistical modeling, machine learning, and data engineering
- Excellent communication, presentation, and interpersonal skills
- Significant relevant experience in applied AI in the financial sector

Desirable Skills:
- M.Sc. in related field is a plus
- Experience with MLOps principles and tools
- Experience with different data platforms
"""


def _mock_llm(must, nice, domain):
    """Helper: build a JobExtractionResult and patch the LLM call to return it."""
    fake = JobExtractionResult(
        must_have_skills=must,
        nice_to_have_skills=nice,
        domain=domain,
    )

    class _FakeChain:
        def invoke(self, _prompt): return fake

    return _FakeChain()


class TestDomainCanonicalization(SimpleTestCase):
    def test_banking_aliases_collapse_to_financial_services(self):
        for raw in ("Banking", "BANK", "Finance", "FinTech", "financial sector", "financial services"):
            self.assertEqual(_canonicalize_domain(raw), "Financial Services", msg=raw)

    def test_healthcare_aliases(self):
        for raw in ("Healthcare", "medical", "Pharma", "biotech"):
            self.assertEqual(_canonicalize_domain(raw), "Healthcare", msg=raw)

    def test_unknown_domain_title_cased(self):
        self.assertEqual(_canonicalize_domain("artisanal cheese"), "Artisanal Cheese")

    def test_empty_returns_empty(self):
        self.assertEqual(_canonicalize_domain(""), "")
        self.assertEqual(_canonicalize_domain("   "), "")

    def test_multi_token_first_match_wins(self):
        # "Banking and Insurance" → first token "banking" maps to Financial Services
        self.assertEqual(_canonicalize_domain("Banking and Insurance"), "Financial Services")


class TestFilterSkills(SimpleTestCase):
    def test_drops_unanchored(self):
        jd_lower = "we need react and typescript".lower()
        result = _filter_skills(["React", "TypeScript", "Rust"], jd_lower)
        self.assertIn("React", result)
        self.assertIn("TypeScript", result)
        self.assertNotIn("Rust", result)

    def test_keeps_leadership_when_verbatim(self):
        jd_lower = "proven leadership skills required".lower()
        result = _filter_skills(["Leadership"], jd_lower)
        self.assertIn("Leadership", result)

    def test_drops_problem_solving_when_not_verbatim(self):
        # Denylisted soft skill not in JD → dropped
        jd_lower = "we use python and ml".lower()
        result = _filter_skills(["Problem Solving"], jd_lower)
        self.assertEqual(result, [])

    def test_canonicalizes_aliases(self):
        jd_lower = "kubernetes and aws required".lower()
        result = _filter_skills(["k8s", "AWS"], jd_lower)
        self.assertIn("Kubernetes", result)
        self.assertIn("AWS", result)


class TestExtractJobInfo(SimpleTestCase):
    def test_tier_split_banque_misr(self):
        """The real Banque Misr JD: MLOps/Data Platforms should land in nice_to_have."""
        with patch(
            "jobs.services.skill_extractor.get_structured_llm",
            return_value=_mock_llm(
                must=[
                    "Data Science", "Leadership", "Statistical Modeling",
                    "Machine Learning", "Data Engineering",
                    "Communication", "Artificial Intelligence",
                ],
                nice=["MLOps", "Data Platforms"],
                domain="Financial Services",
            ),
        ):
            info = extract_job_info(JD_BANQUE_MISR)
        self.assertIn("Leadership", info.must_have_skills)
        self.assertIn("Communication", info.must_have_skills)
        self.assertIn("MLOps", info.nice_to_have_skills)
        self.assertIn("Data Platforms", info.nice_to_have_skills)
        self.assertEqual(info.domain, "Financial Services")

    def test_domain_canonicalized_from_raw_llm_output(self):
        with patch(
            "jobs.services.skill_extractor.get_structured_llm",
            return_value=_mock_llm(must=["Python"], nice=[], domain="Banking"),
        ):
            info = extract_job_info("Looking for a Python dev at Banque ABC.\nPython is required.")
        self.assertEqual(info.domain, "Financial Services")

    def test_cross_tier_dedupe_must_wins(self):
        with patch(
            "jobs.services.skill_extractor.get_structured_llm",
            return_value=_mock_llm(
                must=["Python", "Docker"],
                nice=["Python", "Kubernetes"],
                domain="",
            ),
        ):
            info = extract_job_info("python docker kubernetes")
        self.assertIn("Python", info.must_have_skills)
        self.assertNotIn("Python", info.nice_to_have_skills)
        self.assertIn("Kubernetes", info.nice_to_have_skills)

    def test_empty_text_returns_empty(self):
        info = extract_job_info("")
        self.assertEqual(info.must_have_skills, [])
        self.assertEqual(info.nice_to_have_skills, [])
        self.assertEqual(info.domain, "")

    def test_llm_failure_returns_empty(self):
        class _Boom:
            def invoke(self, _): raise RuntimeError("groq exploded")
        with patch("jobs.services.skill_extractor.get_structured_llm", return_value=_Boom()):
            info = extract_job_info("any text")
        self.assertEqual(info.must_have_skills, [])
        self.assertEqual(info.domain, "")


class TestBackwardCompatShim(SimpleTestCase):
    def test_extract_skills_returns_flat_union_must_first(self):
        with patch(
            "jobs.services.skill_extractor.get_structured_llm",
            return_value=_mock_llm(
                must=["Python", "Docker"],
                nice=["Kubernetes", "MLOps"],
                domain="",
            ),
        ):
            flat = extract_skills("python docker kubernetes mlops")
        # Must-haves come first, in order, then nice-to-haves
        self.assertEqual(flat[:2], ["Python", "Docker"])
        self.assertIn("Kubernetes", flat[2:])
        self.assertIn("MLOps", flat[2:])

    def test_extract_skills_dedupes_across_tiers(self):
        with patch(
            "jobs.services.skill_extractor.get_structured_llm",
            return_value=_mock_llm(
                must=["Python"],
                nice=["Python", "Docker"],
                domain="",
            ),
        ):
            flat = extract_skills("python docker")
        self.assertEqual(flat.count("Python"), 1)

    def test_extract_skills_empty_text(self):
        self.assertEqual(extract_skills(""), [])
        self.assertEqual(extract_skills(None), [])
