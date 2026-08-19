#!/usr/bin/env python3
"""
Matrix Electron Web Deployer - PyQt5 GUI

Builds the Matrix web app + backend API from source and deploys both to the
selected OR appliances over SSH. Shares its visual design language (palette,
buttons, dark console, tabbed layout) with the Matrix Deploy tool so the apps
look cohesive.

Single workflow: set connection in Settings, pick ORs on Deploy, hit
"Build & Deploy".
"""
from __future__ import annotations

import subprocess
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional

from PyQt5.QtCore import QPoint, Qt, QThread, pyqtSignal
from PyQt5.QtGui import QColor, QPainter, QPixmap, QPolygon, QTextCursor
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTabWidget,
    QTextBrowser,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from upgrade_or import (
    BASE_DIR,
    RuntimeConfig,
    check_version_compatibility,
    deploy_or,
    load_env_file,
)

DEFAULT_BACKEND_REPO = BASE_DIR.parent / "repos" / "matrix-api-linux"
DEFAULT_WEB_REPO = BASE_DIR.parent / "repos" / "matrix-app-linux"

# ---------------------------------------------------------------------------
# Shared design language (mirrors Matrix Deploy)
# ---------------------------------------------------------------------------
LEVEL_COLORS = {
    "error": "#ff6b6b",
    "success": "#51cf66",
    "warning": "#ffd43b",
    "info": "#22b8cf",
    "detail": "#868e96",
}

BTN_COLORS = {
    "primary": "#2E7D32",
    "danger": "#C62828",
    "service": "#EF6C00",
    "info": "#1976D2",
    "neutral": "#455A64",
    "utility": "#607D8B",
}

_HIDPI_SCALES = (1, 2, 3)


def _shade(hex_color: str, factor: float) -> str:
    """Lighten (factor>1) or darken (factor<1) a #RRGGBB color."""
    hex_color = hex_color.lstrip("#")
    r, g, b = (int(hex_color[i : i + 2], 16) for i in (0, 2, 4))
    r, g, b = (max(0, min(255, int(c * factor))) for c in (r, g, b))
    return f"#{r:02x}{g:02x}{b:02x}"


def _button_style(bg: str) -> str:
    """Polished button with subtle top-to-bottom gradient, thin border and soft
    rounded corners (matches Matrix Deploy)."""
    top = _shade(bg, 1.18)
    bottom = _shade(bg, 0.9)
    edge = _shade(bg, 0.68)
    return (
        "QPushButton {"
        f"color:white; font-size:13px; font-weight:600; padding:7px 14px; min-height:20px;"
        f"border:1px solid {edge}; border-radius:5px;"
        f"background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 {top},stop:1 {bottom});"
        "}"
        "QPushButton:hover {"
        f"background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 {_shade(bg, 1.28)},stop:1 {bg});"
        "}"
        "QPushButton:pressed {"
        f"background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 {bottom},stop:1 {_shade(bg, 0.8)});"
        f"padding-top:8px; padding-bottom:6px;"
        "}"
        "QPushButton:disabled {"
        "color:#ECEFF1; border:1px solid #90A4AE;"
        "background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #B8C2C8,stop:1 #9EAAB0);"
        "}"
    )


def _atnx_path(base: Path, scale: int) -> Path:
    if scale == 1:
        return base
    return base.with_name(f"{base.stem}@{scale}x{base.suffix}")


