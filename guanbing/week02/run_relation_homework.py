import json
import os
import sys
from pathlib import Path

FILE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = FILE_DIR.parent
if str(FILE_DIR) not in sys.path:
    sys.path.insert(0, str(FILE_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from relation_extractor import (  # noqa: E402
    extract_relation_graph,
    generate_relation_corpus,
    load_env_if_needed,
    print_report,
    run_corpus,
)


def main() -> int:
    loaded_env = load_env_if_needed()
    print("Loaded .env from:", loaded_env)

    provider = os.getenv("LLM_PROVIDER", "deepseek").lower().strip()

    n_corpus = 5
    model_override = os.getenv("RELATION_MODEL") or None

    try:
        corpus = generate_relation_corpus(n=n_corpus, provider=provider, model_override=model_override)
    except Exception as exc:  # noqa: BLE001
        print(f"[ERROR] generate_relation_corpus raised unexpectedly (E1 handled inside fallback): {exc}")
        return 0

    print(f"\nGenerated corpus ({len(corpus)} sentences):")
    for i, sentence in enumerate(corpus, start=1):
        print(f"  [{i}] {sentence}")

    # Also run the exact sample from homework
    sample_text = "小明喜欢小姚，但是小姚喜欢小王。"
    sample_graph = extract_relation_graph(sample_text, provider=provider, model_override=model_override)
    print("\nHomework sample input:", sample_text)
    print("Homework sample output (A1 array):")
    print(json.dumps(sample_graph, ensure_ascii=False, indent=2))

    report = run_corpus(corpus, provider=provider, model_override=model_override)
    print_report(report)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001
        # E1: never crash the user terminal unexpectedly
        print(f"[FATAL ERROR - E1 SAFE CATCH] {type(e).__name__}: {e}")
        sys.exit(0)
