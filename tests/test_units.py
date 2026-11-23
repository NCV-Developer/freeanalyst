"""单位归一化 —— `reconcile.unit_mult()`。

比亚迪 002594：报表「单位为：千元」，对账没换算 → 17 格全差 1000 倍。
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace as NS

from financials import reconcile


def st(unit="", bal_unit=None):
    return NS(unit=unit,
              balance=NS(unit=bal_unit if bal_unit is not None else unit),
              income=NS(unit=unit), cash_flow=NS(unit=unit))


class TestUnitMult(unittest.TestCase):
    def test_yuan_default(self):
        self.assertEqual(reconcile.unit_mult(st(""), "balance"), 1.0)

    def test_thousand(self):
        self.assertEqual(reconcile.unit_mult(st("千元"), "balance"), 1000.0)

    def test_million_not_ten_thousand(self):
        # 「百万元」里含「万元」，顺序错了会差 100 倍
        self.assertEqual(reconcile.unit_mult(st("百万元"), "balance"), 1e6)

    def test_block_unit_wins(self):
        self.assertEqual(reconcile.unit_mult(st("元", bal_unit="万元"), "balance"), 1e4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
