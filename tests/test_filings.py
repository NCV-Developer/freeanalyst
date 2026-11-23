"""财报下载：`datasources/filings.py`。

**不联网** —— 测的是判据，不是网络。真下载在 /tmp 里手工验过三个市场。

★ 钉六件事（每一条都是真踩过的坑）：

    ① 「半年度報告」不能当年报 —— 它含「年度報告」子串 ✗
    ② 交易所的类别标不够 —— 建行把保荐机构的持续督导报告也归在 `[年報]` 下 ✗
    ③ 港股名称搜索会被窝轮淹没 —— 正股代码一律 0 开头
    ④ 报告期和披露日是两个口径 —— 2025 年报是 2026-04 披露的
    ⑤ 查不到编号必须抛，不许返回空列表
    ⑥ 下回来的必须验文件头 —— 东财会把混淆脚本伪装成 application/pdf
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import net
from datasources import filings


class TestYearInTitle(unittest.TestCase):
    """港股标题两种写法都有，都得认。"""

    def test_arabic(self):
        self.assertTrue(filings._year_in("2025年度報告", 2025))
        self.assertFalse(filings._year_in("2024年度報告", 2025))

    def test_chinese_numerals(self):
        self.assertTrue(filings._year_in("二零二五年年報", 2025))
        self.assertFalse(filings._year_in("二零二四年年報", 2025))

    def test_alternate_zero(self):
        """有的写「二〇二五」（用〇不用零）。"""
        self.assertTrue(filings._year_in("二〇二五年年報", 2025))


class TestHkAnnualFilter(unittest.TestCase):
    """★ 港股年报筛选的三道坎。"""

    def _run(self, rows, kind="annual", period=2025):
        payload = {"result": __import__("json").dumps(rows, ensure_ascii=False)}
        with mock.patch.object(filings, "_hk_sid", return_value="190371"), \
             mock.patch.object(filings, "_get", return_value=b"{}"), \
             mock.patch.object(filings, "_jsonp", return_value=payload):
            return filings.find_hk("01810", kind, period)

    @staticmethod
    def _row(title, long="", link="/x.pdf", date="28/04/2026 16:30"):
        return {"TITLE": title, "LONG_TEXT": long, "FILE_LINK": link,
                "DATE_TIME": date}

    def test_interim_not_counted_as_annual(self):
        """★「二零二六年半年度報告」含「年度報告」—— 不能当年报。"""
        got = self._run([self._row("二零二六年半年度報告",
                                   "財務報表&#x2f;[中期報告]")])
        self.assertEqual(got, [])

    def test_interim_ok_when_asked(self):
        got = self._run([self._row("二零二五年中期報告",
                                   "財務報表&#x2f;[中期報告]")], kind="interim")
        self.assertEqual(len(got), 1)

    def test_third_party_report_excluded(self):
        """★ 建行的「…持續督導報告」被归在 `[年報]` 类别下 —— 但它不是年报。"""
        got = self._run([self._row(
            "中信証券股份有限公司關於中國建設銀行股份有限公司2025年持續督導報告",
            "財務報表&#x2f;[年報]")])
        self.assertEqual(got, [])

    def test_real_annual_passes(self):
        got = self._run([self._row("2025年度報告", "財務報表&#x2f;[年報]")])
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0].code, "01810")
        self.assertEqual(got[0].source, "hkexnews")
        self.assertTrue(got[0].url.startswith("https://www1.hkexnews.hk/"))

    def test_reports_without_link_skipped(self):
        got = self._run([self._row("2025年度報告", "", link="")])
        self.assertEqual(got, [])


class TestSuggestFiltersWarrants(unittest.TestCase):
    """★ 东财把窝轮的类型也标成「港股」，必须按代码剔掉。"""

    def _run(self, data):
        payload = {"QuotationCodeTable": {"Data": data}}
        with mock.patch.object(filings, "_get",
                               return_value=__import__("json").dumps(payload).encode()):
            return filings.resolve_suggest("小米", 10)

    def test_warrents_removed(self):
        got = self._run([
            {"Code": "01810", "Name": "小米集团-W", "SecurityTypeName": "港股"},
            {"Code": "13011", "Name": "小米信证七二沽A", "SecurityTypeName": "港股"},
            {"Code": "81810", "Name": "小米集团-WR", "SecurityTypeName": "港股"},
        ])
        self.assertEqual([i.code for i in got], ["01810"])

    def test_non_stock_types_removed(self):
        got = self._run([
            {"Code": "BK0833", "Name": "小米概念", "SecurityTypeName": "板块"},
            {"Code": "00700", "Name": "腾讯控股", "SecurityTypeName": "港股"},
        ])
        self.assertEqual([i.code for i in got], ["00700"])

    def test_multi_market_kept(self):
        got = self._run([
            {"Code": "601939", "Name": "建设银行", "SecurityTypeName": "沪A"},
            {"Code": "00939", "Name": "建设银行", "SecurityTypeName": "港股"},
        ])
        self.assertEqual({i.market for i in got}, {"cn", "hk"})


class TestPeriodVsFiled(unittest.TestCase):
    """★ 两个口径不许混 —— 混用的后果不是报错，是给你一份看起来正常的财报。"""

    def test_period_window_covers_next_year(self):
        """2025 年报是 2026-04 披露的 → 窗口必须覆盖到次年。"""
        captured = {}

        def fake_post(url, params, purpose, **kw):
            captured.update(params)
            return b'{"announcements": []}'

        with mock.patch.object(filings, "_cn_org", return_value="gssh0600519"), \
             mock.patch.object(filings, "_post", fake_post):
            filings.find_cn("600519", "annual", period=2025)
        self.assertIn("2026", captured["seDate"], "窗口没覆盖到次年，会一条不剩")
        self.assertIn("2024", captured["seDate"])

    def test_filed_overrides_period(self):
        captured = {}

        def fake_post(url, params, purpose, **kw):
            captured.update(params)
            return b'{"announcements": []}'

        with mock.patch.object(filings, "_cn_org", return_value="gssh0600519"), \
             mock.patch.object(filings, "_post", fake_post):
            filings.find_cn("600519", "annual", filed=("2025-01-01", "2025-06-30"))
        self.assertEqual(captured["seDate"], "2025-01-01~2025-06-30")

    def test_year_filter_uses_title_not_filed_date(self):
        """★ 2025 年报披露于 2026 —— 按披露日筛会一条不剩。"""
        import json as _json
        rows = [{"adjunctUrl": "a.PDF",
                 "announcementTitle": "X2025年年度报告",
                 "announcementTime": 1776355200000}]      # 2026-04-17
        with mock.patch.object(filings, "_cn_org", return_value="gssh0600519"), \
             mock.patch.object(filings, "_post",
                               return_value=_json.dumps({"announcements": rows}).encode()):
            got = filings.find_cn("600519", "annual", period=2025)
        self.assertEqual(len(got), 1)
        self.assertTrue(got[0].filed_at.startswith("2026"))


class TestFailuresAreClassified(unittest.TestCase):
    """★ 三种完全不同的原因，不许报成同一句。"""

    def test_unknown_query_is_no_match(self):
        with mock.patch.object(filings, "resolve_suggest", return_value=[]), \
             mock.patch.object(filings, "resolve_cn", return_value=[]), \
             mock.patch.object(filings, "resolve_hk", return_value=[]), \
             mock.patch.object(filings, "resolve_us", return_value=[]):
            r = filings.find("不存在的公司名")
        self.assertEqual([x[0] for x in r.failures], [filings.FailReason.NO_MATCH])

    def test_ambiguous_reported_but_not_blocking(self):
        ids = [filings.Identity("hk", "00939", "建设银行", "9970"),
               filings.Identity("cn", "601939", "建设银行", "9900003682")]
        with mock.patch.object(filings, "resolve_suggest", return_value=ids), \
             mock.patch.object(filings, "find_hk", return_value=[]), \
             mock.patch.object(filings, "find_cn", return_value=[]):
            r = filings.find("建設銀行", period=2025)
        self.assertIn(filings.FailReason.AMBIGUOUS, [x[0] for x in r.failures])

    def test_missing_id_is_its_own_reason(self):
        ids = [filings.Identity("hk", "00939", "建设银行", "")]
        with mock.patch.object(filings, "resolve_suggest", return_value=ids), \
             mock.patch.object(filings, "resolve_hk", return_value=[]), \
             mock.patch.object(filings, "find_hk",
                               side_effect=LookupError("查不到编号")):
            r = filings.find("00939")
        self.assertIn(filings.FailReason.MISSING_ID, [x[0] for x in r.failures])


class TestDownloadChecksMagic(unittest.TestCase):
    """★ 东财的 PDF CDN 会把 988 字节的混淆脚本伪装成 application/pdf。"""

    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.dest = Path(self._d.name)

    def tearDown(self):
        self._d.cleanup()

    def test_fake_pdf_raises(self):
        f = filings.Filing(market="cn", code="600519", title="x",
                           filed_at="2026-04-17", url="http://static.cninfo.com.cn/x.PDF")
        with mock.patch.object(filings, "_get",
                               return_value=b"<script>function a(a){_0x649a...}"):
            with self.assertRaises(ValueError) as cm:
                filings.download(f, self.dest)
        self.assertIn("不是 PDF", str(cm.exception))

    def test_no_silent_overwrite(self):
        """同一天同类型的两份（中文/英文版）必须分开存。"""
        bodies = [b"%PDF-1.7" + b"A" * 100, b"%PDF-1.7" + b"B" * 300]
        paths = []
        for t, b in zip(("年报", "年报（英文版）"), bodies):
            f = filings.Filing(market="cn", code="600519", title=t,
                               filed_at="2026-04-17",
                               url="http://static.cninfo.com.cn/x.PDF")
            with mock.patch.object(filings, "_get", return_value=b):
                paths.append(filings.download(f, self.dest))
        self.assertNotEqual(paths[0].name, paths[1].name)

    def test_source_record_written(self):
        f = filings.Filing(market="hk", code="01810", title="2025年度報告",
                           filed_at="2026-04-28", period="2025-12-31",
                           url="https://www1.hkexnews.hk/x.pdf", source="hkexnews")
        with mock.patch.object(filings, "_get", return_value=b"%PDF-1.7\n..."):
            p = filings.download(f, self.dest)
        rec = p.with_suffix(".source.json")
        self.assertTrue(rec.exists())
        txt = rec.read_text(encoding="utf-8")
        self.assertIn("filed_at", txt)
        self.assertIn("hkexnews", txt)


class TestHostsDeclared(unittest.TestCase):
    def test_all_hosts_allowed(self):
        allow = net.allowed_hosts(filings.HOSTS)
        for h in filings.HOSTS:
            self.assertIn(h, allow, f"{h} 没进白名单")


if __name__ == "__main__":
    unittest.main(verbosity=2)
