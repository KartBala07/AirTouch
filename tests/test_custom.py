"""Custom gesture rules: parse, persist, dispatch."""
import json
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from airtouch import custom  # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    print(("PASS  " if ok else "FAIL  ") + name + ("  " + str(detail) if detail else ""))
    if not ok:
        FAILS.append(name)


class FakeActions:
    def __init__(self):
        self.keys = []

    def key_chord(self, code, flags=0):
        self.keys.append((code, flags))
        return True


tmp = pathlib.Path(tempfile.mkdtemp()) / "gestures.json"
acts = FakeActions()
rules = custom.CustomGestures(path=tmp, actions=acts).load()
check("missing file loads empty", rules.rules == [], rules.rules)

rules.add("pinch_double", "key", "cmd+shift+4")
check("rule persisted to disk", tmp.exists() and json.loads(tmp.read_text())["rules"],
      tmp.read_text()[:60])
reloaded = custom.CustomGestures(path=tmp, actions=acts).load()
check("rule survives a reload", len(reloaded.rules) == 1, len(reloaded.rules))

fired = reloaded.fire("pinch_double")
check("matching trigger fires", len(fired) == 1, fired)
check("cmd+shift+4 parsed", acts.keys == [(21, (1 << 20) | (1 << 17))], acts.keys)

check("other triggers stay quiet", reloaded.fire("fist") == [])
reloaded.rules[0].enabled = False
check("disabled rule does not fire", reloaded.fire("pinch_double") == [])
reloaded.rules[0].enabled = True

reloaded.add("fist", "shell", "say hi")
reloaded.remove(0)
check("remove drops the first rule", len(reloaded.rules) == 1, len(reloaded.rules))
check("removal is written through", len(json.loads(tmp.read_text())["rules"]) == 1)

bad = tmp.with_name("bad.json")
bad.write_text('{"rules": [{"trigger": "nonsense", "action": "key", "value": "x"},'
               ' {"trigger": "fist", "action": "launch", "value": "y"}]}')
check("invalid triggers/actions are ignored",
      custom.CustomGestures(path=bad, actions=acts).load().rules == [])

check("empty value is refused, not crashed",
      "empty value" in reloaded.run(custom.Rule("fist", "key", "  ")))
check("bad key name reports instead of throwing",
      "could not parse" in reloaded.run(custom.Rule("fist", "key", "cmd+nosuchkey")))

print()
if FAILS:
    print(f"FAILED: {', '.join(FAILS)}")
    raise SystemExit(1)
print("all custom-gesture checks passed")
