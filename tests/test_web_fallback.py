"""Tests for the snippet parsers in src/collectors/web_fallback.py.

These parse free text scraped off search-result pages, so they are the softest entry
point into the database. Every case below is a real failure mode that used to write
bad data into the same columns as lab-measured assays.
"""

import pytest

from src.collectors.web_fallback import (
    parse_terpenes_from_snippets,
    parse_genetics_from_snippets,
)


class TestParseTerpenes:
    def test_extracts_plain_percentages(self):
        assert parse_terpenes_from_snippets(["Myrcene 0.8%, Limonene 0.4%"]) == {
            "myrcene": 0.8,
            "limonene": 0.4,
        }

    def test_does_not_launder_thc_into_a_terpene(self):
        # The old pattern allowed any 10 non-digit chars between the name and the
        # number, so it bound THC's 24.5% to myrcene and reported myrcene: 1.0.
        assert parse_terpenes_from_snippets(["Dominant terpene: myrcene. THC: 24.5%"]) == {}
        assert parse_terpenes_from_snippets(["has myrcene, and tests at 22% THC"]) == {}

    def test_rejects_implausible_values(self):
        # No real terpene reads above a few percent; anything higher is a cannabinoid.
        assert parse_terpenes_from_snippets(["Myrcene: 18%"]) == {}

    def test_handles_unicode_and_greek_prefixes(self):
        assert parse_terpenes_from_snippets(["high in β-myrcene (0.9%)"]) == {"myrcene": 0.9}
        assert parse_terpenes_from_snippets(["β-caryophyllene: 0.35%, α-pinene at 0.12%"]) == {
            "caryophyllene": 0.35,
            "pinene_alpha": 0.12,
        }

    def test_beta_pinene_is_not_mapped_to_alpha(self):
        # Longest-match-first ordering matters: a bare "pinene" key must not shadow
        # "β-pinene" and silently write the value to the alpha column.
        assert parse_terpenes_from_snippets(["β-pinene 0.2%"]) == {"pinene_beta": 0.2}

    def test_returns_empty_rather_than_fabricating(self):
        # Previously: counted mentions and emitted (count / total) * 1.5 as a value,
        # producing an entirely invented profile that looked like a real measurement.
        assert parse_terpenes_from_snippets(
            ["This strain is known for myrcene and limonene and caryophyllene."]
        ) == {}

    def test_no_snippets(self):
        assert parse_terpenes_from_snippets([]) == {}

    def test_keeps_the_largest_reading_for_a_terpene(self):
        assert parse_terpenes_from_snippets(["myrcene 0.3%", "myrcene 0.9%"]) == {"myrcene": 0.9}


class TestParseGenetics:
    def test_extracts_a_simple_cross(self):
        parents = parse_genetics_from_snippets(
            ["Blue Dream »»» Blueberry x Haze · Mostly Sativa"], "Blue Dream"
        )
        assert parents == ["Blueberry", "Haze"]

    def test_extracts_from_genetic_label(self):
        parents = parse_genetics_from_snippets(
            ["Genetic: Northern Lights x Skunk #1. Flowering 60 days."], "Some Strain"
        )
        assert parents == ["Northern Lights", "Skunk #1"]

    def test_extracts_a_bare_capitalised_cross(self):
        parents = parse_genetics_from_snippets(
            ["Made from Gelato 33 x Wedding Cake by the breeder"], "Some Strain"
        )
        assert parents == ["Gelato 33", "Wedding Cake"]

    def test_rejects_sentence_fragments_as_parent_names(self):
        # These become real CanonicalStrain rows via create_parent_placeholder, so a
        # loose match permanently pollutes the strain table. This snippet used to yield
        # ["I grew this in Week 5", "Week 6 of flowering and it was great"].
        assert parse_genetics_from_snippets(
            ["I grew this in Week 5 x Week 6 of flowering and it was great"], "X"
        ) == []
        assert parse_genetics_from_snippets(
            ["Great Smoke And Very Nice Yield x Amazing Purple Colors In Late Flower"], "X"
        ) == []

    def test_returns_empty_when_nothing_parses(self):
        assert parse_genetics_from_snippets(["No lineage information available."], "X") == []
