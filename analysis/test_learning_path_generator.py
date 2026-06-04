"""Tests for the learning-path URL post-processor.

These tests exercise ``_apply_search_links`` directly — the helper that
fixes the homepage-URL bug by replacing LLM-emitted URLs with
deterministic platform-search links built from the resource ``name`` +
``provider``. We do not call ``generate_learning_path`` itself because
that would hit Groq; the contract under test is the pure-Python
post-processor.

Behaviour matrix (from the task spec):

  (a) mapped provider with non-None template
      -> url becomes the search URL, URL-encoded
  (b) "Official docs"
      -> LLM url preserved
  (c) unknown / unmapped provider
      -> LLM url preserved, no crash
  (d) empty / blank name
      -> LLM url preserved (a search for "" lands on the bare search
         page — no better than the homepage we are trying to replace)
  (e) name with spaces / special chars
      -> properly URL-encoded via ``quote_plus``

Plus a few defensive cases that pin behaviour we depend on:
  * Multiple resources in the same item are all processed.
  * The LLM's url is NEVER dropped — only replaced or preserved.
  * Non-dict items / resources are tolerated (defensive against
    salvaged ``failed_generation`` payloads).
"""
from __future__ import annotations

import unittest

from analysis.services.learning_path_generator import (
    _PROVIDER_SEARCH_TEMPLATES,
    _apply_search_links,
)


def _resource(name: str, provider: str, url: str = "https://placeholder.example/") -> dict:
    """One resource dict in the shape the LLM emits (and the template consumes)."""
    return {"name": name, "provider": provider, "url": url}


def _item(*resources: dict, skill: str = "Python") -> dict:
    """One LearningPathItem dict wrapping the given resources."""
    return {
        "skill": skill,
        "importance": "",
        "resources": list(resources),
        "project_idea": "",
        "time_estimate": "",
    }


# ---------------------------------------------------------------------------
# (a) Mapped provider → search URL replaces homepage
# ---------------------------------------------------------------------------

class MappedProviderReplacesUrlTests(unittest.TestCase):
    """For every provider with a non-None template, the LLM's url must be
    replaced with a deterministic search URL built from the resource name."""

    def test_coursera_homepage_replaced_with_search_url(self):
        items = [_item(
            _resource("Python for Everybody", "Coursera",
                      url="https://www.coursera.org/"),
        )]
        _apply_search_links(items)
        self.assertEqual(
            items[0]["resources"][0]["url"],
            "https://www.coursera.org/search?query=Python+for+Everybody",
        )

    def test_udemy_homepage_replaced(self):
        items = [_item(
            _resource("Flutter Bootcamp", "Udemy",
                      url="https://www.udemy.com/"),
        )]
        _apply_search_links(items)
        self.assertEqual(
            items[0]["resources"][0]["url"],
            "https://www.udemy.com/courses/search/?q=Flutter+Bootcamp",
        )

    def test_youtube_uses_results_endpoint(self):
        items = [_item(
            _resource("CS50 Lecture 1", "YouTube", url="https://youtube.com/"),
        )]
        _apply_search_links(items)
        self.assertEqual(
            items[0]["resources"][0]["url"],
            "https://www.youtube.com/results?search_query=CS50+Lecture+1",
        )

    def test_book_uses_google_with_book_suffix(self):
        items = [_item(
            _resource("Clean Code", "Book", url="https://example.com/clean-code"),
        )]
        _apply_search_links(items)
        self.assertEqual(
            items[0]["resources"][0]["url"],
            "https://www.google.com/search?q=Clean+Code+book",
        )

    def test_other_falls_back_to_google(self):
        items = [_item(
            _resource("Some Tutorial", "Other", url="https://example.com/"),
        )]
        _apply_search_links(items)
        self.assertEqual(
            items[0]["resources"][0]["url"],
            "https://www.google.com/search?q=Some+Tutorial",
        )

    def test_every_mapped_provider_with_non_none_template_replaces(self):
        """Smoke check that every entry in the table produces a search URL
        when applied to a resource — no template-string typos, no missing
        ``{q}`` placeholders, no entries silently mapped to None except
        the documented one ("Official docs")."""
        for provider, template in _PROVIDER_SEARCH_TEMPLATES.items():
            if template is None:
                continue
            with self.subTest(provider=provider):
                items = [_item(
                    _resource("Topic", provider, url="https://homepage.example/"),
                )]
                _apply_search_links(items)
                new_url = items[0]["resources"][0]["url"]
                self.assertNotEqual(new_url, "https://homepage.example/",
                                    f"{provider}: url not replaced")
                self.assertIn("Topic", new_url,
                              f"{provider}: search term not in URL")


