"""Unit tests for profiles/services/cv_parser.py.

PR 1.2 — _is_plausible_skill_name silent-data-loss fix. The pre-existing
``IsPlausibleSkillNameTests`` in ``profiles/tests.py`` covers the broad
true/false cases; this file pins the specific short-skill regressions
that PR 1.2 fixed, one test per skill, so a future failure points to a
named skill instead of a generic batch failure.
"""
from __future__ import annotations

import logging

import pytest


# --------------------------------------------------------------------------
# Group A — short skills that MUST pass (regression locks)
# --------------------------------------------------------------------------

class TestShortSkillsMustPass:
    """Every legitimate short skill name we've observed in real CVs gets
    its own test. A future change that breaks Vue but not the others
    will fail ONE test and identify Vue by name."""

    @pytest.mark.parametrize('skill', [
        'Vue', 'vue',                   # Vue.js — PR1.2 primary fix
        'R', 'r',                       # R statistical language — needed
                                        # the ordering fix to be reachable
        'Go',                           # Go programming language
        'C',                            # C programming language
        'C++', 'C#', 'F#',              # acronym-shape escape (has + or #)
        'Node.js',                      # acronym-shape escape (has .)
        '.NET',                         # PR1.2 addition (leading-. quirk)
        'UX', 'ux',                     # UX design (lowercase added PR1.2)
        'UI',                           # UI design
        'AI',                           # existing
        'ML',                           # existing
        'JS', 'js',                     # JavaScript shorthand (lc PR1.2)
        'TS', 'ts',                     # TypeScript shorthand (lc PR1.2)
    ])
    def test_skill_passes(self, skill):
        from profiles.services.cv_parser import _is_plausible_skill_name
        assert _is_plausible_skill_name(skill) is True, (
            f"{skill!r} must pass — silent dropping at parse time would "
            f"corrupt every downstream stage (gap analysis, planner, "
            f"resume rendering)."
        )


# --------------------------------------------------------------------------
# Group B — noise that MUST fail (false-positive guards)
# --------------------------------------------------------------------------

class TestNoiseMustFail:
    """The function's main job is dropping PDF/extraction noise. These
    are sanity checks that the new permissive allowlist hasn't opened
    a hole — we'd rather a real skill name show up than have, say, a
    bare ``?`` survive into the skills list."""

    @pytest.mark.parametrize('garbage', [
        '',                             # empty
        ' ',                            # whitespace only
        '?',                            # single non-alpha char
        '42',                           # bare number
        'aaa',                          # random lowercase noise, not a skill
        'X',                            # single uppercase, not in allowlist
        '...',                          # punct only
        '(React)',                      # leading non-alpha (legit reject)
    ])
    def test_garbage_rejected(self, garbage):
        from profiles.services.cv_parser import _is_plausible_skill_name
        assert _is_plausible_skill_name(garbage) is False, (
            f"{garbage!r} should NOT survive the noise filter"
        )


# --------------------------------------------------------------------------
# Group C — ordering-bug specific regression lock
# --------------------------------------------------------------------------

class TestAllowlistRunsBeforeLengthCheck:
    """Pre-PR1.2: the ``len(s) < 2`` check ran BEFORE the allowlist
    lookup, so a single-character allowlisted entry like 'r' (R, the
    statistical language) was unreachable — the length guard rejected
    it before the allowlist was consulted. The fix reorders the
    function so the allowlist override fires first. This test catches
    any re-introduction of the bug by exercising the exact case the
    ordering was wrong for."""

    def test_single_char_allowlisted_skill_passes(self):
        from profiles.services.cv_parser import (
            _is_plausible_skill_name, _SKILL_SHORT_ALLOWLIST,
        )
        # Pin the precondition: 'r' must actually be in the allowlist
        # for this test to be meaningful. If a maintainer removes it,
        # the test should fail loudly at THIS line rather than silently
        # turning into a no-op.
        assert 'r' in _SKILL_SHORT_ALLOWLIST, (
            "Precondition: 'r' must be in _SKILL_SHORT_ALLOWLIST for "
            "this regression lock to verify the ordering. If you "
            "intentionally removed it, also remove this test."
        )
        # The actual ordering check: a length-1 allowlisted entry
        # must pass. Pre-PR1.2 this returned False because the
        # `len(s) < 2` guard fired before the allowlist lookup.
        assert _is_plausible_skill_name('R') is True
        assert _is_plausible_skill_name('r') is True


# --------------------------------------------------------------------------
# Group D — log emission on rejection
# --------------------------------------------------------------------------

class TestRejectionLogging:
    """PR1.2 added an INFO-level rejection log so production debugging
    of "user reports skill X is missing" doesn't require re-running
    the user's CV through the pipeline. Test pins the log format so
    grep / observability dashboards built on it don't silently break."""

    def test_rejected_skill_emits_info_log_with_reason(self, caplog):
        from profiles.services.cv_parser import _is_plausible_skill_name
        with caplog.at_level(logging.INFO, logger='profiles.services.cv_parser'):
            result = _is_plausible_skill_name('aaa')
        assert result is False
        # The log line includes both the rejected value and the rule-id
        # so it's both human-debuggable and machine-greppable.
        msgs = [r.message for r in caplog.records
                if 'cv_parser._is_plausible_skill_name rejected' in r.message]
        assert msgs, (
            'expected an INFO log line for the rejection — production '
            'debugging relies on this being observable.'
        )
        # Reason should be specific, not a generic "rejected".
        assert 'reason=' in msgs[-1]
        assert 'aaa' in msgs[-1], 'rejected value must appear in the log'

    def test_accepted_skill_emits_no_rejection_log(self, caplog):
        # Symmetric guard: accepted skills must NOT spam INFO logs
        # (otherwise every accept-path call from parse_cv would dilute
        # the rejection signal we built this log for).
        from profiles.services.cv_parser import _is_plausible_skill_name
        with caplog.at_level(logging.INFO, logger='profiles.services.cv_parser'):
            assert _is_plausible_skill_name('Vue') is True
        rejection_logs = [
            r for r in caplog.records
            if 'cv_parser._is_plausible_skill_name rejected' in r.message
        ]
        assert not rejection_logs, (
            'accepted skills should not emit rejection logs'
        )
