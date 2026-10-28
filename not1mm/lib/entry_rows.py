"""
Second entry row for SO2V: the run radio's QSO on top, the S&P radio's below.
GPL V3

Contest plugins reach the entry widgets by name at call time --
self.callsign.text(), self.other_1.setText(), self.field3.show() -- across
~3,700 call sites in 96 plugins. Rather than touch them, MainWindow rebinds the
five line-edit names to whichever row is active, and replaces the label and
field-frame names with FanOut proxies so a plugin hiding a field or renaming a
label does it on both rows at once.

The S&P row is cloned from main.ui's own entry-row layout, so it matches the
run row exactly: same widgets, sizes, stretch, labels.
"""

import copy
import io
import xml.etree.ElementTree as ET
from pathlib import Path

from PyQt6 import QtWidgets, uic

if __name__ == "__main__":
    print("I'm not the program you are looking for.")

# The five entry line edits, rebound to the active row.
ENTRY_ROLES = ("callsign", "sent", "receive", "other_1", "other_2")
# Field frames and their labels, fanned out to both rows.
FRAME_NAMES = ("callsignfield", "field1", "field2", "field3", "field4")
LABEL_NAMES = ("callsign_label", "snt_label", "rcv_label", "other_label", "exch_label")

ENTRY_ROW_LAYOUT = "horizontalLayout"


class FanOut:
    """Stands in for one widget that exists once per row.

    Mutating calls go to every row; queries answer from the first (the run
    row), which is what a single-row window would have answered.
    """

    BROADCAST = frozenset(
        {
            "show",
            "hide",
            "setVisible",
            "setHidden",
            "setEnabled",
            "setText",
            "setToolTip",
            "setAccessibleName",
            "setAccessibleDescription",
            "setStyleSheet",
            "setFont",
            "setPalette",
            "setAlignment",
            "setWordWrap",
            "clear",
        }
    )

    def __init__(self, *widgets) -> None:
        object.__setattr__(self, "_widgets", widgets)

    @property
    def widgets(self):
        return self._widgets

    def __getattr__(self, name):
        first = getattr(self._widgets[0], name)
        if name == "findChild":

            def find(*args, **kwargs):
                found = [w.findChild(*args, **kwargs) for w in self._widgets]
                if any(child is None for child in found):
                    return found[0]
                return FanOut(*found)

            return find
        if name in self.BROADCAST and callable(first):

            def fan(*args, **kwargs):
                result = first(*args, **kwargs)
                for widget in self._widgets[1:]:
                    getattr(widget, name)(*args, **kwargs)
                return result

            return fan
        return first

    def __setattr__(self, name, value):
        for widget in self._widgets:
            setattr(widget, name, value)


def entry_row_ui_xml(ui_file: Path) -> str:
    """A standalone .ui document holding a copy of main.ui's entry row."""
    root = ET.parse(ui_file).getroot()
    layout = root.find(f".//layout[@name='{ENTRY_ROW_LAYOUT}']")
    if layout is None:
        raise ValueError(f"{ui_file} has no layout named {ENTRY_ROW_LAYOUT}")
    ui = ET.Element("ui", version="4.0")
    ET.SubElement(ui, "class").text = "SpEntryRow"
    widget = ET.SubElement(ui, "widget", {"class": "QWidget", "name": "SpEntryRow"})
    widget.append(copy.deepcopy(layout))
    return ET.tostring(ui, encoding="unicode")


def build_entry_row(ui_file: Path, like_layout=None) -> QtWidgets.QWidget:
    """Build the S&P entry row. Every named widget from the run row exists on
    the returned QWidget under the same attribute name."""
    row = QtWidgets.QWidget()
    uic.loadUi(io.StringIO(entry_row_ui_xml(ui_file)), row)
    # As a widget's top-level layout it would pick up default margins the
    # nested original does not have, and the columns would stop lining up.
    row.layout().setContentsMargins(0, 0, 0, 0)
    if like_layout is not None:
        row.layout().setSpacing(like_layout.spacing())
    return row
