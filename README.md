# FreeAnalyst

**Open source. Air gapped. Off at six.**

[中文版 README](README.zh-CN.md) · [Beta testing guide](docs/Beta-Testing.md)

A due-diligence agent for private-market investing that runs entirely on your own
machine. Your CIMs, meeting notes, valuation logic and term sheets never leave it.

---

## What v0.2.0 actually does

For **A-share annual reports (text-layer PDFs, non-financial companies)**,
FreeAnalyst extracts the three financial statements and maps **18 key line items**,
then assigns **a confidence level to every single number**:

- **High confidence** — reconciled against an independent data source (listed
  companies), or internally consistent evidence (private documents). Safe to use.
- **Low confidence** — consolidated vs. parent-company evidence conflicts, or the
  external source disagrees. The tool shows candidate values and page numbers so
  you can verify by hand.
- **Rejected** — the row was not extracted, or the document is outside the
  supported scope. No number is produced.

**The point of this release is not "parses more documents". It is "what it claims,
you can trust; what it cannot do, it tells you".**

Measured on 33 filings from 20 companies (ChiNext, STAR Market, Shanghai and
Shenzhen main boards, Beijing Stock Exchange):

```
Listed companies   high-confidence coverage 91%   accuracy 100%
Private documents  high-confidence coverage 40%   accuracy 99.1%
```

For private documents there is no external source to reconcile against, so the
tool commits to only 40% of the fields and hands the remaining 60% back to you as
low-confidence or rejected. **That is the design, not a malfunction.**

## Taiwan listed companies (via MOPS)

```bash
.venv/bin/python freeanalyst.py fetch 2330 --market tw --period 2025
```

Taiwan filings come from **MOPS (Market Observation Post System)** as structured
HTML with **bilingual Chinese/English standard captions**, so account mapping is a
lookup rather than a guess. **16/16 applicable items** extracted for TSMC (2330)
and MediaTek (2454); values are in NT$ thousands.

Taiwan cash flow statements use the **indirect method**, so "cash received from
sales" and "cash paid for goods" do not exist there — those two items are marked
**not applicable**, not failures. The same applies to HK/US/SG filings.

## Hong Kong — traditional-Chinese annual reports

Verified against Tencent (00700) FY2025: **15/16 applicable items**, with
articulation intact (current assets 595,460 + non-current 1,443,526 = total
assets 2,038,986; revenue 751,766; net profit 229,801; cash equals closing cash).

Two HK-specific behaviours are handled:

- **Subtotal and revenue-total rows often have no label at all** (the parser sees
  `〔此行没有标签〕`), so they are located by position: revenue is the row above
  cost of sales, and subtotals are the last unlabelled row of each section.
- **Five-year financial-summary pages are excluded** from statement detection, or
  older years get picked up as the current period.

HK issuers also filing with the SEC (e.g. Alibaba 09988/BABA) go through the
20-F + official XBRL route instead (14/16 with official values).

Xiaomi (01810) is a different problem: its text layer is `(cid:xxxx)` encoded
(font subset with no Unicode map), so it needs OCR, not the table parser.

## United States (SEC EDGAR) — 16/16 via official XBRL

```bash
.venv/bin/python freeanalyst.py fetch AAPL --market us --kind 10-K --period 2025
```

Two independent pieces, both verified:

- **Download**: SEC EDGAR 10-K/10-Q retrieval. (Fixed a bug where ticker
  resolution returned identities without a CIK, producing `CIK0000000000.json`
  404s; the CIK is now looked up from the SEC ticker table when missing.)
- **Extraction**: values come from the SEC's **official XBRL `companyconcept`
  endpoint**, not from parsing HTML. Apple FY2025: **16/16 items** — revenue
  $416,161M, net income $112,010M, total assets $359,241M, cash $35,934M.

The same official data doubles as an independent source for reconciliation.

## Singapore (SGX) — validated on real filings

Verified against real SGX annual reports supplied by a user:

- **9R Limited FY2026 and FY2023: 15/16 applicable items**, including the correct
  loss figure (-2,597,300) and matching cash/cash-equivalents across statements.
