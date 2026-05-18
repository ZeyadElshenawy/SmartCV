"""Unit tests for resumes/services/resume_generator.py.

PR 3f — Pass-H recovery handles the invented ``achievements`` wrapper
the Groq tool-call validator occasionally serialises, plus malformed
JSON in the middle of the failed_generation payload.
"""
from __future__ import annotations

import json
import logging

import pytest


# ---------------------------------------------------------------------------
# Group A — _flatten_achievements_wrapper (pure transformer)
# ---------------------------------------------------------------------------


class TestFlattenAchievementsWrapper:
    """The flattener rewrites the LLM-invented ``achievements`` nesting
    into the schema's expected ``highlights`` list. Idempotent on
    already-flat dicts."""

    def test_a_wrapper_flattened_to_highlights(self):
        from resumes.services.resume_generator import _flatten_achievements_wrapper
        parsed = {
            'experience': [{
                'title': 'Digital Transformation Intern',
                'company': 'Almansour Automotive',
                'achievements': [{
                    'description': [
                        'Built data pipeline in Microsoft Fabric.',
                        'Analyzed ERP workflows in SAP.',
                    ],
                }],
            }],
            'skills': ['Python'],
        }
        out = _flatten_achievements_wrapper(parsed)
        exp = out['experience'][0]
        assert 'achievements' not in exp
        assert exp['highlights'] == [
            'Built data pipeline in Microsoft Fabric.',
            'Analyzed ERP workflows in SAP.',
        ]
        # Non-experience sections unchanged.
        assert out['skills'] == ['Python']

    def test_b_merges_with_existing_highlights(self):
        from resumes.services.resume_generator import _flatten_achievements_wrapper
        parsed = {
            'experience': [{
                'title': 'Senior Engineer',
                'highlights': ['Shipped v2 platform.'],
                'achievements': [{
                    'description': ['Led migration to event-sourced billing.'],
                }],
            }],
        }
        out = _flatten_achievements_wrapper(parsed)
        # Existing highlights first, then flattened bullets appended.
        assert out['experience'][0]['highlights'] == [
            'Shipped v2 platform.',
            'Led migration to event-sourced billing.',
        ]
        assert 'achievements' not in out['experience'][0]

    def test_c_achievements_as_string_list(self):
        """Shape C in the docstring: ``achievements`` is a flat list of
        strings (no nested ``description`` dict)."""
        from resumes.services.resume_generator import _flatten_achievements_wrapper
        parsed = {
            'experience': [{
                'title': 'ML Engineer',
                'achievements': [
                    'Fine-tuned Llama-3-8B on internal corpus.',
                    'Deployed via vLLM at p95=620ms.',
                ],
            }],
        }
        out = _flatten_achievements_wrapper(parsed)
        assert out['experience'][0]['highlights'] == [
            'Fine-tuned Llama-3-8B on internal corpus.',
            'Deployed via vLLM at p95=620ms.',
        ]

    def test_c2_achievements_as_dict_with_str_description(self):
        """Shape B: ``description`` is a str, not a list."""
        from resumes.services.resume_generator import _flatten_achievements_wrapper
        parsed = {
            'experience': [{
                'title': 'Data Scientist',
                'achievements': [
                    {'description': 'Owned end-to-end churn model.'},
                ],
            }],
        }
        out = _flatten_achievements_wrapper(parsed)
        assert out['experience'][0]['highlights'] == [
            'Owned end-to-end churn model.',
        ]

    def test_d_no_achievements_key_is_noop(self):
        from resumes.services.resume_generator import _flatten_achievements_wrapper
        parsed = {
            'experience': [{
                'title': 'Backend Engineer',
                'highlights': ['Bullet 1', 'Bullet 2'],
            }],
        }
        before = json.dumps(parsed, sort_keys=True)
        out = _flatten_achievements_wrapper(parsed)
        assert json.dumps(out, sort_keys=True) == before

    def test_idempotent_on_already_flat(self):
        """Calling twice produces same result as calling once — the
        recovery path relies on this safety."""
        from resumes.services.resume_generator import _flatten_achievements_wrapper
        parsed = {
            'experience': [{
                'title': 'X',
                'achievements': [{'description': ['A.', 'B.']}],
            }],
        }
        once = _flatten_achievements_wrapper(parsed)
        first_pass = json.dumps(once, sort_keys=True)
        twice = _flatten_achievements_wrapper(once)
        assert json.dumps(twice, sort_keys=True) == first_pass

    def test_filters_empty_bullets(self):
        from resumes.services.resume_generator import _flatten_achievements_wrapper
        parsed = {
            'experience': [{
                'title': 'X',
                'achievements': [
                    {'description': ['Real bullet.', '', '   ']},
                ],
            }],
        }
        out = _flatten_achievements_wrapper(parsed)
        assert out['experience'][0]['highlights'] == ['Real bullet.']

    def test_handles_alternate_key_experiences(self):
        """Some LLM outputs use 'experiences' (plural). Flatten still applies."""
        from resumes.services.resume_generator import _flatten_achievements_wrapper
        parsed = {
            'experiences': [{
                'title': 'X',
                'achievements': [{'description': ['Bullet.']}],
            }],
        }
        out = _flatten_achievements_wrapper(parsed)
        assert out['experiences'][0]['highlights'] == ['Bullet.']

    def test_no_experience_section_is_noop(self):
        from resumes.services.resume_generator import _flatten_achievements_wrapper
        parsed = {'skills': ['Python'], 'professional_summary': 'X'}
        before = json.dumps(parsed, sort_keys=True)
        out = _flatten_achievements_wrapper(parsed)
        assert json.dumps(out, sort_keys=True) == before


