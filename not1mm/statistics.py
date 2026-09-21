import datetime
import logging

from PyQt6 import QtWidgets
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QDockWidget,
    QGridLayout,
    QLabel,
    QTableWidgetItem,
)

from not1mm import fsutils
from not1mm.data.arrl_sections import (
    ALL_ARRL_SECTIONS,
    ARRL_SECTIONS_BY_CALL_AREA,
    TOTAL_ARRL_SECTIONS,
)
from not1mm.lib.database import DataBase
from not1mm.lib.i18n import load_ui
from not1mm.lib.preferences import Preferences

logger = logging.getLogger(__name__)

# Plugin module names (ContestInstance.ContestName / not1mm/plugins/*.py)
# for the contests that get contest-specific multiplier displays. Any
# contest not listed here keeps the original per-band WPX-prefix column
# and all three mode columns, same as before this was added.
CQ_WW_CONTESTS = frozenset({"cq_ww_cw", "cq_ww_ssb", "cq_ww_rtty"})
CWT_CONTESTS = frozenset({"cwt"})
ARRL_SS_CONTESTS = frozenset({"arrl_ss_cw", "arrl_ss_phone"})

# Each of the contests above runs as a single mode (CW, phone, or
# digital), so there's no need to show empty CW/PH/DI columns for the
# modes that contest doesn't use.
CONTEST_MODE_COLUMN = {
    "cq_ww_cw": "CW",
    "cq_ww_ssb": "PH",
    "cq_ww_rtty": "DI",
    "cwt": "CW",
    "arrl_ss_cw": "CW",
    "arrl_ss_phone": "PH",
}

# ARRL SS's per-band CALLS count isn't particularly useful (a call
# worked on multiple bands doesn't add mults), so that column is
# replaced with a per-band count of distinct sections worked instead.
NO_CALLS_CONTESTS = ARRL_SS_CONTESTS

MODE_QUERY_CASE = (
    "CASE WHEN Mode IN ('LSB','USB','SSB','FM','AM') THEN 'PH' "
    "WHEN Mode IN ('CW', 'CW-U', 'CW-L', 'CW-R', 'CWR') THEN 'CW' "
    "WHEN Mode IN ('FT8','FT4','RTTY','PSK31','FSK441','MSK144','JT65','JT9','Q65') THEN 'DI' "
    "ELSE 'OTHER' END mode"
)