# ---------------------------------------------------------------------------
# (b) Official docs preserves the LLM's url
# ---------------------------------------------------------------------------

class OfficialDocsPreservesUrlTests(unittest.TestCase):
    """``Official docs`` has a None template — the LLM typically knows
    the exact stable doc page (docs.python.org, MDN, React docs), which
    is higher-signal than a search page."""

    def test_official_docs_url_preserved(self):
        items = [_item(
            _resource(
                "asyncio — Asynchronous I/O",
                "Official docs",
                url="https://docs.python.org/3/library/asyncio.html",
            ),
        )]
        _apply_search_links(items)
        self.assertEqual(
            items[0]["resources"][0]["url"],
            "https://docs.python.org/3/library/asyncio.html",
        )

    def test_official_docs_homepage_also_preserved(self):
        """We trust the LLM's choice for Official docs — even if it
        falls back to a bare homepage here, we preserve it. The
        replacement-vs-preserve decision is per provider, not per
        per-URL-quality heuristic."""
        items = [_item(
            _resource("React Hooks", "Official docs",
                      url="https://react.dev/"),
        )]
        _apply_search_links(items)
        self.assertEqual(items[0]["resources"][0]["url"], "https://react.dev/")


# ---------------------------------------------------------------------------
# (c) Unknown provider → LLM url preserved, no crash
# ---------------------------------------------------------------------------

class UnknownProviderPreservesUrlTests(unittest.TestCase):
    """The provider list in the prompt may drift; the LLM may emit a
    provider label we don't have a template for. Don't crash — keep the
    LLM's url as the best available."""

    def test_unknown_provider_url_preserved(self):
        items = [_item(
            _resource("Mystery Course", "BrandNewPlatform",
                      url="https://brand-new.example/courses/123"),
        )]
        _apply_search_links(items)
        self.assertEqual(
            items[0]["resources"][0]["url"],
            "https://brand-new.example/courses/123",
        )

    def test_empty_provider_preserves_url(self):
        items = [_item(
            _resource("Untagged Resource", "",
                      url="https://example.com/tutorial"),
        )]
        _apply_search_links(items)
        self.assertEqual(
            items[0]["resources"][0]["url"],
            "https://example.com/tutorial",
        )

    def test_none_provider_preserves_url(self):
        items = [_item(
            _resource("Some Resource", "",
                      url="https://example.com/x"),
        )]
        # Override provider to None to simulate a stripped Pydantic field.
        items[0]["resources"][0]["provider"] = None
        _apply_search_links(items)
        self.assertEqual(items[0]["resources"][0]["url"], "https://example.com/x")


# ---------------------------------------------------------------------------
# (d) Empty / blank name → LLM url preserved
# ---------------------------------------------------------------------------

class EmptyNameFallsBackToLlmUrlTests(unittest.TestCase):
    """A search for the empty string lands on the bare search page —
    that is no better than the homepage we are trying to replace.
    Keep the LLM's url so we at least preserve any signal it provided."""

    def test_empty_name_keeps_llm_url(self):
        items = [_item(
            _resource("", "Coursera", url="https://www.coursera.org/"),
        )]
        _apply_search_links(items)
        self.assertEqual(
            items[0]["resources"][0]["url"],
            "https://www.coursera.org/",
        )
        # Critical: NO ?query= path was constructed.
        self.assertNotIn("?query=", items[0]["resources"][0]["url"])
        self.assertNotIn("?q=", items[0]["resources"][0]["url"])

    def test_whitespace_only_name_keeps_llm_url(self):
        items = [_item(
            _resource("   \t\n", "Udemy", url="https://www.udemy.com/"),
        )]
        _apply_search_links(items)
        self.assertEqual(
            items[0]["resources"][0]["url"],
            "https://www.udemy.com/",
        )
        self.assertNotIn("?q=", items[0]["resources"][0]["url"])

    def test_none_name_keeps_llm_url(self):
        items = [_item(_resource("dummy", "Coursera", url="https://homepage.example/"))]
        items[0]["resources"][0]["name"] = None
        _apply_search_links(items)
        self.assertEqual(items[0]["resources"][0]["url"], "https://homepage.example/")


