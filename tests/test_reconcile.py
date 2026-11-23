"""对账模块 —— `financials/reconcile.py` + `datasources/sina.paper_code`。

★ 钉四件事：

    ① **差异要分四档**，不能只报「不一致」——
       口径不同、取错年份、真取错，处置完全不同
    ② **「取错年份」要能自动认出来**：我们的数正好等于上一期
    ③ **「对方没有这一项」不算错** —— 否则报告永远红着，人就不看了
    ④ **只在本地判，不改任何数** —— 对不上回去修抽取

全部不联网：patch `datasources.sina.fetch`。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import unittest

from datasources import sina
from financials import reconcile
from financials.canonical import Field


def make_statements(*, income=None, balance=None, cash_flow=None):
    def block(fields):
        return SimpleNamespace(fields=fields) if fields is not None else None
    return SimpleNamespace(income=block(income), balance=block(balance),
                           cash_flow=block(cash_flow))


def period(date, items, scope="合并期末"):
    return sina.SinaPeriod(date=date, scope=scope, audited="是",
                           opinion="标准无保留审计意见", items=items)


class TestPaperCode(unittest.TestCase):
    def test_exchange_prefix(self):
        self.assertEqual(sina.paper_code("600519"), "sh600519")
        self.assertEqual(sina.paper_code("688316"), "sh688316")   # 科创板在沪
        self.assertEqual(sina.paper_code("000001"), "sz000001")
        self.assertEqual(sina.paper_code("300750"), "sz300750")
        self.assertEqual(sina.paper_code("920001"), "bj920001")
        self.assertEqual(sina.paper_code("830799"), "bj830799")

    def test_bad_code_raises(self):
        for bad in ("60051", "abcdef", ""):
            with self.assertRaises(ValueError):
                sina.paper_code(bad)


class TestVerdicts(unittest.TestCase):
    def _run(self, ours, theirs, prior=None, field=Field.REVENUE, name="营业收入"):
        st = make_statements(income={field: ours})
        per = [period("20251231", {name: theirs})]
        if prior is not None:
            per.append(period("20241231", {name: prior}))
        with mock.patch.object(sina, "fetch", return_value=per):
            return reconcile.compare(st, "600519", year=2025)

    def test_match(self):
        rep = self._run(1000.0, 1000.0)
        self.assertEqual(rep.items[0].verdict, reconcile.Verdict.MATCH)
        self.assertTrue(rep.clean())

    def test_tiny_diff_is_rounding_not_failure(self):
        rep = self._run(1000.0, 1000.5)          # 0.05%
        self.assertEqual(rep.items[0].verdict, reconcile.Verdict.ROUNDING)
        self.assertTrue(rep.clean(), "四舍五入不该把整份判成不干净")

    def test_real_mismatch(self):
        rep = self._run(1000.0, 1400.0)
        self.assertEqual(rep.items[0].verdict, reconcile.Verdict.MISMATCH)
        self.assertFalse(rep.clean())

    def test_year_shift_detected(self):
        """我们的数正好等于上一期 —— 取错年份，必须单独认出来。"""
        rep = self._run(900.0, 1000.0, prior=900.0)
        self.assertEqual(rep.items[0].verdict, reconcile.Verdict.YEAR_SHIFT)
        self.assertIn("上期", rep.items[0].note)
        self.assertFalse(rep.clean())

    def test_ours_missing(self):
        st = make_statements(income={})
        with mock.patch.object(sina, "fetch",
                               return_value=[period("20251231", {"营业收入": 1000.0})]):
            rep = reconcile.compare(st, "600519", year=2025)
        self.assertEqual(rep.items[0].verdict, reconcile.Verdict.OURS_MISSING)
        self.assertFalse(rep.clean())

    def test_theirs_missing_is_not_failure(self):
        st = make_statements(income={Field.REVENUE: 1000.0})
        with mock.patch.object(sina, "fetch", return_value=[period("20251231", {})]):
            rep = reconcile.compare(st, "600519", year=2025)
        self.assertEqual(rep.items[0].verdict, reconcile.Verdict.THEIRS_MISSING)
        self.assertTrue(rep.clean(), "对方没收录这一项，不能算我们错")


class TestSourceFailureIsLoud(unittest.TestCase):
    def test_fetch_error_becomes_note_not_silence(self):
        st = make_statements(income={Field.REVENUE: 1000.0})

        def boom(*a, **k):
            raise LookupError("新浪挂了")

        with mock.patch.object(sina, "fetch", side_effect=boom):
            rep = reconcile.compare(st, "600519", year=2025)
        self.assertEqual(rep.items, [])
        self.assertTrue(any("新浪挂了" in n for n in rep.notes),
                        f"取不到必须说出来，notes={rep.notes}")
        self.assertIn("没有可比", rep.headline())


class TestYearMismatchWarns(unittest.TestCase):
    def test_warns_when_counterpart_year_differs(self):
        st = make_statements(income={Field.REVENUE: 1000.0})
        with mock.patch.object(sina, "fetch",
                               return_value=[period("20241231", {"营业收入": 1000.0})]):
            rep = reconcile.compare(st, "600519", year=2025)
        self.assertTrue(any("不能当错误" in n for n in rep.notes), rep.notes)


class TestRender(unittest.TestCase):
    def test_render_shows_marks_and_headline(self):
        st = make_statements(income={Field.REVENUE: 1000.0})
        with mock.patch.object(sina, "fetch",
                               return_value=[period("20251231", {"营业收入": 1400.0})]):
            rep = reconcile.compare(st, "600519", year=2025)
        text = "\n".join(rep.render())
        self.assertIn("未通过", text)
        self.assertIn("✗", text)
        self.assertIn("营业收入", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
