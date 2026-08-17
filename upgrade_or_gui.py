#!/usr/bin/env python3
"""
Matrix Electron Web Deployer - GUI

A simple, straight-to-the-point desktop tool that builds the Matrix web app +
backend API from source and deploys them to the selected OR appliances over SSH.

Single workflow: pick your ORs, hit "Build & Deploy". That's it.
"""
import subprocess
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import scrolledtext, ttk
from typing import List, Optional, Tuple

from upgrade_or import (
    BASE_DIR,
    RuntimeConfig,
    check_version_compatibility,
    deploy_or,
    get_backend_version,
    get_web_version,
    load_env_file,
)

# ---------------------------------------------------------------------------
# Theme / palette
# ---------------------------------------------------------------------------
COLORS = {
    "bg": "#f4f6f8",
    "card": "#ffffff",
    "header": "#1f2933",
    "header_text": "#ffffff",
    "accent": "#2563eb",
    "accent_active": "#1d4ed8",
    "muted": "#6b7280",
    "border": "#d7dde3",
    "log_bg": "#0f172a",
    "log_fg": "#e2e8f0",
    "chip_idle": "#9aa5b1",
    "chip_busy": "#d97706",
    "chip_deploy": "#2563eb",
    "chip_ok": "#16a34a",
    "chip_err": "#dc2626",
}

NPM_CANDIDATES = ["C:\\nvm4w\\nodejs\\npm.cmd", "npm"]


def _npm() -> str:
    for candidate in NPM_CANDIDATES:
        if candidate == "npm" or Path(candidate).exists():
            return candidate
    return "npm"


class MatrixDeployerGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Matrix Electron Web Deployer")
        self.root.geometry("940x780")
        self.root.minsize(820, 680)
        self.root.configure(bg=COLORS["bg"])

        # Build artifact locations (produced by Build from Source).
        project_root = BASE_DIR.parent
        self.backend_repo = project_root / "repos" / "matrix-api-linux"
        self.web_app_dir = project_root / "repos" / "matrix-app-linux"
        self.backend_dist = self.backend_repo / "dist"
        self.web_dist = self.web_app_dir / "dist" / "arthrex-synergy-matrix"

        self._busy = False

        self._load_env()
        self._init_styles()
        self._build_ui()

    # ------------------------------------------------------------------ setup
    def _load_env(self):
        env = load_env_file(BASE_DIR / ".env")
        self.router_ip_var = tk.StringVar(value=env.get("ROUTER_IP", ""))
        self.ssh_user_var = tk.StringVar(value=env.get("SSH_USERNAME", ""))
        self.ssh_password_var = tk.StringVar(value=env.get("SSH_PASSWORD", ""))
        self.sudo_password_var = tk.StringVar(value=env.get("SUDO_PASSWORD", ""))
        self.ssh_show_var = tk.BooleanVar(value=False)
        self.sudo_show_var = tk.BooleanVar(value=False)
        self.or_vars = {i: tk.BooleanVar(value=False) for i in range(1, 13)}

    def _init_styles(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure("TFrame", background=COLORS["bg"])
        style.configure("Card.TFrame", background=COLORS["card"])
        style.configure("TLabel", background=COLORS["bg"], foreground="#1f2933", font=("Segoe UI", 10))
        style.configure("Card.TLabel", background=COLORS["card"], foreground="#1f2933", font=("Segoe UI", 10))
        style.configure("Muted.TLabel", background=COLORS["card"], foreground=COLORS["muted"], font=("Segoe UI", 9))
        style.configure("CardTitle.TLabel", background=COLORS["card"], foreground=COLORS["header"], font=("Segoe UI", 11, "bold"))
        style.configure("TCheckbutton", background=COLORS["card"], font=("Segoe UI", 10))
        style.configure("TEntry", padding=5)
        style.configure("TNotebook", background=COLORS["bg"], borderwidth=0)
        style.configure("TNotebook.Tab", font=("Segoe UI", 10, "bold"), padding=(18, 8))

        style.configure(
            "Primary.TButton",
            font=("Segoe UI", 11, "bold"),
            foreground="#ffffff",
            background=COLORS["accent"],
            borderwidth=0,
            padding=(18, 12),
        )
        style.map(
            "Primary.TButton",
            background=[("active", COLORS["accent_active"]), ("disabled", "#9db4ea")],
        )
        style.configure(
            "Secondary.TButton",
            font=("Segoe UI", 10),
            padding=(12, 8),
        )
        style.configure("Small.TButton", font=("Segoe UI", 9), padding=(8, 4))

    # --------------------------------------------------------------------- ui
    def _build_ui(self):
        # Header bar
        header = tk.Frame(self.root, bg=COLORS["header"], height=64)
        header.pack(fill=tk.X, side=tk.TOP)
        header.pack_propagate(False)
        tk.Label(
            header,
            text="Matrix Electron Web Deployer",
            bg=COLORS["header"],
            fg=COLORS["header_text"],
            font=("Segoe UI", 16, "bold"),
        ).pack(side=tk.LEFT, padx=20)
        tk.Label(
            header,
            text="Build from source \u2192 deploy to ORs",
            bg=COLORS["header"],
            fg="#9aa5b1",
            font=("Segoe UI", 10),
        ).pack(side=tk.LEFT, pady=(6, 0))

        notebook = ttk.Notebook(self.root)
        notebook.pack(fill=tk.BOTH, expand=True, padx=14, pady=12)

        self.deploy_tab = ttk.Frame(notebook, style="TFrame")
        self.faq_tab = ttk.Frame(notebook, style="TFrame")
        notebook.add(self.deploy_tab, text="Deploy")
        notebook.add(self.faq_tab, text="FAQ")

        self._build_deploy_tab(self.deploy_tab)
        self._build_faq_tab(self.faq_tab)

    def _card(self, parent, title: str) -> ttk.Frame:
        outer = tk.Frame(parent, bg=COLORS["border"], bd=0)
        outer.pack(fill=tk.X, padx=4, pady=(0, 12))
        inner = ttk.Frame(outer, style="Card.TFrame", padding=14)
        inner.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)
        ttk.Label(inner, text=title, style="CardTitle.TLabel").pack(anchor=tk.W, pady=(0, 10))
        return inner

    def _build_deploy_tab(self, parent):
        container = ttk.Frame(parent, style="TFrame", padding=8)
        container.pack(fill=tk.BOTH, expand=True)

        # 1. Connection card
        conn = self._card(container, "1. Connection")
        grid = ttk.Frame(conn, style="Card.TFrame")
        grid.pack(fill=tk.X)
        grid.columnconfigure(1, weight=1)
        grid.columnconfigure(3, weight=1)

        ttk.Label(grid, text="Router IP", style="Card.TLabel").grid(row=0, column=0, sticky=tk.W, padx=(0, 8), pady=4)
        ttk.Entry(grid, textvariable=self.router_ip_var, width=22).grid(row=0, column=1, sticky=(tk.W, tk.E), pady=4)
        ttk.Label(grid, text="SSH User", style="Card.TLabel").grid(row=0, column=2, sticky=tk.W, padx=(16, 8), pady=4)
        ttk.Entry(grid, textvariable=self.ssh_user_var, width=22).grid(row=0, column=3, sticky=(tk.W, tk.E), pady=4)

        ttk.Label(grid, text="SSH Password", style="Card.TLabel").grid(row=1, column=0, sticky=tk.W, padx=(0, 8), pady=4)
        self.ssh_pass_entry = ttk.Entry(grid, textvariable=self.ssh_password_var, width=22, show="*")
        self.ssh_pass_entry.grid(row=1, column=1, sticky=(tk.W, tk.E), pady=4)
        ttk.Checkbutton(grid, text="show", variable=self.ssh_show_var, command=self._toggle_ssh).grid(row=1, column=1, sticky=tk.E, padx=4)

        ttk.Label(grid, text="Sudo Password", style="Card.TLabel").grid(row=1, column=2, sticky=tk.W, padx=(16, 8), pady=4)
        self.sudo_pass_entry = ttk.Entry(grid, textvariable=self.sudo_password_var, width=22, show="*")
        self.sudo_pass_entry.grid(row=1, column=3, sticky=(tk.W, tk.E), pady=4)
        ttk.Checkbutton(grid, text="show", variable=self.sudo_show_var, command=self._toggle_sudo).grid(row=1, column=3, sticky=tk.E, padx=4)

        ttk.Label(conn, text="Prefilled from .env when available.", style="Muted.TLabel").pack(anchor=tk.W, pady=(8, 0))

        # 2. Rooms card
        rooms = self._card(container, "2. Target ORs")
        btnrow = ttk.Frame(rooms, style="Card.TFrame")
        btnrow.pack(fill=tk.X, pady=(0, 8))
        ttk.Button(btnrow, text="Select All", style="Small.TButton", command=self._select_all).pack(side=tk.LEFT)
        ttk.Button(btnrow, text="Select None", style="Small.TButton", command=self._select_none).pack(side=tk.LEFT, padx=6)

        checks = ttk.Frame(rooms, style="Card.TFrame")
        checks.pack(fill=tk.X)
        for idx, room in enumerate(range(1, 13)):
            r, c = divmod(idx, 6)
            ttk.Checkbutton(
                checks, text=f"OR{room}", variable=self.or_vars[room], command=self._update_chip
            ).grid(row=r, column=c, sticky=tk.W, padx=8, pady=4)

        # 3. Action card
        action = self._card(container, "3. Build & Deploy")
        chiprow = ttk.Frame(action, style="Card.TFrame")
        chiprow.pack(fill=tk.X, pady=(0, 10))
        self.chip = tk.Label(chiprow, text="IDLE", bg=COLORS["chip_idle"], fg="#ffffff",
                             font=("Segoe UI", 9, "bold"), padx=10, pady=3)
        self.chip.pack(side=tk.LEFT)
        self.status_var = tk.StringVar(value="Select ORs, then Build & Deploy.")
        ttk.Label(chiprow, textvariable=self.status_var, style="Muted.TLabel").pack(side=tk.LEFT, padx=10)

        buttons = ttk.Frame(action, style="Card.TFrame")
        buttons.pack(fill=tk.X)
        self.deploy_button = ttk.Button(buttons, text="Build & Deploy", style="Primary.TButton", command=self.build_and_deploy)
        self.deploy_button.pack(side=tk.LEFT)
        self.build_button = ttk.Button(buttons, text="Build Only", style="Secondary.TButton", command=self.build_only)
        self.build_button.pack(side=tk.LEFT, padx=10)

        # Log
        logframe = self._card(container, "Log")
        self.log_output = scrolledtext.ScrolledText(
            logframe, height=12, bg=COLORS["log_bg"], fg=COLORS["log_fg"],
            insertbackground=COLORS["log_fg"], font=("Consolas", 9), relief=tk.FLAT, wrap=tk.WORD,
        )
        self.log_output.pack(fill=tk.BOTH, expand=True)
        self.log_output.config(state="disabled")

    def _build_faq_tab(self, parent):
        wrap = ttk.Frame(parent, style="TFrame", padding=8)
        wrap.pack(fill=tk.BOTH, expand=True)
        text = scrolledtext.ScrolledText(
            wrap, bg=COLORS["card"], fg="#1f2933", font=("Segoe UI", 10),
            relief=tk.FLAT, wrap=tk.WORD, padx=16, pady=14,
        )
        text.pack(fill=tk.BOTH, expand=True)
        text.insert(tk.END, self._faq_content())
        text.config(state="disabled")

    def _faq_content(self) -> str:
        return (
            "Matrix Electron Web Deployer - FAQ\n"
            "===================================\n\n"
            "What does this tool do?\n"
            "  It builds the Matrix web app (Angular) and backend API from source, then\n"
            "  deploys both to the OR appliances you select and restarts the matrix-api\n"
            "  service. One button: Build & Deploy.\n\n"
            "What do I need before I start?\n"
            "  - A .env file next to the app with ROUTER_IP, SSH_USERNAME, SSH_PASSWORD,\n"
            "    and SUDO_PASSWORD (the Connection fields prefill from it).\n"
            "  - Node.js/npm and Git installed.\n"
            "  - The source repos cloned at ../repos/matrix-api-linux and\n"
            "    ../repos/matrix-app-linux.\n\n"
            "How do I deploy?\n"
            "  1. Confirm the Connection fields.\n"
            "  2. Tick the ORs you want under Target ORs (Select All for every room).\n"
            "  3. Click Build & Deploy. Watch progress in the Log.\n\n"
            "What exactly does 'Build & Deploy' run?\n"
            "  - git pull + npm install + npm run build for the backend (matrix-api-linux)\n"
            "  - git pull + npm install + npm run build for the web app (matrix-app-linux)\n"
            "  - a version-compatibility check (warns on major-version mismatch)\n"
            "  - uploads dist + web assets to each selected OR over SSH and restarts the service\n\n"
            "What is 'Build Only'?\n"
            "  Runs just the build steps so you can validate the source compiles without\n"
            "  deploying anything.\n\n"
            "How are OR SSH ports determined?\n"
            "  Port = 200 + room number (OR3 -> 203). The web URL is\n"
            "  https://<ROUTER_IP>:100<room>/app/ (e.g. OR3 -> :10003).\n\n"
            "Where do logs go?\n"
            "  The on-screen Log shows everything live. CLI runs also write to\n"
            "  work/<run_id>/logs/run.log.\n\n"
            "Troubleshooting\n"
            "  - 'git pull failed': commit/stash local changes in the repo, then retry.\n"
            "  - 'npm is not installed': install Node.js or fix your PATH.\n"
            "  - 'SSH/sudo auth failed': check the Connection fields / .env credentials.\n"
            "  - 'repo not found': clone the source repos under ../repos/.\n"
        )

    # --------------------------------------------------------------- handlers
    def _toggle_ssh(self):
        self.ssh_pass_entry.config(show="" if self.ssh_show_var.get() else "*")

    def _toggle_sudo(self):
        self.sudo_pass_entry.config(show="" if self.sudo_show_var.get() else "*")

    def _select_all(self):
        for var in self.or_vars.values():
            var.set(True)
        self._update_chip()

    def _select_none(self):
        for var in self.or_vars.values():
            var.set(False)
        self._update_chip()

    def _selected_rooms(self) -> List[int]:
        return [i for i, var in self.or_vars.items() if var.get()]

    def _set_chip(self, text: str, color: str):
        self.chip.config(text=text, bg=color)

    def _update_chip(self):
        if self._busy:
            return
        rooms = self._selected_rooms()
        if rooms:
            self._set_chip("READY", COLORS["chip_ok"])
            self.status_var.set(f"{len(rooms)} OR(s) selected: {rooms}")
        else:
            self._set_chip("IDLE", COLORS["chip_idle"])
            self.status_var.set("Select ORs, then Build & Deploy.")

    def log(self, message: str):
        self.log_output.config(state="normal")
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_output.insert(tk.END, f"[{ts}] {message}\n")
        self.log_output.see(tk.END)
        self.log_output.config(state="disabled")
        self.root.update_idletasks()

    def _set_busy(self, busy: bool):
        self._busy = busy
        state = "disabled" if busy else "normal"
        self.deploy_button.config(state=state)
        self.build_button.config(state=state)

    # ---------------------------------------------------------------- actions
    def build_only(self):
        self._start_worker(deploy_after=False)

    def build_and_deploy(self):
        rooms = self._selected_rooms()
        if not rooms:
            self.log("No ORs selected. Tick at least one OR under 'Target ORs'.")
            self._set_chip("ERROR", COLORS["chip_err"])
            self.status_var.set("No ORs selected.")
            return
        if not self.ssh_password_var.get() or not self.sudo_password_var.get():
            self.log("ERROR: SSH Password and Sudo Password are required.")
            self._set_chip("ERROR", COLORS["chip_err"])
            self.status_var.set("Missing credentials.")
            return
        self._start_worker(deploy_after=True, rooms=rooms)

    def _start_worker(self, deploy_after: bool, rooms: Optional[List[int]] = None):
        if self._busy:
            return
        self._set_busy(True)
        thread = threading.Thread(target=self._worker, args=(deploy_after, rooms or []), daemon=True)
        thread.start()

    def _worker(self, deploy_after: bool, rooms: List[int]):
        try:
            self._set_chip("BUILDING", COLORS["chip_busy"])
            self.status_var.set("Building from source...")
            ok = self._run_build()
            if not ok:
                self._set_chip("ERROR", COLORS["chip_err"])
                self.status_var.set("Build failed - see log.")
                return

            if not deploy_after:
                self._set_chip("READY", COLORS["chip_ok"])
                self.status_var.set("Build complete. Ready to deploy.")
                return

            self._set_chip("DEPLOYING", COLORS["chip_deploy"])
            self._run_deploy(rooms)
        finally:
            self._set_busy(False)

    # ------------------------------------------------------------------ build
    def _run_build(self) -> bool:
        self.log("=== Building from Source ===")
        if not self._build_repo(self.backend_repo, "backend", check_file=None):
            return False
        if not self.backend_dist.exists():
            self.log(f"ERROR: Backend dist not found after build: {self.backend_dist}")
            return False
        self.log(f"Backend dist built: {self.backend_dist}")

        if not self._build_repo(self.web_app_dir, "web app", check_file="angular.json"):
            return False
        if not self.web_dist.exists():
            self.log(f"ERROR: Web assets not found after build: {self.web_dist}")
            return False
        self.log(f"Web assets built: {self.web_dist}")

        self.log("=== Build Complete ===")
        return True

    def _build_repo(self, repo: Path, label: str, check_file: Optional[str]) -> bool:
        self.log(f"[{label}] Building from: {repo}")
        if not repo.exists():
            self.log(f"ERROR: {label} repo not found: {repo}")
            self.log("Clone the source repos under ../repos/ (see FAQ).")
            return False

        npm = _npm()
        try:
            subprocess.run([npm, "--version"], capture_output=True, check=True, timeout=10)
        except (subprocess.CalledProcessError, FileNotFoundError):
            self.log("ERROR: npm is not installed or not in PATH.")
            return False

        steps = [
            (["git", "pull"], 60, "git pull"),
        ]
        if check_file and not (repo / check_file).exists():
            self.log(f"ERROR: {check_file} not found in {repo}")
            return False
        steps += [
            ([npm, "install"], 300, "npm install"),
            ([npm, "run", "build"], 300, "npm run build"),
        ]

        for cmd, timeout, desc in steps:
            self.log(f"[{label}] {desc}...")
            try:
                result = subprocess.run(cmd, cwd=str(repo), capture_output=True, text=True, timeout=timeout)
            except subprocess.TimeoutExpired:
                self.log(f"ERROR: [{label}] {desc} timed out.")
                return False
            except Exception as e:
                self.log(f"ERROR: [{label}] {desc} failed: {e}")
                return False
            if result.returncode != 0:
                self.log(f"ERROR: [{label}] {desc} failed: {result.stderr.strip()[:500]}")
                if desc == "git pull":
                    self.log("Resolve the git issue (e.g. uncommitted local changes), then retry.")
                return False
        return True

    # ----------------------------------------------------------------- deploy
    def _run_deploy(self, rooms: List[int]):
        config = RuntimeConfig(
            router_ip=self.router_ip_var.get(),
            ssh_user=self.ssh_user_var.get(),
            ssh_password=self.ssh_password_var.get(),
            sudo_password=self.sudo_password_var.get(),
        )

        class _GuiLogger:
            def __init__(self, gui):
                self.gui = gui

            def line(self, message: str):
                self.gui.log(message)

        logger = _GuiLogger(self)

        self.log("Running version compatibility check...")
        is_safe, message = check_version_compatibility(self.backend_dist, self.web_dist, logger)
        if not is_safe:
            self.log(f"WARNING: {message} (continuing anyway)")

        self.log(f"Starting deployment to {len(rooms)} OR(s): {rooms}")
        results = []
        total = len(rooms)
        for idx, room in enumerate(rooms, 1):
            self.status_var.set(f"Deploying to OR{room} ({idx}/{total})")
            logger.line(f"\n[{idx}/{total}] Processing OR{room}")
            success = deploy_or(room, config, self.backend_dist, self.web_dist, logger, dry_run=False)
            results.append((room, success))

        self.log("\n=== Summary ===")
        for room, success in results:
            self.log(f"OR{room}: {'SUCCESS' if success else 'FAILED'}")
        successful = sum(1 for _, s in results if s)
        self.log(f"\nTotal: {successful}/{total} successful")

        if successful == total:
            self._set_chip("DONE", COLORS["chip_ok"])
            self.status_var.set(f"Deployment complete: {successful}/{total} succeeded.")
        else:
            self._set_chip("ERROR", COLORS["chip_err"])
            self.status_var.set(f"Deployment finished with failures: {successful}/{total}.")


def main():
    root = tk.Tk()
    MatrixDeployerGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
