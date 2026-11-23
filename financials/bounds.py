"""页边界修正 —— 只在「证据明确」时扩，不替换高分页。

实测（2026-11-22，5 家失败样本全部复核）：

    301072  合并利润表从 p102 开始，范围却是 103–105 → 漏了表的第一页，
            营业收入只好从后面的母公司表里取（5.50 亿 vs 8.09 亿）
    600930  现金流量表跨 138–140，只圈了 140 → 经营、筹资两段全没了
    688316  合并现金流量表在 p152–153，却选了 p240 → 那是间接法附注，不是主表

与 `_title_pages` 的区别：**那里是「用表题页替换高分页」，试过会误伤**
（某保险公司：真表页整页没有表题，却是分数最高的）。这里只做两件事：

    ① 左扩：紧邻的前一页就印着本表表题 → 把它并进来（不跳页、不替换）
    ② 现金流量表校验：整段都没有直接法标志（「销售商品」）→ 选到间接法附注了，
       改用含直接法标志的页重选；找不到就保留原样并报警，不硬改。
"""

from __future__ import annotations

# 只认「销售商品」：间接法附注（补充资料）里也有「经营活动产生的现金流量」，
# 拿它当标志就分不出主表和附注 —— 688316 就是这么漏过去的。
_DIRECT_METHOD = ("销售商品",)


def _text(doc, n: int) -> str:
    if 0 < n <= len(doc.pages):
        return doc.pages[n - 1].raw_text or ""
    return ""


def fix_bounds(doc, kind: str, pages: list[int]) -> tuple[list[int], list[str]]:
    """返回 `(修正后的页范围, 说明)`。没有把握就原样返回。"""
    if not pages:
        return pages, []
    notes: list[str] = []
    out = list(pages)

    # ① 左扩到表题页：只看紧邻的前两页，且那一页必须印着本表表题。
    from .from_pdf import TITLES
    # ⚠️ 只认**合并**表题。600930 的第 139 页印着「母公司现金流量表」，
    # 旧判据一看"有表题"就不往前扩了，于是真正的合并表首页 p138 被漏在外面。
    titles = tuple(t for t in TITLES.get(kind, ()) if "母公司" not in t)
    # 有「合并 XX 表」写法时，**只认它**。泛称「现金流量表」会命中
    # 「母公司现金流量表」，于是 600930 的第 139 页被当成"已有表题"，
    # 真正的合并表首页 p138 就补不进来。
    cons = tuple(t for t in titles if t.startswith("合并"))

    def has_title(txt: str) -> bool:
        return any(t in txt for t in (cons or titles))

    if titles and not has_title(_text(doc, out[0])):
        for back in (1, 2):
            prev = out[0] - back
            if prev < 1:
                break
            if has_title(_text(doc, prev)):
                out = list(range(prev, out[0])) + out
                notes.append(f"{kind}：表题在第 {prev} 页，范围已向前补到表题页")
                break

    # ② 右扩到收尾行：整段都没有本表的收尾行 → 表的最后一页没圈进来。
    #    实测：600519 筹资净额与期末现金取不全、688021 期末现金取不全。
    #    **跳过印着「母公司 XX 表」表题的页** —— 那是下一张表的开头，
    #    并进去就会把母公司的收尾行当合并的用（取不全变成取不对，更糟）。
    _CLOSING = {"cash_flow": ("期末现金",),
                "income": ("净利润",),
                "balance": ("所有者权益合计",)}
    closers = _CLOSING.get(kind, ())
    if closers:
        body = "".join(_text(doc, n) for n in out)
        if not any(c in body for c in closers):
            for nxt in (out[-1] + 1, out[-1] + 2):
                t = _text(doc, nxt)
                if not t:
                    continue
                if any(f"母公司{s}" in t for s in ("资产负债表", "利润表", "现金流量表")):
                    break
                if any(c in t for c in closers):
                    out = out + list(range(out[-1] + 1, nxt + 1))
                    notes.append(f"{kind}：收尾行在第 {nxt} 页，范围已向后补到该页")
                    break

    # ③ 现金流量表：整段都没有直接法标志 → 多半是间接法附注（补充资料）
    if kind == "cash_flow":
        body = "".join(_text(doc, n) for n in out)
        if not any(m in body for m in _DIRECT_METHOD):
            cand = [pg.number for pg in doc.pages
                    if "销售商品" in (pg.raw_text or "")
                    and "现金" in (pg.raw_text or "")]
            if cand:
                start = cand[0]
                new = [start]
                for n in (start + 1, start + 2):        # 主表通常跨 2–3 页
                    if any(m in _text(doc, n) for m in
                           ("投资活动", "筹资活动", "期末现金")):
                        new.append(n)
                notes.append(
                    f"cash_flow：原范围 {out} 没有直接法标志（像间接法附注），"
                    f"改取第 {new[0]}–{new[-1]} 页")
                out = new
            else:
                notes.append("cash_flow：选中的页没有直接法标志，但也找不到更好的页")
    return out, notes
