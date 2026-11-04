import ai_explainer as ax

pkg = {"alert": {"first_utc": "2026-11-02 12:45", "last_utc": "2026-11-02 13:30"}, "facts": {"max_value": 33.87},
       "events": [{"ref": f"E{i}", "time_utc": "12:45", "value": 25.83} for i in (1, 2, 3)]}

assert ax.check_text("Peak 33.87 [E1].", pkg) == []
assert ax.check_text("Peak 33.87 [E1][E3].", pkg) == []
assert ax.check_text("Peak 33.87 [E1, E3].", pkg) == []
assert ax.check_text("Peak 33.87 [E1-E3].", pkg) == []
assert "unknown citation [E4]" in ax.check_text("Peak 33.87 [E1-E4].", pkg)
assert "unknown citation [E99]" in ax.check_text("Peak 33.87 [E99].", pkg)
assert "malformed citation [C1]" in ax.check_text("Peak 33.87 [E1] [C1].", pkg)
assert "no event citation" in ax.check_text("Peak 33.87.", pkg)
assert any("number 41.7" in v for v in ax.check_text("It hit 41.7 [E1].", pkg))
assert any("time 03:17" in v for v in ax.check_text("At 03:17 [E1].", pkg))
t, n = ax.normalize_summary("Held open [E1]. May be obstructed [C1].")
assert t == "Held open [E1]. May be obstructed." and n
t, n = ax.normalize_summary("Forced open (E1) at 12:45.")
assert t == "Forced open [E1] at 12:45." and n
t, _ = ax.normalize_summary("It was forced [C1].")
assert "no event citation" in ax.check_text(t, pkg)
t, _ = ax.normalize_summary("Peak 33.87 [E99].")
assert "unknown citation [E99]" in ax.check_text(t, pkg)
print("ok")