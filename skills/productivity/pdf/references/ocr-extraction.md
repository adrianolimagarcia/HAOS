# OCR & Document Text Extraction (merged from the ocr-and-documents skill)

Scripts referenced below live in this skill's scripts/ directory.
# PDF & Document Extraction

For DOCX: see the `docx` skill (create/edit) or use `python-docx` for structured reads.
For PPTX: see the `powerpoint` skill (full create/read/edit support).
For PDF manipulation (merge, split, forms, watermarks, creation): see the `pdf` skill.
This skill covers **text extraction from PDFs and scanned documents**.

> **Coming from a `read_file` EXTRACTION COVERAGE WARNING?** `read_file` auto-converts local PDFs but reads the text layer only; the warning footer lists the pages that yielded no text (scanned images). For a handful of pages, render + vision is fastest: `pdftoppm -jpeg -r 150 -f N -l N file.pdf /tmp/page` then `vision_analyze` each image. For bulk OCR of many pages, use marker-pdf below (Step 2).

## Step 1: Remote URL Available?

If the document has a URL, **always try `web_extract` first**:

```
web_extract(urls=["https://arxiv.org/pdf/2402.03300"])
web_extract(urls=["https://example.com/report.pdf"])
```

This handles PDF-to-markdown conversion via Firecrawl with no local dependencies.

Only use local extraction when: the file is local, web_extract fails, or you need batch processing.

## Step 2: Choose Local Extractor

| Feature | pymupdf (+pymupdf4llm) | marker-pdf (~3-5GB) |
|---------|-----------------|---------------------|
| **Text-based PDF** | ✅ | ✅ |
| **Scanned PDF (OCR)** | ❌ | ✅ (90+ languages) |
| **Tables** | ✅ (basic) | ✅ (high accuracy) |
| **Equations / LaTeX** | ❌ | ✅ |
| **Code blocks** | ❌ | ✅ |
| **Forms** | ❌ | ✅ |
| **Headers/footers removal** | ❌ | ✅ |
| **Reading order detection** | ❌ | ✅ |
| **Images extraction** | ✅ (embedded) | ✅ (with context) |
| **Images → text (OCR)** | ❌ | ✅ |
| **EPUB** | ✅ | ✅ |
| **Markdown output** | ✅ (via pymupdf4llm) | ✅ (native, higher quality) |
| **Install size** | ~60MB (pymupdf alone); ~116MB with pymupdf4llm (pymupdf_layout ships 49MB of ONNX layout models); ~243MB in a bare venv that also lacks onnxruntime/numpy | ~3-5GB (PyTorch + models) |
| **Speed** | Instant | ~1-14s/page (CPU), ~0.2s/page (GPU) |

**Decision**: Use pymupdf unless you need OCR, equations, forms, or complex layout analysis.

## Measured comparison vs docling (2026-09-13) — pymupdf4llm is the default here

A `docling-serve` container was evaluated head-to-head against `pymupdf4llm` on 4 text-based PDFs under 1MB (BERT 760K, ResNet 804K, seq2seq 436K, IRS Form W-9 140K). Results, so do not re-litigate this by guessing:

- **No single winner.** `pymupdf4llm` wins on numeric fidelity and speed; docling wins on forms/non-tabular layout.
- **Numeric fidelity**: docling normalised table cells (`35.0` -> `35`, `81.0` -> `81`) where `pymupdf4llm` preserved the source decimals exactly. Verify against `page.get_text("words")` before trusting either.
- **Forms**: on the W-9, `pymupdf4llm` forced the whole form into a 2-column table and split sentences mid-cell (`...enter the owner's name on line` | `1, and enter the business/disregarded`) and glued words (`Beforeyou`, `Forguidance`). docling produced clean linear text with `- [ ]` checkbox items. For form-like documents, do NOT rely on `pymupdf4llm`.
- **Speed**: docling ~1.5x slower on 3 of 4.
- **Ties**: both clean on headers/footers and correct on 2-column reading order. **Neither produced LaTeX.**
- **docling was removed** because on a 4GB GPU it pinned ~3.5GiB of VRAM with the default pipeline loaded and its formula path (`do_formula_enrichment`) could not run at all: `CodeFormulaV2` was missing from the image cache, and once downloaded the layout backbone OOM'd. `pymupdf4llm` needs no GPU.
- **Pitfall (measured)**: counting `arXiv:` matches as "header/footer noise" is invalid — those are bibliography references, not running headers. Measure running headers by literal repeated strings.
- **Pitfall (docling, if ever revisited)**: the API exposes no layout-model parameter; and the image's own `DOCLING_SERVE_ARTIFACTS_PATH` overrides any host volume mount, so downloaded models land in the container's writable layer and are lost on recreate.

