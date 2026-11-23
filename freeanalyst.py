#!/usr/bin/env python3
"""freeanalyst —— 本地优先的投资尽调 agent。

    ingest   把材料切分、本地建索引（数据不出机器）
    ask      检索 + 本地模型作答，每条结论强制带原文出处
    appraise 从材料目录跑到估值报告：认三张表 → 列问答清单 → 出报告
    models   看本机有哪些模型，够不够格（质量门槛）
    audit    查看出网审计日志
    doctor   检查本地模型是否就绪

零第三方依赖。所有网络调用经过 guard.guarded_request()。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from guard import EgressBlocked, audit_summary, guarded_request
from retrieval import Chunk, BM25, chunk_document, load_text

ROOT = Path(__file__).resolve().parent
INDEX_DIR = ROOT / "index"
DEFAULT_MODEL = "qwen2.5-coder:7b"
OLLAMA_URL = "http://127.0.0.1:11434/v1/chat/completions"

#: **本机模型走原生接口，不走 /v1。** 实测：思考型模型（qwen3.5 等）在 /v1 上传
#: `think:false` 会被忽略 —— 答案全被挤进 reasoning，content 是空的（0 字 / reasoning
#: 3098 字 / finish_reason=length）；换成原生 /api/chat 就正常（content 63 字 /
#: thinking 0 字 / 37 tokens）。本机推理只在这里改，云端仍走各自的服务商接口。
NATIVE_CHAT_URL = "http://127.0.0.1:11434/api/chat"

SYSTEM_PROMPT = """你是一名投资尽调助手。你只能依据用户提供的【材料片段】回答。

铁律：
1. 每条结论后面必须标注来源编号，格式 [S1] 或 [S2][S3]。没有来源的话不要写。
2. 材料里没有的信息，一律写"材料未提供"，绝对禁止推测或用常识补全。
3. 数字必须原样引用。**不要换算单位、不要四舍五入、不要改变符号。**
   材料写"18,300 万元"就写"18,300 万元"，不要写成"1.83 亿元"；
   材料写"千美元"就写"千美元"。换算会引入量级错误
   （实测有模型把"千美元"当成"万美元"，差 10 倍），而且前面的数还是对的，
   读者看不出哪里错了。
4. 如果材料之间存在冲突，明确指出冲突，不要替它下结论。
5. 区分义务主体。实控人、董事长、股东个人的义务不等于公司的义务。
   写"公司面临…压力/责任/义务"之前，先确认材料说的是公司还是个人。

输出格式：
## 结论
（逐条，每条带 [Sn] 出处）

## 材料缺口
（要回答这个问题但材料里没有的东西）

