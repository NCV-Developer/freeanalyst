"""估值自检 —— `valuation/sanity.py`。

★ 钉四件事：

    ① **有 fail 就必须判「不可用」** —— 这是整个模块存在的理由
    ② 缺参数标 `na`，**不许拿默认值凑一条检查**
    ③ 单位错和科目错要能被揪出来（EBITDA 率 > 100%）
    ④ 金融业没有 EBITDA 概念 → `na`，不是 fail
"""

from __future__ import annotations

import unittest

from valuation import sanity


class TestHardGate(unittest.TestCase):
    """① 不过就不出结论。"""

    def test_fail_makes_unusable(self):
        rep = sanity.check_all(
            ev_mid=1_000_000, market_cap=1_000_000,
            terminal_share=0.95)          # 终值 95% → fail
        self.assertFalse(rep.usable())
        self.assertIn("不可用", rep.headline())
        self.assertEqual(len(rep.failed), 1)

    def test_all_ok_is_usable(self):
        rep = sanity.check_all(
            ev_mid=1_000_000_000, market_cap=1_000_000_000,
            terminal_share=0.65, value_per_share=20, fcf_per_share=1,
            ebitda=680, revenue=1000, industry="白酒",
            wacc=0.08, roic=0.11, ev_low=800, ev_high=1200)
        self.assertTrue(rep.usable(), rep.render())
        self.assertEqual(rep.failed, [])

    def test_warn_alone_still_usable(self):
        """只有 warn 时**仍然可用** —— 别把提示当拦截。"""
        rep = sanity.check_all(ev_mid=1_000_000, market_cap=600_000)
        self.assertTrue(rep.usable())
        self.assertTrue(rep.warned)


class TestNoDefaults(unittest.TestCase):
    """② 缺参数就是 na，不许猜。"""

    def test_empty_input_all_na(self):
        rep = sanity.check_all()
        self.assertTrue(all(c.verdict == "na" for c in rep.checks),
                        [(c.name, c.verdict) for c in rep.checks])
        self.assertTrue(rep.usable(), "na 不该让结论不可用")

    def test_missing_market_cap_is_na_not_fail(self):
        rep = sanity.check_all(ev_mid=1_000_000)
        ev = next(c for c in rep.checks if "企业价值" in c.name)
        self.assertEqual(ev.verdict, "na")


class TestCatchesUnitAndAccountErrors(unittest.TestCase):
    """③ 这是自检最核心的用途。"""

    def test_magnitude_error_in_ev(self):
        """算出的 EV 只有市值的 1/10 —— 典型单位错。"""
        rep = sanity.check_all(ev_mid=100_000_000, market_cap=1_000_000_000)
        ev = next(c for c in rep.checks if "企业价值" in c.name)
        self.assertEqual(ev.verdict, "fail")
        self.assertIn("单位", ev.detail)

    def test_absurd_ebitda_margin(self):
        """EBITDA 率 6800% —— 一定是单位或科目错。"""
        rep = sanity.check_all(ebitda=68_000, revenue=1_000, industry="白酒")
        c = next(x for x in rep.checks if "EBITDA 率" in x.name)
        self.assertEqual(c.verdict, "fail")

    def test_normal_baijiu_margin_ok(self):
        rep = sanity.check_all(ebitda=680, revenue=1_000, industry="白酒")
        c = next(x for x in rep.checks if "EBITDA 率" in x.name)
        self.assertEqual(c.verdict, "ok")

    def test_margin_outside_industry_warns(self):
        """60% 的 EBITDA 率放在钢铁行业 → warn，指向科目取错。"""
        rep = sanity.check_all(ebitda=600, revenue=1_000, industry="钢铁")
        c = next(x for x in rep.checks if "EBITDA 率" in x.name)
        self.assertEqual(c.verdict, "warn")
        self.assertIn("科目", c.detail)

    def test_absurd_per_share_multiple(self):
        rep = sanity.check_all(value_per_share=300, fcf_per_share=1)
        c = next(x for x in rep.checks if "每股" in x.name)
        self.assertEqual(c.verdict, "fail")


class TestFinancialSectorNotApplicable(unittest.TestCase):
    """④ 银行没有 EBITDA 概念。"""

    def test_bank_margin_is_na(self):
        rep = sanity.check_all(ebitda=100, revenue=1000, industry="银行")
        c = next(x for x in rep.checks if "EBITDA 率" in x.name)
        self.assertEqual(c.verdict, "na")

    def test_unknown_industry_is_na(self):
        """认不出行业 → `na`，**不是 ok**。

        第一版这里用了一个 0–90% 的宽区间当默认，结果某营销公司 0.5% 的
        EBITDA 率被打成 ✓ —— 那等于说「这数没问题」，而实际上根本没检查。
        `na` 的语义是「查不了」，不是「通过了」。
        """
        rep = sanity.check_all(ebitda=500, revenue=1000, industry="")
        c = next(x for x in rep.checks if "EBITDA 率" in x.name)
        self.assertEqual(c.verdict, "na")


class TestTerminalShare(unittest.TestCase):
    def test_over_90_fails(self):
        rep = sanity.check_all(terminal_share=0.94)
        c = next(x for x in rep.checks if "终值" in x.name)
        self.assertEqual(c.verdict, "fail")

    def test_under_40_warns(self):
        rep = sanity.check_all(terminal_share=0.3)
        c = next(x for x in rep.checks if "终值" in x.name)
        self.assertEqual(c.verdict, "warn")

    def test_no_dcf_is_na(self):
        rep = sanity.check_all()
        c = next(x for x in rep.checks if "终值" in x.name)
        self.assertEqual(c.verdict, "na")


class TestRender(unittest.TestCase):
    def test_render_has_marks_and_reasons(self):
        rep = sanity.check_all(ev_mid=100, market_cap=1_000_000,
                               terminal_share=0.95)
        text = "\n".join(rep.render())
        self.assertIn("不可用", text)
        self.assertIn("✗", text)
        self.assertIn("单位", text)      # 失败理由要写出来


if __name__ == "__main__":
    unittest.main(verbosity=2)
