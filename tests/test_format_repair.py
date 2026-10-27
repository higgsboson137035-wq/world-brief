from collections import Counter
import unittest

from scripts.format_repair import (
    FORMAT_REPAIRABLE,
    SUBSTANTIVE_FAILURE,
    classify_failure,
    repair_text,
)
from scripts.review_draft_gate import HEADINGS


def sample(extra=False):
    lines = ["_Date: 2026-10-27_", ""]
    for heading in HEADINGS:
        lines.extend([heading, f"Body for {heading}", ""])
    if extra:
        lines.extend(["## 詳細：アジア", "Asian fact and context.", "🔗 [source](https://example.com/asia)", ""])
    return "\n".join(lines)


class FormatRepairTests(unittest.TestCase):
    def test_extra_asia_heading_is_repairable_and_only_section_structure_moves(self):
        original = sample(extra=True)
        repaired = repair_text(original)
        self.assertIsNotNone(repaired)
        self.assertEqual(classify_failure("required_heading_order_invalid", original), FORMAT_REPAIRABLE)
        self.assertNotIn("## 詳細：アジア", repaired)
        self.assertIn("Asian fact and context.", repaired)
        self.assertEqual(
            Counter(line for line in original.splitlines() if line and not line.startswith("## ")),
            Counter(line for line in repaired.splitlines() if line and not line.startswith("## ")),
        )
        self.assertEqual(
            original.split("## Executive Summary", 1)[0],
            repaired.split("## Executive Summary", 1)[0],
        )
        self.assertEqual(
            [line for line in original.splitlines() if "https://" in line],
            [line for line in repaired.splitlines() if "https://" in line],
        )

    def test_minor_heading_spacing_is_normalized(self):
        original = sample().replace("## 🇯🇵 日本への影響", "##🇯🇵日本への影響")
        repaired = repair_text(original)
        self.assertIsNotNone(repaired)
        self.assertIn("## 🇯🇵 日本への影響", repaired)

    def test_missing_required_heading_is_not_repaired(self):
        original = sample().replace("## 🔬 科学\n", "")
        self.assertIsNone(repair_text(original))
        self.assertEqual(classify_failure("required_heading_invalid", original), SUBSTANTIVE_FAILURE)

    def test_content_gate_failures_are_substantive(self):
        original = sample(extra=True)
        self.assertEqual(classify_failure("top3_invalid", original), SUBSTANTIVE_FAILURE)
        self.assertEqual(classify_failure("japan_impact_too_shallow", original), SUBSTANTIVE_FAILURE)


if __name__ == "__main__":
    unittest.main()
