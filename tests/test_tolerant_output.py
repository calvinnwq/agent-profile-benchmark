"""Tests for the conservative tolerant-output parser."""

from __future__ import annotations

import unittest

from scripts.tolerant_output import MAX_SURROUNDING_CHARS, parse_tolerant_output


class TolerantOutputTests(unittest.TestCase):
    def test_bare_object_is_strict_and_recovered(self) -> None:
        result = parse_tolerant_output('  {"goal":"ship","items":[1,2]}\n')

        self.assertEqual(result.classification, "strict-json-object")
        self.assertTrue(result.strict_contract_valid)
        self.assertTrue(result.recovered)
        self.assertEqual(result.candidate, {"goal": "ship", "items": [1, 2]})
        self.assertEqual(result.surrounding_chars, 0)

    def test_json_markdown_fence_is_recoverable_but_not_strict(self) -> None:
        result = parse_tolerant_output('```json\n{"goal":"ship"}\n```')

        self.assertEqual(result.classification, "markdown-fenced-json")
        self.assertFalse(result.strict_contract_valid)
        self.assertTrue(result.recovered)
        self.assertEqual(result.candidate, {"goal": "ship"})

    def test_unlabelled_markdown_fence_is_recoverable(self) -> None:
        result = parse_tolerant_output('```\n{"goal":"ship"}\n```')

        self.assertEqual(result.classification, "markdown-fenced-json")
        self.assertEqual(result.candidate, {"goal": "ship"})

    def test_limited_surrounding_prose_is_recoverable(self) -> None:
        result = parse_tolerant_output('Here is the plan:\n{"goal":"ship"}\nThanks.')

        self.assertEqual(result.classification, "surrounded-json")
        self.assertFalse(result.strict_contract_valid)
        self.assertTrue(result.recovered)
        self.assertEqual(result.candidate, {"goal": "ship"})
        self.assertEqual(result.surrounding_chars, len("Here is the plan:") + len("Thanks."))

    def test_multiple_objects_are_not_disambiguated(self) -> None:
        result = parse_tolerant_output('{"first":1} and {"second":2}')

        self.assertEqual(result.classification, "ambiguous-json")
        self.assertFalse(result.recovered)
        self.assertIsNone(result.candidate)

    def test_nested_objects_are_recovered_as_one_outer_object(self) -> None:
        result = parse_tolerant_output('Answer: {"outer":{"inner":true}}')

        self.assertEqual(result.classification, "surrounded-json")
        self.assertEqual(result.candidate, {"outer": {"inner": True}})

    def test_truncated_json_is_not_repaired(self) -> None:
        result = parse_tolerant_output('```json\n{"goal":"ship"}\n')

        self.assertFalse(result.recovered)
        self.assertIn(result.classification, {"invalid-json", "fenced-invalid-json"})

    def test_duplicate_keys_are_not_repaired(self) -> None:
        result = parse_tolerant_output('{"goal":"first","goal":"second"}')

        self.assertEqual(result.classification, "duplicate-key")
        self.assertFalse(result.recovered)

    def test_non_object_json_is_not_recovered(self) -> None:
        result = parse_tolerant_output('[{"goal":"ship"}]')

        self.assertEqual(result.classification, "json-value")
        self.assertFalse(result.recovered)

    def test_non_json_fence_is_not_recovered(self) -> None:
        result = parse_tolerant_output('```python\nprint("no")\n```')

        self.assertEqual(result.classification, "markdown-fence-non-json")
        self.assertFalse(result.recovered)

    def test_surrounding_text_limit_is_fail_closed(self) -> None:
        result = parse_tolerant_output("x" * (MAX_SURROUNDING_CHARS + 1) + '{"goal":"ship"}')

        self.assertEqual(result.classification, "surrounding-text-too-large")
        self.assertFalse(result.recovered)

    def test_json_like_surrounding_delimiters_are_ambiguous(self) -> None:
        result = parse_tolerant_output('[answer] {"goal":"ship"}')

        self.assertEqual(result.classification, "ambiguous-json")
        self.assertFalse(result.recovered)


if __name__ == "__main__":
    unittest.main()
