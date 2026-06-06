"""gap_eval profile-plumbing tests.

The gap analyzer's grounding validator reads ``profile.data_content`` (mirroring
a production UserProfile, where data_content holds the whole parsed CV). The
benchmark's _profile_from_parsed must therefore populate data_content with the
parsed skills/experiences/projects in the SAME shape — otherwise grounding sees
an empty profile and false-demotes every real skill.

These are unit tests on a synthetic parsed dict (no PDF, no Groq).
"""
from __future__ import annotations

from django.test import SimpleTestCase

from benchmarks.gap_eval import _profile_from_parsed
import analysis.services.gap_analyzer as ga


_PARSED_REACT_DEV = {
    "skills": [
        {"name": "React.js", "category": "technical_skills"},
        {"name": "JavaScript/TypeScript", "category": "technical_skills"},
        {"name": "CSS", "category": "technical_skills"},
    ],
    "experiences": [
        {"company": "Acme", "position": "Frontend Dev",
         "responsibilities": ["Built RESTful API integrations for the dashboard"]},
    ],
    "projects": [{"name": "Portfolio", "technologies": ["Webpack"]}],
    "certifications": [],
    "education": [],
}


class ProfilePlumbingTests(SimpleTestCase):
    def test_data_content_is_populated_in_production_shape(self):
        prof = _profile_from_parsed(_PARSED_REACT_DEV)
        dc = prof.data_content
        # data_content (what grounding reads) carries the full profile.
        self.assertTrue(dc.get("skills"), "data_content.skills must be populated")
        self.assertEqual([s["name"] for s in dc["skills"]],
                         ["React.js", "JavaScript/TypeScript", "CSS"])
        self.assertTrue(dc.get("experiences"))
        self.assertIn("projects", dc)
        # parse_cv bullets ("responsibilities") folded into "description"
        # (the field _grounding_prose_corpus reads).
        self.assertEqual(dc["experiences"][0]["description"],
                         ["Built RESTful API integrations for the dashboard"])
        # signal stubs preserved for the analyzer's github/scholar/kaggle readers.
        for k in ("github_signals", "scholar_signals", "kaggle_signals"):
            self.assertIn(k, dc)
        # attributes mirror data_content (gap analyzer reads profile.skills too).
        self.assertEqual(prof.skills, dc["skills"])

    def test_real_skill_now_grounds_against_data_content(self):
        prof = _profile_from_parsed(_PARSED_REACT_DEV)
        dc = prof.data_content
        prose = ga._grounding_prose_corpus(dc)
        # React.js is declared -> grounds.
        self.assertTrue(ga._skill_is_grounded("React.js", dc, prose))
        # JavaScript grounds via the grouped "JavaScript/TypeScript" (slash split).
        self.assertTrue(ga._skill_is_grounded("JavaScript", dc, prose))
        self.assertTrue(ga._skill_is_grounded("TypeScript", dc, prose))
        # CSS3 grounds the declared "CSS" via canonical match.
        self.assertTrue(ga._skill_is_grounded("CSS3", dc, prose))
        # prose-only variant: "REST API" via the folded bullet "RESTful API ...".
        self.assertTrue(ga._skill_is_grounded("REST API", dc, prose))

    def test_genuine_phantom_still_demoted(self):
        prof = _profile_from_parsed(_PARSED_REACT_DEV)
        dc = prof.data_content
        prose = ga._grounding_prose_corpus(dc)
        for phantom in ("Kubernetes", "Terraform", "GoRouter"):
            self.assertFalse(ga._skill_is_grounded(phantom, dc, prose),
                             f"{phantom} must stay demoted (no evidence)")
