"""User-defined gestures: bind something AirTouch already detects to an action.

A rule is deliberately dumb - trigger, action, value - so the file can be edited
by hand as easily as from the window:

    {"trigger": "pinch_double", "action": "key", "value": "cmd+shift+4"}

Triggers are the gestures the engine already recognises; actions are things
macOS can actually do for us. Nothing here invents new tracking.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

TRIGGERS = [
    ("pinch", "Quick pinch"),
    ("pinch_double", "Double pinch"),
    ("pinch_hold", "Pinch held"),
    ("fist", "Fist held"),
    ("palm_up", "Palm swipe up"),
    ("palm_left", "Palm swipe left"),
    ("palm_right", "Palm swipe right"),
    ("two_finger", "Two-finger scroll"),
]

ACTIONS = [
    ("key", "Keystroke"),
    ("app", "Open app"),
    ("url", "Open URL"),
    ("shell", "Shell command"),
]

# virtual key codes: enough of them that chords feel natural
KEYS: Dict[str, int] = {
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9,
    "b": 11, "q": 12, "w": 13, "e": 14, "r": 15, "y": 16, "t": 17, "1": 18, "2": 19,
    "3": 20, "4": 21, "5": 23, "6": 22, "7": 26, "8": 28, "9": 25, "0": 29,
    "o": 31, "u": 32, "i": 34, "p": 35, "l": 37, "j": 38, "k": 40, "n": 45, "m": 46,
    "return": 36, "tab": 48, "space": 49, "delete": 51, "escape": 53,
    "left": 123, "right": 124, "down": 125, "up": 126,
    "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96, "f6": 97,
    "f7": 98, "f8": 100, "f9": 101, "f10": 109, "f11": 103, "f12": 111,
    "-": 27, "=": 24, "[": 33, "]": 30, ";": 41, "'": 39, ",": 43, ".": 47, "/": 44,
    "`": 50, "\\": 42,
}

MOD_CMD = 1 << 20
MOD_SHIFT = 1 << 17
MOD_OPT = 1 << 19
MOD_CTRL = 1 << 18


def send_chord(actions, text: str) -> bool:
    """Post something like 'cmd+shift+4' or 'ctrl+alt+delete'."""
    parts = [p.strip().lower() for p in text.replace(" ", "").split("+") if p.strip()]
    if not parts:
        return False
    flags = 0
    key: Optional[str] = None
    for part in parts:
        if part in ("cmd", "command", "meta"):
            flags |= MOD_CMD
        elif part == "shift":
            flags |= MOD_SHIFT
        elif part in ("opt", "alt", "option"):
            flags |= MOD_OPT
        elif part in ("ctrl", "control"):
            flags |= MOD_CTRL
        else:
            key = part
    if key is None or key not in KEYS:
        return False
    return bool(actions.key_chord(KEYS[key], flags))


@dataclass
class Rule:
    trigger: str = "pinch_double"
    action: str = "key"
    value: str = ""
    enabled: bool = True

    @property
    def label(self) -> str:
        names = dict(TRIGGERS)
        acts = dict(ACTIONS)
        return f"{names.get(self.trigger, self.trigger)} → {acts.get(self.action, self.action)}: {self.value}"


@dataclass
class CustomGestures:
    """Rules on disk plus the code that runs them."""

    path: Path
    actions: Any = None
    rules: List[Rule] = field(default_factory=list)
    log: List[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    def load(self) -> "CustomGestures":
        try:
            raw = json.loads(Path(self.path).read_text())
        except FileNotFoundError:
            self.rules = []
            return self
        except Exception as exc:
            print(f"[airtouch] custom gestures unreadable: {exc}", file=sys.stderr)
            self.rules = []
            return self
        items = raw.get("rules", raw) if isinstance(raw, dict) else raw
        out: List[Rule] = []
        for item in items or []:
            if not isinstance(item, dict):
                continue
            trigger = str(item.get("trigger", "")).strip()
            action = str(item.get("action", "key")).strip()
            if trigger not in dict(TRIGGERS) or action not in dict(ACTIONS):
                continue
            out.append(
                Rule(
                    trigger=trigger,
                    action=action,
                    value=str(item.get("value", "")),
                    enabled=bool(item.get("enabled", True)),
                )
            )
        self.rules = out
        return self

    def save(self) -> None:
        path = Path(self.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"rules": [asdict(r) for r in self.rules]}, indent=2))
        tmp.replace(path)

    # ------------------------------------------------------------------
    def add(self, trigger: str, action: str, value: str) -> Rule:
        rule = Rule(trigger=trigger, action=action, value=value.strip())
        self.rules.append(rule)
        self.save()
        return rule

    def remove(self, index: int) -> None:
        if 0 <= index < len(self.rules):
            del self.rules[index]
            self.save()

    def fire(self, trigger: str) -> List[str]:
        """Run every enabled rule bound to this trigger. Returns log lines."""
        done: List[str] = []
        for rule in list(self.rules):
            if not rule.enabled or rule.trigger != trigger:
                continue
            outcome = self.run(rule)
            done.append(outcome)
            self.log.append(outcome)
            del self.log[:-20]
        return done

    def run(self, rule: Rule) -> str:
        value = rule.value.strip()
        if not value:
            return f"{rule.trigger}: empty value, skipped"
        try:
            if rule.action == "key":
                ok = send_chord(self.actions, value)
                return f"{rule.trigger} → key {value}" + ("" if ok else " (could not parse)")
            if rule.action == "app":
                subprocess.Popen(["open", "-a", value], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return f"{rule.trigger} → open {value}"
            if rule.action == "url":
                subprocess.Popen(["open", value], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return f"{rule.trigger} → url {value}"
            if rule.action == "shell":
                subprocess.Popen(
                    ["/bin/sh", "-c", value],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                return f"{rule.trigger} → shell {value}"
        except Exception as exc:
            return f"{rule.trigger} failed: {exc}"
        return f"{rule.trigger}: unknown action"
