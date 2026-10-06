import hashlib
import json
from pathlib import Path

FIXTURES_DIR = Path(__file__).parent / "fixtures"
CORPUS_FILE = FIXTURES_DIR / "corpus_cases.json"
MANIFEST_FILE = FIXTURES_DIR / "manifest.json"


def load_corpus():
    with open(CORPUS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def load_manifest():
    with open(MANIFEST_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def compute_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def verify_corpus_integrity() -> bool:
    manifest = load_manifest()
    expected_hash = manifest.get("corpus_cases_sha256")
    actual_hash = compute_sha256(CORPUS_FILE)
    return expected_hash == actual_hash


def run_benchmark():
    corpus = load_corpus()
    cases = corpus.get("cases", [])
    print(f"Loaded {len(cases)} cases from frozen corpus.")
    
    summary = {
        "total": len(cases),
        "by_category": {},
        "current_haos_status": {
            "temporal_filtering_active": False,  # Gap 1
            "observed_at_tracked": False,        # Gap 2
            "render_metadata_transparent": False, # Gap 3
            "dream_reopened_session_aware": False, # Gap 4
            "conflict_surfacing_to_owner": False  # Gap 5
        }
    }
    
    for c in cases:
        cat = c.get("category")
        summary["by_category"][cat] = summary["by_category"].get(cat, 0) + 1
        
    print(f"Summary: {summary}")
    return summary


if __name__ == "__main__":
    import sys
    if not verify_corpus_integrity():
        print("Corpus integrity check FAILED!", file=sys.stderr)
        sys.exit(1)
    print("Corpus integrity check PASSED.")
    run_benchmark()