# ---------------------------------------------------------------------------
# Group B — _tolerant_json_parse (recovery fallback)
# ---------------------------------------------------------------------------


class TestTolerantJsonParse:
    """Strict parse → trailing-comma strip → brace-truncation cascade."""

    def test_clean_json_parses_strictly(self):
        from resumes.services.resume_generator import _tolerant_json_parse
        raw = '{"a": 1, "b": [2, 3]}'
        assert _tolerant_json_parse(raw) == {'a': 1, 'b': [2, 3]}

    def test_trailing_comma_in_object_repaired(self, caplog):
        from resumes.services.resume_generator import _tolerant_json_parse
        raw = '{"a": 1, "b": 2,}'
        with caplog.at_level(logging.WARNING, logger='resumes.services.resume_generator'):
            assert _tolerant_json_parse(raw) == {'a': 1, 'b': 2}
        assert any('trailing-comma-strip' in r.message for r in caplog.records)

    def test_trailing_comma_in_array_repaired(self):
        from resumes.services.resume_generator import _tolerant_json_parse
        raw = '{"arr": [1, 2, 3,]}'
        assert _tolerant_json_parse(raw) == {'arr': [1, 2, 3]}

    def test_truncated_tail_recovers_to_last_balanced_brace(self, caplog):
        """The original Pass-H truncation case: LLM cut off mid-string
        after a complete previous section."""
        from resumes.services.resume_generator import _tolerant_json_parse
        # Complete object followed by malformed garbage.
        raw = '{"name": "X", "age": 30}{"another": "incomplete'
        with caplog.at_level(logging.WARNING, logger='resumes.services.resume_generator'):
            parsed = _tolerant_json_parse(raw)
        # The brace-truncation strategy walks until the LAST point where
        # depth returns to 0; for two adjacent objects that's after the
        # first one (the second never closes).
        assert parsed == {'name': 'X', 'age': 30}
        assert any('brace-truncation' in r.message for r in caplog.records)

    def test_unrecoverable_raises_json_decode_error(self):
        from resumes.services.resume_generator import _tolerant_json_parse
        # '{{garbage' is malformed AND can't be fixed by appending closers
        # because 'garbage' isn't valid JSON content. Use something that
        # can't be salvaged.
        raw = '{this is just nonsense not even close to json}'
        with pytest.raises(json.JSONDecodeError):
            _tolerant_json_parse(raw)

    def test_missing_outer_close_brace_repaired(self, caplog):
        """Zeyad failure from 2026-05-18: the LLM emitted the inner
        ``}`` of ``parameters`` but forgot the outer ``}`` of the
        ``{"name", "parameters"}`` tool-call wrapper. The brace-repair
        sees ``]`` with ``{`` on stack, auto-inserts ``}`` to close the
        orphan object before matching the ``]`` to ``[``."""
        from resumes.services.resume_generator import _tolerant_json_parse
        raw = '[{"name": "X", "parameters": {"a": 1, "b": 2}]'
        #                                                 ^ missing }
        with caplog.at_level(logging.WARNING, logger='resumes.services.resume_generator'):
            parsed = _tolerant_json_parse(raw)
        assert parsed == [{"name": "X", "parameters": {"a": 1, "b": 2}}]
        assert any('brace-repair' in r.message for r in caplog.records)

    def test_missing_close_for_nested_array_repaired(self):
        """Nested missing closer at end: ``[1, 2, {"a": 3`` is missing
        both ``}`` and ``]``. Both get appended in the right order."""
        from resumes.services.resume_generator import _tolerant_json_parse
        raw = '[1, 2, {"a": 3'
        parsed = _tolerant_json_parse(raw)
        assert parsed == [1, 2, {"a": 3}]

    def test_strings_with_braces_dont_confuse_depth_tracker(self):
        """The brace-truncation strategy must NOT count braces inside strings.
        ``"a": "{not a brace}"`` should still parse with brace depth =0
        at the trailing }."""
        from resumes.services.resume_generator import _tolerant_json_parse
        raw = '{"a": "{not a brace}", "b": 2}'
        assert _tolerant_json_parse(raw) == {'a': '{not a brace}', 'b': 2}

    def test_escaped_quotes_in_strings_handled(self):
        from resumes.services.resume_generator import _tolerant_json_parse
        raw = r'{"msg": "He said \"hi\"", "n": 1}'
        assert _tolerant_json_parse(raw) == {'msg': 'He said "hi"', 'n': 1}