class StatsWindow(QDockWidget):
    """The stats window. Shows something important."""

    message = pyqtSignal(dict)
    dbname = None
    pref = {}  # noqa: RUF012
    poll_time = datetime.datetime.now(datetime.UTC) + datetime.timedelta(
        milliseconds=1000
    )
    statisticswindow_closed = pyqtSignal()

    def __init__(self, action):
        super().__init__()
        self.action = action
        self.active: bool = False
        self.load_pref()
        self.dbname: str = fsutils.USER_DATA_PATH / self.pref.get(
            "current_database", "ham.db"
        )
        self.database: DataBase = DataBase(self.dbname, fsutils.APP_DATA_PATH)
        self.database.current_contest = self.pref.get("contest", 0)
        load_ui(self, fsutils.APP_DATA_PATH / "statistics.ui")

        self._sections_grid_layout: QGridLayout | None = None
        self._sections_grid_colors_cache: tuple[str, str] | None = None

        def invalidate_sections_grid_colors_cache_on_mode_change():
            self._sections_grid_colors_cache = None

        app = QApplication.instance()
        if app is not None:
            app.styleHints().colorSchemeChanged.connect(
                invalidate_sections_grid_colors_cache_on_mode_change
            )

    def msg_from_main(self, packet):
        """Process messages from the main window."""
        if packet.get("cmd", "") == "NEWDB":
            self.load_pref()
            self.dbname: str = fsutils.USER_DATA_PATH / self.pref.get(
                "current_database", "ham.db"
            )
            self.database: DataBase = DataBase(self.dbname, fsutils.APP_DATA_PATH)
            self.database.current_contest = self.pref.get("contest", 0)
            self.get_run_and_total_qs()

        if self.active is False:
            return

        if packet.get("cmd", "") in (
            "CONTACTCHANGED",
            "UPDATELOG",
            "DELETE",
            "DELETED",
        ):
            self.get_run_and_total_qs()
            return

    def setActive(self, mode: bool) -> None:
        self.active: bool = bool(mode)

    def load_pref(self) -> None:
        """
        Load preference file to get current db filename and sets the initial darkmode state.

        Parameters
        ----------
        None

        Returns
        -------
        None
        """
        self.pref = Preferences.data()

    def get_contest_name(self) -> str:
        """
        Return the plugin module name (not1mm/plugins/<name>.py) of the
        currently selected contest instance, lowercased, or "" if it
        can't be determined.
        """
        contest_settings = self.database.fetch_contest_by_id(
            self.database.current_contest
        )
        if not contest_settings:
            return ""
        return str(contest_settings.get("ContestName", "") or "").strip().lower()

    def _mode_headers_for(self, contest_name: str) -> list:
        """
        Return which of the CW/PH/DI columns to show. Contests known to
        run a single mode only show that one column; everything else
        keeps showing all three, same as before this was added.
        """
        single_mode = CONTEST_MODE_COLUMN.get(contest_name)
        if single_mode:
            return [single_mode]
        return ["CW", "PH", "DI"]

    def _mult_columns_for(self, contest_name: str):
        """
        Return (headers, per_band_getters, total_getters) describing the
        extra multiplier column(s) to show in the per-band table.

        per_band_getters take a single band value and return an int for
        that band's row. total_getters take no arguments and return an
        int for the TOTAL row, using each contest's own mult-counting
        rule (not just a sum of the per-band column, which isn't always
        the same thing).
        """
        if contest_name in CQ_WW_CONTESTS:
            # CQ WW: each zone and each DXCC entity is a separate mult,
            # once per band.
            headers = ["ZN", "DXCC"]
            per_band_getters = [
                lambda band: int(
                    self.database.fetch_zone_count_for_band(band).get(
                        "zone_count", 0
                    )
                    or 0
                ),
                lambda band: int(
                    self.database.fetch_dxcc_count_for_band(band).get(
                        "dxcc_count", 0
                    )
                    or 0
                ),
            ]
            total_getters = [
                lambda: int(
                    self.database.fetch_zn_band_count().get("zb_count", 0) or 0
                ),
                lambda: int(
                    self.database.fetch_country_band_count().get("cb_count", 0)
                    or 0
                ),
            ]
            return headers, per_band_getters, total_getters

        if contest_name in CWT_CONTESTS or contest_name in ARRL_SS_CONTESTS:
            # CWT (unique calls) and ARRL SS (sections) mults are each
            # counted once for the whole contest, not per band, so no
            # per-band mult column applies here. A running total (and,
            # for SS, clean-sweep tracking) is shown below the table
            # instead, see _update_mult_summary().
            return [], [], []

        # Default / not-yet-specialized contests: keep the original
        # WPX-prefix-once-per-band column.
        headers = ["WPX"]
        per_band_getters = [
            lambda band: int(
                self.database.fetch_wpx_count_for_band(band).get("wpx_count", 0)
                or 0
            )
        ]
        total_getters = [
            lambda: int(
                self.database.fetch_wpx_band_count().get("wpxb_count", 0) or 0
            )
        ]
        return headers, per_band_getters, total_getters

    def get_run_and_total_qs(self):
        """get numbers"""
        if self.active is False:
            return

        contest_name = self.get_contest_name()
        mode_headers = self._mode_headers_for(contest_name)
        include_calls = contest_name not in NO_CALLS_CONTESTS
        mult_headers, mult_per_band_getters, mult_total_getters = (
            self._mult_columns_for(contest_name)
        )

        base_headers = ["BAND", "QSO"]
        if include_calls:
            base_headers.append("CALLS")
        base_headers.extend(mode_headers)

        calls_col = base_headers.index("CALLS") if include_calls else None
        mode_cols = {
            name: base_headers.index(name, len(base_headers) - len(mode_headers))
            for name in mode_headers
        }
        mult_col_start = len(base_headers)
        pts_col = mult_col_start + len(mult_headers)

        self.tableWidget.clear()
        self.tableWidget.setAlternatingRowColors(True)
        self.tableWidget.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        headers = [*base_headers, *mult_headers, "PTS"]
        self.tableWidget.setColumnCount(len(headers))
        self.tableWidget.setHorizontalHeaderLabels(headers)
        self.tableWidget.verticalHeader().setVisible(False)
        self.tableWidget.setEditTriggers(
            QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.tableWidget.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.NoSelection
        )

        def set_item(row: int, col: int, value) -> None:
            item = QTableWidgetItem(
                str(value if value is not None else "0").replace("None", "0")
            )
            item.setTextAlignment(0x0002)
            self.tableWidget.setItem(row, col, item)

        def fill_mode_columns(row: int, band_value=None) -> None:
            band_clause = f" and Band = '{band_value}'" if band_value is not None else ""
            mode_query = (
                "select sum(sortedmode.mode == 'CW') as CW, sum(sortedmode.mode == 'PH') as PH, "
                f"sum(sortedmode.mode == 'DI') as DI from (select {MODE_QUERY_CASE} from DXLOG "
                f"where ContestNR = {self.database.current_contest}{band_clause}) as sortedmode;"
            )
            mode_result = self.database.exec_sql(mode_query)
            for name, col in mode_cols.items():
                set_item(row, col, mode_result.get(name, "0"))

        query = f"select DISTINCT(Band) as band from DXLOG where ContestNR = {self.database.current_contest} ORDER BY band DESC;"
        bands = self.database.exec_sql_mult(query)
        self.tableWidget.setRowCount(len(bands) + 1)
        row = 0
        for band in bands:
            band_value = band.get("band", "")
            query = f"select count(*) as qs, count(DISTINCT(Call)) as calls, sum(Points) as points from DXLOG where ContestNR = {self.database.current_contest} and Band = '{band_value}';"
            totals_result = self.database.exec_sql(query)
            set_item(row, 0, str(band_value).replace("None", ""))
            set_item(row, 1, totals_result.get("qs", "0"))
            if include_calls:
                set_item(row, calls_col, totals_result.get("calls", "0"))
            set_item(row, pts_col, totals_result.get("points", "0"))

            fill_mode_columns(row, band_value)

            for col_offset, getter in enumerate(mult_per_band_getters):
                set_item(row, mult_col_start + col_offset, getter(band_value))

            row += 1

        query = f"select count(*) as qs, count(DISTINCT(Call)) as calls, sum(Points) as points from DXLOG where ContestNR = {self.database.current_contest};"
        totals_result = self.database.exec_sql(query)
        set_item(row, 0, "TOTAL")
        set_item(row, 1, totals_result.get("qs", "0"))
        if include_calls:
            set_item(row, calls_col, totals_result.get("calls", "0"))
        set_item(row, pts_col, totals_result.get("points", "0"))

        fill_mode_columns(row)

        for col_offset, getter in enumerate(mult_total_getters):
            set_item(row, mult_col_start + col_offset, getter())

        self.tableWidget.resizeColumnsToContents()
        self.tableWidget.resizeRowsToContents()

        self._update_mult_summary(contest_name)

    def _update_mult_summary(self, contest_name: str) -> None:
        """
        Update the below-table summary for contests whose mults aren't
        counted per-band: a running total for CWT, and a total plus a
        clean-sweep tracker (worked/missing ARRL sections by call area)
        for ARRL Sweepstakes.
        """
        if contest_name in ARRL_SS_CONTESTS:
            worked_rows = self.database.fetch_worked_sections()
            worked = {
                str(row.get("sect", "")).strip().upper()
                for row in worked_rows
                if row.get("sect")
            }
            worked &= ALL_ARRL_SECTIONS
            count = len(worked)
            text = f"Mults (sections): {count} / {TOTAL_ARRL_SECTIONS}"
            if count == TOTAL_ARRL_SECTIONS:
                text += "  —  CLEAN SWEEP!"
            self.multSummaryLabel.setText(text)
            self.multSummaryLabel.setVisible(True)
            self._update_sections_grid(worked)
            self.sectionsGridContainer.setVisible(True)
            return

        if contest_name in CWT_CONTESTS:
            result = self.database.fetch_call_count()
            count = int(result.get("call_count", 0) or 0) if result else 0
            self.multSummaryLabel.setText(f"Mult (unique calls): {count}")
            self.multSummaryLabel.setVisible(True)
            self.sectionsGridContainer.setVisible(False)
            return

        self.multSummaryLabel.setVisible(False)
        self.sectionsGridContainer.setVisible(False)

    def _sections_grid_colors(self) -> "tuple[str, str]":
        """Returns (worked_color, missing_color) depending on dark or light mode."""
        if self._sections_grid_colors_cache is None:
            palette = self.palette()
            text_lightness = palette.windowText().color().lightness()
            background_lightness = palette.window().color().lightness()
            if background_lightness < text_lightness:
                # dark mode - Catppuccin Mocha
                self._sections_grid_colors_cache = ("#a6e3a1", "#45475a")
            else:
                # light mode - Catppuccin Latte
                self._sections_grid_colors_cache = ("#40a02b", "#ccd0da")
        return self._sections_grid_colors_cache

    def _ensure_sections_grid_layout(self) -> QGridLayout:
        if self._sections_grid_layout is None:
            self._sections_grid_layout = QGridLayout(self.sectionsGridContainer)
            self._sections_grid_layout.setSpacing(2)
        return self._sections_grid_layout

    @staticmethod
    def _clear_layout(layout: QGridLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _update_sections_grid(self, worked: set) -> None:
        """Rebuild the call-area grid of ARRL/RAC sections, coloring
        worked sections vs. sections still needed for a clean sweep."""
        layout = self._ensure_sections_grid_layout()
        self._clear_layout(layout)
        worked_color, missing_color = self._sections_grid_colors()

        for row, (call_area, sections) in enumerate(
            ARRL_SECTIONS_BY_CALL_AREA.items()
        ):
            area_label = QLabel(f"{call_area}:")
            area_label.setStyleSheet("font-weight: bold;")
            layout.addWidget(area_label, row, 0)
            for col, section in enumerate(sections, start=1):
                chip = QLabel(section)
                chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
                chip.setMinimumWidth(34)
                color = worked_color if section in worked else missing_color
                text_color = "#1e1e2e" if section in worked else "#a6adc8"
                chip.setStyleSheet(
                    f"background-color: {color}; color: {text_color}; "
                    "border-radius: 3px; padding: 1px 3px;"
                )
                layout.addWidget(chip, row, col)

    def closeEvent(self, event) -> None:
        self.action.setChecked(False)
        self.statisticswindow_closed.emit()
        event.accept()


if __name__ == "__main__":
    print("This is not a program.\nTry Again.")
