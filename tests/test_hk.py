"""港股繁体：无标签小计行的结构兜底 —— `hk._unlabeled_before()`。

腾讯实测：非流动资产合计、流动资产合计、收入合计在 PDF 里都是
`〔此行没有标签〕`，只能靠段落位置认。
"""

from __future__ import annotations

import unittest

from financials import hk

ROWS = [
    ["物業、設備及器材", "17", "149,905", "80,185"],
    ["無形資產", "21", "205,999", "196,127"],
    ["〔此行没有标签〕", "", "1,443,526", "1,284,815"],   # 非流动资产合计
    ["存貨", "", "530", "440"],
    ["應收賬款", "31", "49,930", "48,203"],
    ["現金及現金等價物", "32", "141,041", "132,519"],
    ["〔此行没有标签〕", "", "595,460", "496,180"],         # 流动资产合计
    ["資產總額", "", "2,038,986", "1,780,995"],
]


class TestUnlabeledBefore(unittest.TestCase):
    def test_noncurrent_subtotal_before_inventories(self):
        self.assertEqual(hk._unlabeled_before(ROWS, "存貨"), 1443526)

    def test_current_subtotal_before_total_assets(self):
        self.assertEqual(hk._unlabeled_before(ROWS, "資產總額"), 595460)

    def test_articulation(self):
        # 流动 + 非流动 必须等于 资产总计（腾讯实测勾稽）
        a = hk._unlabeled_before(ROWS, "資產總額")
        b = hk._unlabeled_before(ROWS, "存貨")
        self.assertIsNotNone(a)
        self.assertIsNotNone(b)
        self.assertEqual(a + b, 2038986)

    def test_no_match_returns_none(self):
        self.assertIsNone(hk._unlabeled_before(ROWS, "不存在的科目"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
