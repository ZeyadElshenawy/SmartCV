"""skills_match grouped-enumeration split tests.

A grouped skill token ("JavaScript/TypeScript", "Jest, Cypress",
"Redux and Zustand") names multiple skills; matching any member should ground
the individual skill. The split must NOT weaken the phantom guard: a
space-separated distinguishing modifier ("React Native") or a "+"-bearing name
("C++") must still NOT match its bare root.
"""
from __future__ import annotations

from django.test import SimpleTestCase

from jobs.services.skill_extractor import skills_match


class SkillsMatchGroupingTests(SimpleTestCase):
    def test_slash_group_matches_member(self):
        self.assertTrue(skills_match("JavaScript/TypeScript", "JavaScript"))
        self.assertTrue(skills_match("JavaScript/TypeScript", "TypeScript"))
        self.assertTrue(skills_match("JavaScript", "JavaScript/TypeScript"))  # symmetric

    def test_comma_and_word_groups_match(self):
        self.assertTrue(skills_match("Jest, Cypress", "Jest"))
        self.assertTrue(skills_match("Redux and Zustand", "Zustand"))
        self.assertTrue(skills_match("REST & GraphQL", "GraphQL"))

    def test_distinguishing_modifier_still_not_matched(self):
        # "Native" is part of the skill name, NOT an enumeration delimiter.
        self.assertFalse(skills_match("React Native", "React"))
        # one shared token via ratio() stays below cutoff.
        self.assertFalse(skills_match("Firebase Messaging", "Firebase"))

    def test_plus_not_split_protects_cpp_csharp(self):
        # "+" is deliberately excluded from the split set.
        self.assertFalse(skills_match("C++", "C"))
        self.assertFalse(skills_match("C#", "C"))

    def test_existing_canonical_behaviour_preserved(self):
        self.assertTrue(skills_match("Vue.js", "Vue"))
        self.assertTrue(skills_match("RESTful APIs", "REST API"))
        self.assertFalse(skills_match("Kubernetes", "Docker"))
