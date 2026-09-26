import json
import sys
import io
from pathlib import Path

# Ensure UTF-8 output on Windows console
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Add vera_bot to path
sys.path.insert(0, str(Path(__file__).parent / "vera_bot"))

from fact_extractor import extract_facts
from composer import compose_message

def main():
    with open("expanded/test_pairs.json", "r", encoding="utf-8") as f:
        test_pairs = json.load(f)["pairs"]

    print(f"Loaded {len(test_pairs)} test pairs.")

    categories = {}
    for p in Path("expanded/categories").glob("*.json"):
        with open(p, "r", encoding="utf-8") as f:
            c = json.load(f)
            categories[c["slug"]] = c

    merchants = {}
    for p in Path("expanded/merchants").glob("*.json"):
        with open(p, "r", encoding="utf-8") as f:
            m = json.load(f)
            merchants[m["merchant_id"]] = m

    customers = {}
    for p in Path("expanded/customers").glob("*.json"):
        with open(p, "r", encoding="utf-8") as f:
            cu = json.load(f)
            customers[cu["customer_id"]] = cu

    triggers = {}
    for p in Path("expanded/triggers").glob("*.json"):
        with open(p, "r", encoding="utf-8") as f:
            t = json.load(f)
            triggers[t["id"]] = t

    print(f"Loaded {len(categories)} categories, {len(merchants)} merchants, {len(customers)} customers, {len(triggers)} triggers.")

    now_str = "2026-04-29T10:00:00Z"
    failed = 0
    for pair in test_pairs:
        tid = pair["trigger_id"]
        mid = pair["merchant_id"]
        cid = pair.get("customer_id")
        
        t = triggers.get(tid)
        m = merchants.get(mid)
        c = customers.get(cid) if cid else None
        cat = categories.get(m.get("category_slug", "")) if m else None
        
        try:
            brief = extract_facts(t, m, cat, c, now_str, 5.0)
            res = compose_message(brief, m, cat, c)
            body = res.get("body", "")
            cta = res.get("cta", "")
            sup = res.get("suppression_key", "")
            rat = res.get("rationale", "")
            assert len(body) > 10, f"Body too short: {body}"
            assert len(rat) > 10, f"Rationale too short: {rat}"
            assert res.get("send_as") in ["vera", "merchant_on_behalf"], f"Invalid send_as: {res.get('send_as')}"
            assert cta in ["binary_yes_no", "single_choice", "multi_choice", "open_ended", "none"], f"Invalid cta: {cta}"
            print(f"[{pair['test_id']}] PASS | kind={brief.trigger_kind} | as={res.get('send_as')} | cta={cta} | {body[:70]}...")
        except Exception as e:
            print(f"[{pair['test_id']}] FAILED: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print(f"\n==========================================")
    print(f"RESULT: {len(test_pairs) - failed}/{len(test_pairs)} test pairs passed.")
    print(f"==========================================")
    if failed > 0:
        sys.exit(1)

if __name__ == "__main__":
    main()