If the user needs marker capabilities but the system lacks ~5GB free disk:
> "This document needs OCR/advanced extraction (marker-pdf), which requires ~5GB for PyTorch and models. Your system has [X]GB free. Options: free up space, provide a URL so I can use web_extract, or I can try pymupdf which works for text-based PDFs but not scanned documents or equations."

---

## pymupdf (lightweight)

```bash
pip install pymupdf pymupdf4llm
```

**Via helper script**:
```bash
python scripts/extract_pymupdf.py document.pdf              # Plain text
python scripts/extract_pymupdf.py document.pdf --markdown    # Markdown
python scripts/extract_pymupdf.py document.pdf --tables      # Tables
python scripts/extract_pymupdf.py document.pdf --images out/ # Extract images
python scripts/extract_pymupdf.py document.pdf --metadata    # Title, author, pages
python scripts/extract_pymupdf.py document.pdf --pages 0-4   # Specific pages
```

**Inline**:
```bash
python -c "
import pymupdf
doc = pymupdf.open('document.pdf')
for page in doc:
    print(page.get_text())
"
```

---

## marker-pdf (high-quality OCR)

```bash
# Check disk space first
python scripts/extract_marker.py --check

pip install marker-pdf
```

**Via helper script**:
```bash
python scripts/extract_marker.py document.pdf                # Markdown
python scripts/extract_marker.py document.pdf --json         # JSON with metadata
python scripts/extract_marker.py document.pdf --output_dir out/  # Save images
python scripts/extract_marker.py scanned.pdf                 # Scanned PDF (OCR)
python scripts/extract_marker.py document.pdf --use_llm      # LLM-boosted accuracy
```

**CLI** (installed with marker-pdf):
```bash
marker_single document.pdf --output_dir ./output
marker /path/to/folder --workers 4    # Batch
```

---

## Arxiv Papers

```
# Abstract only (fast)
web_extract(urls=["https://arxiv.org/abs/2402.03300"])

# Full paper
web_extract(urls=["https://arxiv.org/pdf/2402.03300"])

# Search
web_search(query="arxiv GRPO reinforcement learning 2026")
```

## Split, Merge & Search

pymupdf handles these natively — use `execute_code` or inline Python:

```python
# Split: extract pages 1-5 to a new PDF
import pymupdf
doc = pymupdf.open("report.pdf")
new = pymupdf.open()
for i in range(5):
    new.insert_pdf(doc, from_page=i, to_page=i)
new.save("pages_1-5.pdf")
```

```python
# Merge multiple PDFs
import pymupdf
result = pymupdf.open()
for path in ["a.pdf", "b.pdf", "c.pdf"]:
    result.insert_pdf(pymupdf.open(path))
result.save("merged.pdf")
```

```python
# Search for text across all pages
import pymupdf
doc = pymupdf.open("report.pdf")
for i, page in enumerate(doc):
    results = page.search_for("revenue")
    if results:
        print(f"Page {i+1}: {len(results)} match(es)")
        print(page.get_text("text"))
```

No extra dependencies needed — pymupdf covers split, merge, search, and text extraction in one package.

---

## Notes

- `web_extract` is always first choice for URLs
- pymupdf is the safe default — instant, no models, works everywhere
- marker-pdf is for OCR, scanned docs, equations, complex layouts — install only when needed
- Both helper scripts accept `--help` for full usage
- marker-pdf downloads ~2.5GB of models to `~/.cache/huggingface/` on first use
- For Word docs: `pip install python-docx` (better than OCR — parses actual structure)
- For PowerPoint: see the `powerpoint` skill (uses python-pptx)
