import warnings; warnings.filterwarnings("ignore")
from fastapi.testclient import TestClient
import App, ai_explainer as ax

orig = ax.call_ollama
def spy(messages):
    raw = orig(messages)
    print("MODEL:", ax.OLLAMA_MODEL)
    print("RAW  :", raw, "\n")
    return raw
ax.call_ollama = spy

with TestClient(App.app) as c:
    r = c.post("/alerts/35/explain").json()["explanation"]
print("source:", r["source"], "| model:", r["model"])
print(r["grounding"])