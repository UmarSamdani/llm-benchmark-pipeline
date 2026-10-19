"""Tests for the entity-resolution normalizer.

normalize() is a pure function: it reduces a model name from either data
source to a comparable key. These tests encode the matching decisions the
resolver is built on, including the ones it deliberately does not make.
"""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load_resolve():
    spec = importlib.util.spec_from_file_location(
        "resolve_app", ROOT / "functions" / "resolve" / "app.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


resolve = _load_resolve()
normalize = resolve.normalize


class TestOrganizationPrefixes:
    """Epoch uses display names, OpenRouter prefixes with the organization."""

    def test_strips_slash_prefix(self):
        assert normalize("anthropic/claude-opus-5.5") == "claudeopus55"

    def test_strips_colon_prefix(self):
        assert normalize("Anthropic: Claude Opus 5.5") == "claudeopus55"

    def test_strips_tilde_router_marker(self):
        assert normalize("~anthropic/claude-opus-latest") == "claudeopuslatest"


class TestVersionPunctuation:
    """The same version is written 5-5 by one source and 5.5 by the other."""

    def test_dashes_and_dots_converge(self):
        assert normalize("claude-opus-5-5") == normalize("Claude Opus 5.5")

    def test_spaces_are_removed(self):
        assert normalize("GPT-6 Astra") == "gpt6astra"


class TestReasoningEffortSuffixes:
    """Epoch evaluates one model at several reasoning efforts."""

    @pytest.mark.parametrize(
        "raw",
        [
            "claude-opus-5-5_max",
            "claude-opus-5-5_high",
            "claude-opus-5-5_low",
            "claude-opus-5-5_none",
            "claude-opus-5-5_medium",
        ],
    )
    def test_all_efforts_collapse_to_one_key(self, raw):
        assert normalize(raw) == "claudeopus55"

    def test_effort_and_date_strip_together(self):
        assert normalize("gpt-5.4-mini-2026-03-17_high") == "gpt54mini"


class TestContextWindowSuffixes:
    def test_strips_context_suffix(self):
        assert normalize("claude-sonnet-4-6_32K") == "claudesonnet46"

    def test_strips_non_round_context_suffix(self):
        assert normalize("claude-sonnet-4-5-20250929_59K") == "claudesonnet45"


class TestDateStamps:
    def test_strips_hyphenated_date(self):
        assert normalize("claude-3-opus-20240229") == "claude3opus"

    def test_strips_dotted_version_with_date(self):
        assert normalize("gpt-4o-mini-2024-07-18") == "gpt4omini"


class TestParentheticals:
    """Epoch disambiguates releases with parentheses; OpenRouter does not."""

    def test_strips_parenthetical(self):
        assert normalize("Claude 3.5 Sonnet (October 2024)") == "claude35sonnet"

    def test_strips_complex_parenthetical(self):
        assert normalize("DeepSeek-V2 (MoE-236B, May 2024)") == "deepseekv2"


class TestPlusSign:
    """'+' would be stripped as punctuation and silently merge two models."""

    def test_plus_becomes_plus_word(self):
        assert normalize("Command R+") == "commandrplus"

    def test_plus_model_does_not_collide_with_base_model(self):
        assert normalize("Command R+") != normalize("Command R")


class TestVersionIsPartOfIdentity:
    """A version number is the model's identity, not a strippable suffix."""

    def test_deepseek_versions_stay_distinct(self):
        assert normalize("DeepSeek-V2") != normalize("DeepSeek-V3")

    def test_version_survives_parenthetical_strip(self):
        assert normalize("DeepSeek-V2 (MoE-236B, May 2024)") == "deepseekv2"

class TestKnownNonMatches:
    """Cases the resolver deliberately does not resolve.

    Each of these would require a rule that causes false positives
    elsewhere. They are documented as intentional, not as bugs.
    """

    def test_org_in_display_name_does_not_match(self):
        # Epoch embeds the organization; OpenRouter separates it. Fixing this
        # would require stripping trailing version numbers from display names,
        # which would collapse "Claude Opus 4" to "claudeopus".
        assert normalize("Amazon Nova Pro") != normalize("amazon/nova-pro-v1")

    def test_distinct_model_sizes_stay_distinct(self):
        assert normalize("DeepSeek Coder 33B") != normalize("DeepSeek Coder 6.7B")

    def test_distilled_model_is_not_the_base_model(self):
        assert normalize("DeepSeek-R1-Distill-Qwen-14B") != normalize("DeepSeek-R1")


class TestEdgeCases:
    def test_empty_string(self):
        assert normalize("") == ""

    def test_whitespace_only(self):
        assert normalize("   ") == ""

    def test_is_idempotent(self):
        once = normalize("anthropic/claude-opus-5-5_max")
        assert normalize(once) == once
