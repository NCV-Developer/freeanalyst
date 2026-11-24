"""SEC XBRL 取数 —— `datasources/sec_xbrl.annual_values()`（打桩，不联网）。"""

from __future__ import annotations

import unittest

from datasources import sec_xbrl


def fake_concept(cik, tag, *, unit="USD"):
    if tag == "Assets":
        return [
            {"end": "2025-09-27", "form": "10-K", "fp": "FY", "val": 359241000000},
            {"end": "2025-06-28", "form": "10-Q", "fp": "Q3", "val": 350000000000},
            {"end": "2024-09-28", "form": "10-K", "fp": "FY", "val": 300000000000},
        ]
    if tag == "CashAndCashEquivalentsAtCarryingValue":
        return [{"end": "2025-09-27", "form": "10-K", "fp": "FY", "val": 35934000000}]
    if tag == "Revenues":
        return []                      # 没有这个概念 → 回退到下一个候选
    if tag == "RevenueFromContractWithCustomerExcludingAssessedTax":
        return [{"end": "2025-09-27", "form": "10-K", "fp": "FY", "val": 416161000000}]
    return []


class TestAnnualValues(unittest.TestCase):
    def setUp(self):
        self.orig = sec_xbrl.concept
        sec_xbrl.concept = fake_concept

    def tearDown(self):
        sec_xbrl.concept = self.orig

    def test_picks_fy_10k_of_requested_year(self):
        vals = sec_xbrl.annual_values("320193", 2025)
        self.assertEqual(vals["资产总计"], 359241000000)

    def test_fallback_to_alternative_concept(self):
        vals = sec_xbrl.annual_values("320193", 2025)
        self.assertEqual(vals["营业收入"], 416161000000)

    def test_missing_items_are_omitted_not_zero(self):
        vals = sec_xbrl.annual_values("320193", 2025)
        self.assertNotIn("存货", vals, "取不到就不给值，不填 0")

    def test_does_not_use_other_year_or_quarter(self):
        vals = sec_xbrl.annual_values("320193", 2025)
        self.assertNotEqual(vals.get("资产总计"), 350000000000)   # 不是 10-Q
        self.assertNotEqual(vals.get("资产总计"), 300000000000)   # 不是上一年


if __name__ == "__main__":
    unittest.main(verbosity=2)