## 风险提示
（材料中明确写出的不利条款或异常数据；没有就写"未发现"）
"""


# ---------------------------------------------------------------- 索引

def index_path() -> Path:
    return INDEX_DIR / "chunks.json"


def save_index(chunks: list[Chunk]) -> None:
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    payload = [
        {
            "chunk_id": c.chunk_id,
            "source": c.source,
            "para_start": c.para_start,
            "para_end": c.para_end,
            "text": c.text,
        }
        for c in chunks
    ]
    index_path().write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_index() -> list[Chunk]:
    path = index_path()
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    chunks = [
        Chunk(
            chunk_id=item["chunk_id"],
            source=item["source"],
            para_start=item["para_start"],
            para_end=item["para_end"],
            text=item["text"],
        )
        for item in raw
    ]
    from retrieval import tokenize

    for c in chunks:
        c.tokens = tokenize(c.text)
    return chunks


def _load_material(path: Path) -> tuple[str, list[str]]:
    """读一份材料 → (文本, 提示信息)。

    `.txt` / `.md` 走标准库；`.htm` / `.html` 走标准库；`.pdf` 走 `ingest/pdf.py`
    （需要可选的 pdfplumber）。

    **三种格式都要过归一化。** 只归一化 PDF 是不够的 ——
    索引侧和查询侧口径不一致就会静默失配，那比不归一化还隐蔽。
    """
    from ingest.normalize import normalize_text

    ext = path.suffix.lower()
    notes: list[str] = []

    if ext == ".pdf":
        from ingest import pdf as ip
        doc = ip.extract_pdf(path)
        notes.append(doc.summary())
        return doc.to_text(), notes

    if ext in (".htm", ".html"):
        from ingest import html as ih
        doc = ih.extract_html(path)
        notes.append(f"{path.name}：{len(doc.tables)} 个表格")
        return doc.to_text(), notes

    return normalize_text(load_text(path)), notes


def cmd_ingest(args: argparse.Namespace) -> int:
    corpus = Path(args.path).expanduser().resolve()
    if not corpus.exists():
        print(f"找不到路径：{corpus}", file=sys.stderr)
        return 1

    patterns = ("*.txt", "*.md", "*.pdf", "*.htm", "*.html")
    files: list[Path] = []
    for pattern in patterns:
        files.extend(sorted(corpus.rglob(pattern)))
    files = [f for f in files if not f.name.startswith(".")]
    if not files:
        print(f"{corpus} 下没有 .txt / .md / .pdf / .htm / .html 材料", file=sys.stderr)
        return 1

    # 重新编号，保证 S编号全局唯一且可追溯
    all_chunks: list[Chunk] = []
    counter = 0
    failed: list[tuple[Path, str]] = []

    for path in files:
        rel = str(path.relative_to(corpus))
        try:
            text, notes = _load_material(path)
        except Exception as exc:  # noqa: BLE001
            # **取不到的材料必须报出来** —— 静默跳过会让索引少几份，
            # 而后面所有问答都会以"材料里没写"来解释这个缺失。
            failed.append((path, f"{type(exc).__name__}: {exc}"))
            continue
        for n in notes:
            print(f"  {n}")
        n_before = len(all_chunks)
        for chunk in chunk_document(rel, text):
            counter += 1
            chunk.chunk_id = f"S{counter}"
            all_chunks.append(chunk)
        if len(all_chunks) == n_before:
            failed.append((path, "抽出来的文本为空 —— 可能是扫描件（本工具不做 OCR）"))

    save_index(all_chunks)
    n_pdf = sum(1 for f in files if f.suffix.lower() == ".pdf")
    print(f"\n已入库 {len(files) - len(failed)}/{len(files)} 份材料 "
          f"（其中 PDF {n_pdf} 份）→ {len(all_chunks)} 个片段")
    print(f"索引位置：{index_path()}")

    if failed:
        print(f"\n⚠ {len(failed)} 份没能入库：")
        for p, why in failed:
            print(f"  ✗ {p.relative_to(corpus)}：{why}")
        print("  **不静默跳过** —— 少了材料会让后面的问答以「材料里没写」"
              "来解释这个缺失，而实际上是没读进来。")

    print("（全部处理在本机完成，未发生任何出网调用）")
    return 0 if not failed else 2


# ---------------------------------------------------------------- 问答

def call_local_model(model: str, system: str, user: str) -> str:
    """本机模型（ollama）—— 老名字，保住了别的调用方。"""
    return call_model(model, system, user)


#: 云端模型的写法：**`服务商/模型名`**，例如 `deepseek/deepseek-chat`。
#: 不带前缀的一律当本机模型（默认那条路不变）。
CLOUD_MODEL_PAT = re.compile(r"^([a-z0-9_]+)/(.+)$")


def split_model(model: str) -> tuple[str, str]:
    """`deepseek/deepseek-chat` → `("deepseek", "deepseek-chat")`；本机模型 → `("", 名字)`。

    只在**前缀是已注册的服务商**时才当云端 —— 本机模型名里也可能带斜杠
    （ollama 的 `library/qwen3:14b`），不能一见到斜杠就往云端送。
    """
    m = CLOUD_MODEL_PAT.match(model or "")
    if m:
        from llm.cloud import PROVIDERS
        if m.group(1) in PROVIDERS:
            return m.group(1), m.group(2)
    return "", model


def call_model(model: str, system: str, user: str, *, consent=None,
               timeout: int = 180) -> str:
    """问模型 —— **本机直连，云端必须带按次授权**。

    `consent=None` 且模型是云端时**直接拒绝**（在 `llm/cloud.py` 里挡着），
    不是"悄悄降级"也不是"静默拒绝"：会抛出说明"云端调用必须带按次授权"。
    """
    provider, real = split_model(model)
    if provider:
        from llm import cloud
        backend = cloud.build(provider)
        r = backend.generate(real, prompt=user, system=system,
                            timeout=timeout, consent=consent)
        return r.text

    # **本机走 ollama 的原生 /api/chat，不走 /v1。**
    # 原因（实测，不是猜）：思考型模型（qwen3.5 等）在 /v1 上**关不掉思考** ——
    # 传 think:false 会被忽略，答案全被挤进 reasoning 字段，content 是空的：
    #     /v1 + think:false → content 0 字 / reasoning 3098 字 / finish_reason=length
    #     原生 + think:false → content 63 字 / thinking 0 字 / eval_count 37 ✓
    # 后果很隐蔽：五道评测题四道"回答为空"，而裁定写成"未达门槛 1/5" ——
    # 拿空回答判质量等于没测（这是同一类假裁定的第三次）。
    msgs = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    payload = json.dumps(
        {
            "model": real,
            "messages": msgs,
            "stream": False,
            # 有思考能力的模型一律关掉：这个工具的活是"照材料答"，不需要它想，
            # 而它想的时候会把预算烧完、答案出不来。
            "think": False,
            "options": {"temperature": 0.1, "num_predict": 4096},
        }
    ).encode("utf-8")

    try:
        raw = guarded_request(
            NATIVE_CHAT_URL,
            data=payload,
            headers={"Content-Type": "application/json"},
            purpose=f"local LLM inference ({real})",
        )
    except Exception as exc:                                # noqa: BLE001
        # 老版本 ollama 或不吃 think 参数的模型 → 去掉它重试一次（不让它变成"用不了"）
        if "400" not in str(exc) and "think" not in str(exc).lower():
            raise
        body = {"model": real, "messages": msgs, "stream": False,
                "options": {"temperature": 0.1, "num_predict": 4096}}
        raw = guarded_request(NATIVE_CHAT_URL, data=json.dumps(body).encode("utf-8"),
                              headers={"Content-Type": "application/json"},
                              purpose=f"local LLM inference ({real})")

    body = json.loads(raw.decode("utf-8"))
    msg = body.get("message") or {}
    text = (msg.get("content") or "").strip()
    if not text:
        # **宁可说清楚，也不返回空字符串。** 空回答会让上游把"没测成"当成"答错了"。
        thinking = (msg.get("thinking") or "").strip()
        raise RuntimeError(
            "本机模型返回了空回答"
            + (f"（思考 {len(thinking)} 字，答案一个字没出）" if thinking else "")
            + " —— 这通常是思考模式没关掉或 token 预算不够，不是模型答不出。")
    return text


def build_context(hits: list[tuple[Chunk, float]]) -> str:
    blocks = []
    for chunk, _score in hits:
        blocks.append(f"[{chunk.chunk_id}] 来源：{chunk.cite_label()}\n{chunk.text}")
    return "\n\n---\n\n".join(blocks)


def verify_citations(answer: str, allowed_ids: set[str]) -> dict:
    """校验模型引用的编号是否都真实存在——防悬空引用。"""
    cited = set(re.findall(r"\[(S\d+)\]", answer))
    return {
        "cited": sorted(cited),
        "dangling": sorted(cited - allowed_ids),
        "grounded": not (cited - allowed_ids),
    }


def cmd_ask(args: argparse.Namespace) -> int:
    chunks = load_index()
    if not chunks:
        print("索引为空。先运行：python3 freeanalyst.py ingest <材料目录>", file=sys.stderr)
        return 1

    engine = BM25(chunks)
    hits = engine.search(args.question, top_k=args.top_k)
    if not hits:
        print("检索未命中任何片段。换个说法，或确认材料已入库。", file=sys.stderr)
        return 1

    allowed_ids = {c.chunk_id for c, _ in hits}
    user_prompt = f"【材料片段】\n{build_context(hits)}\n\n【问题】\n{args.question}"

    try:
        answer = call_local_model(args.model, SYSTEM_PROMPT, user_prompt)
    except EgressBlocked as exc:
        print(f"出网被拦截：{exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"连不上本地模型服务（{OLLAMA_URL}）。先运行 `ollama serve`。\n{exc}", file=sys.stderr)
        return 1

    check = verify_citations(answer, allowed_ids)

    print("=" * 68)
    print(f"问题：{args.question}")
    print(f"模型：{args.model}（本地） · 检索命中 {len(hits)} 个片段")
    print("=" * 68)
    print(answer.strip())
    print("-" * 68)
    print("检索到的片段（可追溯）：")
    for chunk, score in hits:
        preview = chunk.text.replace("\n", " ")[:58]
        print(f"  [{chunk.chunk_id}] {chunk.cite_label():<28} 相关度 {score:5.2f}  {preview}…")
    print("-" * 68)
    status = "通过" if check["grounded"] else f"发现悬空引用 {check['dangling']}"
    print(f"引用校验：{status}（引用 {check['cited'] or '无'}）")
    egress = audit_summary()
    external = egress["external_hosts"] or "无"
    print(f"本次出网目标：仅 127.0.0.1 · 累计外部主机：{external}")
    return 0


# ---------------------------------------------------------------- 运维

def cmd_models(args: argparse.Namespace) -> int:
    """看本机有哪些模型运行时 —— 以及选中的那个够不够格。"""
    from llm import backends, gate

    print("模型运行时（全部走 127.0.0.1，不出网）：")
    found = backends.detect()
    for name, ok, why in found:
        print(f"  {'✓' if ok else '✗'} {name:14} {why}")
    usable = [n for n, ok, _ in found if ok]
    if not usable:
        print()
        print("  一个都没在跑。**这不是错误** —— 提取和估值是纯代码算的，")
        print("  没有模型也能跑出结论，只是「读了材料回答问题」不可用。")
        # 照着做就能装好，别只丢一句"装 ollama"
        from llm import guide as _guide

        rec = _guide.recommend(_guide.detect_ram_gb())
        print()
        print("  三条路（挑一条）：")
        for i, p in enumerate(_guide.NO_MODEL_PATHS, 1):
            print(f"   {i}. {p['title']}")
            print(f"      适合：{p['for']}　代价：{p['cost']}")
        print()
        print("  装本机模型：")
        for s in _guide.INSTALL_STEPS:
            print(f"   {s}")
        print()
        print(f"  该装哪个：{rec['note']}")
        print(f"   {rec['command']}　（{rec['download']}，{rec['why']}）")
        return 0

    b = backends.pick()
    print(f"\n用 {b.name} 的模型：")
    try:
        for m in b.list_models():
            print(f"  · {m}")
    except Exception as exc:                                  # noqa: BLE001
        print(f"  列不出来：{exc}")
        return 1

    if args.gate:
        print(f"\n跑质量门槛（{args.gate}）—— 三类致命错误一个都不能犯：")
        r = gate.run_gate(args.gate)
        for line in gate.report_lines([r]):
            print(line)
        return 0 if r.passed else 1

    return 0


def cmd_reconcile(args: argparse.Namespace) -> int:
    """**把抽出来的报表跟独立来源逐项对账。**

    ```
    freeanalyst.py reconcile materials/600519 --code 600519
    freeanalyst.py reconcile /tmp/x/600519_2026-04-17_annual.pdf --code 600519 --year 2025
    ```

    只说「对不对」，**不改任何数** —— 对不上就回去修抽取，不是在这儿覆盖。

    退出码：全部一致 0；有硬错 1；连材料都没读到 2。
    """
    from financials import reconcile
    from financials.from_pdf import load_pdf_statements

    p = Path(args.path)
    if p.is_dir():
        allf = sorted(x for x in p.rglob("*")
                      if x.suffix.lower() in (".pdf", ".htm", ".html"))
        if not allf:
            print(f"✗ {p} 里没有 PDF/HTML")
            return 2
        # ⚠️ **必须按代码过滤。** 直接取第一个文件会拿错公司的年报去对账，
        # 而且不报错 —— 整页 ✗ 看着像"报表全错"，其实是比错了对象。
        mine = [x for x in allf if str(args.code) in x.name]
        if not mine:
            print(f"✗ {p} 里没有文件名含 {args.code} 的材料（共 {len(allf)} 个文件）")
            print("   要么换目录，要么直接把 PDF 路径传进来")
            return 2
        if len(mine) > 1:
            print(f"　目录里有 {len(mine)} 份带 {args.code} 的材料，用第一份：{mine[0].name}")
        p = mine[0]
    if not p.exists():
        print(f"✗ 找不到 {p}")
        return 2

    print(f"材料：{p.name}")
    st = load_pdf_statements(p)
    print(f"期间：{st.period or '（未识别）'}　单位：{st.unit or '（未识别）'}　"
          f"口径：{st.scope or '（未判定）'}")

    year = args.year
    if year is None:
        m = re.search(r"(19|20)\d{2}", str(st.period or ""))
        year = int(m.group(0)) if m else None

    rep = reconcile.compare(st, args.code, periods=args.periods, year=year)
    for line in rep.render():
        print(line)
    if not rep.clean():
        print("\n**对不上不等于我们错了，也不等于对方错了** ——"
              "先核原件那一行；口径差异（合并/母公司、含/不含受限资金）也会报不一致。")
    return 0 if rep.clean() else 1


def cmd_coverage(args: argparse.Namespace) -> int:
    """**取数覆盖矩阵** —— 把「还有多少取数问题」变成可数的数字。

    ```
    freeanalyst.py coverage /tmp/roundtrip --year 2025
    freeanalyst.py coverage materials/a.pdf materials/b.pdf --out out/cov.csv
    ```

    每份材料 × 18 个关键科目 = 一格，逐格判：一致 / 取不对 / 取不全 / 取不到 / 未验证。

    三条硬规矩：
      ① `取不到`（整张表没定位）独立于 `取不全`，一次定位失败不放大成 6 个缺
      ② `未验证`（没有独立真值）**不进通过率** —— 没有真值谈不上对错
      ③ 文件名认不出市场/代码 → 标未验证，不瞎猜

    退出码：0 通过；1 失败格超过阈值；2 没有材料。
    """
    from bench import coverage

    files: list[Path] = []
    for d in args.paths:
        p = Path(d)
        if p.is_dir():
            files += [x for x in sorted(p.rglob("*"))
                      if x.suffix.lower() in (".pdf", ".htm", ".html")]
        elif p.exists():
            files.append(p)
        else:
            print(f"✗ 找不到 {p}")
    if not files:
        print("✗ 没有可处理的材料")
        return 2

    print(f"跑覆盖矩阵：{len(files)} 份材料 × 18 科目 ……（A 股会联网取对照）")
    m = coverage.build(files, year=args.year)
    for line in coverage.render(m):
        print(line)

    out = Path(args.out) if args.out else Path("out/coverage.csv")
    coverage.write_csv(m, out)
    print(f"\n明细已写入 {out}")

    ok = coverage.gate(m, max_bad=args.max_bad)
    if not ok:
        print(f"\n✗ 失败 {len(m.failed())} 格，超过阈值 {args.max_bad}")
    return 0 if ok else 1


def cmd_audit(args: argparse.Namespace) -> int:
    summary = audit_summary()
    print(f"审计日志：{summary['path']}")
    print(f"  总调用   {summary['total']}")
    print(f"  放行     {summary['allowed']}  （应全部为回环地址）")
    print(f"  拦截     {summary['blocked']}")
    print(f"  外部主机 {summary['external_hosts'] or '无'}")
    print()
    print("注意：`拦截 0 / 外部主机 无` 才代表从未向外部发起过请求。")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    print(f"Python      {sys.version.split()[0]}")
    print(f"项目目录    {ROOT}")
    print(f"索引        {'存在' if index_path().exists() else '未建立'}"
          f"（片段数 {len(load_index())}）")
    try:
        raw = guarded_request("http://127.0.0.1:11434/api/tags", purpose="health check")
        models = [m["name"] for m in json.loads(raw.decode()).get("models", [])]
        print(f"本地模型    {len(models)} 个：{', '.join(models) or '无'}")
    except Exception as exc:  # noqa: BLE001
        print(f"本地模型    未就绪（{exc}）")
    print(f"审计条目    {audit_summary()['total']}")
    return 0


def cmd_appraise(args: argparse.Namespace) -> int:
    """**材料目录 → 估值报告，全程不碰 JSON。**

    三张表在内存里装好（PDF / Excel 走不了配置节，只能这么进来），
    事实类自动填、假设类只列成问答清单等用户给。
    """
    from intake import appraise
    return appraise(args.materials, args.answers, unit=args.unit,
                    out_dir=args.out, no_trace=args.no_trace,
                    growth_years=args.years)


def cmd_fetch(args: argparse.Namespace) -> int:
    """**按公司名称或代码，下载指定时间段的财报。**

    ```
    freeanalyst.py fetch 某白酒公司 --period 2025
    freeanalyst.py fetch 600519   --period 2025 --kind annual
    freeanalyst.py fetch 建設銀行  --filed 2025-01-01:2026-12-31
    freeanalyst.py fetch "Western Union" --kind 10-K --period 2024
    ```

    ## 两个时间参数，**不许混**

        --period 2025              报告期是 2025 年（2025-12-31 为期末）
        --filed 2025-01-01:2025-12-31  披露日在这个窗口内

    **2025 年报的披露日是 2026-04-17。** 这两个问法的答案完全相反，
    混用的后果不是报错，是静静地给你一份看起来正常的财报 ✗
    """
    from datasources import filings
    from pathlib import Path

    filed = None
    if args.filed:
        if ":" in args.filed:
            a, b = args.filed.split(":", 1)
            filed = (a.strip(), b.strip())
        else:
            print("✗ --filed 要写成 起:止，例如 2025-01-01:2025-12-31")
            return 2

    if args.period and filed:
        print("✗ --period 和 --filed 是两个不同的口径，只能给一个")
        return 2

    res = filings.find(args.query, market=args.market, kind=args.kind or "annual",
                       period=args.period, filed=filed, limit=args.limit)

    if res.identities:
        print("候选公司：")
        for i in res.identities[:5]:
            print(f"  {i.market}:{i.code:<9} {i.name[:26]:<28} {i.exchange}")
    for reason, msg in res.failures:
        mark = "⚠" if reason.name in ("AMBIGUOUS", "NO_MATCH") else "✗"
        print(f"{mark} {reason.name}: {msg}")
    if not res.filings:
        if not res.failures:
            print("✗ 没有结果")
        return 1

    print(f"\n找到 {len(res.filings)} 份：")
    for f in res.filings:
        print(f"  {f.filed_at}  [{f.kind}]  {f.title[:46]}")

    out = Path(args.out).expanduser() if args.out else \
        Path("materials") / re.sub(r"[^\w\-]", "_", str(args.query))[:24]
    got = 0
    for f in res.filings:
        try:
            p = filings.download(f, out)
            print(f"✓ {p.name}（{p.stat().st_size:,} 字节）")
            got += 1
        except Exception as exc:                       # noqa: BLE001
            print(f"✗ {f.title[:40]} → {exc}")
    if got:
        print(f"\n存到 {out}")
        for line in filings.render_sources(out):
            print(line)
    return 0 if got else 1


def cmd_ui(args: argparse.Namespace) -> int:
    """起本地网页向导。**只绑 127.0.0.1** —— 同一个 WiFi 下别的机器访问不到。"""
    import webapp
    return webapp.serve(args.materials, port=args.port,
                        open_browser=not args.no_open)


def main() -> int:
    parser = argparse.ArgumentParser(prog="freeanalyst", description="本地优先的投资尽调 agent")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_ingest = sub.add_parser("ingest", help="把材料入库到本机索引")
    p_ingest.add_argument("path", help="材料目录")
    p_ingest.set_defaults(func=cmd_ingest)

    p_ask = sub.add_parser("ask", help="基于本机索引提问")
    p_ask.add_argument("question")
    p_ask.add_argument("--model", default=DEFAULT_MODEL)
    p_ask.add_argument("--top-k", type=int, default=6)
    p_ask.set_defaults(func=cmd_ask)

    p_app = sub.add_parser("appraise", help="从材料目录跑到估值报告（不用手写 JSON）")
    p_app.add_argument("materials", help="材料目录")
    p_app.add_argument("--answers", help="填好的问答清单（不填就先出清单）")
    p_app.add_argument("--unit", default="", help="金额单位（认不出时用它声明）")
    p_app.add_argument("--out", help="配置与报告的输出目录（默认放材料目录）")
    p_app.add_argument("--years", type=int, default=5, help="预测年数（默认 5）")
    p_app.add_argument("--no-trace", action="store_true", help="不打印计算追溯")
    p_app.set_defaults(func=cmd_appraise)

    p_fetch = sub.add_parser("fetch", help="按公司名称或代码下载指定时间段的财报")
    p_fetch.add_argument("query", help="公司名称或股票代码：某白酒公司 / 600519 / 建設銀行 / AAPL")
    p_fetch.add_argument("--market", choices=["cn", "us", "hk"], default="",
                         help="市场（默认按代码形态或名称自动判）")
    p_fetch.add_argument("--kind", default="annual",
                         help="annual/interim/quarterly；美股可给 10-K 或 \"10-K 20-F\"")
    p_fetch.add_argument("--period", type=int, default=None,
                         help="**报告期**年份，例如 2025（指报告期末是 2025-12-31）")
    p_fetch.add_argument("--filed", default="",
                         help="**披露日**窗口 起:止，例如 2025-01-01:2025-12-31")
    p_fetch.add_argument("--limit", type=int, default=5, help="最多取几份（默认 5）")
    p_fetch.add_argument("--out", default="", help="存到哪（默认 materials/<公司>）")
    p_fetch.set_defaults(func=cmd_fetch)

    p_ui = sub.add_parser("ui", help="起本地网页向导（只绑 127.0.0.1）")
    p_ui.add_argument("materials", nargs="?", default=None, help="材料目录或单个文件")
    p_ui.add_argument("--port", type=int, default=8765)
    p_ui.add_argument("--no-open", action="store_true", help="不自动开浏览器")
    p_ui.set_defaults(func=cmd_ui)

    p_models = sub.add_parser("models", help="看本机有哪些模型运行时，能不能用")
    p_models.add_argument("--gate", default="",
                          help="对这个模型跑质量门槛（不跑就只列模型）")
    p_models.set_defaults(func=cmd_models)

    p_rec = sub.add_parser("reconcile",
                           help="把抽出来的报表跟独立来源逐项对账（只判定，不改数）")
    p_rec.add_argument("path", help="单个 PDF，或含 PDF 的目录")
    p_rec.add_argument("--code", required=True, help="6 位 A 股代码，例如 600519")
    p_rec.add_argument("--year", type=int, default=None, help="报告期年份（默认识别）")
    p_rec.add_argument("--periods", type=int, default=2,
                       help="取对照数据几期（默认 2：本期 + 上期，用来自动识别取错年份）")
    p_rec.set_defaults(func=cmd_reconcile)

    p_cov = sub.add_parser("coverage",
                           help="取数覆盖矩阵（材料 × 18 科目，逐格判对错）")
    p_cov.add_argument("paths", nargs="+", help="目录或文件，可给多个")
    p_cov.add_argument("--year", type=int, default=None, help="报告期年份")
    p_cov.add_argument("--out", default="", help="明细 CSV 存到哪（默认 out/coverage.csv）")
    p_cov.add_argument("--max-bad", type=int, default=0,
                       help="允许几格失败（默认 0，超过就非零退出）")
    p_cov.set_defaults(func=cmd_coverage)

    sub.add_parser("audit", help="查看出网审计").set_defaults(func=cmd_audit)
    sub.add_parser("doctor", help="环境自检").set_defaults(func=cmd_doctor)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
