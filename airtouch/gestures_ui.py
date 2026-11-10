"""The custom-gesture editor window.

A list of rules, each a row you can switch off or delete, plus one line of
controls to add a new one. Rebuilt from the model on every change, so the file
and the window can never disagree.
"""

from __future__ import annotations

from typing import List

import objc
from AppKit import (
    NSBackingStoreBuffered,
    NSButton,
    NSColor,
    NSFont,
    NSMakeRect,
    NSObject,
    NSPopUpButton,
    NSSwitchButton,
    NSTextField,
    NSView,
    NSWindow,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskTitled,
)

from . import custom

WIDTH, HEIGHT = 640, 460
ROW_H = 30


def _label(text: str, x: float, y: float, w: float, size: float = 12.5,
           secondary: bool = False, bold: bool = False) -> NSTextField:
    field = NSTextField.alloc().initWithFrame_(NSMakeRect(x, HEIGHT - y, w, 18))
    field.setStringValue_(text)
    field.setBezeled_(False)
    field.setDrawsBackground_(False)
    field.setEditable_(False)
    field.setSelectable_(False)
    font = NSFont.boldSystemFontOfSize_(size) if bold else NSFont.systemFontOfSize_(size)
    field.setFont_(font)
    if secondary:
        field.setTextColor_(NSColor.secondaryLabelColor())
    return field


class GesturesEditor(NSObject):
    """One window, rebuilt from `controller.custom_gestures` on every edit."""

    def initWithController_(self, ctl):
        self = objc.super(GesturesEditor, self).init()
        if self is None:
            return None
        self.ctl = ctl
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, WIDTH, HEIGHT),
            NSWindowStyleMaskTitled | NSWindowStyleMaskClosable,
            NSBackingStoreBuffered,
            False,
        )
        self.window.setTitle_("Custom gestures")
        self.window.center()
        self.window.setReleasedWhenClosed_(False)
        self.window.setDelegate_(self)
        self.rebuild()
        return self

    # ------------------------------------------------------------------
    @property
    def model(self) -> "custom.CustomGestures":
        return self.ctl.custom_gestures

    def rebuild(self) -> None:
        self.window.contentView().subviews() and [
            v.removeFromSuperview() for v in list(self.window.contentView().subviews())
        ]
        view = self.window.contentView()

        view.addSubview_(_label("Custom gestures", 16, 34, 300, size=16, bold=True))
        view.addSubview_(
            _label("Bind a gesture AirTouch already sees to a keystroke, app, URL or shell command.",
                   16, 58, WIDTH - 32, secondary=True)
        )

        # ---- existing rules
        y = 92
        capacity = max(1, (HEIGHT - 118 - 92) // ROW_H)   # room before the add row
        shown = self.model.rules[:capacity]
        if not self.model.rules:
            view.addSubview_(
                _label("No rules yet - pick a trigger below and press Add.", 16, y, WIDTH - 32,
                       secondary=True)
            )
            y += ROW_H
        for i, rule in enumerate(shown):
            box = NSButton.alloc().initWithFrame_(NSMakeRect(16, HEIGHT - y - 18, 18, 18))
            box.setButtonType_(NSSwitchButton)
            box.setTitle_("")
            box.setState_(1 if rule.enabled else 0)
            box.setTag_(i)
            box.setTarget_(self)
            box.setAction_("toggleRule:")
            view.addSubview_(box)

            title = rule.label if rule.enabled else rule.label + "  (off)"
            view.addSubview_(_label(title, 44, y, WIDTH - 130, size=12.5))

            drop = NSButton.alloc().initWithFrame_(NSMakeRect(WIDTH - 76, HEIGHT - y - 20, 44, 20))
            drop.setTitle_("Remove")
            drop.setBordered_(False)
            drop.setFont_(NSFont.systemFontOfSize_(11.5))
            drop.setTag_(i)
            drop.setTarget_(self)
            drop.setAction_("removeRule:")
            view.addSubview_(drop)
            y += ROW_H
        if len(self.model.rules) > capacity:
            view.addSubview_(
                _label(f"+{len(self.model.rules) - capacity} more - remove some to see them",
                       44, y, WIDTH - 130, size=11.5, secondary=True)
            )

        # ---- add a rule
        y = HEIGHT - 118
        view.addSubview_(_label("New rule", 16, y, 120, size=11, secondary=True, bold=True))

        self.trigger_popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(
            NSMakeRect(16, HEIGHT - y - 54, 172, 26), False
        )
        self.trigger_popup.addItemsWithTitles_([label for _, label in custom.TRIGGERS])
        view.addSubview_(self.trigger_popup)

        self.action_popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(
            NSMakeRect(196, HEIGHT - y - 54, 136, 26), False
        )
        self.action_popup.addItemsWithTitles_([label for _, label in custom.ACTIONS])
        self.action_popup.setTarget_(self)
        self.action_popup.setAction_("actionChanged:")
        view.addSubview_(self.action_popup)

        self.value_field = NSTextField.alloc().initWithFrame_(NSMakeRect(340, HEIGHT - y - 52, 210, 24))
        self.value_field.setPlaceholderString_("cmd+shift+4")
        self.value_field.setFont_(NSFont.systemFontOfSize_(12.5))
        view.addSubview_(self.value_field)

        add = NSButton.alloc().initWithFrame_(NSMakeRect(560, HEIGHT - y - 54, 64, 28))
        add.setTitle_("Add")
        add.setBezelStyle_(1)
        add.setTarget_(self)
        add.setAction_("addRule:")
        view.addSubview_(add)

        view.addSubview_(
            _label("Rules run in addition to the built-in action. Keystrokes need Accessibility.",
                   16, HEIGHT - 26, WIDTH - 32, size=11, secondary=True)
        )

    # ------------------------------------------------------------------
    def addRule_(self, sender=None) -> None:
        trigger = custom.TRIGGERS[max(0, self.trigger_popup.indexOfSelectedItem())][0]
        action = custom.ACTIONS[max(0, self.action_popup.indexOfSelectedItem())][0]
        value = self.value_field.stringValue()
        if not value.strip():
            self.ctl.state.last_event = "custom gesture needs a value"
            return
        self.model.add(trigger, action, value)
        self.ctl.state.last_event = f"added gesture: {trigger} → {action}"
        self.rebuild()

    def removeRule_(self, sender) -> None:
        self.model.remove(int(sender.tag()))
        self.rebuild()

    def toggleRule_(self, sender) -> None:
        index = int(sender.tag())
        if 0 <= index < len(self.model.rules):
            self.model.rules[index].enabled = bool(sender.state())
            self.model.save()
        self.rebuild()

    def actionChanged_(self, sender=None) -> None:
        action = custom.ACTIONS[max(0, self.action_popup.indexOfSelectedItem())][0]
        self.value_field.setPlaceholderString_(
            {
                "key": "cmd+shift+4",
                "app": "Safari",
                "url": "https://example.com",
                "shell": "say hello",
            }.get(action, "")
        )

    def windowWillClose_(self, note) -> None:
        self.ctl.gestures_editor = None

    def show(self) -> None:
        self.window.makeKeyAndOrderFront_(None)


def open_editor(ctl) -> None:
    if getattr(ctl, "gestures_editor", None) is None:
        ctl.gestures_editor = GesturesEditor.alloc().initWithController_(ctl)
    ctl.gestures_editor.show()