# ---------------------------------------------------------------------------
# Group C — End-to-end recovery
# ---------------------------------------------------------------------------


class _FakeGroqException(Exception):
    """Minimal stand-in for groq.BadRequestError with the .body attribute
    that _extract_failed_generation reads."""
    def __init__(self, failed_generation: str):
        super().__init__("simulated tool_use_failed")
        self.body = {
            'error': {
                'message': "Failed to call a function.",
                'type': 'invalid_request_error',
                'code': 'tool_use_failed',
                'failed_generation': failed_generation,
            }
        }


class TestRecoverResumeFromFailedGenerationEndToEnd:
    """Full recovery path: malformed payload with achievements wrapper →
    valid ResumeContentResult."""

    def _build_payload(self, *, with_achievements: bool, with_trailing_comma: bool) -> str:
        """Assemble a realistic Groq tool-call payload — the
        `[{name, parameters}]` envelope plus the experience block we
        care about."""
        if with_achievements:
            experience = [{
                'title': 'Digital Transformation Intern',
                'company': 'Almansour Automotive',
                'location': 'Giza, EG',
                'start_date': 'Aug 2025',
                'end_date': 'Present',
                'duration': 'Aug 2025 - Present',
                'achievements': [{
                    'description': [
                        'Built data pipeline in Microsoft Fabric with PySpark.',
                        'Analyzed ERP workflows in SAP for procurement insights.',
                    ],
                }],
            }]
        else:
            experience = [{
                'title': 'Digital Transformation Intern',
                'company': 'Almansour Automotive',
                'location': 'Giza, EG',
                'start_date': 'Aug 2025',
                'end_date': 'Present',
                'duration': 'Aug 2025 - Present',
                'highlights': ['Built data pipeline.'],
            }]
        payload = {
            'name': 'ResumeContentResult',
            'parameters': {
                'professional_summary': 'Data scientist.',
                'professional_title': 'Data Scientist',
                'skills': [{'name': 'Python'}],
                'experience': experience,
                'projects': [],
                'education': [],
                'certifications': [],
                'languages': [],
            },
        }
        serialized = json.dumps([payload])
        if with_trailing_comma:
            # Inject a trailing comma before the array close — common LLM bug.
            serialized = serialized[:-1] + ',]'
        return serialized

    def test_h_full_recovery_with_achievements_wrapper(self, caplog):
        from resumes.services.resume_generator import (
            _recover_resume_from_failed_generation,
        )
        from profiles.services.schemas import ResumeContentResult

        raw = self._build_payload(with_achievements=True, with_trailing_comma=False)
        exc = _FakeGroqException(raw)

        with caplog.at_level(logging.INFO, logger='resumes.services.resume_generator'):
            result = _recover_resume_from_failed_generation(exc)

        assert isinstance(result, ResumeContentResult)
        assert len(result.experience) == 1
        exp = result.experience[0]
        assert exp.title == 'Digital Transformation Intern'
        # Achievements wrapper flattened into highlights.
        bullets = getattr(exp, 'highlights', None) or getattr(exp, 'description', None)
        # Schema may have validated bullets onto either highlights (list)
        # or description (str/list). The flattener writes to highlights;
        # check at least one of the two carries the recovered content.
        assert bullets, f'expected highlights or description populated; got {exp!r}'
        # Flattener log line fired.
        assert any(
            'achievements-wrapper flattening' in r.message for r in caplog.records
        ), [r.message for r in caplog.records]

    def test_full_recovery_with_trailing_comma_and_wrapper(self, caplog):
        from resumes.services.resume_generator import (
            _recover_resume_from_failed_generation,
        )
        from profiles.services.schemas import ResumeContentResult

        raw = self._build_payload(with_achievements=True, with_trailing_comma=True)
        exc = _FakeGroqException(raw)

        with caplog.at_level(logging.WARNING, logger='resumes.services.resume_generator'):
            result = _recover_resume_from_failed_generation(exc)

        assert isinstance(result, ResumeContentResult)
        # Both sub-paths fired: tolerant parse AND flattener.
        msgs = [r.message for r in caplog.records]
        assert any('trailing-comma-strip' in m for m in msgs)

    def test_clean_payload_still_recovers_cleanly(self):
        """Regression: a well-formed payload with no achievements wrapper
        and no JSON malformation should still recover (the flattener and
        tolerant parser are no-ops in this case)."""
        from resumes.services.resume_generator import (
            _recover_resume_from_failed_generation,
        )
        from profiles.services.schemas import ResumeContentResult

        raw = self._build_payload(with_achievements=False, with_trailing_comma=False)
        exc = _FakeGroqException(raw)

        result = _recover_resume_from_failed_generation(exc)
        assert isinstance(result, ResumeContentResult)
        assert len(result.experience) == 1
