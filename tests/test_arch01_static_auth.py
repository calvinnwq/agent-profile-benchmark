"""ARCH-01 static authentication check: reject false passes, accept correct variants."""

from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from scripts.evaluate_task import _python_auth_static, evaluate_task


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "fixtures" / "arch-01"

GUARD = "    if not isinstance(token, str) or not isinstance(expected, str):\n        return False\n"


def _static(source: str) -> str:
    return _python_auth_static({"implementation": {"auth.py": source}})[0]


class Arch01StaticAuthFalsePassTests(unittest.TestCase):
    """Each case passed the PR #9 check; all must fail."""

    CASES = {
        "ignored compare result then return True": (
            "import hmac\n\ndef verify(token, expected):\n" + GUARD
            + "    hmac.compare_digest(token, expected)\n    return True\n"
        ),
        "unreachable compare after plain equality": (
            "import hmac\n\ndef verify(token, expected):\n" + GUARD
            + "    return token == expected\n    return hmac.compare_digest(token, expected)\n"
        ),
        "negated compare": (
            "import hmac\n\ndef verify(token, expected):\n" + GUARD
            + "    return not hmac.compare_digest(token, expected)\n"
        ),
        "guard in dead branch": (
            "import hmac\n\ndef verify(token, expected):\n    if False:\n"
            "        if not isinstance(token, str) or not isinstance(expected, str):\n"
            "            return False\n    return hmac.compare_digest(token, expected)\n"
        ),
        "guard joined with and": (
            "import hmac\n\ndef verify(token, expected):\n"
            "    if not isinstance(token, str) and not isinstance(expected, str):\n        return False\n"
            "    return hmac.compare_digest(token, expected)\n"
        ),
        "guard after compare": (
            "import hmac\n\ndef verify(token, expected):\n    return hmac.compare_digest(token, expected)\n" + GUARD
        ),
        "hmac shadowed by local class": (
            "import hmac\n\nclass hmac:\n    @staticmethod\n    def compare_digest(a, b):\n        return True\n\n"
            "def verify(token, expected):\n" + GUARD + "    return hmac.compare_digest(token, expected)\n"
        ),
        "second verify definition": (
            "import hmac\n\ndef verify(token, expected):\n" + GUARD
            + "    return hmac.compare_digest(token, expected)\n\n"
            "def verify(token, expected):\n    return token == expected\n"
        ),
    }

    def test_each_review_false_pass_now_fails(self) -> None:
        for name, source in self.CASES.items():
            with self.subTest(case=name):
                self.assertEqual(_static(source), "fail")

    def test_rebinding_names_the_check_depends_on_fails(self) -> None:
        cases = {
            "verify reassigned": "verify = 1\n",
            "hmac import alias": "import hashlib as hmac\n",
            "isinstance imported": "from os import isinstance\n",
            "hmac monkeypatched": "hmac.compare_digest = lambda a, b: True\n",
            "hmac rebound inside verify": None,
        }
        base = "import hmac\n\ndef verify(token, expected):\n" + GUARD + "    return hmac.compare_digest(token, expected)\n"
        for name, extra in cases.items():
            with self.subTest(case=name):
                if extra is None:
                    source = base.replace(GUARD, "    hmac = None\n" + GUARD)
                else:
                    source = base + extra
                self.assertEqual(_static(source), "fail")

    def test_compare_digest_keyword_arguments_fail(self) -> None:
        # hmac.compare_digest is positional-only: this raises TypeError at runtime.
        source = "import hmac\n\ndef verify(token, expected):\n" + GUARD + "    return hmac.compare_digest(a=token, b=expected)\n"
        self.assertEqual(_static(source), "fail")

    def test_mixed_str_and_bytes_arguments_fail(self) -> None:
        source = "import hmac\n\ndef verify(token, expected):\n" + GUARD + "    return hmac.compare_digest(token.encode(), expected)\n"
        self.assertEqual(_static(source), "fail")

    def test_ignored_compare_no_longer_passes_the_whole_task(self) -> None:
        candidate = json.loads((PACKAGE / "controls" / "known-good.json").read_text(encoding="utf-8"))
        candidate["implementation"]["auth.py"] = self.CASES["ignored compare result then return True"]
        fixture = json.loads((PACKAGE / "input.json").read_text(encoding="utf-8"))
        evaluation = evaluate_task("ARCH-01", fixture, candidate)
        self.assertEqual(evaluation["status"], "failed")
        checks = {item["id"]: item for item in evaluation["automatic_checks"]}
        self.assertEqual(checks["hidden-behavioral-tests"]["status"], "fail")


