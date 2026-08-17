import tkinter as tk
from tkinter import ttk, scrolledtext, filedialog
import threading
from pathlib import Path
from datetime import datetime
import os
import shutil
import sys
from typing import List, Tuple
import subprocess

# Import the deployment logic from upgrade_or.py
from upgrade_or import (
    BASE_DIR,
    RuntimeConfig,
    Logger,
    deploy_or,
    extract_swu_package,
    get_backend_version,
    get_web_version,
    check_version_compatibility,
)


class ORDeploymentGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Matrix OR Electron Configuration Tool")
        self.root.geometry("1000x850")

        # Load credentials from .env as defaults
        self.config = self.load_config()

        # State variables
        self.extracting = False
        self.cancel_requested = False

        # Path variables
        script_dir = BASE_DIR
        self.backend_dist_var = tk.StringVar(value="")
        self.web_assets_var = tk.StringVar(value="")

        # Setup UI
        self.setup_ui()
        
    def load_config(self) -> RuntimeConfig:
        # Resolve .env from the script/exe directory (not the current working dir)
        script_dir = BASE_DIR
        env_file = script_dir / ".env"
        env_vals = {}
        if env_file.exists():
            with env_file.open("r") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, value = line.split("=", 1)
                        env_vals[key.strip()] = value.strip()

        return RuntimeConfig(
            router_ip=env_vals.get("ROUTER_IP", ""),
            ssh_user=env_vals.get("SSH_USERNAME", ""),
            ssh_password=env_vals.get("SSH_PASSWORD", ""),
            sudo_password=env_vals.get("SUDO_PASSWORD", ""),
        )

    def setup_ui(self):
        # Main frame
        main_frame = ttk.Frame(self.root, padding="15")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))

        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        main_frame.columnconfigure(0, weight=1)
        main_frame.rowconfigure(3, weight=1)

        # Title
        title_label = ttk.Label(main_frame, text="Matrix OR Electron Configuration Tool", font=("Arial", 18, "bold"))
        title_label.grid(row=0, column=0, pady=(0, 15))

        # Config input frame
        config_frame = ttk.LabelFrame(main_frame, text="Configuration", padding="10")
        config_frame.grid(row=1, column=0, sticky=(tk.W, tk.E), pady=(0, 15))
        config_frame.columnconfigure(1, weight=1)

        # Router IP
        ttk.Label(config_frame, text="Router IP:").grid(row=0, column=0, sticky=tk.W, pady=5, padx=(10, 5))
        self.router_ip_var = tk.StringVar(value=self.config.router_ip)
        ttk.Entry(config_frame, textvariable=self.router_ip_var).grid(row=0, column=1, sticky=(tk.W, tk.E), pady=5, padx=(0, 10))

        # SSH Username
        ttk.Label(config_frame, text="SSH Username:").grid(row=1, column=0, sticky=tk.W, pady=5, padx=(10, 5))
        self.ssh_user_var = tk.StringVar(value=self.config.ssh_user)
        ttk.Entry(config_frame, textvariable=self.ssh_user_var).grid(row=1, column=1, sticky=(tk.W, tk.E), pady=5, padx=(0, 10))

        # SSH Password
        ttk.Label(config_frame, text="SSH Password:").grid(row=2, column=0, sticky=tk.W, pady=5, padx=(10, 5))
        self.ssh_password_var = tk.StringVar(value=self.config.ssh_password)
        ssh_pass_frame = ttk.Frame(config_frame)
        ssh_pass_frame.grid(row=2, column=1, sticky=(tk.W, tk.E), pady=5, padx=(0, 10))
        ssh_pass_frame.columnconfigure(0, weight=1)
        self.ssh_pass_entry = ttk.Entry(ssh_pass_frame, textvariable=self.ssh_password_var, show="*")
        self.ssh_pass_entry.grid(row=0, column=0, sticky=(tk.W, tk.E))
        self.ssh_show_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(ssh_pass_frame, text="Show", variable=self.ssh_show_var, command=self.toggle_ssh_password).grid(row=0, column=1, padx=(5, 0))

        # Sudo Password
        ttk.Label(config_frame, text="Sudo Password:").grid(row=3, column=0, sticky=tk.W, pady=5, padx=(10, 5))
        self.sudo_password_var = tk.StringVar(value=self.config.sudo_password)
        sudo_pass_frame = ttk.Frame(config_frame)
        sudo_pass_frame.grid(row=3, column=1, sticky=(tk.W, tk.E), pady=5, padx=(0, 10))
        sudo_pass_frame.columnconfigure(0, weight=1)
        self.sudo_pass_entry = ttk.Entry(sudo_pass_frame, textvariable=self.sudo_password_var, show="*")
        self.sudo_pass_entry.grid(row=0, column=0, sticky=(tk.W, tk.E))
        self.sudo_show_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(sudo_pass_frame, text="Show", variable=self.sudo_show_var, command=self.toggle_sudo_password).grid(row=0, column=1, padx=(5, 0))

        # Notebook (tabs)
        self.notebook = ttk.Notebook(main_frame)
        self.notebook.grid(row=2, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), pady=(0, 15))
        main_frame.rowconfigure(2, weight=1)

        # Tab 1: Prepare Source
        source_tab = ttk.Frame(self.notebook, padding="15")
        self.notebook.add(source_tab, text="Prepare Source")
        self._build_source_tab(source_tab)

        # Tab 2: Select Targets
        targets_tab = ttk.Frame(self.notebook, padding="15")
        self.notebook.add(targets_tab, text="Select Targets")
        self._build_targets_tab(targets_tab)

        # Tab 3: Deploy
        deploy_tab = ttk.Frame(self.notebook, padding="15")
        self.notebook.add(deploy_tab, text="Deploy")
        self._build_deploy_tab(deploy_tab)

        # Log output
        log_frame = ttk.LabelFrame(main_frame, text="Log Output", padding="10")
        log_frame.grid(row=3, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), pady=(0, 10))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)

        self.log_output = scrolledtext.ScrolledText(log_frame, wrap=tk.WORD, height=10, state='disabled')
        self.log_output.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))

        # Status bar
        self.status_var = tk.StringVar(value="Ready")
        status_bar = ttk.Label(main_frame, textvariable=self.status_var, relief=tk.SUNKEN, padding=(5, 2))
        status_bar.grid(row=4, column=0, sticky=(tk.W, tk.E))
    
    def _build_source_tab(self, parent):
        parent.columnconfigure(0, weight=1)

        # Workflow selection
        workflow_frame = ttk.LabelFrame(parent, text="Choose Artifact Workflow", padding="10")
        workflow_frame.grid(row=0, column=0, sticky=(tk.W, tk.E), pady=(0, 15))
        workflow_frame.columnconfigure(0, weight=1)

        self.workflow_var = tk.StringVar(value="swu")
        ttk.Radiobutton(workflow_frame, text="SWU Path (Extract SWU + Get web assets separately)", variable=self.workflow_var, value="swu", command=self._on_workflow_change).grid(row=0, column=0, sticky=tk.W, pady=5)
        ttk.Radiobutton(workflow_frame, text="Build Path (Build from source - produces both backend + web)", variable=self.workflow_var, value="build", command=self._on_workflow_change).grid(row=1, column=0, sticky=tk.W, pady=5)

        # SWU Workflow section
        self.swu_workflow_frame = ttk.Frame(parent)
        self.swu_workflow_frame.grid(row=1, column=0, sticky=(tk.W, tk.E), pady=(0, 15))
        self._build_swu_workflow_section(self.swu_workflow_frame)

        # Build Workflow section
        self.build_workflow_frame = ttk.Frame(parent)
        self.build_workflow_frame.grid(row=2, column=0, sticky=(tk.W, tk.E), pady=(0, 15))
        self._build_build_workflow_section(self.build_workflow_frame)

        # Initially show SWU workflow
        self._on_workflow_change()

        # Summary
        summary_frame = ttk.LabelFrame(parent, text="Prepared Artifacts", padding="12")
        summary_frame.grid(row=3, column=0, sticky=(tk.W, tk.E), pady=(0, 15))
        summary_frame.columnconfigure(0, weight=1)

        self.backend_status_var = tk.StringVar(value="Backend: not ready")
        self.web_status_var = tk.StringVar(value="Web assets: not ready")
        self.backend_version_var = tk.StringVar(value="Backend version: --")
        self.web_version_var = tk.StringVar(value="Web version: --")
        self.version_status_var = tk.StringVar(value="Version check: not run")

        ttk.Label(summary_frame, textvariable=self.backend_status_var, font=("Arial", 10, "bold")).grid(row=0, column=0, sticky=tk.W, pady=(5, 0))
        ttk.Label(summary_frame, textvariable=self.backend_version_var, font=("Arial", 9)).grid(row=1, column=0, sticky=tk.W, padx=(20, 0), pady=(0, 5))
        ttk.Label(summary_frame, textvariable=self.web_status_var, font=("Arial", 10, "bold")).grid(row=2, column=0, sticky=tk.W, pady=(10, 0))
        ttk.Label(summary_frame, textvariable=self.web_version_var, font=("Arial", 9)).grid(row=3, column=0, sticky=tk.W, padx=(20, 0), pady=(0, 5))
        ttk.Label(summary_frame, textvariable=self.version_status_var, font=("Arial", 10, "bold")).grid(row=4, column=0, sticky=tk.W, pady=(10, 0))

        ttk.Button(summary_frame, text="Go to Deploy", command=lambda: self.notebook.select(2)).grid(row=5, column=0, sticky=tk.W, pady=(15, 0))

    def _build_swu_workflow_section(self, parent):
        parent.columnconfigure(0, weight=1)

        ttk.Label(parent, text="Step 1: Extract SWU to get backend dist", font=("Arial", 11, "bold")).grid(row=0, column=0, sticky=tk.W, pady=(0, 10))

        swu_frame = ttk.Frame(parent)
        swu_frame.grid(row=1, column=0, sticky=(tk.W, tk.E), pady=(0, 20))
        swu_frame.columnconfigure(1, weight=1)

        ttk.Label(swu_frame, text="SWU File:").grid(row=0, column=0, sticky=tk.W, padx=(0, 8))
        self.swu_file_var = tk.StringVar(value="")
        swu_entry = ttk.Entry(swu_frame, textvariable=self.swu_file_var)
        swu_entry.grid(row=0, column=1, sticky=(tk.W, tk.E), padx=(0, 8))
        ttk.Button(swu_frame, text="Browse", command=self.browse_swu).grid(row=0, column=2, padx=(0, 8))
        self.extract_button = ttk.Button(swu_frame, text="Extract", command=self.extract_swu)
        self.extract_button.grid(row=0, column=3, padx=(0, 8))
        self.cancel_button = ttk.Button(swu_frame, text="Cancel", command=self.cancel_extraction, state='disabled')
        self.cancel_button.grid(row=0, column=4)

        ttk.Label(parent, text="Step 2: Get web assets", font=("Arial", 11, "bold")).grid(row=2, column=0, sticky=tk.W, pady=(0, 10))

        web_frame = ttk.Frame(parent)
        web_frame.grid(row=3, column=0, sticky=(tk.W, tk.E), pady=(0, 20))

        self.download_web_button = ttk.Button(web_frame, text="Download Web from Selected OR", command=self.download_web_from_or)
        self.download_web_button.grid(row=0, column=0, padx=(0, 8))

        ttk.Label(web_frame, text="or set manual path:").grid(row=0, column=1, padx=(0, 8))

        ttk.Label(parent, text="Web Assets Path:").grid(row=4, column=0, sticky=tk.W, pady=(10, 5))
        script_dir = BASE_DIR
        web_default = os.environ.get("OR_WEB_ASSETS", str(script_dir / "web-assets" / "dist" / "arthrex-synergy-matrix"))
        self.web_assets_var = tk.StringVar(value=web_default)
        web_entry = ttk.Entry(parent, textvariable=self.web_assets_var)
        web_entry.grid(row=5, column=0, sticky=(tk.W, tk.E), pady=(0, 20))

    def _build_build_workflow_section(self, parent):
        parent.columnconfigure(0, weight=1)

        ttk.Label(parent, text="Build from Source (produces both backend dist and web assets)", font=("Arial", 11, "bold")).grid(row=0, column=0, sticky=tk.W, pady=(0, 10))

        build_frame = ttk.Frame(parent)
        build_frame.grid(row=1, column=0, sticky=(tk.W, tk.E), pady=(0, 20))

        self.build_button = ttk.Button(build_frame, text="Build from Source", command=self.build_from_source)
        self.build_button.pack(pady=10)

        ttk.Label(parent, text="This will pull latest code and build both backend and web assets from source repositories.", font=("Arial", 9)).grid(row=2, column=0, sticky=tk.W)

    def _on_workflow_change(self):
        workflow = self.workflow_var.get()
        if workflow == "swu":
            self.swu_workflow_frame.grid(row=1, column=0, sticky=(tk.W, tk.E), pady=(0, 15))
            self.build_workflow_frame.grid_forget()
        else:
            self.swu_workflow_frame.grid_forget()
            self.build_workflow_frame.grid(row=1, column=0, sticky=(tk.W, tk.E), pady=(0, 15))

    def _build_targets_tab(self, parent):
        parent.columnconfigure(0, weight=1)

        ttk.Label(parent, text="Select the operating rooms to deploy to.", font=("Arial", 11)).grid(row=0, column=0, sticky=tk.W, pady=(0, 10))

        or_frame = ttk.LabelFrame(parent, text="Operating Rooms", padding="10")
        or_frame.grid(row=1, column=0, sticky=(tk.W, tk.E), pady=(0, 10))
        or_frame.columnconfigure(0, weight=1)

        self.or_vars = {}
        for i in range(1, 13):
            self.or_vars[i] = tk.BooleanVar(value=False)

        for i in range(1, 13):
            cb = ttk.Checkbutton(or_frame, text=f"OR{i}", variable=self.or_vars[i], command=self.refresh_deploy_summary)
            row = (i - 1) // 4
            col = (i - 1) % 4
            cb.grid(row=row, column=col, padx=5, pady=2)

        button_frame = ttk.Frame(or_frame)
        button_frame.grid(row=3, column=0, columnspan=4, pady=(5, 0))
        ttk.Button(button_frame, text="Select All", command=self.select_all).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="Select None", command=self.select_none).pack(side=tk.LEFT, padx=5)

    def _build_deploy_tab(self, parent):
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)

        ttk.Label(parent, text="Review deployment configuration and start deployment.", font=("Arial", 11)).grid(row=0, column=0, sticky=tk.W, pady=(0, 10))

        self.deploy_summary_var = tk.StringVar(value="Select targets in the 'Select Targets' tab.")
        summary_frame = ttk.LabelFrame(parent, text="Deployment Summary", padding="10")
        summary_frame.grid(row=1, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        summary_frame.columnconfigure(0, weight=1)
        summary_frame.rowconfigure(0, weight=1)

        summary_label = ttk.Label(summary_frame, textvariable=self.deploy_summary_var, wraplength=900, justify=tk.LEFT)
        summary_label.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))

        button_frame = ttk.Frame(parent)
        button_frame.grid(row=2, column=0, pady=(10, 0))
        self.deploy_button = ttk.Button(button_frame, text="Deploy to Selected ORs", command=self.start_deployment)
        self.deploy_button.pack(side=tk.LEFT, padx=5)

    def select_all(self):
        for var in self.or_vars.values():
            var.set(True)
        self.refresh_deploy_summary()

    def select_none(self):
        for var in self.or_vars.values():
            var.set(False)
        self.refresh_deploy_summary()

    def toggle_ssh_password(self):
        if self.ssh_show_var.get():
            self.ssh_pass_entry.config(show="")
        else:
            self.ssh_pass_entry.config(show="*")
    
    def toggle_sudo_password(self):
        if self.sudo_show_var.get():
            self.sudo_pass_entry.config(show="")
        else:
            self.sudo_pass_entry.config(show="*")

    def browse_swu(self):
        swu_file = filedialog.askopenfilename(
            title="Select SWU File",
            filetypes=[("SWU files", "*.swu"), ("All files", "*.*")]
        )
        if swu_file:
            self.swu_file_var.set(swu_file)

    def extract_swu(self):
        swu_file = self.swu_file_var.get()
        if not swu_file:
            self.log("Please select a SWU file first.")
            return

        self.extract_button.config(state='disabled')
        self.cancel_button.config(state='normal')
        self.cancel_requested = False

        if not self.extracting:
            self.extracting = True

        thread = threading.Thread(target=self._extract_swu_thread, args=(swu_file,))
        thread.daemon = True
        thread.start()

    def cancel_extraction(self):
        if self.extracting:
            self.cancel_requested = True
            self.log("Cancellation requested...")
            self.cancel_button.config(state='disabled')

    def _extract_swu_thread(self, swu_file):
        script_dir = BASE_DIR
        extract_dir = script_dir / "swu-extracted"

        class GuiLogger:
            def __init__(self, gui):
                self.gui = gui

            def line(self, message: str):
                self.gui.log(message)

        self.log(f"Extracting SWU file: {swu_file}")
        self.log(f"To: {extract_dir}")

        try:
            swu_path = Path(swu_file)
            if not swu_path.exists():
                self.log(f"ERROR: SWU file does not exist: {swu_file}")
                return

            backend_path = extract_swu_package(swu_path, extract_dir, GuiLogger(self))
            script_dir = BASE_DIR
            self.backend_dist_var.set(str(backend_path))
            self.log("✅ SWU extraction complete")
            self.update_status_indicators()
        except Exception as e:
            self.log(f"ERROR: Failed to extract SWU: {e}")
            import traceback
            self.log(f"Traceback: {traceback.format_exc()}")
            if self.cancel_requested:
                self.log("Extraction cancelled, cleaning up...")
                extraction_folders = sorted(extract_dir.glob("extraction_*"), reverse=True)
                if extraction_folders:
                    try:
                        shutil.rmtree(extraction_folders[0])
                        self.log(f"Cleaned up: {extraction_folders[0]}")
                    except Exception as cleanup_error:
                        self.log(f"Cleanup failed: {cleanup_error}")
        finally:
            self._enable_extract_button()

    def _enable_extract_button(self):
        self.extract_button.config(state='normal')
        self.cancel_button.config(state='disabled')
        if self.extracting:
            self.extracting = False
            self.deploy_button.config(state='normal')

    def build_from_source(self):
        self.build_button.config(state='disabled')
        self.deploy_button.config(state='disabled')

        thread = threading.Thread(target=self._build_from_source_thread)
        thread.daemon = True
        thread.start()

    def download_web_from_or(self):
        selected_rooms = [i for i in range(1, 13) if self.or_vars[i].get()]
        if not selected_rooms:
            self.log("ERROR: Please select at least one OR in the 'Select Targets' tab first")
            self.notebook.select(1)
            return

        room = selected_rooms[0]
        self.download_web_button.config(state='disabled')
        self.deploy_button.config(state='disabled')

        thread = threading.Thread(target=self._download_web_from_or_thread, args=(room,))
        thread.daemon = True
        thread.start()

    def _download_web_from_or_thread(self, room: int):
        self.log(f"=== Downloading Web Assets from OR{room} ===")

        import paramiko
        from upgrade_or import extract_asar

        script_dir = BASE_DIR
        work_dir = script_dir / "web-downloaded"
        work_dir.mkdir(parents=True, exist_ok=True)

        local_asar = work_dir / f"or{room}-matrix-app.asar"
        web_dir = work_dir / f"or{room}-web"

        try:
            if local_asar.exists():
                local_asar.unlink()
            if web_dir.exists():
                shutil.rmtree(web_dir)

            port = 200 + room
            self.log(f"Connecting to OR{room} on port {port}...")
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(
                self.router_ip_var.get(),
                port=port,
                username=self.ssh_user_var.get(),
                password=self.ssh_password_var.get(),
                timeout=30
            )

            self.log("Downloading matrix-app.asar...")
            sftp = client.open_sftp()
            sftp.get('/usr/share/matrix-app/matrix-app.asar', str(local_asar))
            sftp.close()
            client.close()

            self.log(f"ASAR downloaded: {local_asar} ({local_asar.stat().st_size:,} bytes)")

            self.log("Extracting web assets...")
            class GuiLogger:
                def __init__(self, gui):
                    self.gui = gui
                def line(self, message: str):
                    self.gui.log(message)

            extract_asar(local_asar, web_dir, GuiLogger(self))

            possible_paths = [
                web_dir / "arthrex-synergy-matrix",
                web_dir / "dist" / "arthrex-synergy-matrix",
            ]

            found_path = None
            for path in possible_paths:
                if path.exists():
                    found_path = path
                    break

            if found_path:
                self.web_assets_var.set(str(found_path))
                self.log(f"✅ Web assets ready: {found_path}")
            else:
                self.log(f"✅ Web assets extracted to: {web_dir}")
                self.log("Please set the web assets path manually if needed")

            self.update_status_indicators()

        except Exception as e:
            self.log(f"ERROR: Failed to download web assets from OR{room}: {e}")
            import traceback
            self.log(f"Traceback: {traceback.format_exc()}")
        finally:
            self._enable_build_button()

    def _build_from_source_thread(self):
        project_root = BASE_DIR.parent
        backend_repo = project_root / "repos" / "matrix-api-linux"
        web_app_dir = project_root / "repos" / "matrix-app-linux"

        self.log("=== Building from Source ===")

        # Build backend dist
        self.log(f"Building backend dist from: {backend_repo}")
        try:
            if not backend_repo.exists():
                self.log(f"ERROR: Backend repo not found: {backend_repo}")
                self._enable_build_button()
                return

            self.log("Pulling latest code from git...")
            result = subprocess.run(
                ["git", "pull"],
                cwd=str(backend_repo),
                capture_output=True,
                text=True,
                timeout=60
            )
            if result.returncode != 0:
                self.log(f"ERROR: git pull failed, aborting build to avoid deploying stale code: {result.stderr}")
                self.log("Resolve the git issue (e.g. uncommitted local changes) in the repo, then try again.")
                self._enable_build_button()
                return
            else:
                self.log(f"✅ Git pull successful")

            npm_path = "C:\\nvm4w\\nodejs\\npm.cmd"
            if not Path(npm_path).exists():
                npm_path = "npm"

            try:
                subprocess.run([npm_path, "--version"], capture_output=True, check=True, timeout=10)
            except (subprocess.CalledProcessError, FileNotFoundError):
                self.log("ERROR: npm is not installed or not in PATH")
                self._enable_build_button()
                return

            self.log("Running: npm install (backend)...")
            result = subprocess.run(
                [npm_path, "install"],
                cwd=str(backend_repo),
                capture_output=True,
                text=True,
                timeout=300
            )
            if result.returncode != 0:
                self.log(f"ERROR: npm install failed: {result.stderr}")
                self._enable_build_button()
                return

            self.log("Running: npm run build (backend)...")
            result = subprocess.run(
                [npm_path, "run", "build"],
                cwd=str(backend_repo),
                capture_output=True,
                text=True,
                timeout=300
            )
            if result.returncode != 0:
                self.log(f"ERROR: npm run build failed: {result.stderr}")
                self._enable_build_button()
                return

            backend_dist = backend_repo / "dist"
            if backend_dist.exists():
                self.backend_dist_var.set(str(backend_dist))
                self.log(f"✅ Backend dist built: {backend_dist}")
            else:
                self.log(f"ERROR: Backend dist not found after build: {backend_dist}")
                self._enable_build_button()
                return

        except subprocess.TimeoutExpired:
            self.log("ERROR: Build timed out")
            self._enable_build_button()
            return
        except Exception as e:
            self.log(f"ERROR: Build failed: {e}")
            self._enable_build_button()
            return

        # Build web assets
        self.log(f"Building web assets from: {web_app_dir}")
        try:
            if not web_app_dir.exists():
                self.log(f"ERROR: Web app directory not found: {web_app_dir}")
                self._enable_build_button()
                return

            self.log("Pulling latest code from git...")
            result = subprocess.run(
                ["git", "pull"],
                cwd=str(web_app_dir),
                capture_output=True,
                text=True,
                timeout=60
            )
            if result.returncode != 0:
                self.log(f"ERROR: git pull failed, aborting build to avoid deploying stale UI: {result.stderr}")
                self.log("Resolve the git issue (e.g. uncommitted local changes) in the repo, then try again.")
                self._enable_build_button()
                return
            else:
                self.log(f"✅ Git pull successful")

            if not (web_app_dir / "angular.json").exists():
                self.log(f"ERROR: angular.json not found in {web_app_dir}")
                self._enable_build_button()
                return

            npm_path = "C:\\nvm4w\\nodejs\\npm.cmd"
            if not Path(npm_path).exists():
                npm_path = "npm"

            self.log("Running: npm install (web assets)...")
            result = subprocess.run(
                [npm_path, "install"],
                cwd=str(web_app_dir),
                capture_output=True,
                text=True,
                timeout=300
            )
            if result.returncode != 0:
                self.log(f"ERROR: npm install failed for web assets: {result.stderr}")
                self._enable_build_button()
                return

            self.log("Running: npm run build (web assets)...")
            result = subprocess.run(
                [npm_path, "run", "build"],
                cwd=str(web_app_dir),
                capture_output=True,
                text=True,
                timeout=300
            )
            if result.returncode != 0:
                self.log(f"ERROR: ng build failed: {result.stderr}")
                self._enable_build_button()
                return

            web_dist = web_app_dir / "dist" / "arthrex-synergy-matrix"
            if web_dist.exists():
                self.web_assets_var.set(str(web_dist))
                self.log(f"✅ Web assets built: {web_dist}")
            else:
                dist_contents = list((web_app_dir / "dist").iterdir())
                if dist_contents:
                    self.log(f"✅ Web assets built: {web_app_dir / 'dist'}")
                    self.web_assets_var.set(str(web_app_dir / "dist"))
                else:
                    self.log(f"WARNING: Web assets dist not found at expected location: {web_dist}")

            self.update_status_indicators()

        except subprocess.TimeoutExpired:
            self.log("ERROR: Web assets build timed out")
            self._enable_build_button()
            return
        except Exception as e:
            self.log(f"ERROR: Web assets build failed: {e}")
            self._enable_build_button()
            return

        self.log("=== Build Complete ===")
        self.log("Both backend dist and web assets are ready for deployment!")
        self._enable_build_button()

    def _enable_build_button(self):
        self.build_button.config(state='normal')
        self.download_web_button.config(state='normal')
        self.deploy_button.config(state='normal')

    def update_status_indicators(self):
        script_dir = BASE_DIR
        backend = Path(self.backend_dist_var.get())
        web = Path(self.web_assets_var.get())

        backend_exists = backend.exists()
        web_exists = web.exists()

        self.backend_status_var.set(f"Backend: {'READY' if backend_exists else 'not ready'} ({backend})")
        self.web_status_var.set(f"Web assets: {'READY' if web_exists else 'not ready'} ({web})")

        backend_version = get_backend_version(backend) if backend_exists else None
        web_version = get_web_version(web) if web_exists else None

        self.backend_version_var.set(f"Backend version: {backend_version or 'unknown'}")
        self.web_version_var.set(f"Web version: {web_version or 'unknown'}")

        if backend_exists and web_exists and backend_version and web_version:
            try:
                backend_major = backend_version.split(".")[0]
                web_major = web_version.split(".")[0]
                if backend_major != web_major:
                    self.version_status_var.set(
                        f"⚠️ Version mismatch: backend {backend_version} vs web {web_version}"
                    )
                else:
                    self.version_status_var.set(
                        f"✅ Versions compatible: backend {backend_version}, web {web_version}"
                    )
            except Exception:
                self.version_status_var.set("Version check: could not compare")
        else:
            self.version_status_var.set("Version check: waiting for both artifacts")

    def refresh_deploy_summary(self):
        selected_ors = [i for i, var in self.or_vars.items() if var.get()]
        backend = self.backend_dist_var.get()
        web = self.web_assets_var.get()

        summary = f"Selected ORs: {selected_ors}\n"
        summary += f"Backend dist: {backend}\n"
        summary += f"Web assets: {web}\n"
        summary += f"Status: {'Ready' if selected_ors and Path(backend).exists() and Path(web).exists() else 'Not ready - check source and targets'}"

        self.deploy_summary_var.set(summary)
        self.update_status_indicators()

    def log(self, message: str):
        self.log_output.config(state='normal')
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_output.insert(tk.END, f"[{timestamp}] {message}\n")
        self.log_output.see(tk.END)
        self.log_output.config(state='disabled')
        self.root.update()

    def start_deployment(self):
        if self.extracting:
            self.log("Extraction in progress. Please wait until it completes before deploying.")
            return

        selected_ors = [i for i, var in self.or_vars.items() if var.get()]
        if not selected_ors:
            self.log("No ORs selected!")
            self.notebook.select(1)
            return

        local_dist = Path(self.backend_dist_var.get())
        local_web = Path(self.web_assets_var.get())

        if not local_dist.exists():
            self.log(f"ERROR: Backend dist not found: {local_dist}")
            self.log("Please extract the SWU in the 'Prepare Source' tab")
            self.notebook.select(0)
            return

        if not local_web.exists():
            self.log(f"ERROR: Web assets not found: {local_web}")
            self.log("Please build or download web assets in the 'Prepare Source' tab")
            self.notebook.select(0)
            return

        self.log("Running version compatibility check...")

        class _GuiLogger:
            def __init__(self, gui):
                self.gui = gui
            def line(self, message: str):
                self.gui.log(message)

        is_safe, message = check_version_compatibility(local_dist, local_web, _GuiLogger(self))
        if not is_safe:
            self.log(f"⚠️ {message}")
            self.log("Deploying incompatible versions may cause runtime errors.")
            # Allow the user to continue; the mismatch is a warning, not a hard block.

        self.deploy_button.config(state='disabled')
        self.log(f"Starting deployment to {len(selected_ors)} ORs: {selected_ors}")

        thread = threading.Thread(target=self.run_deployment, args=(selected_ors,))
        thread.daemon = True
        thread.start()

    def run_deployment(self, rooms: List[int]):
        self.config = RuntimeConfig(
            router_ip=self.router_ip_var.get(),
            ssh_user=self.ssh_user_var.get(),
            ssh_password=self.ssh_password_var.get(),
            sudo_password=self.sudo_password_var.get(),
        )

        if not self.config.ssh_password or not self.config.sudo_password:
            self.log("ERROR: SSH Password and Sudo Password are required!")
            self.deploy_button.config(state='normal')
            return

        local_dist = Path(self.backend_dist_var.get())
        local_web = Path(self.web_assets_var.get())

        class GUILogger:
            def __init__(self, gui):
                self.gui = gui

            def line(self, message: str):
                self.gui.log(message)

        logger = GUILogger(self)

        # Re-run version check before each deployment
        check_version_compatibility(local_dist, local_web, logger)
        results = []
        total = len(rooms)

        for idx, room in enumerate(rooms, 1):
            self.status_var.set(f"Deploying to OR{room} ({idx}/{total})")
            self.root.update()

            logger.line(f"\n[{idx}/{total}] Processing OR{room}")
            success = deploy_or(room, self.config, local_dist, local_web, logger, dry_run=False)
            results.append((room, success))

            if success:
                logger.line(f"✅ OR{room} - Deployment SUCCESS")
            else:
                logger.line(f"❌ OR{room} - Deployment FAILED")

        self.log("\n=== Summary ===")
        for room, success in results:
            status = "SUCCESS" if success else "FAILED"
            self.log(f"OR{room}: {status}")

        successful = sum(1 for _, s in results if s)
        self.log(f"\nTotal: {successful}/{total} successful")

        self.status_var.set("Deployment complete")
        self.deploy_button.config(state='normal')


def main():
    root = tk.Tk()
    app = ORDeploymentGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
