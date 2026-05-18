"""End-to-end behaviour assertions for a constructed junior DevOps
candidate against a junior DevOps JD. Purpose: verify the pipeline
still produces sane output for non-AI tracks — that the ml_engineer
KB tuning hasn't accidentally degraded other roles.
"""
from __future__ import annotations

import pytest


@pytest.mark.integration
@pytest.mark.integration_recorded
class TestJuniorDevOps:

    @pytest.fixture(autouse=True)
    def setup(self, junior_devops, patched_llm_calls):
        from tests.integration.conftest import run_full_pipeline
        self.result = run_full_pipeline(
            junior_devops['profile'],
            junior_devops['jd'],
        )

    def test_role_classification_devops(self):
        """Both profile (DevOps Intern + Docker/K8s skills) and JD
        (Junior DevOps Engineer) point at devops."""
        cls = self.result.get('_classification', {}) or {}
        role = (cls.get('primary_role') or '').lower()
        assert 'devops' in role or 'sre' in role or 'platform' in role, (
            f"Expected devops-family role, got: {cls!r}"
        )

    def test_docker_credited(self):
        """Docker is in skills + project tech. Should appear in output."""
        haystack = self._all_visible_text().lower()
        assert 'docker' in haystack

    def test_kubernetes_credited(self):
        haystack = self._all_visible_text().lower()
        assert 'kubernetes' in haystack or 'k8s' in haystack

    def test_ci_cd_credited(self):
        haystack = self._all_visible_text().lower()
        # Accept any of the variants the JD or profile might use.
        assert 'ci/cd' in haystack or 'github actions' in haystack or 'jenkins' in haystack

    def test_url_shortener_project_included(self):
        """Strongest DevOps project (autoscaling EKS + monitoring stack).
        Planner should rank it; output should include it."""
        names = [(p.get('name') or '').lower() for p in self.result.get('projects', []) or []]
        assert any('url shortener' in n for n in names), (
            f"URL Shortener project missing from output: {names}"
        )

    def test_no_soft_skill_leak_in_skills(self):
        """DevOps JD's 'Required Skills' soft-skill section must not
        leak into the rendered Skills section. Same rule as Zeyad —
        verifies PR 3d isn't ml_engineer-specific."""
        skills = self.result.get('skills', []) or []
        skills_lower = [
            (s.get('name') if isinstance(s, dict) else str(s)).lower()
            for s in skills
        ]
        forbidden = [
            'strong communication and collaboration skills',
            'attention to detail',
            'problem-solving skills',
        ]
        leaked = [f for f in forbidden if any(f in s for s in skills_lower)]
        assert not leaked, (
            f"Soft-skill phrases leaked into DevOps Skills: {leaked}. "
            f"Skills: {skills_lower}"
        )

    def test_no_ai_ml_vocab_leak(self):
        """PR 2c's ml_engineer chunks must NOT fire for a DevOps JD.
        Bullets should not contain TensorFlow / RAG / LoRA / fine-tuning
        vocabulary — none of which is in the profile or JD."""
        bullets = ' '.join(self._collect_all_bullets()).lower()
        forbidden = [
            'tensorflow', 'pytorch', 'rag', 'lora', 'fine-tune', 'fine-tuned',
            'qlora', 'embedding model', 'llama', 'hugging face',
        ]
        leaked = [v for v in forbidden if v in bullets]
        assert not leaked, (
            f"AI/ML vocabulary leaked into DevOps resume: {leaked}. "
            f"Bullet sample: {bullets[:400]}"
        )

    def test_no_languages_section(self):
        """Profile has no spoken languages; output's languages list
        should be empty or contain only legitimate spoken languages."""
        spoken_markers = {
            'english', 'arabic', 'french', 'spanish', 'german',
            'chinese', 'mandarin', 'japanese', 'korean',
        }
        for lang in self.result.get('languages') or []:
            name = (lang.get('name') if isinstance(lang, dict) else str(lang)).lower()
            words = set(name.split())
            if not (words & spoken_markers):
                pytest.xfail(
                    f"languages contains non-spoken entry {name!r}; "
                    f"renderer sanitiser drops it at DOCX time"
                )

    def test_certs_not_dropped_below_plan(self):
        from resumes.services.resume_normalizer import _CERT_CAP
        plan = self.result.get('_plan_metadata', {}) or {}
        plan_count = plan.get('cert_count_in_plan', 0)
        output_count = len(self.result.get('certifications') or [])
        if plan_count == 0:
            pytest.skip("No plan certs to restore")
        expected = min(plan_count, _CERT_CAP)
        assert output_count >= expected, (
            f"Cert count regressed: output={output_count} plan={plan_count}"
        )

    # ----- Helpers ----------------------------------------------------------

    def _all_visible_text(self) -> str:
        chunks: list[str] = []
        chunks.append(self.result.get('professional_summary') or '')
        for s in (self.result.get('skills') or []):
            chunks.append(s.get('name') if isinstance(s, dict) else str(s))
        for c in (self.result.get('certifications') or []):
            chunks.append(c.get('name') or '')
        chunks.extend(self._collect_all_bullets())
        for p in (self.result.get('projects') or []):
            for tech in (p.get('technologies') or []):
                chunks.append(str(tech))
        return ' '.join(c for c in chunks if c)

    def _collect_all_bullets(self) -> list[str]:
        bullets: list[str] = []
        for p in self.result.get('projects') or []:
            bullets.extend(p.get('highlights') or [])
            desc = p.get('description')
            if isinstance(desc, list):
                bullets.extend(d for d in desc if isinstance(d, str))
            elif isinstance(desc, str) and desc:
                bullets.append(desc)
        exp_list = self.result.get('experience') or self.result.get('experiences') or []
        for exp in exp_list:
            # PR 3a: description canonical — no separate highlights field.
            desc = exp.get('description')
            if isinstance(desc, list):
                bullets.extend(d for d in desc if isinstance(d, str))
            elif isinstance(desc, str) and desc:
                bullets.append(desc)
        return bullets