class Arch01StaticAuthParseFailureTests(unittest.TestCase):
    def test_nul_byte_is_a_failed_check_not_a_crash(self) -> None:
        self.assertEqual(_static("import hmac\x00\n"), "fail")

    def test_nul_byte_through_evaluate_task_is_a_failed_task(self) -> None:
        candidate = json.loads((PACKAGE / "controls" / "known-good.json").read_text(encoding="utf-8"))
        candidate["implementation"]["auth.py"] += "\x00"
        fixture = json.loads((PACKAGE / "input.json").read_text(encoding="utf-8"))
        evaluation = evaluate_task("ARCH-01", fixture, candidate)
        self.assertEqual(evaluation["status"], "failed")

    def test_any_parse_exception_is_a_failed_check(self) -> None:
        for error in (ValueError("source code string cannot contain null bytes"), MemoryError(), RecursionError()):
            with self.subTest(error=type(error).__name__), mock.patch("scripts.evaluate_task.ast.parse", side_effect=error):
                self.assertEqual(_static("import hmac\n"), "fail")

    def test_static_check_never_launches_a_process(self) -> None:
        sources = [*Arch01StaticAuthFalsePassTests.CASES.values(), *Arch01StaticAuthAcceptedVariantTests.VARIANTS.values()]
        with mock.patch.object(subprocess, "Popen", side_effect=AssertionError("launched")), mock.patch.object(
            subprocess, "run", side_effect=AssertionError("launched")
        ):
            for source in sources:
                _static(source)


class Arch01StaticAuthAcceptedVariantTests(unittest.TestCase):
    """Correct verifiers written differently from the known-good control must pass."""

    VARIANTS = {
        "known-good form": "import hmac\n\ndef verify(token, expected):\n" + GUARD + "    return hmac.compare_digest(token, expected)\n",
        "from hmac import compare_digest": (
            "from hmac import compare_digest\n\ndef verify(token, expected):\n" + GUARD
            + "    return compare_digest(token, expected)\n"
        ),
        "two separate guards": (
            "import hmac\n\ndef verify(token, expected):\n"
            "    if not isinstance(token, str):\n        return False\n"
            "    if not isinstance(expected, str):\n        return False\n"
            "    return hmac.compare_digest(token, expected)\n"
        ),
        "positive isinstance and guard": (
            "import hmac\n\ndef verify(token, expected):\n"
            "    if isinstance(token, str) and isinstance(expected, str):\n"
            "        return hmac.compare_digest(token, expected)\n    return False\n"
        ),
        "not (a and b)": (
            "import hmac\n\ndef verify(token, expected):\n"
            "    if not (isinstance(token, str) and isinstance(expected, str)):\n        return False\n"
            "    return hmac.compare_digest(token, expected)\n"
        ),
        "type(x) is not str": (
            "import hmac\n\ndef verify(token, expected):\n"
            "    if type(token) is not str or type(expected) is not str:\n        return False\n"
            "    return hmac.compare_digest(token, expected)\n"
        ),
        "encode with keyword argument": (
            "import hmac\n\ndef verify(token, expected):\n" + GUARD
            + '    return hmac.compare_digest(token.encode(encoding="utf-8"), expected.encode(encoding="utf-8"))\n'
        ),
        "encode arguments": (
            "import hmac\n\ndef verify(token, expected):\n" + GUARD
            + "    return hmac.compare_digest(token.encode(), expected.encode())\n"
        ),
        "annotations docstring and else branch": (
            '"""Auth helpers."""\nfrom __future__ import annotations\n\nimport hmac\n\n'
            "def verify(token: str, expected: str) -> bool:\n"
            '    """Compare in constant time."""\n'
            "    if not isinstance(token, str) or not isinstance(expected, str):\n        return False\n"
            "    else:\n        return hmac.compare_digest(expected, token)\n"
        ),
    }

    def test_known_good_control_passes_the_model_output_path(self) -> None:
        candidate = json.loads((PACKAGE / "controls" / "known-good.json").read_text(encoding="utf-8"))
        fixture = json.loads((PACKAGE / "input.json").read_text(encoding="utf-8"))
        with mock.patch.object(subprocess, "Popen", side_effect=AssertionError("launched")):
            evaluation = evaluate_task("ARCH-01", fixture, candidate)
        self.assertEqual(evaluation["status"], "passed")

    def test_each_correct_variant_passes(self) -> None:
        for name, source in self.VARIANTS.items():
            with self.subTest(case=name):
                self.assertEqual(_static(source), "pass")

    def test_each_variant_is_behaviourally_correct(self) -> None:
        """Trusted test sources only: confirm the accepted variants really satisfy the hidden tests."""
        for name, source in self.VARIANTS.items():
            with self.subTest(case=name):
                namespace: dict[str, object] = {}
                exec(compile(source, f"<{name}>", "exec"), namespace)  # noqa: S102 - fixed test source
                verify = namespace["verify"]
                assert callable(verify)
                self.assertIs(verify("abc", "abc"), True)
                self.assertIs(verify("abc", "abd"), False)
                self.assertIs(verify(b"abc", "abc"), False)
                self.assertIs(verify("abc", None), False)


if __name__ == "__main__":
    unittest.main()
