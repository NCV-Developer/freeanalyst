"""附注列识别 —— `from_pdf._looks_like_note_ref` 与 `_fill` 的取值规则。

## 背景（这是实测踩出来的，不是想出来的）

A 股报表的列是「科目 | 附注 | 期末 | 期初」。`_fill` 原来的规则是
「标签之后第一个能解析成金额的单元格」—— 而**附注号本身也是个数字**：

    一、营业总收入   172,054,171,890.91    ← 这行没附注号，取对了
    其中:营业收入              44.0         ← 那是「附注 44」，不是金额 ✗
    营业成本                   44.0         ← 附注 44 ✗

取出来的 44、45、46 形状完全正常，不报错，只有跟另一份数据源对账才发现。
（这次是靠新浪的结构化三表对账查出来的：营业成本应为 148.9 亿，不是 44。）

★ 关键：跳附注**必须保守** —— 单看「小于 1000 的纯整数」会把合法的
  小额误判成附注，所以还要「后一个数大一个量级」才跳。
"""

from __future__ import annotations

import unittest

from financials.from_pdf import _looks_like_note_ref


class TestNoteRefShape(unittest.TestCase):
    def test_small_bare_integers_are_note_like(self):
        for s in ("1", "44", "45", "160", "999"):
            self.assertTrue(_looks_like_note_ref(s), s)

    def test_note_written_with_decimals_is_note_like(self):
        """实测：附注号也会被格式化成两位小数 —— ``131.00``。

        这就是某次对账里漏跳的那一行：``['收到其他与筹资活动有关的现金',
        '131.00', '625,000.00']`` —— 真值是 62.5 万，取到了 131。
        只认 ``isdigit()`` 会漏掉这种写法。
        """
        for s in ("44.00", "131.00", "1.0", "999.000"):
            self.assertTrue(_looks_like_note_ref(s), s)

    def test_amounts_are_not_note_like(self):
        for s in ("1,234", "65.66", "14892277570.91", "-44", "1000", "1234",
                  "五、1", "", "  ", "44(1)", "131.5", "1,234.56"):
            self.assertFalse(_looks_like_note_ref(s), s)

    def test_whitespace_tolerated(self):
        self.assertTrue(_looks_like_note_ref("  44  "))


class TestNoteSkipRequiresMagnitude(unittest.TestCase):
    """跳与不跳的分界：后一个数是否大一个量级。"""

    @staticmethod
    def _pick(cells: list[str]) -> float | None:
        """复刻 `_fill` 里的取值逻辑（保持与生产代码同构）。"""
        from financials.from_pdf import _to_number

        nums: list[tuple[str, float]] = []
        for s in cells:
            s = (s or "").strip()
            if not s or s.startswith("？"):
                continue
            got = _to_number(s)
            if got is not None:
                nums.append((s, got))
        if not nums:
            return None
        take = 0
        if (len(nums) >= 2 and _looks_like_note_ref(nums[0][0])
                and abs(nums[1][1]) >= max(1000.0, abs(nums[0][1]) * 10)):
            take = 1
        return nums[take][1]

    def test_note_column_skipped(self):
        """科目 | 44 | 14,892,277,570.91 | 13,000,000,000.00"""
        self.assertEqual(self._pick(["44", "14,892,277,570.91", "13,000,000,000.00"]),
                         14_892_277_570.91)

    def test_no_note_column_unchanged(self):
        """深市 3 列表：科目 | 期末 | 期初 —— 不能跳。"""
        self.assertEqual(self._pick(["1,234,490,44", "2,218,042,51"]), 123449044.0)

    def test_small_legit_amount_not_skipped(self):
        """两个都小 → 不跳。否则「单价 500」会被当成附注丢掉。"""
        self.assertEqual(self._pick(["500", "600"]), 500.0)

    def test_note_without_amount_keeps_note(self):
        """只有附注号、没有金额 → 只能取它（行本身是空的，由上层判空）。"""
        self.assertEqual(self._pick(["44"]), 44.0)

    def test_per_share_kept(self):
        """每股收益 65.66 是合法金额 —— 有小数点，不是附注形状。"""
        self.assertEqual(self._pick(["65.66", "60.79"]), 65.66)

    def test_ocr_suspect_skipped(self):
        self.assertEqual(self._pick(["？44", "1,000,000.00"]), 1_000_000.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