def _make_chevron_icon(color: str, width: int = 12, height: int = 8) -> str:
    """Render a down-chevron PNG (per color/size, High-DPI @Nx) and return its path."""
    safe_name = f"{color.lstrip('#')}_{width}x{height}"
    base = Path(tempfile.gettempdir()) / f"matrix_web_deployer_chevron_{safe_name}.png"
    for scale in _HIDPI_SCALES:
        target = _atnx_path(base, scale)
        if target.exists():
            continue
        w, h = width * scale, height * scale
        margin_x = max(1, round(w / 12))
        margin_y = max(1, round(h / 8))
        pixmap = QPixmap(w, h)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(color))
        painter.drawPolygon(
            QPolygon(
                [
                    QPoint(margin_x, margin_y),
                    QPoint(w - margin_x, margin_y),
                    QPoint(w // 2, h - margin_y),
                ]
            )
        )
        painter.end()
        pixmap.save(str(target), "PNG")
    return str(base).replace("\\", "/")


def build_app_stylesheet() -> str:
    """Application-wide look: neutral light surface, cohesive inputs/combos/
    frames matching the polished buttons. Built lazily (after QApplication
    exists) so the chevron icon can be rendered with QPainter."""
    chevron = _make_chevron_icon("#455A64")
    chevron_open = _make_chevron_icon("#1976D2")
    group_chevron = _make_chevron_icon("#37474F", width=22, height=16)
    css = """
QWidget {
    background-color: #ECEFF1;
    color: #263238;
    font-family: 'Segoe UI', 'Helvetica Neue', Arial, sans-serif;
    font-size: 13px;
}
QLineEdit {
    background: #FFFFFF;
    border: 1px solid #B0BEC5;
    border-radius: 5px;
    padding: 6px 8px;
    selection-background-color: #90CAF9;
}
QLineEdit:focus { border: 1px solid #1976D2; }
QLineEdit:disabled { background: #ECEFF1; color: #90A4AE; }
QGroupBox {
    background: #F5F7F8;
    border: 1px solid #CFD8DC;
    border-radius: 8px;
    margin-top: 14px;
    padding: 10px 10px 10px 10px;
    font-weight: 600;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    padding: 0 6px;
    color: #37474F;
}
QCheckBox { spacing: 6px; }
QScrollArea { border: 1px solid #CFD8DC; border-radius: 6px; background: #FFFFFF; }
QProgressBar {
    border: 1px solid #B0BEC5;
    border-radius: 4px;
    background: #FFFFFF;
    text-align: center;
    height: 16px;
}
QProgressBar::chunk { background-color: #1976D2; border-radius: 3px; }
QTabWidget::pane { border: 1px solid #CFD8DC; border-radius: 6px; top: -1px; background: #ECEFF1; }
QTabBar::tab {
    background: #CFD8DC; color: #37474F;
    padding: 9px 22px; margin-right: 2px; font-weight: 600;
    min-width: 90px; min-height: 20px;
    border: 1px solid #CFD8DC;
    border-top-left-radius: 6px; border-top-right-radius: 6px;
}
QTabBar::tab:selected { background: #ECEFF1; color: #1565C0; border-bottom-color: #ECEFF1; }
QTabBar::tab:hover { background: #B0BEC5; }
QStatusBar { background: #CFD8DC; color: #37474F; }
QToolTip { background: #37474F; color: white; border: none; padding: 4px 6px; }
"""
    _ = (chevron, chevron_open, group_chevron)  # rendered/cached for combo + group indicators
    return css


SECTION_LABEL_STYLE = (
    "font-size:15px; font-weight:700; color:#1565C0;"
    "border-bottom:2px solid #90CAF9; padding-bottom:3px;"
)

_CONSOLE_STYLE = (
    "QTextEdit { background-color:#1e1e1e; color:#d4d4d4;"
    "border:1px solid #37474F; border-radius:6px; padding:6px;"
    "font-family:'Consolas','Courier New',monospace; }"
    "QScrollBar:vertical { background:#1e1e1e; width:12px; margin:0; }"
    "QScrollBar::handle:vertical { background:#555b62; border-radius:6px; min-height:24px; }"
    "QScrollBar::handle:vertical:hover { background:#6b7280; }"
    "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }"
)


def _make_console() -> QTextEdit:
    console = QTextEdit()
    console.setReadOnly(True)
    console.setMinimumHeight(220)
    console.setLineWrapMode(QTextEdit.WidgetWidth)
    console.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
    console.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
    console.setStyleSheet(_CONSOLE_STYLE)
    return console


def _append_console(console: QTextEdit, message: str, level: str) -> None:
    color = LEVEL_COLORS.get(level, "#d4d4d4")
    stamp = time.strftime("%H:%M:%S")
    safe = (
        message.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
    console.append(
        f'<span style="color:#5c6773;">[{stamp}]</span> '
        f'<span style="color:{color};">{safe}</span>'
    )
    console.moveCursor(QTextCursor.End)


def _classify(message: str) -> str:
    """Pick a log color level from a plain deploy/build message."""
    lowered = message.lower()
    if "error" in lowered or "failed" in lowered or "fail" == lowered.strip():
        return "error"
    if "success" in lowered or "complete" in lowered or message.strip().startswith("OR") and "SUCCESS" in message:
        return "success"
    if "warning" in lowered or "mismatch" in lowered:
        return "warning"
    if message.startswith("==="):
        return "info"
    return "detail"


NPM_CANDIDATES = ["C:\\nvm4w\\nodejs\\npm.cmd", "npm"]


def _npm() -> str:
    for candidate in NPM_CANDIDATES:
        if candidate == "npm" or Path(candidate).exists():
            return candidate
    return "npm"


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------
class BuildDeployWorker(QThread):
    log = pyqtSignal(str, str)          # message, level
    progress = pyqtSignal(int, int)     # done, total
    status = pyqtSignal(str)
    done = pyqtSignal(bool)             # overall success

    def __init__(self, deploy_after: bool, rooms: List[int], config: Optional[RuntimeConfig],
                 backend_repo: Path, web_app_dir: Path, skip_build: bool = False):
        super().__init__()
        self.deploy_after = deploy_after
        self.rooms = rooms
        self.config = config
        self.skip_build = skip_build
        self._cancel = False

        self.backend_repo = Path(backend_repo)
        self.web_app_dir = Path(web_app_dir)
        self.backend_dist = self.backend_repo / "dist"
        self.web_dist = self.web_app_dir / "dist" / "arthrex-synergy-matrix"

    def cancel(self):
        self._cancel = True
        self.log.emit("Cancellation requested; finishing current step...", "warning")

    def _emit(self, message: str, level: Optional[str] = None):
        self.log.emit(message, level or _classify(message))

    # -- logger adapter for upgrade_or.deploy_or ---------------------------
    class _Logger:
        def __init__(self, worker: "BuildDeployWorker"):
            self.worker = worker

        def line(self, message: str):
            for part in str(message).split("\n"):
                if part.strip():
                    self.worker._emit(part)

    def run(self):
        try:
            if self.skip_build:
                self._emit("=== Skipping build (using existing dist) ===", "info")
                if not self.backend_dist.exists() or not self.web_dist.exists():
                    self._emit(
                        "ERROR: No existing build found. Run 'Build && Deploy' or "
                        "'Build Only' first.", "error",
                    )
                    self.done.emit(False)
                    return
                self._emit(f"Backend dist: {self.backend_dist}", "detail")
                self._emit(f"Web assets: {self.web_dist}", "detail")
            else:
                self.status.emit("Building from source...")
                if not self._run_build():
                    self.done.emit(False)
                    return
            if self._cancel:
                self._emit("Cancelled before deployment.", "warning")
                self.done.emit(False)
                return
            if not self.deploy_after:
                self.status.emit("Build complete. Ready to deploy.")
                self.done.emit(True)
                return
            self.status.emit("Deploying...")
            ok = self._run_deploy()
            self.done.emit(ok)
        except Exception as exc:  # pragma: no cover - safety net
            self._emit(f"ERROR: Unexpected failure: {exc}", "error")
            self.done.emit(False)

    # -- build -------------------------------------------------------------
    def _run_build(self) -> bool:
        self._emit("=== Building from Source ===", "info")
        if not self._build_repo(self.backend_repo, "backend", None):
            return False
        if not self.backend_dist.exists():
            self._emit(f"ERROR: Backend dist not found after build: {self.backend_dist}", "error")
            return False
        self._emit(f"Backend dist built: {self.backend_dist}", "success")

        if self._cancel:
            return False

        if not self._build_repo(self.web_app_dir, "web app", "angular.json"):
            return False
        if not self.web_dist.exists():
            self._emit(f"ERROR: Web assets not found after build: {self.web_dist}", "error")
            return False
        self._emit(f"Web assets built: {self.web_dist}", "success")
        self._emit("=== Build Complete ===", "info")
        return True

    def _build_repo(self, repo: Path, label: str, check_file: Optional[str]) -> bool:
        self._emit(f"[{label}] Building from: {repo}")
        if not repo.exists():
            self._emit(f"ERROR: {label} repo not found: {repo}", "error")
            self._emit("Clone the source repos under ../repos/ (see FAQ).", "detail")
            return False

        npm = _npm()
        try:
            subprocess.run([npm, "--version"], capture_output=True, check=True, timeout=10)
        except (subprocess.CalledProcessError, FileNotFoundError):
            self._emit("ERROR: npm is not installed or not in PATH.", "error")
            return False

        if check_file and not (repo / check_file).exists():
            self._emit(f"ERROR: {check_file} not found in {repo}", "error")
            return False

        steps = [
            (["git", "pull"], 60, "git pull"),
            ([npm, "install"], 300, "npm install"),
            ([npm, "run", "build"], 300, "npm run build"),
        ]
        for cmd, timeout, desc in steps:
            if self._cancel:
                self._emit(f"[{label}] Cancelled before {desc}.", "warning")
                return False
            self._emit(f"[{label}] {desc}...")
            try:
                result = subprocess.run(cmd, cwd=str(repo), capture_output=True, text=True, timeout=timeout)
            except subprocess.TimeoutExpired:
                self._emit(f"ERROR: [{label}] {desc} timed out.", "error")
                return False
            except Exception as exc:
                self._emit(f"ERROR: [{label}] {desc} failed: {exc}", "error")
                return False
            if result.returncode != 0:
                self._emit(f"ERROR: [{label}] {desc} failed: {result.stderr.strip()[:500]}", "error")
                if desc == "git pull":
                    self._emit("Resolve the git issue (e.g. uncommitted local changes), then retry.", "detail")
                return False
        return True

    # -- deploy ------------------------------------------------------------
    def _run_deploy(self) -> bool:
        logger = self._Logger(self)
        self._emit("Running version compatibility check...")
        is_safe, message = check_version_compatibility(self.backend_dist, self.web_dist, logger)
        if not is_safe:
            self._emit(f"WARNING: {message} (continuing anyway)", "warning")

        total = len(self.rooms)
        self._emit(f"Starting deployment to {total} OR(s): {self.rooms}", "info")
        results = []
        for idx, room in enumerate(self.rooms, 1):
            if self._cancel:
                self._emit("Deployment cancelled.", "warning")
                break
            self.status.emit(f"Deploying to OR{room} ({idx}/{total})")
            self._emit(f"[{idx}/{total}] Processing OR{room}", "info")
            success = deploy_or(room, self.config, self.backend_dist, self.web_dist, logger, dry_run=False)
            results.append((room, success))
            self.progress.emit(idx, total)

        self._emit("=== Summary ===", "info")
        for room, success in results:
            self._emit(f"OR{room}: {'SUCCESS' if success else 'FAILED'}",
                       "success" if success else "error")
        successful = sum(1 for _, s in results if s)
        self._emit(f"Total: {successful}/{total} successful",
                   "success" if successful == total and total else "warning")
        return bool(results) and successful == total


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------
class MatrixWebDeployerWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.worker: Optional[BuildDeployWorker] = None
        self.room_checkboxes: Dict[int, QCheckBox] = {}

        self.setWindowTitle("Matrix Electron Web Deployer")
        self.setGeometry(100, 60, 1080, 820)
        self.setMinimumSize(900, 700)
        self.setStyleSheet(build_app_stylesheet())

        self._build_ui()
        self._load_env()
        self._update_status_bar()
        self._refresh_config_status()
        self._warn_if_unconfigured()

    # -- UI ----------------------------------------------------------------
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(10, 10, 10, 8)

        self.tabs = QTabWidget()
        deploy_tab = self._build_deploy_tab()
        self.settings_tab = self._build_settings_tab()
        self.tabs.addTab(self.settings_tab, "Settings")
        self.tabs.addTab(deploy_tab, "Deploy")
        self.tabs.addTab(self._build_faq_tab(), "FAQ")
        root.addWidget(self.tabs)
        self.tabs.setCurrentWidget(deploy_tab)
        self.statusBar().showMessage("Ready")

    def _build_deploy_tab(self) -> QWidget:
        tab = QWidget()
        outer = QVBoxLayout(tab)
        outer.setContentsMargins(8, 10, 8, 8)
        outer.setSpacing(12)

        outer.addWidget(self._build_deploy_header())
        outer.addWidget(self._build_rooms_group())
        outer.addLayout(self._build_action_row())

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        outer.addWidget(self.progress)

        self.console = _make_console()
        outer.addWidget(self.console, stretch=1)
        return tab

    def _build_deploy_header(self) -> QWidget:
        """Title + subtitle and a compact 'at a glance' 3-step workflow."""
        header = QFrame()
        header.setStyleSheet(
            "QFrame { background:#FFFFFF; border:1px solid #CFD8DC; border-radius:8px; }"
        )
        lay = QVBoxLayout(header)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(4)

        title = QLabel("Matrix Electron Web Deployer")
        title.setStyleSheet("font-size:20px; font-weight:800; color:#1565C0; border:none;")
        lay.addWidget(title)

        subtitle = QLabel("Build the Matrix web app + backend API from source and deploy them to the selected ORs.")
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("color:#546E7A; font-size:12px; border:none;")
        lay.addWidget(subtitle)

        steps = QLabel(
            "<table cellspacing='0' cellpadding='0' style='margin-top:6px;'><tr>"
            "<td style='color:#1565C0; font-weight:700;'>1&nbsp;</td>"
            "<td style='color:#37474F;'>Set connection &amp; repo paths in <b>Settings</b>&nbsp;&nbsp;&rarr;&nbsp;&nbsp;</td>"
            "<td style='color:#1565C0; font-weight:700;'>2&nbsp;</td>"
            "<td style='color:#37474F;'>Pick <b>Target ORs</b> below&nbsp;&nbsp;&rarr;&nbsp;&nbsp;</td>"
            "<td style='color:#1565C0; font-weight:700;'>3&nbsp;</td>"
            "<td style='color:#37474F;'>Click <b>Build &amp; Deploy</b> and watch the log</td>"
            "</tr></table>"
        )
        steps.setTextFormat(Qt.RichText)
        steps.setStyleSheet("border:none;")
        lay.addWidget(steps)
        return header

    def _build_rooms_group(self) -> QGroupBox:
        group = QGroupBox("Target Operating Rooms")
        layout = QVBoxLayout()

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(6)
        for idx, room in enumerate(range(1, 13)):
            cb = QCheckBox(f"OR{room}")
            cb.stateChanged.connect(self._update_status_bar)
            self.room_checkboxes[room] = cb
            grid.addWidget(cb, idx // 6, idx % 6)
        layout.addLayout(grid)

        btn_row = QHBoxLayout()
        select_all = self._make_button("Select All", "utility")
        select_all.clicked.connect(lambda: self._set_all_rooms(True))
        clear_all = self._make_button("Clear All", "utility")
        clear_all.clicked.connect(lambda: self._set_all_rooms(False))
        btn_row.addWidget(select_all)
        btn_row.addWidget(clear_all)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        group.setLayout(layout)
        return group

    def _build_action_row(self) -> QHBoxLayout:
        """Action buttons with a one-line caption under each so a new user
        immediately knows what each does. 'Build & Deploy' is the dominant
        primary/recommended action."""
        row = QHBoxLayout()
        row.setSpacing(10)

        self.deploy_btn = self._make_button("Build && Deploy", "primary")
        self.deploy_btn.setStyleSheet(
            _button_style(BTN_COLORS["primary"]) + "QPushButton { font-size:16px; font-weight:700; padding:13px; }"
        )
        self.deploy_btn.clicked.connect(self._build_and_deploy)
        row.addLayout(
            self._action_column(
                self.deploy_btn,
                "Recommended \u2014 builds fresh from source, then deploys to the selected ORs.",
            ),
            stretch=3,
        )

        self.build_btn = self._make_button("Build Only", "info")
        self.build_btn.setStyleSheet(
            _button_style(BTN_COLORS["info"]) + "QPushButton { font-size:14px; padding:13px; }"
        )
        self.build_btn.clicked.connect(self._build_only)
        row.addLayout(
            self._action_column(
                self.build_btn,
                "Compiles the code only \u2014 no deploy. Use to check the build succeeds.",
            ),
            stretch=2,
        )

        self.deploy_only_btn = self._make_button("Deploy Only", "service")
        self.deploy_only_btn.setStyleSheet(
            _button_style(BTN_COLORS["service"]) + "QPushButton { font-size:14px; padding:13px; }"
        )
        self.deploy_only_btn.setToolTip(
            "Deploy the existing build output without rebuilding. Requires a "
            "successful 'Build Only' or 'Build && Deploy' run first."
        )
        self.deploy_only_btn.clicked.connect(self._deploy_only)
        row.addLayout(
            self._action_column(
                self.deploy_only_btn,
                "Deploys the last build \u2014 skips rebuilding. Run Build Only first.",
            ),
            stretch=2,
        )

        self.cancel_btn = self._make_button("Cancel", "danger")
        self.cancel_btn.setStyleSheet(
            _button_style(BTN_COLORS["danger"]) + "QPushButton { font-size:14px; padding:13px; }"
        )
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._cancel)
        row.addLayout(
            self._action_column(self.cancel_btn, "Stops the current run."),
            stretch=1,
        )
        return row

    @staticmethod
    def _action_column(button: QPushButton, caption: str) -> QVBoxLayout:
        col = QVBoxLayout()
        col.setSpacing(3)
        col.addWidget(button)
        label = QLabel(caption)
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        label.setStyleSheet("color:#607D8B; font-size:11px; border:none;")
        label.setMinimumHeight(30)
        col.addWidget(label)
        return col

    def _build_settings_tab(self) -> QWidget:
        tab = QWidget()
        outer = QVBoxLayout(tab)
        outer.setContentsMargins(8, 10, 8, 8)
        outer.setSpacing(12)

        # Live readiness banner (red until required fields + repo paths are valid).
        self.config_status_label = QLabel()
        self.config_status_label.setWordWrap(True)
        self.config_status_label.setStyleSheet(
            "padding:10px 12px; border-radius:6px; font-weight:600;"
        )
        outer.addWidget(self.config_status_label)

        group = QGroupBox("Connection Settings")
        layout = QGridLayout()
        layout.setHorizontalSpacing(10)
        layout.setVerticalSpacing(8)
        layout.setColumnStretch(1, 1)

        layout.addWidget(QLabel("Router IP:"), 0, 0)
        self.router_ip_input = QLineEdit()
        layout.addWidget(self.router_ip_input, 0, 1)

        layout.addWidget(QLabel("SSH Username:"), 1, 0)
        self.username_input = QLineEdit()
        layout.addWidget(self.username_input, 1, 1)

        layout.addWidget(QLabel("SSH Password:"), 2, 0)
        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.Password)
        self.password_input.setPlaceholderText("Required for deployment")
        layout.addWidget(self.password_input, 2, 1)
        layout.addWidget(self._password_toggle(self.password_input), 2, 2)

        layout.addWidget(QLabel("Sudo Password:"), 3, 0)
        self.sudo_password_input = QLineEdit()
        self.sudo_password_input.setEchoMode(QLineEdit.Password)
        self.sudo_password_input.setPlaceholderText("Required for deployment")
        layout.addWidget(self.sudo_password_input, 3, 1)
        layout.addWidget(self._password_toggle(self.sudo_password_input), 3, 2)
        group.setLayout(layout)
        outer.addWidget(group)

        # Source repositories: build inputs. Editable + Browse, validated below.
        repo_group = QGroupBox("Source Repositories")
        repo_layout = QGridLayout()
        repo_layout.setHorizontalSpacing(10)
        repo_layout.setVerticalSpacing(8)
        repo_layout.setColumnStretch(1, 1)

        repo_layout.addWidget(QLabel("Backend repo (matrix-api-linux):"), 0, 0)
        self.backend_repo_input = QLineEdit()
        self.backend_repo_input.setPlaceholderText(str(DEFAULT_BACKEND_REPO))
        repo_layout.addWidget(self.backend_repo_input, 0, 1)
        b_browse = self._make_button("Browse...", "utility")
        b_browse.clicked.connect(lambda: self._browse_repo(self.backend_repo_input))
        repo_layout.addWidget(b_browse, 0, 2)

        repo_layout.addWidget(QLabel("Web app repo (matrix-app-linux):"), 1, 0)
        self.web_repo_input = QLineEdit()
        self.web_repo_input.setPlaceholderText(str(DEFAULT_WEB_REPO))
        repo_layout.addWidget(self.web_repo_input, 1, 1)
        w_browse = self._make_button("Browse...", "utility")
        w_browse.clicked.connect(lambda: self._browse_repo(self.web_repo_input))
        repo_layout.addWidget(w_browse, 1, 2)
        repo_group.setLayout(repo_layout)
        outer.addWidget(repo_group)

        note = QLabel(
            "Prefilled from a gitignored .env file when present (ROUTER_IP, "
            "SSH_USERNAME, SSH_PASSWORD, SUDO_PASSWORD, BACKEND_REPO, WEB_REPO). "
            "Repo paths must point to clones of the backend and web source. "
            "Secrets are never written back to disk by this tool."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color:#607D8B; font-size:12px;")
        outer.addWidget(note)

        reload_row = QHBoxLayout()
        reload_row.addStretch()
        reload_btn = self._make_button("Reload from .env", "utility")
        reload_btn.clicked.connect(self._load_env)
        reload_row.addWidget(reload_btn)
        outer.addLayout(reload_row)

        # Live-refresh the readiness banner as critical fields change.
        for widget in (
            self.router_ip_input, self.username_input, self.password_input,
            self.sudo_password_input, self.backend_repo_input, self.web_repo_input,
        ):
            widget.textChanged.connect(self._refresh_config_status)

        outer.addStretch()
        return tab

    def _build_faq_tab(self) -> QWidget:
        tab = QWidget()
        outer = QVBoxLayout(tab)
        outer.setContentsMargins(8, 10, 8, 8)
        outer.setSpacing(8)

        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("Search:"))
        self.faq_search_input = QLineEdit()
        self.faq_search_input.setPlaceholderText("Find a topic, then press Enter for the next match")
        self.faq_search_input.returnPressed.connect(self._faq_search_next)
        search_row.addWidget(self.faq_search_input, stretch=1)
        find_btn = self._make_button("Find Next", "utility")
        find_btn.clicked.connect(self._faq_search_next)
        search_row.addWidget(find_btn)
        outer.addLayout(search_row)

        self.faq_browser = QTextBrowser()
        self.faq_browser.setOpenExternalLinks(True)
        self.faq_browser.setStyleSheet(
            "QTextBrowser { background:#FFFFFF; border:1px solid #CFD8DC;"
            "border-radius:6px; padding:10px; }"
        )
        self.faq_browser.setHtml(self._faq_html())
        outer.addWidget(self.faq_browser, stretch=1)
        return tab

    @staticmethod
    def _faq_html() -> str:
        style = (
            "<style>"
            "h2 { color:#1565C0; border-bottom:2px solid #90CAF9; padding-bottom:4px; margin-top:18px; }"
            "h3 { color:#37474F; margin-bottom:2px; }"
            "code { background:#ECEFF1; color:#C62828; padding:1px 4px; border-radius:3px; font-family:Consolas,monospace; }"
            "p, li { color:#263238; line-height:1.5; }"
            "</style>"
            "<h1>Matrix Electron Web Deployer - Reference</h1>"
        )
        sections = [
            ("Getting Started", [
                ("What does this tool do?",
                 "It builds the Matrix web app (Angular) and backend API from source, then "
                 "deploys both to the OR appliances you select and restarts the "
                 "<code>matrix-api</code> service. One button: <b>Build &amp; Deploy</b>."),
                ("What do I configure first?",
                 "Open the <b>Settings</b> tab and set <b>Router IP</b>, <b>SSH Username</b>, "
                 "<b>SSH Password</b>, and <b>Sudo Password</b>. These prefill from a "
                 "gitignored <code>.env</code> file if present."),
                ("What are the prerequisites?",
                 "<ul><li>Node.js/npm and Git installed.</li>"
                 "<li>Source repos cloned at <code>../repos/matrix-api-linux</code> and "
                 "<code>../repos/matrix-app-linux</code>.</li></ul>"),
            ]),
            ("Deploying", [
                ("How do I deploy?",
                 "<ul><li>Confirm <b>Settings</b>.</li>"
                 "<li>On <b>Deploy</b>, tick the ORs to target (<b>Select All</b> for every room).</li>"
                 "<li>Click <b>Build &amp; Deploy</b> and watch the console.</li></ul>"),
                ("What exactly does 'Build &amp; Deploy' run?",
                 "<ul><li><code>git pull</code> + <code>npm install</code> + <code>npm run build</code> for the backend.</li>"
                 "<li>Same for the web app.</li>"
                 "<li>A version-compatibility check (warns on major-version mismatch).</li>"
                 "<li>Uploads dist + web assets to each OR over SSH and restarts the service.</li></ul>"),
                ("What is 'Build Only'?",
                 "Runs just the build steps so you can validate the source compiles without deploying. "
                 "Once it succeeds, use <b>Deploy Only (skip build)</b> to ship that same build "
                 "without rebuilding."),
                ("What is 'Deploy Only (skip build)'?",
                 "Deploys the most recent build output (backend <code>dist/</code> and web "
                 "<code>dist/arthrex-synergy-matrix/</code>) straight to the selected ORs, skipping "
                 "<code>git pull</code>/<code>npm install</code>/<code>npm run build</code>. Requires a "
                 "prior successful <b>Build Only</b> or <b>Build &amp; Deploy</b> run."),
                ("Can I stop a run?",
                 "Yes - click <b>Cancel</b>. It stops after the current step/room finishes."),
            ]),
            ("Reference & Troubleshooting", [
                ("How are OR SSH ports determined?",
                 "Port = <code>200 + room</code> (OR3 &rarr; 203). Web URL: "
                 "<code>https://&lt;ROUTER_IP&gt;:100&lt;room&gt;/app/</code> (OR3 &rarr; :10003)."),
                ("Where do logs go?",
                 "The console shows everything live. CLI runs also write to <code>work/&lt;run_id&gt;/logs/run.log</code>."),
                ("Common errors",
                 "<ul><li><b>git pull failed</b>: commit/stash local changes in the repo, then retry.</li>"
                 "<li><b>npm is not installed</b>: install Node.js or fix your PATH.</li>"
                 "<li><b>SSH/sudo auth failed</b>: check the Settings credentials / .env.</li>"
                 "<li><b>repo not found</b>: clone the source repos under <code>../repos/</code>.</li></ul>"),
            ]),
        ]
        parts = [style]
        for title, items in sections:
            parts.append(f"<h2>{title}</h2>")
            for question, answer in items:
                parts.append(f"<h3>{question}</h3><p>{answer}</p>")
        return "".join(parts)

    def _faq_search_next(self):
        term = self.faq_search_input.text().strip()
        if not term:
            return
        if not self.faq_browser.find(term):
            self.faq_browser.moveCursor(QTextCursor.Start)
            if not self.faq_browser.find(term):
                self.statusBar().showMessage(f"No match for '{term}'", 2000)

    # -- helpers -----------------------------------------------------------
    def _make_button(self, text: str, kind: str) -> QPushButton:
        btn = QPushButton(text)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setStyleSheet(_button_style(BTN_COLORS[kind]))
        return btn

    @staticmethod
    def _password_toggle(line_edit: QLineEdit) -> QToolButton:
        btn = QToolButton()
        btn.setCheckable(True)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setText("Show")
        btn.setToolTip("Show / hide")
        btn.setFixedWidth(52)
        btn.setStyleSheet(
            "QToolButton { background:#CFD8DC; color:#37474F; border:1px solid #B0BEC5;"
            "border-radius:5px; padding:5px 4px; font-size:11px; font-weight:600; }"
            "QToolButton:hover { background:#B0BEC5; }"
            "QToolButton:checked { background:#1976D2; color:white; border-color:#1565C0; }"
        )

        def _toggle(checked: bool) -> None:
            line_edit.setEchoMode(QLineEdit.Normal if checked else QLineEdit.Password)
            btn.setText("Hide" if checked else "Show")

        btn.toggled.connect(_toggle)
        return btn

    def _load_env(self):
        env = load_env_file(BASE_DIR / ".env")
        self.router_ip_input.setText(env.get("ROUTER_IP", ""))
        self.username_input.setText(env.get("SSH_USERNAME", ""))
        self.password_input.setText(env.get("SSH_PASSWORD", ""))
        self.sudo_password_input.setText(env.get("SUDO_PASSWORD", ""))
        self.backend_repo_input.setText(env.get("BACKEND_REPO", str(DEFAULT_BACKEND_REPO)))
        self.web_repo_input.setText(env.get("WEB_REPO", str(DEFAULT_WEB_REPO)))
        self._refresh_config_status()

    def _browse_repo(self, line_edit: QLineEdit):
        start = line_edit.text().strip() or str(BASE_DIR.parent / "repos")
        path = QFileDialog.getExistingDirectory(self, "Select Repository Folder", start)
        if path:
            line_edit.setText(path)

    def _backend_repo_path(self) -> Path:
        text = self.backend_repo_input.text().strip()
        return Path(text) if text else DEFAULT_BACKEND_REPO

    def _web_repo_path(self) -> Path:
        text = self.web_repo_input.text().strip()
        return Path(text) if text else DEFAULT_WEB_REPO

    # -- validation --------------------------------------------------------
    def _missing_connection(self) -> List[str]:
        missing = []
        if not self.router_ip_input.text().strip():
            missing.append("Router IP")
        if not self.username_input.text().strip():
            missing.append("SSH Username")
        if not self.password_input.text():
            missing.append("SSH Password")
        if not self.sudo_password_input.text():
            missing.append("Sudo Password")
        return missing

    def _missing_repos(self) -> List[str]:
        missing = []
        if not self._backend_repo_path().exists():
            missing.append("Backend repo (matrix-api-linux)")
        if not self._web_repo_path().exists():
            missing.append("Web app repo (matrix-app-linux)")
        return missing

    def _refresh_config_status(self):
        label = getattr(self, "config_status_label", None)
        if label is None:
            return
        missing = self._missing_connection() + self._missing_repos()
        if missing:
            label.setText("\u26a0  Not ready: fix " + ", ".join(missing) + ".")
            label.setStyleSheet(
                "padding:10px 12px; border-radius:6px; font-weight:600;"
                "background:#FDECEA; color:#C62828; border:1px solid #F5C6CB;"
            )
        else:
            label.setText("\u2713  Ready: connection and repo paths are set.")
            label.setStyleSheet(
                "padding:10px 12px; border-radius:6px; font-weight:600;"
                "background:#E8F5E9; color:#2E7D32; border:1px solid #A5D6A7;"
            )

    def _warn_if_unconfigured(self):
        missing = self._missing_connection() + self._missing_repos()
        if not missing:
            return
        self.tabs.setCurrentWidget(self.settings_tab)

    def _require_ready(self, need_deploy: bool) -> bool:
        """Gate for build/deploy. Repo paths are always required (for the
        build); connection creds are additionally required for deployment.
        On failure, warns, jumps to Settings, and returns False."""
        missing = list(self._missing_repos())
        if need_deploy:
            missing = self._missing_connection() + missing
        if not missing:
            return True
        self.tabs.setCurrentWidget(self.settings_tab)
        self._refresh_config_status()
        QMessageBox.warning(
            self,
            "Configuration Needed",
            "Set the following in the Settings tab first:\n\n\u2022 "
            + "\n\u2022 ".join(missing),
        )
        return False

    def _set_all_rooms(self, checked: bool):
        for cb in self.room_checkboxes.values():
            cb.setChecked(checked)
        self._update_status_bar()

    def _selected_rooms(self) -> List[int]:
        return [room for room, cb in self.room_checkboxes.items() if cb.isChecked()]

    def _update_status_bar(self):
        rooms = self._selected_rooms()
        if rooms:
            self.statusBar().showMessage(f"{len(rooms)} OR(s) selected: {rooms}")
        else:
            self.statusBar().showMessage("No ORs selected")

    def _append(self, message: str, level: str):
        _append_console(self.console, message, level)

    # -- actions -----------------------------------------------------------
    def _build_only(self):
        if not self._require_ready(need_deploy=False):
            return
        self._start(deploy_after=False)

    def _build_and_deploy(self):
        rooms = self._selected_rooms()
        if not rooms:
            self._append("No ORs selected. Tick at least one OR on the Deploy tab.", "error")
            QMessageBox.warning(self, "No ORs Selected",
                                "Tick at least one OR on the Deploy tab.")
            return
        if not self._require_ready(need_deploy=True):
            return
        self._start(deploy_after=True, rooms=rooms)

    def _deploy_only(self):
        """Deploy the existing build output without rebuilding."""
        rooms = self._selected_rooms()
        if not rooms:
            self._append("No ORs selected. Tick at least one OR on the Deploy tab.", "error")
            QMessageBox.warning(self, "No ORs Selected",
                                "Tick at least one OR on the Deploy tab.")
            return
        if not self._require_ready(need_deploy=True):
            return
        backend_dist = self._backend_repo_path() / "dist"
        web_dist = self._web_repo_path() / "dist" / "arthrex-synergy-matrix"
        if not backend_dist.exists() or not web_dist.exists():
            QMessageBox.warning(
                self, "No Build Found",
                "No existing build output was found. Run 'Build Only' or "
                "'Build && Deploy' first.",
            )
            return
        self._start(deploy_after=True, rooms=rooms, skip_build=True)

    def _start(self, deploy_after: bool, rooms: Optional[List[int]] = None, skip_build: bool = False):
        if self.worker and self.worker.isRunning():
            return
        config = RuntimeConfig(
            router_ip=self.router_ip_input.text(),
            ssh_user=self.username_input.text(),
            ssh_password=self.password_input.text(),
            sudo_password=self.sudo_password_input.text(),
        )
        self.progress.setValue(0)
        self._set_running(True)
        self.worker = BuildDeployWorker(
            deploy_after, rooms or [], config,
            self._backend_repo_path(), self._web_repo_path(),
            skip_build=skip_build,
        )
        self.worker.log.connect(self._append)
        self.worker.progress.connect(self._on_progress)
        self.worker.status.connect(self.statusBar().showMessage)
        self.worker.done.connect(self._on_done)
        self.worker.start()

    def _cancel(self):
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.cancel_btn.setEnabled(False)

    def _on_progress(self, done: int, total: int):
        if total:
            self.progress.setValue(int(done / total * 100))

    def _on_done(self, success: bool):
        self._set_running(False)
        if success:
            self.progress.setValue(100)
            self.statusBar().showMessage("Done")
        else:
            self.statusBar().showMessage("Finished with errors - see console")

    def _set_running(self, running: bool):
        self.deploy_btn.setEnabled(not running)
        self.build_btn.setEnabled(not running)
        self.deploy_only_btn.setEnabled(not running)
        self.cancel_btn.setEnabled(running)


def run():
    import sys

    # High-DPI: render consistently across monitors with different scale
    # factors. Must be set before QApplication is constructed.
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MatrixWebDeployerWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    run()