# ---------------------------------------------------------------------------
# (e) Names with spaces / special chars → properly URL-encoded
# ---------------------------------------------------------------------------

class NameUrlEncodingTests(unittest.TestCase):
    """``quote_plus`` is the correct encoder for ``application/x-www-
    form-urlencoded`` query strings (spaces → ``+``, special chars
    percent-encoded). We assert the encoding shape directly so any
    accidental switch to ``quote`` (which leaves spaces as ``%20``) or
    raw concatenation is caught."""

    def test_spaces_become_plus(self):
        items = [_item(
            _resource("Intro to Algorithms", "Coursera",
                      url="https://homepage.example/"),
        )]
        _apply_search_links(items)
        self.assertEqual(
            items[0]["resources"][0]["url"],
            "https://www.coursera.org/search?query=Intro+to+Algorithms",
        )

    def test_ampersand_encoded(self):
        items = [_item(
            _resource("Crockford & ES5", "Book", url="https://homepage.example/"),
        )]
        _apply_search_links(items)
        # & must be percent-encoded so it doesn't break out of the
        # query-string parameter; otherwise the search engine sees a
        # second parameter named "ES5".
        self.assertIn("Crockford+%26+ES5", items[0]["resources"][0]["url"])

    def test_question_mark_and_slash_encoded(self):
        items = [_item(
            _resource("What is REST?/HTTP", "Udemy",
                      url="https://homepage.example/"),
        )]
        _apply_search_links(items)
        new_url = items[0]["resources"][0]["url"]
        self.assertIn("What+is+REST%3F%2FHTTP", new_url)

    def test_unicode_in_name(self):
        items = [_item(
            _resource("Café — Métriques", "YouTube",
                      url="https://homepage.example/"),
        )]
        _apply_search_links(items)
        # Just assert encoding ran without crashing and produced ASCII.
        new_url = items[0]["resources"][0]["url"]
        self.assertTrue(new_url.startswith("https://www.youtube.com/results?search_query="))
        self.assertTrue(new_url.isascii(),
                        f"Search URL should be ASCII after quote_plus, got: {new_url}")

    def test_leading_trailing_whitespace_stripped(self):
        """Whitespace around a real name shouldn't pollute the search
        URL — we strip before encoding so trailing/leading ``+`` chars
        don't appear in the URL."""
        items = [_item(
            _resource("  React Hooks  ", "Frontend Masters",
                      url="https://homepage.example/"),
        )]
        _apply_search_links(items)
        self.assertEqual(
            items[0]["resources"][0]["url"],
            "https://frontendmasters.com/search/?q=React+Hooks",
        )


# ---------------------------------------------------------------------------
# Defensive: multi-resource, multi-item, malformed payloads
# ---------------------------------------------------------------------------

