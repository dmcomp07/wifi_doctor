import os
import re
import threading
import time
import webbrowser
from typing import List, Dict, Optional, Any, Tuple
import tkinter as tk
from tkinter import (
    Text, END, BOTH, LEFT, RIGHT, X, Y, TOP, BOTTOM,
    WORD, VERTICAL, W, E,
    filedialog, messagebox,
)
from tkinter import ttk

from wifi_doctor.core.engine import WifiDoctor
from wifi_doctor.core.router import RouterManager
from wifi_doctor.core.constants import APP_NAME, APP_VERSION, SEV_COLORS, STATUS_COLORS
from wifi_doctor.utils.shell import run_cmd, is_windows

class WifiDoctorApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.doctor = WifiDoctor()
        self._busy = False
        self._ping_running = False
        self._ping_thread: Optional[threading.Thread] = None
        self._drv_url: Optional[str] = None
        self._router_mgr: Optional[RouterManager] = None
        self._adapters: List[Dict[str, str]] = []
        self._adapter_var = tk.StringVar()

        root.title(f"{APP_NAME} v{APP_VERSION}")
        root.geometry("900x680")
        root.minsize(760, 540)
        root.configure(bg="#1e2736")

        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TNotebook", background="#1e2736", borderwidth=0)
        style.configure("TNotebook.Tab", padding=[14, 6], font=("Segoe UI", 10))
        style.configure("TFrame", background="#1e2736")
        style.configure("TLabel", background="#1e2736", foreground="#ecf0f1", font=("Segoe UI", 10))
        style.configure("Header.TLabel", font=("Segoe UI", 12, "bold"), foreground="#3498db")
        style.configure("TButton", font=("Segoe UI", 10), padding=[10, 5])
        style.configure("TProgressbar", troughcolor="#2c3e50", background="#3498db", thickness=6)
        style.configure("TCombobox", fieldbackground="#1e2736", background="#2c3e50", foreground="#ecf0f1")

        self._build_status_bar()
        self._build_notebook()
        self._build_bottom_bar()

        self._update_status("idle", f"{APP_NAME} v{APP_VERSION} — ready")
        self.root.after(400, self._initial_refresh)

    # ---------------------------------------------------------------- layout builders
    def _build_status_bar(self):
        self.status_frame = tk.Frame(self.root, bg="#2c3e50", height=52)
        self.status_frame.pack(fill=X, side=TOP)
        self.status_frame.pack_propagate(False)

        self.status_icon = tk.Label(self.status_frame, text="●", font=("Segoe UI", 20),
                                    bg="#2c3e50", fg="#bdc3c7")
        self.status_icon.pack(side=LEFT, padx=(14, 6))

        text_col = tk.Frame(self.status_frame, bg="#2c3e50")
        text_col.pack(side=LEFT, fill=Y, expand=True)

        self.status_title = tk.Label(text_col, text="WiFi Doctor", font=("Segoe UI", 13, "bold"),
                                     bg="#2c3e50", fg="#ecf0f1", anchor=W)
        self.status_title.pack(fill=X)
        self.status_sub = tk.Label(text_col, text="Starting…", font=("Segoe UI", 9),
                                   bg="#2c3e50", fg="#95a5a6", anchor=W)
        self.status_sub.pack(fill=X)

        # Adapter selector
        adapter_frame = tk.Frame(self.status_frame, bg="#2c3e50")
        adapter_frame.pack(side=RIGHT, padx=14)
        
        tk.Label(adapter_frame, text="Adapter:", font=("Segoe UI", 9),
                 bg="#2c3e50", fg="#95a5a6").pack(side=LEFT, padx=(0, 6))
        
        self._adapter_cb = ttk.Combobox(adapter_frame, textvariable=self._adapter_var, 
                                        state="readonly", width=30)
        self._adapter_cb.pack(side=LEFT)
        self._adapter_cb.bind("<<ComboboxSelected>>", lambda e: self._auto_quick_scan())

        self.progress = ttk.Progressbar(self.root, mode="indeterminate", style="TProgressbar")
        self.progress.pack(fill=X, side=TOP)

    def _build_notebook(self):
        self.nb = ttk.Notebook(self.root)
        self.nb.pack(fill=BOTH, expand=True, padx=8, pady=(4, 0))

        self._build_overview_tab()
        self._build_diag_tab()
        self._build_fixes_tab()
        self._build_speedtest_tab()
        self._build_scan_tab()
        self._build_ping_tab()
        self._build_router_tab()

    def _build_bottom_bar(self):
        bar = tk.Frame(self.root, bg="#151d27", height=28)
        bar.pack(fill=X, side=BOTTOM)
        bar.pack_propagate(False)
        self._bottom_lbl = tk.Label(bar, text="Ready", font=("Segoe UI", 8),
                                    bg="#151d27", fg="#7f8c8d", anchor=W)
        self._bottom_lbl.pack(fill=X, padx=8)

    # ---------------------------------------------------------------- tab: overview
    def _build_overview_tab(self):
        f = tk.Frame(self.nb, bg="#1e2736")
        self.nb.add(f, text=" Overview ")

        card = tk.Frame(f, bg="#2c3e50", relief="flat", bd=0)
        card.pack(fill=X, padx=14, pady=10)

        hdr = tk.Label(card, text="Current Connection", font=("Segoe UI", 11, "bold"),
                       bg="#2c3e50", fg="#3498db", anchor=W)
        hdr.pack(fill=X, padx=10, pady=(8, 2))

        self._info_vars = {}
        fields = [
            ("SSID", "ssid"), ("Band", "band"), ("Channel", "channel"),
            ("Signal", "signal"), ("Radio Type", "radio type"),
            ("Rx Rate", "receive rate (mbps)"), ("Tx Rate", "transmit rate (mbps)"),
            ("State", "state"),
        ]
        grid = tk.Frame(card, bg="#2c3e50")
        grid.pack(fill=X, padx=10, pady=(0, 8))
        for row, (label, key) in enumerate(fields):
            tk.Label(grid, text=label + ":", font=("Segoe UI", 9, "bold"),
                     bg="#2c3e50", fg="#95a5a6", width=14, anchor=E).grid(
                row=row, column=0, sticky=E, padx=(0, 6), pady=1)
            var = tk.StringVar(value="—")
            self._info_vars[key] = var
            tk.Label(grid, textvariable=var, font=("Segoe UI", 9),
                     bg="#2c3e50", fg="#ecf0f1", anchor=W).grid(
                row=row, column=1, sticky=W, pady=1)

        btn_row = tk.Frame(f, bg="#1e2736")
        btn_row.pack(fill=X, padx=14, pady=4)

        ttk.Button(btn_row, text="Run Full Diagnostics",
                   command=self._run_diagnostics).pack(side=LEFT, padx=(0, 6))
        ttk.Button(btn_row, text="Apply Fixes + Retest",
                   command=self._apply_and_retest).pack(side=LEFT, padx=(0, 6))
        ttk.Button(btn_row, text="Refresh",
                   command=self._auto_quick_scan).pack(side=LEFT)

        # findings summary
        sum_hdr = tk.Label(f, text="Findings Summary", font=("Segoe UI", 11, "bold"),
                           bg="#1e2736", fg="#3498db", anchor=W)
        sum_hdr.pack(fill=X, padx=14, pady=(8, 2))

        self._findings_frame = tk.Frame(f, bg="#1e2736")
        self._findings_frame.pack(fill=BOTH, expand=True, padx=14, pady=(0, 4))
        tk.Label(self._findings_frame, text="Run diagnostics to see findings.",
                 font=("Segoe UI", 9), bg="#1e2736", fg="#7f8c8d").pack(anchor=W)

    # ---------------------------------------------------------------- tab: diagnostics
    def _build_diag_tab(self):
        f = tk.Frame(self.nb, bg="#1e2736")
        self.nb.add(f, text=" Diagnostics ")

        self.diag_text = self._make_log(f)

    # ---------------------------------------------------------------- tab: fixes
    def _build_fixes_tab(self):
        f = tk.Frame(self.nb, bg="#1e2736")
        self.nb.add(f, text=" Fixes ")

        info = tk.Label(f, text=(
            "These fixes are safe, reversible client-side tweaks applied to the Wi-Fi adapter and power settings.\n"
            "A network reset can also fix corrupted TCP/IP stack issues."
        ), font=("Segoe UI", 9), bg="#1e2736", fg="#95a5a6", wraplength=820, justify=LEFT, anchor=W)
        info.pack(fill=X, padx=14, pady=(10, 4))

        btn_row = tk.Frame(f, bg="#1e2736")
        btn_row.pack(fill=X, padx=14, pady=4)
        ttk.Button(btn_row, text="Apply Adapter Fixes",
                   command=self._apply_fixes_only).pack(side=LEFT, padx=(0, 6))
        ttk.Button(btn_row, text="Rollback Fixes",
                   command=self._rollback_fixes).pack(side=LEFT, padx=(0, 6))
        ttk.Button(btn_row, text="Network Stack Reset",
                   command=self._network_reset).pack(side=LEFT, padx=(0, 6))
        ttk.Button(btn_row, text="Forget Wi-Fi Profile…",
                   command=self._forget_profile_dialog).pack(side=LEFT)

        self.fixes_text = self._make_log(f, height=8)

        # ── Driver Update ─────────────────────────────────────────────────
        ttk.Separator(f, orient="horizontal").pack(fill=X, padx=14, pady=(4, 0))

        tk.Label(f, text="Driver Update", font=("Segoe UI", 11, "bold"),
                 bg="#1e2736", fg="#3498db", anchor=W).pack(fill=X, padx=14, pady=(6, 2))

        self._drv_info_lbl = tk.Label(
            f, text="Run Diagnostics first to populate driver info.",
            font=("Segoe UI", 9), bg="#1e2736", fg="#95a5a6", anchor=W)
        self._drv_info_lbl.pack(fill=X, padx=14)

        drv_btn_row = tk.Frame(f, bg="#1e2736")
        drv_btn_row.pack(fill=X, padx=14, pady=4)
        ttk.Button(drv_btn_row, text="Check Windows Update",
                   command=self._check_driver_update).pack(side=LEFT, padx=(0, 6))
        ttk.Button(drv_btn_row, text="Install Found Updates",
                   command=self._install_driver_update).pack(side=LEFT, padx=(0, 6))
        self._drv_mfr_btn = ttk.Button(drv_btn_row, text="Open Manufacturer Page",
                                        command=self._open_driver_page, state="disabled")
        self._drv_mfr_btn.pack(side=LEFT)

        self.drv_text = self._make_log(f, height=6)

    # ---------------------------------------------------------------- tab: speed test
    def _build_speedtest_tab(self):
        f = tk.Frame(self.nb, bg="#1e2736")
        self.nb.add(f, text=" Speed Test ")

        card = tk.Frame(f, bg="#2c3e50")
        card.pack(fill=X, padx=14, pady=10)

        results_lbl = tk.Label(card, text="Speed Test Results", font=("Segoe UI", 11, "bold"),
                                bg="#2c3e50", fg="#3498db", anchor=W)
        results_lbl.pack(fill=X, padx=10, pady=(8, 4))

        metrics = tk.Frame(card, bg="#2c3e50")
        metrics.pack(fill=X, padx=10, pady=(0, 8))

        self._speed_vars = {}
        for col, (lbl, key, unit, color) in enumerate([
            ("Download", "download_mbps", "Mbps", "#27ae60"),
            ("Upload", "upload_mbps", "Mbps", "#2980b9"),
            ("Ping", "ping_ms", "ms", "#e67e22"),
            ("Packet Loss", "packet_loss", "%", "#e74c3c"),
        ]):
            cell = tk.Frame(metrics, bg="#243447", relief="flat")
            cell.grid(row=0, column=col, padx=6, pady=2, ipadx=10, ipady=6, sticky="nsew")
            metrics.columnconfigure(col, weight=1)
            var = tk.StringVar(value="—")
            self._speed_vars[key] = var
            tk.Label(cell, textvariable=var, font=("Segoe UI", 20, "bold"),
                     bg="#243447", fg=color).pack()
            tk.Label(cell, text=f"{lbl} ({unit})", font=("Segoe UI", 8),
                     bg="#243447", fg="#7f8c8d").pack()

        btn_row = tk.Frame(f, bg="#1e2736")
        btn_row.pack(fill=X, padx=14, pady=4)
        ttk.Button(btn_row, text="Run Speed Test",
                   command=self._run_speedtest).pack(side=LEFT, padx=(0, 6))
        ttk.Button(btn_row, text="Save CSV Report…",
                   command=self._save_csv).pack(side=LEFT, padx=(0, 6))
        ttk.Button(btn_row, text="Save JSON Report…",
                   command=self._save_json).pack(side=LEFT)

        self.speed_text = self._make_log(f)

    # ---------------------------------------------------------------- tab: network scan
    def _build_scan_tab(self):
        f = tk.Frame(self.nb, bg="#1e2736")
        self.nb.add(f, text=" Network Scan ")

        btn_row = tk.Frame(f, bg="#1e2736")
        btn_row.pack(fill=X, padx=14, pady=8)
        ttk.Button(btn_row, text="Scan Nearby Networks",
                   command=self._run_network_scan).pack(side=LEFT, padx=(0, 6))

        self.rec_label = tk.Label(f, text="", font=("Segoe UI", 10),
                                  bg="#1e2736", fg="#2ecc71", anchor=W)
        self.rec_label.pack(fill=X, padx=14, pady=(0, 4))

        cols = ("SSID", "Channel", "Signal", "Radio", "Auth")
        self.scan_tree = ttk.Treeview(f, columns=cols, show="headings", height=10)
        widths = [280, 80, 80, 120, 120]
        for col, w in zip(cols, widths):
            self.scan_tree.heading(col, text=col)
            self.scan_tree.column(col, width=w, anchor=W)
        vsb = ttk.Scrollbar(f, orient=VERTICAL, command=self.scan_tree.yview)
        self.scan_tree.configure(yscrollcommand=vsb.set)
        self.scan_tree.pack(fill=BOTH, expand=True, padx=14, side=LEFT)
        vsb.pack(side=RIGHT, fill=Y, pady=14, padx=(0, 14))

    # ---------------------------------------------------------------- tab: router config
    def _build_router_tab(self):
        f = tk.Frame(self.nb, bg="#1e2736")
        self.nb.add(f, text=" Router ")

        card = tk.Frame(f, bg="#2c3e50")
        card.pack(fill=X, padx=14, pady=10)
        tk.Label(card, text="Router Connection", font=("Segoe UI", 11, "bold"),
                 bg="#2c3e50", fg="#3498db", anchor=W).pack(fill=X, padx=10, pady=(8, 4))

        form = tk.Frame(card, bg="#2c3e50")
        form.pack(fill=X, padx=10, pady=(0, 8))

        self._router_ip_var   = tk.StringVar(value="")
        self._router_user_var = tk.StringVar(value="admin")
        self._router_pass_var = tk.StringVar(value="")

        for row, (lbl, var, show) in enumerate([
            ("Router IP:",  self._router_ip_var,   ""),
            ("Username:",   self._router_user_var,  ""),
            ("Password:",   self._router_pass_var,  "*"),
        ]):
            tk.Label(form, text=lbl, font=("Segoe UI", 9, "bold"),
                     bg="#2c3e50", fg="#95a5a6", width=12, anchor=E).grid(
                row=row, column=0, padx=(0, 6), pady=2, sticky=E)
            tk.Entry(form, textvariable=var, show=show, width=26,
                     bg="#1e2736", fg="#ecf0f1", insertbackground="white",
                     relief="flat", font=("Segoe UI", 9)).grid(
                row=row, column=1, pady=2, sticky=W)

        btn_row = tk.Frame(f, bg="#1e2736")
        btn_row.pack(fill=X, padx=14, pady=4)
        ttk.Button(btn_row, text="Connect & Detect",
                   command=self._router_connect).pack(side=LEFT, padx=(0, 6))
        ttk.Button(btn_row, text="Apply Best Channel",
                   command=self._router_apply_channel).pack(side=LEFT, padx=(0, 6))
        ttk.Button(btn_row, text="Show Manual Instructions",
                   command=self._router_show_instructions).pack(side=LEFT)

        self.router_text = self._make_log(f)
        self.root.after(800, self._router_auto_fill_ip)

    # ---------------------------------------------------------------- tab: live ping
    def _build_ping_tab(self):
        f = tk.Frame(self.nb, bg="#1e2736")
        self.nb.add(f, text=" Live Ping ")

        top = tk.Frame(f, bg="#1e2736")
        top.pack(fill=X, padx=14, pady=8)
        tk.Label(top, text="Target:", font=("Segoe UI", 9),
                 bg="#1e2736", fg="#95a5a6").pack(side=LEFT)
        self.ping_target_var = tk.StringVar(value="8.8.8.8")
        tk.Entry(top, textvariable=self.ping_target_var, width=18,
                 bg="#2c3e50", fg="#ecf0f1", insertbackground="white",
                 relief="flat", font=("Segoe UI", 9)).pack(side=LEFT, padx=6)
        self.ping_btn = ttk.Button(top, text="Start Ping", command=self._toggle_ping)
        self.ping_btn.pack(side=LEFT, padx=(0, 6))

        self.ping_stats = tk.Label(top, text="", font=("Segoe UI", 9),
                                   bg="#1e2736", fg="#2ecc71")
        self.ping_stats.pack(side=LEFT, padx=8)

        self.ping_text = self._make_log(f, height=22)

    # ---------------------------------------------------------------- helpers
    def _make_log(self, parent, height=14):
        frame = tk.Frame(parent, bg="#1e2736")
        frame.pack(fill=BOTH, expand=True, padx=14, pady=(0, 6))
        txt = Text(frame, bg="#151d27", fg="#ecf0f1", insertbackground="white",
                   font=("Consolas", 9), wrap=WORD, height=height, relief="flat",
                   selectbackground="#2980b9")
        vsb = ttk.Scrollbar(frame, orient=VERTICAL, command=txt.yview)
        txt.configure(yscrollcommand=vsb.set)
        txt.pack(side=LEFT, fill=BOTH, expand=True)
        vsb.pack(side=RIGHT, fill=Y)
        txt.tag_configure("high", foreground="#e74c3c")
        txt.tag_configure("medium", foreground="#e67e22")
        txt.tag_configure("low", foreground="#27ae60")
        txt.tag_configure("info", foreground="#3498db")
        txt.tag_configure("ok", foreground="#2ecc71")
        txt.tag_configure("header", foreground="#3498db", font=("Consolas", 9, "bold"))
        txt.tag_configure("sub", foreground="#95a5a6")
        return txt

    def _log(self, widget, text, tag=None):
        widget.configure(state="normal")
        if tag:
            widget.insert(END, text, tag)
        else:
            widget.insert(END, text)
        widget.see(END)
        widget.configure(state="disabled")

    def _clear(self, widget):
        widget.configure(state="normal")
        widget.delete("1.0", END)
        widget.configure(state="disabled")

    def _update_status(self, state, title, sub=""):
        bg, fg = STATUS_COLORS.get(state, STATUS_COLORS["idle"])
        icon = {"idle": "●", "scanning": "⟳", "issue": "✖", "fixing": "⚙",
                "resolving": "⟳", "good": "✔", "done": "✔"}.get(state, "●")
        self.status_frame.configure(bg=bg)
        self.status_icon.configure(bg=bg, fg=fg, text=icon)
        self.status_title.configure(bg=bg, fg=fg, text=title)
        self.status_sub.configure(bg=bg, fg=fg, text=sub)
        self._bottom_lbl.configure(text=sub or title)

    def _set_busy(self, busy: bool, progress: bool = True) -> None:
        self._busy = busy
        state = "disabled" if busy else "normal"
        
        # Update all buttons that should be disabled when busy
        for btn in self._get_all_buttons():
            btn.configure(state=state)
        
        # Also disable adapter selection
        self._adapter_cb.configure(state="disabled" if busy else "readonly")

        if busy and progress:
            self.progress.start(12)
        else:
            self.progress.stop()

    def _get_all_buttons(self) -> List[ttk.Button]:
        """Recursively find all ttk.Buttons in the application."""
        btns = []
        def find_btns(parent):
            for child in parent.winfo_children():
                if isinstance(child, ttk.Button):
                    btns.append(child)
                elif child.winfo_children():
                    find_btns(child)
        find_btns(self.root)
        return btns

    def _run_in_thread(self, fn):
        if self._busy:
            messagebox.showwarning("Busy", "Another operation is already running.")
            return
        t = threading.Thread(target=fn, daemon=True)
        t.start()

    def _refresh_info_card(self, iface):
        if not iface:
            for var in self._info_vars.values():
                var.set("—")
            return
        self._info_vars["ssid"].set(iface.get("ssid") or "Not connected")
        self._info_vars["band"].set(iface.get("band", "—"))
        self._info_vars["channel"].set(iface.get("channel", "—"))
        sig = iface.get("signal", "—")
        self._info_vars["signal"].set(sig)
        self._info_vars["radio type"].set(iface.get("radio type", "—"))
        self._info_vars["receive rate (mbps)"].set(
            f'{iface.get("receive rate (mbps)", "—")} Mbps')
        self._info_vars["transmit rate (mbps)"].set(
            f'{iface.get("transmit rate (mbps)", "—")} Mbps')
        self._info_vars["state"].set(iface.get("state", "—"))

    def _refresh_findings_summary(self, findings):
        for w in self._findings_frame.winfo_children():
            w.destroy()
        if not findings:
            tk.Label(self._findings_frame, text="No findings.", font=("Segoe UI", 9),
                     bg="#1e2736", fg="#7f8c8d").pack(anchor=W)
            return
        for f in findings:
            color = SEV_COLORS.get(f.severity, "#ecf0f1")
            row = tk.Frame(self._findings_frame, bg="#243447")
            row.pack(fill=X, pady=2)
            tk.Label(row, text=f"  [{f.severity.upper()}]", font=("Segoe UI", 8, "bold"),
                     bg="#243447", fg=color, width=10, anchor=W).pack(side=LEFT)
            tk.Label(row, text=f.title, font=("Segoe UI", 9),
                     bg="#243447", fg="#ecf0f1", anchor=W).pack(side=LEFT, padx=4)

    def _initial_refresh(self) -> None:
        """Initial load of adapters and quick scan."""
        def worker():
            self.root.after(0, lambda: self._update_status("scanning", "Detecting adapters…"))
            self.root.after(0, lambda: self._set_busy(True))
            
            adapters = self.doctor.get_wifi_adapters()
            self._adapters = adapters
            
            names = [a["Name"] for a in adapters]
            self.root.after(0, lambda: self._adapter_cb.configure(values=names))
            if names:
                self.root.after(0, lambda: self._adapter_var.set(names[0]))
            
            self.root.after(0, lambda: self._set_busy(False))
            self.root.after(0, self._auto_quick_scan)
        
        self._run_in_thread(worker)

    def _auto_quick_scan(self):
        def worker():
            adapter = self._adapter_var.get()
            self.root.after(0, lambda: self._update_status("scanning", "Scanning Wi-Fi…", f"Reading {adapter} info"))
            self.root.after(0, lambda: self._set_busy(True))
            data = self.doctor.collect(adapter_name=adapter)
            iface = data["wifi_interfaces"][0] if data["wifi_interfaces"] else None
            self.root.after(0, lambda: self._refresh_info_card(iface))
            self.root.after(0, lambda: self._set_busy(False))
            self.root.after(0, lambda: self._update_status(
                "idle", "Ready", f"Connected: {iface.get('ssid', 'unknown') if iface else 'no adapter found'}"
            ))
        self._run_in_thread(worker)

    def _run_diagnostics(self):
        def worker():
            adapter = self._adapter_var.get()
            self.root.after(0, lambda: self._update_status("scanning", "Running diagnostics…", f"Collecting data for {adapter}"))
            self.root.after(0, lambda: self._set_busy(True))
            self.root.after(0, lambda: self._clear(self.diag_text))
            self.root.after(0, lambda: self.nb.select(1))

            data = self.doctor.collect(adapter_name=adapter)
            iface = data["wifi_interfaces"][0] if data["wifi_interfaces"] else {}

            self.root.after(0, lambda: self._refresh_info_card(iface))

            def _log(t, tag=None):
                self.root.after(0, lambda: self._log(self.diag_text, t, tag))

            _log("=" * 60 + "\n", "header")
            _log(f"  WiFi Doctor v{APP_VERSION} — Diagnostic Report\n", "header")
            _log(f"  {data.get('timestamp', '')}   Host: {data.get('hostname', '')}\n", "sub")
            _log("=" * 60 + "\n\n", "header")

            _log("── Connection ──────────────────────────\n", "header")
            if iface:
                _log(f"  SSID      : {iface.get('ssid', '—')}\n")
                _log(f"  Band      : {iface.get('band', '—')}\n")
                _log(f"  Channel   : {iface.get('channel', '—')}\n")
                _log(f"  Signal    : {iface.get('signal', '—')}\n")
                _log(f"  Rx Rate   : {iface.get('receive rate (mbps)', '—')} Mbps\n")
                _log(f"  Tx Rate   : {iface.get('transmit rate (mbps)', '—')} Mbps\n")
            else:
                _log("  No Wi-Fi interface detected.\n", "high")

            gp = data.get("ping_gateway")
            if isinstance(gp, dict):
                _log("\n── Gateway Ping (IPv4) ─────────────────\n", "header")
                _log(f"  Target     : {gp.get('target', '—')}\n")
                loss = gp.get("packet_loss_percent")
                avg  = gp.get("avg_ms")
                loss_tag = "high" if (loss or 0) > 10 else "ok"
                avg_tag  = "high" if (avg or 0) > 50 else "ok"
                _log(f"  Avg latency: {avg} ms\n", avg_tag)
                _log(f"  Packet loss: {loss}%\n", loss_tag)

            gp6 = data.get("ping_gateway_v6")
            if isinstance(gp6, dict):
                _log("\n── Gateway Ping (IPv6) ─────────────────\n", "header")
                _log(f"  Target     : {gp6.get('target', '—')}\n")
                loss = gp6.get("packet_loss_percent")
                avg  = gp6.get("avg_ms")
                loss_tag = "high" if (loss or 0) > 10 else "ok"
                avg_tag  = "high" if (avg or 0) > 50 else "ok"
                _log(f"  Avg latency: {avg} ms\n", avg_tag)
                _log(f"  Packet loss: {loss}%\n", loss_tag)

            _log("\n── Findings ────────────────────────────\n", "header")
            findings = self.doctor.analyze(data)
            self.root.after(0, lambda: self._refresh_findings_summary(findings))

            has_high = any(f.severity == "high" for f in findings)
            for finding in findings:
                tag = finding.severity
                _log(f"\n  [{finding.severity.upper()}] {finding.title}\n", tag)
                _log(f"  Details: {finding.details}\n", "sub")
                _log(f"  Fix    : {finding.recommendation}\n")

            _log("\n── Recommendations ─────────────────────\n", "header")
            for r in self.doctor.recommend_changes(data):
                _log(f"  • {r}\n")

            _log("\n── Saved Profiles ──────────────────────\n", "header")
            profiles = data.get("profiles", [])
            if profiles:
                for p in profiles:
                    _log(f"  • {p}\n")
            else:
                _log("  No saved profiles found.\n", "sub")

            _log("\n" + "=" * 60 + "\n", "header")

            # Refresh driver info label
            drv = data.get("driver_info", {})
            if isinstance(drv, dict) and drv.get("DriverVersion"):
                ver = drv.get("DriverVersion", "")
                desc = drv.get("InterfaceDescription", "")
                m_ts = re.search(r"/Date\((\d+)\)/", str(drv.get("DriverDate", "")))
                date_s = (time.strftime("%Y-%m-%d", time.localtime(int(m_ts.group(1)) / 1000))
                          if m_ts else "date unknown")
                lbl_text = f"{desc or 'Wi-Fi adapter'}   v{ver}   ({date_s})"
                self.root.after(0, lambda t=lbl_text: self._drv_info_lbl.configure(text=t, fg="#ecf0f1"))
                _, url = self.doctor.get_manufacturer_driver_info(desc)
                if url:
                    self._drv_url = url
                    self.root.after(0, lambda: self._drv_mfr_btn.configure(state="normal"))

            status = "issue" if has_high else "done"
            msg = "Issue found — check Findings tab" if has_high else "Diagnostics complete — no critical issues"
            self.root.after(0, lambda: self._update_status(status, msg, data.get("timestamp", "")))
            self.root.after(0, lambda: self._set_busy(False))

        self._run_in_thread(worker)

    def _apply_fixes_only(self):
        def worker():
            adapter = self._adapter_var.get()
            self.root.after(0, lambda: self._update_status("fixing", "Applying fixes…", f"Modifying {adapter} settings"))
            self.root.after(0, lambda: self._set_busy(True))
            self.root.after(0, lambda: self._clear(self.fixes_text))
            self.root.after(0, lambda: self.nb.select(2))

            def progress_cb(msg):
                self.root.after(0, lambda: self._log(self.fixes_text, f"  → {msg}\n", "info"))
                self.root.after(0, lambda: self._update_status("fixing", "Applying fixes…", msg))

            self.root.after(0, lambda: self._log(self.fixes_text, f"Applying fixes for {adapter}…\n\n", "header"))
            results = self.doctor.apply_fixes(adapter_name=adapter, progress_cb=progress_cb)
            for r in results:
                tag = "ok" if r["ok"] else "medium"
                status_str = "✔" if r["ok"] else "✖"
                self.root.after(0, lambda r=r, tag=tag, status_str=status_str:
                    self._log(self.fixes_text, f"  {status_str}  {r['step']}\n", tag))

            self.root.after(0, lambda: self._log(self.fixes_text, "\nDone. Reboot may be needed for some changes.\n", "ok"))
            self.root.after(0, lambda: self._update_status("good", "Fixes applied", "Reboot if prompted"))
            self.root.after(0, lambda: self._set_busy(False))

        self._run_in_thread(worker)

    def _apply_and_retest(self):
        def worker():
            adapter = self._adapter_var.get()
            self.root.after(0, lambda: self._update_status("fixing", "Applying fixes…", f"Targeting {adapter}"))
            self.root.after(0, lambda: self._set_busy(True))
            self.root.after(0, lambda: self._clear(self.fixes_text))
            self.root.after(0, lambda: self.nb.select(2))

            def progress_cb(msg):
                self.root.after(0, lambda: self._log(self.fixes_text, f"  → {msg}\n", "info"))
                self.root.after(0, lambda: self._update_status("fixing", "Applying fixes…", msg))

            self.root.after(0, lambda: self._log(self.fixes_text, f"Applying fixes to {adapter}…\n\n", "header"))
            self.doctor.apply_fixes(adapter_name=adapter, progress_cb=progress_cb)

            self.root.after(0, lambda: self._update_status("resolving", "Resolving…", "Running post-fix tests"))
            self.root.after(0, lambda: self._log(self.fixes_text, "\nRunning post-fix test suite…\n\n", "header"))

            def retest_progress(msg):
                self.root.after(0, lambda: self._log(self.fixes_text, f"  → {msg}\n", "info"))
                self.root.after(0, lambda: self._update_status("resolving", "Resolving…", msg))

            results = self.doctor.run_post_fix_test_suite(progress_cb=retest_progress)

            gp = results.get("gateway_ping", {})
            ip = results.get("internet_ping", {})
            st = results.get("speedtest", {})

            def _log(t, tag=None):
                self.root.after(0, lambda: self._log(self.fixes_text, t, tag))

            _log("\n── Post-Fix Results ────────────────────\n", "header")
            _log(f"  Gateway ping : {gp.get('avg_ms', '—')} ms  loss {gp.get('packet_loss_percent', '—')}%\n")
            _log(f"  Internet ping: {ip.get('avg_ms', '—')} ms  loss {ip.get('packet_loss_percent', '—')}%\n")
            if "error" not in st:
                _log(f"  Download     : {st.get('download_mbps', '—')} Mbps\n")
                _log(f"  Upload       : {st.get('upload_mbps', '—')} Mbps\n")
                _log(f"  Speed test ping: {st.get('ping_ms', '—')} ms\n")

            loss_ok = (gp.get("packet_loss_percent") or 0) < 5 and (ip.get("packet_loss_percent") or 0) < 5
            latency_ok = (gp.get("avg_ms") or 999) < 40
            overall = "good" if (loss_ok and latency_ok) else "issue"
            msg = "Working good — connection looks healthy" if overall == "good" else "Issue found — some metrics still poor"
            _log(f"\n  Status: {msg}\n", overall)

            self.root.after(0, lambda: self._update_status(overall, msg, results.get("timestamp", "")))
            self.root.after(0, lambda: self._set_busy(False))

            self._update_speed_card(st)

        self._run_in_thread(worker)

    def _rollback_fixes(self):
        def worker():
            adapter = self._adapter_var.get()
            self.root.after(0, lambda: self._update_status("fixing", "Rolling back fixes…", f"Restoring {adapter} settings"))
            self.root.after(0, lambda: self._set_busy(True))
            self.root.after(0, lambda: self._clear(self.fixes_text))
            self.root.after(0, lambda: self.nb.select(2))

            def progress_cb(msg):
                self.root.after(0, lambda: self._log(self.fixes_text, f"  → {msg}\n", "info"))

            self.root.after(0, lambda: self._log(self.fixes_text, f"Rolling back fixes for {adapter}…\n\n", "header"))
            results = self.doctor.rollback_fixes(adapter_name=adapter, progress_cb=progress_cb)
            
            if not results or (len(results) == 1 and not results[0]["ok"]):
                msg = results[0]["msg"] if results else "No fixes to rollback."
                self.root.after(0, lambda: self._log(self.fixes_text, f"  ✖  {msg}\n", "medium"))
                self.root.after(0, lambda: self._update_status("idle", "Rollback failed", msg))
            else:
                for r in results:
                    tag = "ok" if r["ok"] else "medium"
                    status_str = "✔" if r["ok"] else "✖"
                    self.root.after(0, lambda r=r, tag=tag, status_str=status_str:
                        self._log(self.fixes_text, f"  {status_str}  {r['step']}\n", tag))

                self.root.after(0, lambda: self._log(self.fixes_text, "\nDone. Original settings restored where possible.\n", "ok"))
                self.root.after(0, lambda: self._update_status("good", "Fixes rolled back", "Restored original settings"))
            
            self.root.after(0, lambda: self._set_busy(False))

        self._run_in_thread(worker)

    def _network_reset(self):
        if not messagebox.askyesno(
            "Network Stack Reset",
            "This will reset Winsock, IP stack, flush DNS, and renew your IP.\n"
            "Your network connection will drop momentarily.\n\nContinue?"
        ):
            return

        def worker():
            self.root.after(0, lambda: self._update_status("fixing", "Network stack reset…", ""))
            self.root.after(0, lambda: self._set_busy(True))
            self.root.after(0, lambda: self._clear(self.fixes_text))
            self.root.after(0, lambda: self.nb.select(2))

            def progress_cb(msg):
                self.root.after(0, lambda: self._log(self.fixes_text, f"  → {msg}\n", "info"))

            self.root.after(0, lambda: self._log(self.fixes_text, "Network stack reset…\n\n", "header"))
            results = self.doctor.network_reset_commands(progress_cb=progress_cb)
            for r in results:
                rc, out, _ = r["result"]
                tag = "ok" if rc == 0 else "medium"
                self.root.after(0, lambda r=r, tag=tag:
                    self._log(self.fixes_text, f"  {r['cmd']}\n", tag))
                if out:
                    self.root.after(0, lambda out=out:
                        self._log(self.fixes_text, f"    {out}\n", "sub"))

            self.root.after(0, lambda: self._log(self.fixes_text,
                "\nNetwork reset complete. Reconnect to Wi-Fi if needed.\n", "ok"))
            self.root.after(0, lambda: self._update_status("good", "Network reset done", ""))
            self.root.after(0, lambda: self._set_busy(False))

        self._run_in_thread(worker)

    def _forget_profile_dialog(self):
        profiles = self.doctor.snapshot.get("profiles") or self.doctor.collect().get("profiles", [])
        if not profiles:
            messagebox.showinfo("No Profiles", "No saved Wi-Fi profiles found.")
            return

        win = tk.Toplevel(self.root)
        win.title("Forget Wi-Fi Profile")
        win.geometry("360x260")
        win.configure(bg="#1e2736")
        win.grab_set()

        tk.Label(win, text="Select a profile to forget:", font=("Segoe UI", 10),
                 bg="#1e2736", fg="#ecf0f1").pack(pady=(12, 4))
        lb = tk.Listbox(win, bg="#2c3e50", fg="#ecf0f1", selectbackground="#2980b9",
                        font=("Segoe UI", 10), height=8, relief="flat")
        lb.pack(fill=BOTH, expand=True, padx=14, pady=4)
        for p in profiles:
            lb.insert(END, p)

        def do_forget():
            sel = lb.curselection()
            if not sel:
                return
            profile = profiles[sel[0]]
            if messagebox.askyesno("Confirm", f"Forget profile '{profile}'?", parent=win):
                rc, out, err = self.doctor.forget_profile(profile)
                if rc == 0:
                    messagebox.showinfo("Done", f"Profile '{profile}' removed.", parent=win)
                    win.destroy()
                else:
                    messagebox.showerror("Error", err or out, parent=win)

        ttk.Button(win, text="Forget Selected", command=do_forget).pack(pady=8)

    def _run_speedtest(self):
        def worker():
            self.root.after(0, lambda: self._update_status("scanning", "Running speed test…", "This may take 30–60 seconds"))
            self.root.after(0, lambda: self._set_busy(True))
            self.root.after(0, lambda: self._clear(self.speed_text))
            self.root.after(0, lambda: self.nb.select(3))
            self.root.after(0, lambda: self._log(self.speed_text, "Preparing speed test…\n", "sub"))

            st = self.doctor.run_speedtest()
            self._update_speed_card(st)

            def _log(t, tag=None):
                self.root.after(0, lambda: self._log(self.speed_text, t, tag))

            if "error" in st and not st.get("fallback"):
                _log(f"\nError: {st['error']}\n", "high")
                _log("Tip: run as Administrator and check Windows Firewall / antivirus.\n", "sub")
                self.root.after(0, lambda: self._update_status("issue", "Speed test failed", ""))
            else:
                if st.get("fallback"):
                    cli_err = st.get("cli_error", "CLI unavailable")
                    _log(f"\n  [Ookla CLI failed ({cli_err}) — using HTTP fallback]\n", "medium")
                    _log("  Upload not measured in fallback mode.\n", "sub")
                _log(f"\n  Download   : {st.get('download_mbps') or '—'} Mbps\n", "ok")
                upload = st.get("upload_mbps")
                _log(f"  Upload     : {upload if upload is not None else 'N/A (fallback)'} Mbps\n",
                     "ok" if upload is not None else "sub")
                _log(f"  Ping       : {st.get('ping_ms', '—')} ms\n")
                _log(f"  Packet loss: {st.get('packet_loss', '—')}%\n")
                _log(f"  Server     : {st.get('server', '—')}\n", "sub")
                _log(f"  ISP        : {st.get('isp') or '—'}\n", "sub")
                self.root.after(0, lambda: self._update_status("good", "Speed test complete", ""))

            self.root.after(0, lambda: self._set_busy(False))

        self._run_in_thread(worker)

    def _update_speed_card(self, st):
        if not st or ("error" in st and not st.get("fallback")):
            return
        def upd():
            self._speed_vars["download_mbps"].set(str(st.get("download_mbps") or "—"))
            upload = st.get("upload_mbps")
            self._speed_vars["upload_mbps"].set(str(upload) if upload is not None else "N/A")
            self._speed_vars["ping_ms"].set(str(st.get("ping_ms") or "—"))
            self._speed_vars["packet_loss"].set(str(st.get("packet_loss") or "0"))
        self.root.after(0, upd)

    def _run_network_scan(self):
        def worker():
            self.root.after(0, lambda: self._update_status("scanning", "Scanning nearby networks…", ""))
            self.root.after(0, lambda: self._set_busy(True))
            for row in self.scan_tree.get_children():
                self.scan_tree.delete(row)

            networks = self.doctor.scan_networks()
            current_channel = None
            if self.doctor.snapshot.get("wifi_interfaces"):
                current_channel = self.doctor.snapshot["wifi_interfaces"][0].get("channel")

            best_ch, ch_load = self.doctor.recommend_channel(networks, current_channel)
            rec_text = (
                f"Recommended channel: {best_ch}   "
                f"(Current: {current_channel or '?'}, "
                f"Channel load: {dict(sorted(ch_load.items()))})"
            )
            self.root.after(0, lambda: self.rec_label.configure(text=rec_text))

            for net in networks:
                row = (
                    net.get("ssid", ""),
                    net.get("channel", ""),
                    net.get("signal", ""),
                    net.get("radio", ""),
                    net.get("auth", ""),
                )
                self.root.after(0, lambda row=row: self.scan_tree.insert("", END, values=row))

            count = len(networks)
            self.root.after(0, lambda: self._update_status(
                "done", f"Scan complete — {count} network(s) found", rec_text))
            self.root.after(0, lambda: self._set_busy(False))

        self._run_in_thread(worker)

    # ---------------------------------------------------------------- live ping
    def _toggle_ping(self):
        if self._ping_running:
            self._ping_running = False
            self.ping_btn.configure(text="Start Ping")
        else:
            self._ping_running = True
            self.ping_btn.configure(text="Stop Ping")
            self._clear(self.ping_text)
            self._ping_thread = threading.Thread(target=self._ping_loop, daemon=True)
            self._ping_thread.start()

    def _ping_loop(self):
        target = self.ping_target_var.get().strip() or "8.8.8.8"
        seq = 0
        latencies = []
        lost = 0
        while self._ping_running:
            seq += 1
            _, out, err = run_cmd(["ping", "-n", "1", "-w", "2000", target], timeout=4)
            txt = out or err
            m = re.search(r"time[=<](\d+)ms", txt)
            if m:
                ms = int(m.group(1))
                latencies.append(ms)
                tag = "high" if ms > 150 else ("medium" if ms > 60 else "ok")
                line = f"  [{seq:>4}]  {target}  reply  {ms} ms\n"
            else:
                lost += 1
                tag = "high"
                line = f"  [{seq:>4}]  {target}  TIMEOUT\n"

            self.root.after(0, lambda line=line, tag=tag: self._log(self.ping_text, line, tag))

            if latencies:
                avg = round(sum(latencies) / len(latencies))
                mn = min(latencies)
                mx = max(latencies)
                loss_pct = round(lost / seq * 100)
                stats = f"avg {avg} ms  min {mn}  max {mx}  loss {loss_pct}%"
                self.root.after(0, lambda stats=stats: self.ping_stats.configure(text=stats))

            time.sleep(1)

        self.root.after(0, lambda: self.ping_stats.configure(
            text=self.ping_stats.cget("text") + "  [stopped]"))

    # ---------------------------------------------------------------- driver update actions
    def _check_driver_update(self):
        def worker():
            self.root.after(0, lambda: self._update_status(
                "scanning", "Checking Windows Update for drivers…", ""))
            self.root.after(0, lambda: self._set_busy(True))
            self.root.after(0, lambda: self._clear(self.drv_text))
            self.root.after(0, lambda: self.nb.select(2))

            def _log(t, tag=None):
                self.root.after(0, lambda: self._log(self.drv_text, t, tag))

            _log("── Driver Update Check ──────────────────\n", "header")
            result = self.doctor.check_driver_updates()
            status = result.get("status", "error")

            if status == "none":
                _log("  No Wi-Fi driver updates found in Windows Update.\n", "ok")
            elif status == "found":
                updates = result.get("updates", [])
                if not isinstance(updates, list):
                    updates = [updates]
                _log(f"  {len(updates)} update(s) available:\n", "info")
                for u in updates:
                    title = u.get("Title") or u.get("title") or str(u)
                    kb = u.get("KB") or u.get("kb") or ""
                    _log(f"    • {title}" + (f"  [KB{kb}]" if kb else "") + "\n")
                _log("\nClick 'Install Found Updates' to download and install.\n", "sub")
            else:
                _log(f"  Query failed: {result.get('error', 'unknown error')}\n", "medium")
                _log("  Tip: run the app as Administrator for Windows Update access.\n", "sub")

            # Enable manufacturer link if not already set from diagnostics
            if not self._drv_url:
                drv = self.doctor.snapshot.get("driver_info", {})
                desc = drv.get("InterfaceDescription", "") if isinstance(drv, dict) else ""
                _, url = self.doctor.get_manufacturer_driver_info(desc)
                if url:
                    self._drv_url = url
                    self.root.after(0, lambda: self._drv_mfr_btn.configure(state="normal"))
                    _log(f"\n  Manufacturer driver page available — click 'Open Manufacturer Page'.\n", "info")

            self.root.after(0, lambda: self._update_status("idle", "Driver check complete", ""))
            self.root.after(0, lambda: self._set_busy(False))

        self._run_in_thread(worker)

    def _install_driver_update(self):
        if not messagebox.askyesno(
            "Install Driver Update",
            "This will download and install Wi-Fi driver updates via Windows Update.\n"
            "Your Wi-Fi may disconnect briefly during installation.\n\nContinue?"
        ):
            return

        def worker():
            self.root.after(0, lambda: self._update_status(
                "fixing", "Installing driver update…", ""))
            self.root.after(0, lambda: self._set_busy(True))
            self.root.after(0, lambda: self._clear(self.drv_text))
            self.root.after(0, lambda: self.nb.select(2))

            def _log(t, tag=None):
                self.root.after(0, lambda: self._log(self.drv_text, t, tag))

            _log("── Installing Driver Update ─────────────\n", "header")

            def progress_cb(msg):
                _log(f"  → {msg}\n", "info")
                self.root.after(0, lambda: self._update_status("fixing", "Installing driver…", msg))

            results = self.doctor.install_driver_via_windows_update(progress_cb=progress_cb)
            for r in results:
                s = r.get("status", "")
                if s == "none":
                    _log("  No updates to install.\n", "sub")
                elif s == "downloading":
                    _log(f"  Downloading {r.get('count', '?')} update(s)…\n", "info")
                elif s == "done":
                    msg = "Driver installed successfully!"
                    if r.get("reboot"):
                        msg += " — Reboot required to finish."
                    _log(f"  {msg}\n", "ok")
                elif s == "error":
                    _log(f"  Error: {r.get('error', 'unknown')}\n", "high")
                    _log("  Tip: run as Administrator for Windows Update access.\n", "sub")

            self.root.after(0, lambda: self._update_status("idle", "Driver update complete", ""))
            self.root.after(0, lambda: self._set_busy(False))

        self._run_in_thread(worker)

    def _open_driver_page(self):
        if self._drv_url:
            webbrowser.open(self._drv_url)

    # ---------------------------------------------------------------- router actions
    def _router_auto_fill_ip(self):
        gw = self.doctor.snapshot.get("default_gateway", "")
        if gw and not self._router_ip_var.get():
            self._router_ip_var.set(gw)

    def _router_connect(self):
        def worker():
            ip   = self._router_ip_var.get().strip()
            user = self._router_user_var.get().strip()
            pwd  = self._router_pass_var.get()
            if not ip:
                self.root.after(0, lambda: messagebox.showwarning(
                    "No IP", "Enter the router IP address first."))
                return

            self.root.after(0, lambda: self._update_status("scanning", "Connecting to router…", ip))
            self.root.after(0, lambda: self._set_busy(True))
            self.root.after(0, lambda: self._clear(self.router_text))

            def _log(t, tag=None):
                self.root.after(0, lambda: self._log(self.router_text, t, tag))

            rm = RouterManager(ip, user, pwd)
            self._router_mgr = rm

            brand, detect_msg = rm.detect_brand()
            _log(f"  {detect_msg}\n", "info" if brand else "high")

            ok, login_msg = rm.login()
            _log(f"  Login: {login_msg}\n", "ok" if ok else "high")

            if ok:
                channels = rm.get_current_channels()
                if channels:
                    _log("\nCurrent Wi-Fi channels:\n", "header")
                    for band, cfg in channels.items():
                        if isinstance(cfg, dict):
                            ch = cfg.get("channel") or cfg.get("wl0_channel") or cfg.get("wl1_channel") or "?"
                            _log(f"  {band.upper()}: channel {ch}\n")
                        elif cfg:
                            _log(f"  {band.upper()}: {cfg}\n", "sub")

                nets = self.doctor.scan_networks()
                if nets:
                    best_2g, _ = self.doctor.recommend_channel(nets, "6")
                    best_5g, _ = self.doctor.recommend_channel(nets, "36")
                    _log(f"\nRecommended channels from scan ({len(nets)} nearby networks):\n", "header")
                    _log(f"  2.4 GHz → channel {best_2g}\n", "ok")
                    _log(f"  5 GHz   → channel {best_5g}\n", "ok")
                    _log("\nClick 'Apply Best Channel' to push these to the router.\n", "sub")

            state = "done" if ok else "issue"
            sub = f"{rm.brand.upper() if rm.brand else 'Router'} at {ip}"
            self.root.after(0, lambda: self._update_status(state, "Router", sub))
            self.root.after(0, lambda: self._set_busy(False))

        self._run_in_thread(worker)

    def _router_apply_channel(self):
        def worker():
            rm = self._router_mgr
            if not rm or not rm.brand:
                self.root.after(0, lambda: messagebox.showwarning(
                    "Not connected", "Click 'Connect & Detect' first."))
                return

            self.root.after(0, lambda: self._update_status("fixing", "Applying router channel…", ""))
            self.root.after(0, lambda: self._set_busy(True))

            def _log(t, tag=None):
                self.root.after(0, lambda: self._log(self.router_text, t, tag))

            nets = self.doctor.scan_networks()
            best_2g, _ = self.doctor.recommend_channel(nets, "6")
            best_5g, _ = self.doctor.recommend_channel(nets, "36")

            _log(f"\n── Applying best channels ───────────────\n", "header")
            for band, ch in [("2g", best_2g), ("5g", best_5g)]:
                ok, msg = rm.apply_channel(band, ch)
                _log(f"  {band.upper()} → channel {ch}: {msg}\n", "ok" if ok else "medium")

            self.root.after(0, lambda: self._update_status("good", "Router config applied", ""))
            self.root.after(0, lambda: self._set_busy(False))

        self._run_in_thread(worker)

    def _router_show_instructions(self):
        rm = self._router_mgr
        if not rm:
            ip = self._router_ip_var.get().strip() or "192.168.1.1"
            rm = RouterManager(ip)
            rm.brand = "generic"

        nets = self.doctor.scan_networks() if self.doctor.snapshot else []
        ch_2g = self.doctor.recommend_channel(nets, "6")[0]  if nets else None
        ch_5g = self.doctor.recommend_channel(nets, "36")[0] if nets else None

        self._clear(self.router_text)
        self._log(self.router_text,
                  rm.manual_instructions(ch_2g=ch_2g, ch_5g=ch_5g) + "\n")

    # ---------------------------------------------------------------- reports
    def _save_csv(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".csv", filetypes=[("CSV files", "*.csv")],
            title="Save CSV Report", initialfile="wifi_report.csv")
        if path:
            try:
                self.doctor.save_csv_report(path)
                messagebox.showinfo("Saved", f"CSV report saved to:\n{path}")
            except Exception as e:
                messagebox.showerror("Error", str(e))

    def _save_json(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".json", filetypes=[("JSON files", "*.json")],
            title="Save JSON Report", initialfile="wifi_report.json")
        if path:
            try:
                self.doctor.save_json_report(path)
                messagebox.showinfo("Saved", f"JSON report saved to:\n{path}")
            except Exception as e:
                messagebox.showerror("Error", str(e))