- One other issuer: 10/16 (report denominated in S$'000).
- **CRCT (a REIT): not yet supported** — its statement layout is not extracted.

Four real bugs were found and fixed by this validation: page/note numbers being
read as amounts, current-period values packed into the label cell, matches taken
from MD&A or notes instead of the primary statements, and missing vocabulary
(`Revenue`, `Trade receivables`, `Loss for the year`, `at end of`).

**Operating profit is not applicable for many IFRS issuers** (no subtotal row).
Automatic download from SGX is not wired up yet — place the PDF in a folder and
run `appraise` on it.

## What it refuses to parse (file-level rejection, with a reason)

- Scanned / image-only PDFs (OCR path not yet reliable)
- Financial institutions: banks, insurers, brokers (different chart of accounts)
- English-language annual reports (statement location usually fails)
- T-shaped two-column layouts, encrypted PDFs
- PDFs whose text layer is `(cid:xxxx)` encoded (e.g. Xiaomi 01810) — needs OCR

**A file-level rejection also fires when fewer than half of the 18 key items can
be extracted** — the tool does not need to recognise *why* a document is hard in
order to say "this one cannot be parsed, please handle it manually".

## Confidence is decided by evidence, not by the model

- **Numbers are computed by code, never generated by an LLM.** The model may touch
  layout and wording; it may not decide what a number is.
- **External reconciliation wins over internal heuristics.** If an independent
  source agrees, that is the strongest evidence available.
- **Without an external source, internal evidence must agree.** Examples:
  cash at period end vs. cash on the balance sheet must be of the same magnitude;
  a "cash" value of 5.10 is a ratio or a note reference, not an amount.
- **Units are normalised before reconciliation.** A report denominated in
  thousands is converted to yuan first — getting this wrong makes every value off
  by 1000x while looking like a parsing failure.

## Privacy model

Two promises, two ways to verify them:

- **All code is open source**, so the behaviour can be audited.
- **Confidential material never leaves the machine.** There is a single network
  egress point (`guard.py`), which by default allows only `localhost`. Sending
  anything to a cloud model requires an explicit, per-call `CloudConsent` bound to
  host + purpose + content description, and every call is written to an audit log.

There is deliberately **no global `ALLOW_CLOUD` switch**: a switch you flip once
stays on forever, and the promise becomes meaningless.

The target company's name is itself confidential, so public-data lookups use
industry codes rather than company names.

## Install

Requires **Python 3.12**. The only third-party dependency is `pdfplumber`;
everything else is the standard library.

```bash
git clone https://github.com/NCV-Developer/freeanalyst.git
cd freeanalyst
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python freeanalyst.py doctor        # environment self-check
```

Verified on a clean clone: `doctor` prints your Python version, available local
models and the audit log location.

## First run

Put one or more annual-report PDFs in a folder, then:

```bash
.venv/bin/python freeanalyst.py appraise /path/to/folder --out /tmp/out
```

**`appraise` takes a directory, not a single PDF.** It prints the statement
extraction result (rows mapped per statement, unit, consolidation scope) and
writes a question list to `/tmp/out/估值问答.txt`.

Fill in the assumptions it asks for (it never guesses them for you), then run it
again with `--answers`:

```bash
.venv/bin/python freeanalyst.py appraise /path/to/folder --out /tmp/out \
    --answers /tmp/out/估值问答.txt
```

Extraction and reconciliation work without any model download. Fetching market
comparables requires network access to public data sources.

If you hit `PdfDependencyMissing`, check the interpreter version first.

## Verification

```bash
python3.12 -m unittest discover -s tests     # 1479 tests
python3.12 -m bench.accept materials/corpus  # three-tier acceptance report
```

The acceptance report prints, per segment: high-confidence coverage,
high-confidence accuracy, and rejection counts. **Coverage and accuracy are always
reported together** — accuracy alone can be gamed by rejecting everything.

## Known limitations (please do not re-report)

- One Vanke (000002) operating cash flow figure has the wrong sign; internal
  evidence did not catch it. On the fix list.
- Gree (000651), Sinopec (600028), CITIC Securities (600030), Ping An (601318):
  file-level rejection due to insufficient extraction coverage.
- Beijing Stock Exchange directory names use legacy codes while file names use
  new ones; code resolution is unified on file names.

## What is next

- Broader internal-evidence coverage for private documents (without loosening
  the confidence rules)
- US filing ingestion
- Financial-sector account groups

**If you find a number marked high confidence that is actually wrong, that is the
single most valuable thing you can report.**
