import warnings; warnings.filterwarnings("ignore")
from collections import Counter
from fastapi.testclient import TestClient
import App

with TestClient(App.app) as c:
    resp = c.get("/alerts?limit=100")
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    seen, sample = set(), []
    for a in items:
        if a["rule_id"] not in seen:
            seen.add(a["rule_id"])
            sample.append(a)
    print(f"Explaining {len(sample)} alerts (one per rule)...\n")
    rows = []
    for a in sample:
        ex = c.post(f"/alerts/{a['id']}/explain").json()["explanation"]
        g = ex["grounding"]
        reasons = [v for r in g.get("rejected_answers", []) for v in r["violations"]]
        rows.append({
            "rule": a["rule_id"], 
            "source": ex["source"], 
            "model": ex["model"],
            "attempts": g.get("attempts"), 
            "error": g.get("llm_error"), 
            "reasons": reasons
        })
        print(rows[-1], flush=True)
    print("\nsource:", dict(Counter(r["source"] for r in rows)), "| n =", len(rows))
    print("models:", dict(Counter(r["model"] for r in rows)))
    print("rejection reasons:", dict(Counter(x for r in rows for x in r["reasons"])))