class MultipleResourcesProcessedTests(unittest.TestCase):
    """Every resource on every item is post-processed — the loop must
    not bail early or skip the second resource in a list."""

    def test_all_resources_in_one_item_processed(self):
        items = [_item(
            _resource("A Course", "Coursera", url="https://www.coursera.org/"),
            _resource("Docs Page", "Official docs",
                      url="https://docs.python.org/3/"),
            _resource("A Book", "Book", url="https://homepage.example/"),
        )]
        _apply_search_links(items)
        urls = [r["url"] for r in items[0]["resources"]]
        self.assertEqual(urls[0], "https://www.coursera.org/search?query=A+Course")
        self.assertEqual(urls[1], "https://docs.python.org/3/")  # preserved
        self.assertEqual(urls[2], "https://www.google.com/search?q=A+Book+book")

    def test_multiple_items_independently_processed(self):
        items = [
            _item(_resource("Item1 Topic", "Coursera",
                            url="https://www.coursera.org/"),
                  skill="Python"),
            _item(_resource("Item2 Topic", "edX", url="https://www.edx.org/"),
                  skill="Algorithms"),
        ]
        _apply_search_links(items)
        self.assertEqual(
            items[0]["resources"][0]["url"],
            "https://www.coursera.org/search?query=Item1+Topic",
        )
        self.assertEqual(
            items[1]["resources"][0]["url"],
            "https://www.edx.org/search?q=Item2+Topic",
        )


class DefensiveAgainstMalformedPayloadTests(unittest.TestCase):
    """Salvaged ``failed_generation`` payloads can contain odd shapes;
    the helper must tolerate non-dict items / resources / missing keys
    without crashing — the prior code path swallowed exceptions, this
    helper should never raise."""

    def test_non_dict_item_skipped(self):
        items = ["not a dict", None, 42]  # type: ignore[list-item]
        # Should not raise.
        _apply_search_links(items)  # type: ignore[arg-type]

    def test_non_list_resources_field_skipped(self):
        items = [{"resources": "not a list"}]
        _apply_search_links(items)  # no raise

    def test_missing_resources_field_skipped(self):
        items = [{"skill": "Python"}]
        _apply_search_links(items)  # no raise

    def test_non_dict_resource_in_list_skipped(self):
        items = [{"resources": ["not a dict", None, {"name": "X", "provider": "Coursera",
                                                    "url": "https://homepage.example/"}]}]
        _apply_search_links(items)
        # The valid resource at index 2 still gets processed.
        self.assertEqual(
            items[0]["resources"][2]["url"],
            "https://www.coursera.org/search?query=X",
        )

    def test_returns_same_list_reference(self):
        """``return items`` after in-place mutation lets callers chain;
        we depend on the same-reference behaviour at the call sites."""
        items = [_item(
            _resource("X", "Coursera", url="https://homepage.example/"),
        )]
        result = _apply_search_links(items)
        self.assertIs(result, items)


# ---------------------------------------------------------------------------
# Provider table itself — the contract the prompt depends on
# ---------------------------------------------------------------------------

class ProviderTableContractTests(unittest.TestCase):
    """The provider enum the prompt instructs the LLM to use must be
    fully covered by the search-template map. Drift between the two
    surfaces (prompt adds a provider, map doesn't) is exactly the
    "unknown provider" silent-fallback case — these tests pin the
    invariant so any future addition to one is caught against the
    other."""

    PROMPTED_PROVIDERS = {
        "Coursera", "Udemy", "edX", "YouTube", "MDN", "Official docs",
        "Book", "freeCodeCamp", "Frontend Masters", "Pluralsight",
        "Roadmap.sh", "Khan Academy", "Other",
    }

    def test_every_prompted_provider_is_in_the_map(self):
        for p in self.PROMPTED_PROVIDERS:
            with self.subTest(provider=p):
                self.assertIn(p, _PROVIDER_SEARCH_TEMPLATES)

    def test_official_docs_is_the_only_none_template(self):
        none_keys = [k for k, v in _PROVIDER_SEARCH_TEMPLATES.items() if v is None]
        self.assertEqual(none_keys, ["Official docs"])

    def test_every_template_has_q_placeholder(self):
        for provider, template in _PROVIDER_SEARCH_TEMPLATES.items():
            if template is None:
                continue
            with self.subTest(provider=provider):
                self.assertIn("{q}", template,
                              f"{provider}: template missing {{q}} placeholder")

    def test_every_template_is_https(self):
        for provider, template in _PROVIDER_SEARCH_TEMPLATES.items():
            if template is None:
                continue
            with self.subTest(provider=provider):
                self.assertTrue(template.startswith("https://"),
                                f"{provider}: template not https")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
