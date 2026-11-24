# FreeAnalyst Beta Testing Guide (v0.2.0, 2026-11-22)

[Chinese version](beta-testing-zh.md)

**Please read this before you start: what this build can and cannot do.**

---

## 1. What it does

For **A-share annual reports (text-layer PDF, non-financial companies)**, it
extracts 18 key line items from the three financial statements and labels every
number with a confidence level.

- **High confidence** — reconciled with an independent source (listed companies),
  or internally consistent (private documents). You can use it directly.
- **Low confidence** — consolidated/parent evidence conflicts, or the external
  source disagrees. **Verify by hand**; candidate values and page numbers are given.
- **Rejected** — not extracted, or the document is out of scope. Do not use any
  number it might show; handle the document manually.

Measured on 33 filings from 20 companies:

```
Listed companies   high-confidence coverage 91%   accuracy 100%
Private documents  high-confidence coverage 40%   accuracy 99.1%
```

With private documents there is no external source to reconcile against, so only
40% of fields get a confident answer and the rest are explicitly handed back to
you. **This is intentional.**

## 2. Do not test these (they will be rejected at file level)

- Scanned / image-only PDFs
- Banks, insurers, brokers
- US 10-K filings
- Hong Kong filings
- English-language annual reports
- T-shaped two-column layouts, encrypted PDFs

**These produce a clear "this document cannot be parsed" message, not a fabricated
number.**

## 3. Install and first run (verified on a clean clone)

```bash
git clone https://github.com/NCV-Developer/freeanalyst.git
cd freeanalyst
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt     # pdfplumber is the only dependency
.venv/bin/python freeanalyst.py doctor        # environment self-check
```

Put one or more annual-report PDFs in a folder, then:

```bash
.venv/bin/python freeanalyst.py appraise /path/to/folder --out /tmp/out
```

**`appraise` takes a directory, not a single PDF.** It prints rows mapped per
statement, unit and consolidation scope, and writes a question list to
`/tmp/out/估值问答.txt`. Fill in the assumptions (it never guesses them), then:

```bash
.venv/bin/python freeanalyst.py appraise /path/to/folder --out /tmp/out \
    --answers /tmp/out/估值问答.txt
```

Extraction and reconciliation need **no model download**; market comparables need
network access.

Known environment trap: if you see `PdfDependencyMissing`, check your interpreter
version before anything else.

## 4. The feedback we actually want

This is **not** "please ask us to support more document types".

1. **Are rejection reasons understandable?** When it says "this document cannot be
   parsed", do you know what to do next?
2. **Are low-confidence hints useful?** Do the candidate values and page numbers
   let you verify quickly?
3. **Did installation go smoothly?** Where did it break?
4. **Any number marked high confidence that is actually wrong?** — This is the
   most important one. The design goal is "what it commits to must be right";
   please report every single case.

## 5. Known issues (no need to report again)

- Some figures can have the wrong sign (typically consolidated vs. parent-company
  scope confusion); internal evidence does not always catch it.
- Some issuers are rejected at file level for insufficient extraction coverage.
  This is intended behaviour, not a malfunction.

---

**In one sentence: the value of this build is not "it parses everything", but
"what it says it can do, you can trust; what it cannot, it tells you".**
