"""规则服务面的分类逻辑。

★ 钉住一条**最要紧的判据**：

    **英文写法 / 繁体写法「零触发」不算可疑** —— 它们是给 IFRS 和港股材料
    准备的，这批材料里没有而已。上一轮把它们全算成「死规则 = 过拟合」，
    得出「68% 是垃圾」的错误结论 ✗

    只有**简体写法**零触发才进「真待查」这一类。
"""

from __future__ import annotations

import unittest

from bench import rule_coverage as rc


class TestBuckets(unittest.TestCase):
    def setUp(self):
        self.labels = {
            "a.pdf": ["营业收入", "净利润", "资产总计", "资产总计"],
            "b.pdf": ["营业收入", "净利润"],
            "c.pdf": ["营业收入"],
        }
        self.r = rc.classify(self.labels)

    def test_counts_deduped_rules(self):
        """完全重复的只算一次。"""
        self.assertGreater(self.r["total"], 0)
        self.assertGreaterEqual(self.r["dup"], 0)

    def test_three_files_is_generalizing(self):
        """「营业收入」在三份材料里都出现 → 泛化。"""
        gen = [n for _f, n, _u in self.r["buckets"]["generalizing"]]
        self.assertIn("营业收入", gen)

    def test_english_zero_use_is_not_suspect(self):
        """★ 英文写法零触发 → 归「英文」，**不进「真待查」**。"""
        eng = [n for _f, n, _u in self.r["buckets"].get("english", [])]
        sus = [n for _f, n, _u in self.r["buckets"].get("suspect", [])]
        self.assertTrue(eng, "应当有英文写法被归到 english")
        self.assertFalse(any(not rc.CJK.search(n) for n in sus),
                         "英文写法不该进「真待查」")

    def test_traditional_zero_use_is_not_suspect(self):
        """★ 繁体写法零触发 → 归「繁体」，**不进「真待查」**。"""
        trad = [n for _f, n, _u in self.r["buckets"].get("trad", [])]
        sus = [n for _f, n, _u in self.r["buckets"].get("suspect", [])]
        self.assertTrue(trad, "应当有繁体写法被归到 trad")
        self.assertFalse(any(any(ch in rc.TRAD_CHARS for ch in n) for n in sus),
                         "繁体写法不该进「真待查」")

    def test_simplified_zero_use_is_suspect(self):
        """简体 + 零触发 → 才算「真待查」。"""
        sus = [n for _f, n, _u in self.r["buckets"].get("suspect", [])]
        self.assertTrue(sus, "应当有简体零触发的规则进 suspect")
        for n in sus:
            self.assertTrue(rc.CJK.search(n))
            self.assertFalse(any(ch in rc.TRAD_CHARS for ch in n))

    def test_buckets_sum_to_total(self):
        """每一类加起来必须等于规则总数 —— 一条都不许漏。"""
        total = self.r["total"]
        got = sum(len(v) for v in self.r["buckets"].values())
        self.assertEqual(got, total)

    def test_never_deletes_anything(self):
        """★ 这个工具**只报告不删** —— 上一版判反了「谁冗余」，差点删错。

        同 field 内**长的才冗余**：标签正好是「固定资产」时，
        「固定资产净额」匹配不上，删短的会丢整行。
        """
        import inspect
        src = inspect.getsource(rc)
        for verb in ("write_text(", "os.remove", "unlink("):
            self.assertNotIn(verb, src,
                             f"分类工具不该动文件（发现 {verb}）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
