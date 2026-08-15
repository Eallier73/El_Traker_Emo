#!/usr/bin/env python3
"""
El Traker – sencillo registro de tiempo estilo pomodoro.
Guarda un archivo por día en logs/YYYY-MM-DD.log con:
- Inicio y fin de jornada
- Inicio y fin de cada pomodoro y descanso
- Notas de jornada y notas por pomodoro
- Ajustes manuales de tiempo (minutos +/-)
- Registro diario de medicinas (mg) + ajustes (mg +/-) + fijar total mg

Muestra estadísticas por día, semana ISO y mes:
- Trabajo (min)
- Medicinas (mg) por medicina
"""
from __future__ import annotations

import os
import sys
import shutil
import subprocess
import tkinter as tk

# Desactivar métodos de entrada complejos que pueden interferir con acentos en Tkinter Linux
os.environ['XMODIFIERS'] = "@im=none"
from tkinter import messagebox, ttk
from datetime import date, datetime
from collections import defaultdict
from typing import Optional

APP_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(APP_DIR, "logs")

# --- Medicinas (mg) ---
MEDS = ["bupropion", "escitalopram", "cafeina", "prednisona", "pregabalina", "modafinilo"]
MED_ALL_OPTION = "todas"

# --- Tema visual ---
COLOR_BG = "#0f1117"
COLOR_PANEL = "#181b22"
COLOR_PANEL_ALT = "#232734"
COLOR_BORDER = "#34394a"
COLOR_TEXT = "#f3f4f7"
COLOR_MUTED = "#a7adbf"
COLOR_ACCENT = "#7c4dff"
COLOR_ACCENT_ACTIVE = "#295dff"
COLOR_ENTRY_BG = "#12151d"
ALARM_SNOOZE_MINUTES = 5


def ensure_log_dir() -> None:
    os.makedirs(LOG_DIR, exist_ok=True)


def log_path_for_today() -> str:
    ensure_log_dir()
    return os.path.join(LOG_DIR, datetime.now().strftime("%Y-%m-%d.log"))


def log_path_for_date(target_date: date) -> str:
    ensure_log_dir()
    return os.path.join(LOG_DIR, target_date.strftime("%Y-%m-%d.log"))


def write_log(line: str, path: Optional[str] = None) -> None:
    if path is None:
        path = log_path_for_today()
    ensure_log_dir()
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def sanitize_note(text: str) -> str:
    """
    Conserva el texto como un solo token loggable.
    - Reemplaza saltos de línea por \\n
    - Reemplaza tabs por espacios
    - Recorta espacios extremos
    """
    text = (text or "").replace("\t", " ").strip()
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\n", "\\n")
    return text


def parse_kv(parts: list[str]) -> dict[str, str]:
    """
    Parseo muy simple de key=value (sin comillas sofisticadas).
    Permite valores con espacios si son el último parámetro (como note=).
    """
    out: dict[str, str] = {}
    for i, p in enumerate(parts):
        if "=" in p:
            k, v = p.split("=", 1)
            # Si es el último parámetro y tiene espacios después, los recogemos
            if k == "note" or k == "reason":
                # Buscamos de nuevo note= en la línea original para evitar problemas con split
                full_line = " ".join(parts)
                if f" {k}=" in full_line:
                    v = full_line.split(f" {k}=", 1)[1]
                elif full_line.startswith(f"{k}="):
                    v = full_line.split(f"{k}=", 1)[1]
                else:
                    v = " ".join(parts[i:]).split("=", 1)[1]
                out[k.strip()] = v.strip()
                break
            if k == "title":
                remainder = " ".join(parts[i:])
                if " note=" in remainder:
                    v = remainder.split("=", 1)[1].split(" note=")[0]
                else:
                    v = remainder.split("=", 1)[1]
                out[k.strip()] = v.strip()
                continue
            out[k.strip()] = v.strip()
    return out


class ElTrakerApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("El Traker")
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self._configure_theme()

        self.pomodoro_minutes_var = tk.StringVar(value="25")
        self.break_minutes_var = tk.StringVar(value="5")

        self.status_var = tk.StringVar(value="Esperando para iniciar la jornada…")
        self.timer_var = tk.StringVar(value="00:00")
        self.clock_var = tk.StringVar(value=datetime.now().strftime("%H:%M:%S"))

        # Ajustes manuales (trabajo)
        self.adjust_delta_minutes_var = tk.StringVar(value="")
        self.adjust_target_today_var = tk.StringVar(value="")
        self.adjust_reason_var = tk.StringVar(value="")
        self.edit_date_var = tk.StringVar(value=datetime.now().strftime("%Y-%m-%d"))

        self.today_total_var = tk.StringVar(value="Trabajo 0.0 min")

        # ---- Medicinas (UI vars) ----
        self.med_vars: dict[str, tk.StringVar] = {m: tk.StringVar(value="") for m in MEDS}
        self.med_today_summary_var = tk.StringVar(value="Medicinas: (sin datos)")

        self.med_adjust_med_var = tk.StringVar(value=MEDS[0])
        self.med_adjust_delta_var = tk.StringVar(value="")
        self.med_adjust_target_var = tk.StringVar(value="")
        self.med_adjust_reason_var = tk.StringVar(value="")

        # Selectores de estadísticas
        self.med_stats_selected_med = tk.StringVar(value=MEDS[0])
        self.stats_scope_var = tk.StringVar(value="Trabajo")
        self.stats_group_var = tk.StringVar(value="Día")

        self.workday_active = False
        self.current_phase: Optional[str] = None  # "pomodoro" | "break" | None
        self.remaining_seconds: int = 0
        self.timer_paused = False
        self.after_id: Optional[str] = None
        self.current_log_path: Optional[str] = None
        self.current_pomo_start: Optional[datetime] = None
        self.current_break_start: Optional[datetime] = None

        self.alarm_active = False
        self.alarm_after_id: Optional[str] = None
        self.alarm_resume_after_id: Optional[str] = None
        self.alarm_process: Optional[subprocess.Popen] = None
        self.alarm_message: str = ""
        self.alarm_message_var = tk.StringVar(value="")
        self.alarm_window: Optional[tk.Toplevel] = None
        self._alarm_player = self._detect_alarm_player()
        self.stats_window: Optional[tk.Toplevel] = None
        self.stats_chart_canvas: Optional[tk.Canvas] = None
        self.stats_summary_text: Optional[tk.Text] = None
        self.stats_note_var = tk.StringVar(value="")
        self.stats_med_combo: Optional[ttk.Combobox] = None
        self._stats_chart_state: Optional[tuple[str, list[tuple[str, float]], str]] = None
        self.journal_title_var = tk.StringVar(value="")
        self.journal_list_items: list[tuple[date, str]] = []

        self._build_ui()
        self._refresh_today_total()
        self._refresh_today_meds_summary()
        self._refresh_journal_view()
        self._update_clock()

    # --------------- Compat: tokens pegados en logs ---------------
    def _clean_ts_token(self, ts_token: str) -> str:
        """
        Corrige tokens tipo:
          2026-02-06T04:16:18.454604amount=10.0
        Dejando solo el ISO timestamp.
        """
        for marker in ("amount=", "duration=", "cancelled=", "forced=", "reason=", "note=", "pomo_start=", "med="):
            ts_token = ts_token.split(marker, 1)[0]
        return ts_token

    # ---------------- UI -----------------
    def _configure_theme(self) -> None:
        self.root.configure(bg=COLOR_BG)

        style = ttk.Style()
        style.theme_use("clam")

        style.configure(".", background=COLOR_BG, foreground=COLOR_TEXT, fieldbackground=COLOR_ENTRY_BG)
        style.configure("TFrame", background=COLOR_BG)
        style.configure("TLabel", background=COLOR_BG, foreground=COLOR_TEXT)
        style.configure("TLabelFrame", background=COLOR_BG, foreground=COLOR_TEXT, bordercolor=COLOR_BORDER)
        style.configure("TLabelFrame.Label", background=COLOR_BG, foreground=COLOR_TEXT)
        style.configure(
            "TButton",
            background=COLOR_PANEL_ALT,
            foreground=COLOR_TEXT,
            bordercolor=COLOR_BORDER,
            focusthickness=1,
            focuscolor=COLOR_ACCENT,
            padding=(10, 6),
        )
        style.map(
            "TButton",
            background=[("active", COLOR_ACCENT), ("disabled", COLOR_PANEL)],
            foreground=[("disabled", COLOR_MUTED)],
            bordercolor=[("focus", COLOR_ACCENT), ("active", COLOR_ACCENT)],
        )
        style.configure(
            "TEntry",
            fieldbackground=COLOR_ENTRY_BG,
            foreground=COLOR_TEXT,
            bordercolor=COLOR_BORDER,
            insertcolor=COLOR_TEXT,
        )
        style.configure(
            "TCombobox",
            fieldbackground=COLOR_ENTRY_BG,
            foreground=COLOR_TEXT,
            background=COLOR_PANEL_ALT,
            arrowcolor=COLOR_TEXT,
            bordercolor=COLOR_BORDER,
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", COLOR_ENTRY_BG)],
            foreground=[("readonly", COLOR_TEXT), ("disabled", COLOR_MUTED)],
            selectbackground=[("readonly", COLOR_ENTRY_BG)],
            selectforeground=[("readonly", COLOR_TEXT)],
        )
        style.configure("TNotebook", background=COLOR_BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=COLOR_PANEL, foreground=COLOR_MUTED, padding=(14, 8))
        style.map(
            "TNotebook.Tab",
            background=[("selected", COLOR_PANEL_ALT)],
            foreground=[("selected", COLOR_TEXT)],
        )
        style.configure("TScrollbar", background=COLOR_PANEL_ALT, troughcolor=COLOR_PANEL, bordercolor=COLOR_BORDER)

        self.root.option_add("*TCombobox*Listbox*Background", COLOR_PANEL_ALT)
        self.root.option_add("*TCombobox*Listbox*Foreground", COLOR_TEXT)
        self.root.option_add("*TCombobox*Listbox*selectBackground", COLOR_ACCENT)
        self.root.option_add("*TCombobox*Listbox*selectForeground", COLOR_TEXT)

    def _apply_text_theme(self, widget: tk.Text) -> None:
        widget.configure(
            bg=COLOR_ENTRY_BG,
            fg=COLOR_TEXT,
            insertbackground=COLOR_TEXT,
            selectbackground=COLOR_ACCENT,
            selectforeground=COLOR_TEXT,
            highlightthickness=1,
            highlightbackground=COLOR_BORDER,
            highlightcolor=COLOR_ACCENT_ACTIVE,
            relief="flat",
            padx=8,
            pady=8,
        )

    def _build_ui(self) -> None:
        padding = {"padx": 12, "pady": 6}

        edit_frame = ttk.LabelFrame(self.root, text="Edición de logs")
        edit_frame.grid(row=0, column=0, columnspan=2, sticky="ew", padx=12, pady=(10, 6))
        ttk.Label(edit_frame, text="Fecha (YYYY-MM-DD):").grid(row=0, column=0, sticky="w", padx=6, pady=6)
        ttk.Entry(edit_frame, textvariable=self.edit_date_var, width=14).grid(row=0, column=1, sticky="w", padx=6, pady=6)
        ttk.Button(edit_frame, text="Hoy", command=self.set_edit_date_today).grid(row=0, column=2, sticky="w", padx=6, pady=6)
        ttk.Button(edit_frame, text="Recalcular", command=self.refresh_selected_date_views).grid(row=0, column=3, sticky="w", padx=6, pady=6)
        ttk.Label(
            edit_frame,
            text="La fecha aplica a notas de jornada, ajustes y medicinas. La jornada/pomodoro siguen usando hoy.",
            foreground=COLOR_MUTED,
        ).grid(row=1, column=0, columnspan=4, sticky="w", padx=6, pady=(0, 6))

        ttk.Label(self.root, text="Tiempo de sesión pomodoro (min)").grid(row=1, column=0, sticky="w", **padding)
        ttk.Entry(self.root, textvariable=self.pomodoro_minutes_var, width=8).grid(row=1, column=1, sticky="w", **padding)

        ttk.Label(self.root, text="Descanso entre sesiones (min)").grid(row=2, column=0, sticky="w", **padding)
        ttk.Entry(self.root, textvariable=self.break_minutes_var, width=8).grid(row=2, column=1, sticky="w", **padding)

        btn_frame = ttk.Frame(self.root)
        btn_frame.grid(row=3, column=0, columnspan=2, pady=(10, 0), padx=12, sticky="ew")

        self.start_day_btn = ttk.Button(btn_frame, text="Iniciar jornada", command=self.start_workday)
        self.start_day_btn.grid(row=0, column=0, padx=6)

        self.finish_day_btn = ttk.Button(btn_frame, text="Finalizar jornada", command=self.finish_workday, state=tk.DISABLED)
        self.finish_day_btn.grid(row=0, column=1, padx=6)

        self.start_pomo_btn = ttk.Button(btn_frame, text="Iniciar pomodoro", command=self.start_pomodoro, state=tk.DISABLED)
        self.start_pomo_btn.grid(row=0, column=2, padx=6)

        self.pause_pomo_btn = ttk.Button(btn_frame, text="Pausar", command=self.toggle_pomodoro_pause, state=tk.DISABLED)
        self.pause_pomo_btn.grid(row=0, column=3, padx=6)

        ttk.Button(btn_frame, text="Ver estadísticas", command=self.show_stats).grid(row=0, column=4, padx=6)
        ttk.Button(btn_frame, text="Probar alarma", command=self.test_alarm).grid(row=0, column=5, padx=6)

        self.stop_alarm_btn = ttk.Button(btn_frame, text="Detener alarma", command=self.stop_alarm, state=tk.DISABLED)
        self.stop_alarm_btn.grid(row=0, column=6, padx=6)

        ttk.Label(self.root, textvariable=self.status_var, font=("Helvetica", 11)).grid(row=4, column=0, columnspan=2, sticky="w", **padding)
        ttk.Label(self.root, text="Temporizador", font=("Helvetica", 11, "bold")).grid(row=5, column=0, sticky="w", **padding)
        
        timer_frame = ttk.Frame(self.root)
        timer_frame.grid(row=5, column=1, sticky="w", **padding)
        ttk.Label(timer_frame, textvariable=self.timer_var, font=("Consolas", 18)).grid(row=0, column=0, sticky="w")
        ttk.Label(timer_frame, text=" | Reloj: ", font=("Helvetica", 11)).grid(row=0, column=1, sticky="w")
        ttk.Label(timer_frame, textvariable=self.clock_var, font=("Consolas", 18)).grid(row=0, column=2, sticky="w")

        ttk.Label(self.root, textvariable=self.today_total_var, font=("Helvetica", 11, "bold")).grid(
            row=6, column=0, columnspan=2, sticky="w", padx=12, pady=(2, 2)
        )

        # Resumen medicinas hoy
        ttk.Label(self.root, textvariable=self.med_today_summary_var, font=("Helvetica", 10)).grid(
            row=7, column=0, columnspan=2, sticky="w", padx=12, pady=(0, 10)
        )

        # --------- Secciones: Notas + Ajustes + Medicinas ---------
        notebook = ttk.Notebook(self.root)
        notebook.grid(row=8, column=0, columnspan=2, sticky="nsew", padx=12, pady=8)

        # Tab 1: Diario
        journal_tab = ttk.Frame(notebook)
        notebook.add(journal_tab, text="Diario")

        # Layout para el diario: Izquierda (Editor) | Derecha (Historial)
        journal_paned = ttk.PanedWindow(journal_tab, orient="horizontal")
        journal_paned.pack(fill="both", expand=True)

        editor_frame = ttk.Frame(journal_paned)
        history_frame = ttk.Frame(journal_paned)
        journal_paned.add(editor_frame, weight=3)
        journal_paned.add(history_frame, weight=1)

        # Editor
        ttk.Label(editor_frame, text="Título:").grid(row=0, column=0, sticky="w", pady=(6, 2), padx=6)
        ttk.Entry(editor_frame, textvariable=self.journal_title_var).grid(row=1, column=0, sticky="ew", padx=6)
        
        ttk.Label(editor_frame, text="Entrada diaria (fecha seleccionada):").grid(row=2, column=0, sticky="w", pady=(6, 2), padx=6)
        self.journal_text = tk.Text(editor_frame, height=15, width=50, wrap="word")
        self._apply_text_theme(self.journal_text)
        self.journal_text.grid(row=3, column=0, sticky="nsew", padx=6)

        ttk.Button(editor_frame, text="Guardar Diario", command=self.save_journal).grid(row=4, column=0, sticky="w", padx=6, pady=6)
        
        editor_frame.grid_columnconfigure(0, weight=1)
        editor_frame.grid_rowconfigure(3, weight=1)

        # Historial
        ttk.Label(history_frame, text="Entradas pasadas:").grid(row=0, column=0, sticky="w", pady=(6, 2), padx=6)
        
        list_container = ttk.Frame(history_frame)
        list_container.grid(row=1, column=0, sticky="nsew", padx=6)
        
        self.journal_listbox = tk.Listbox(
            list_container, 
            bg=COLOR_ENTRY_BG, 
            fg=COLOR_TEXT, 
            selectbackground=COLOR_ACCENT,
            selectforeground=COLOR_TEXT,
            borderwidth=0,
            highlightthickness=1,
            highlightbackground=COLOR_BORDER
        )
        self.journal_listbox.pack(side="left", fill="both", expand=True)
        self.journal_listbox.bind("<<ListboxSelect>>", self._on_journal_select)
        
        journal_scroll = ttk.Scrollbar(list_container, orient="vertical", command=self.journal_listbox.yview)
        journal_scroll.pack(side="right", fill="y")
        self.journal_listbox.config(yscrollcommand=journal_scroll.set)

        history_frame.grid_columnconfigure(0, weight=1)
        history_frame.grid_rowconfigure(1, weight=1)

        # Tab 2: Notas
        notes_tab = ttk.Frame(notebook)
        notebook.add(notes_tab, text="Notas")

        ttk.Label(notes_tab, text="Notas de la jornada (fecha seleccionada):").grid(row=0, column=0, sticky="w", pady=(6, 2), padx=6)
        self.day_notes_text = tk.Text(notes_tab, height=6, width=70, wrap="word")
        self._apply_text_theme(self.day_notes_text)
        self.day_notes_text.grid(row=1, column=0, columnspan=2, sticky="ew", padx=6)

        ttk.Button(notes_tab, text="Guardar nota de jornada", command=self.save_day_note).grid(row=2, column=0, sticky="w", padx=6, pady=6)

        ttk.Separator(notes_tab, orient="horizontal").grid(row=3, column=0, columnspan=2, sticky="ew", padx=6, pady=8)

        ttk.Label(notes_tab, text="Notas del pomodoro actual:").grid(row=4, column=0, sticky="w", pady=(2, 2), padx=6)
        self.pomo_notes_text = tk.Text(notes_tab, height=6, width=70, wrap="word")
        self._apply_text_theme(self.pomo_notes_text)
        self.pomo_notes_text.grid(row=5, column=0, columnspan=2, sticky="ew", padx=6)

        ttk.Button(notes_tab, text="Guardar nota de este pomodoro", command=self.save_pomodoro_note).grid(row=6, column=0, sticky="w", padx=6, pady=6)

        # Tab 2: Ajustes (trabajo)
        adjust_tab = ttk.Frame(notebook)
        notebook.add(adjust_tab, text="Ajustes")

        ttk.Label(adjust_tab, text="Ajuste manual (minutos +/-) para la fecha seleccionada:").grid(row=0, column=0, sticky="w", padx=6, pady=(8, 2))
        ttk.Entry(adjust_tab, textvariable=self.adjust_delta_minutes_var, width=12).grid(row=0, column=1, sticky="w", padx=6, pady=(8, 2))

        ttk.Label(adjust_tab, text="Motivo (opcional):").grid(row=1, column=0, sticky="w", padx=6, pady=2)
        ttk.Entry(adjust_tab, textvariable=self.adjust_reason_var, width=50).grid(row=1, column=1, sticky="w", padx=6, pady=2)

        ttk.Button(adjust_tab, text="Aplicar ajuste (+/-)", command=self.apply_manual_adjustment).grid(row=2, column=0, sticky="w", padx=6, pady=8)

        ttk.Separator(adjust_tab, orient="horizontal").grid(row=3, column=0, columnspan=3, sticky="ew", padx=6, pady=10)

        ttk.Label(adjust_tab, text="O fijar el total de la fecha seleccionada (min) y calcular el delta automáticamente:").grid(row=4, column=0, sticky="w", padx=6, pady=(2, 2))
        ttk.Entry(adjust_tab, textvariable=self.adjust_target_today_var, width=12).grid(row=4, column=1, sticky="w", padx=6, pady=(2, 2))
        ttk.Button(adjust_tab, text="Fijar total de la fecha", command=self.set_today_total).grid(row=5, column=0, sticky="w", padx=6, pady=8)

        ttk.Button(adjust_tab, text="Recalcular total de la fecha", command=self._refresh_today_total).grid(row=5, column=1, sticky="w", padx=6, pady=8)

        # Tab 3: Medicinas
        meds_tab = ttk.Frame(notebook)
        notebook.add(meds_tab, text="Medicinas")

        ttk.Label(meds_tab, text="Registro diario (mg) para la fecha seleccionada — deja vacío lo que no tomaste:").grid(
            row=0, column=0, columnspan=4, sticky="w", padx=6, pady=(10, 4)
        )

        r = 1
        for med in MEDS:
            ttk.Label(meds_tab, text=f"{med} (mg):").grid(row=r, column=0, sticky="w", padx=6, pady=3)
            ttk.Entry(meds_tab, textvariable=self.med_vars[med], width=12).grid(row=r, column=1, sticky="w", padx=6, pady=3)
            r += 1

        ttk.Button(meds_tab, text="Guardar dosis de la fecha", command=self.save_meds_today).grid(row=r, column=0, sticky="w", padx=6, pady=(10, 6))
        ttk.Button(meds_tab, text="Recalcular resumen de la fecha", command=self._refresh_today_meds_summary).grid(row=r, column=1, sticky="w", padx=6, pady=(10, 6))
        r += 1

        ttk.Separator(meds_tab, orient="horizontal").grid(row=r, column=0, columnspan=4, sticky="ew", padx=6, pady=12)
        r += 1

        ttk.Label(meds_tab, text="Ajustes manuales (mg) — como en trabajo:").grid(row=r, column=0, columnspan=4, sticky="w", padx=6, pady=(2, 8))
        r += 1

        ttk.Label(meds_tab, text="Medicina:").grid(row=r, column=0, sticky="w", padx=6, pady=3)
        med_adjust_values = [MED_ALL_OPTION, *MEDS]
        ttk.Combobox(meds_tab, textvariable=self.med_adjust_med_var, values=med_adjust_values, width=16, state="readonly").grid(
            row=r, column=1, sticky="w", padx=6, pady=3
        )
        r += 1

        ttk.Label(meds_tab, text="Ajuste (mg +/-) para la fecha seleccionada:").grid(row=r, column=0, sticky="w", padx=6, pady=3)
        ttk.Entry(meds_tab, textvariable=self.med_adjust_delta_var, width=12).grid(row=r, column=1, sticky="w", padx=6, pady=3)
        ttk.Button(meds_tab, text="Aplicar ajuste", command=self.apply_med_adjustment).grid(row=r, column=2, sticky="w", padx=6, pady=3)
        r += 1

        ttk.Label(meds_tab, text="O fijar total de la fecha (mg):").grid(row=r, column=0, sticky="w", padx=6, pady=3)
        ttk.Entry(meds_tab, textvariable=self.med_adjust_target_var, width=12).grid(row=r, column=1, sticky="w", padx=6, pady=3)
        ttk.Button(meds_tab, text="Fijar total", command=self.set_med_today_total).grid(row=r, column=2, sticky="w", padx=6, pady=3)
        r += 1

        ttk.Label(meds_tab, text="Motivo (opcional):").grid(row=r, column=0, sticky="w", padx=6, pady=3)
        ttk.Entry(meds_tab, textvariable=self.med_adjust_reason_var, width=50).grid(row=r, column=1, columnspan=2, sticky="w", padx=6, pady=3)

        # Make window resizable properly
        self.root.grid_rowconfigure(8, weight=1)
        self.root.grid_columnconfigure(0, weight=1)
        self.root.grid_columnconfigure(1, weight=1)

    def _get_selected_date(self, show_error: bool = True) -> Optional[date]:
        raw = (self.edit_date_var.get() or "").strip()
        if not raw:
            if show_error:
                messagebox.showerror("Fecha inválida", "Escribe una fecha en formato YYYY-MM-DD.")
            return None
        try:
            return date.fromisoformat(raw)
        except ValueError:
            if show_error:
                messagebox.showerror("Fecha inválida", f"'{raw}' no es una fecha válida. Usa YYYY-MM-DD.")
            return None

    def _build_log_ts_for_date(self, target_date: date) -> datetime:
        now = datetime.now()
        return datetime.combine(target_date, now.time())

    def _write_event_for_date(self, target_date: date, event: str, extra: str = "") -> None:
        ts = self._build_log_ts_for_date(target_date)
        line = f"{event} {ts.isoformat()}"
        if extra:
            line += f" {extra}"
        write_log(line, log_path_for_date(target_date))

    def set_edit_date_today(self) -> None:
        self.edit_date_var.set(datetime.now().strftime("%Y-%m-%d"))
        self.refresh_selected_date_views()

    def refresh_selected_date_views(self) -> None:
        if not self._get_selected_date(show_error=True):
            return
        self._refresh_today_total()
        self._refresh_today_meds_summary()
        self._refresh_journal_view()

    # --------------- Logging helpers ---------------
    def _log(self, event: str, ts: Optional[datetime] = None, extra: str = "") -> None:
        ts = ts or datetime.now()
        line = f"{event} {ts.isoformat()}"
        if extra:
            line += f" {extra}"  # espacio garantizado
        self.current_log_path = self.current_log_path or log_path_for_today()
        write_log(line, self.current_log_path)

    # --------------- Diario ---------------
    def _refresh_journal_view(self) -> None:
        self.journal_text.delete("1.0", "end")
        self.journal_title_var.set("")
        target_date = self._get_selected_date(show_error=False)
        if not target_date:
            return

        path = log_path_for_date(target_date)
        if not os.path.exists(path):
            self._update_journal_list()
            return

        last_journal = ""
        last_title = ""
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                # Usamos split(None, 2) para no romper el contenido del diario si tiene espacios
                parts = line.strip().split(None, 2)
                if len(parts) >= 3 and parts[0] == "JOURNAL_ENTRY":
                    kv = parse_kv(parts[2:])
                    if "note" in kv:
                        last_journal = kv["note"].replace("\\n", "\n")
                    if "title" in kv:
                        last_title = kv["title"]
        
        if last_journal or last_title:
            self.journal_text.delete("1.0", "end") # Limpiar de nuevo por si acaso
            self.journal_text.insert("1.0", last_journal)
            self.journal_title_var.set(last_title)
        
        self._update_journal_list()

    def _update_journal_list(self) -> None:
        if not hasattr(self, "journal_listbox"):
            return

        self.journal_listbox.delete(0, "end")
        self.journal_list_items = []

        ensure_log_dir()
        selected_date = self._get_selected_date(show_error=False)
        log_files = sorted([f for f in os.listdir(LOG_DIR) if f.endswith(".log")], reverse=True)

        if selected_date:
            selected_filename = f"{selected_date.isoformat()}.log"
            if selected_filename not in log_files:
                log_files.insert(0, selected_filename)

        for filename in log_files:
            try:
                date_part = filename.replace(".log", "")
                d = date.fromisoformat(date_part)
            except ValueError:
                continue

            path = os.path.join(LOG_DIR, filename)
            title = ""
            # Buscar el último JOURNAL_ENTRY en el archivo
            path_exists = os.path.exists(path)
            if path_exists:
                with open(path, "r", encoding="utf-8") as fh:
                    for line in fh:
                        parts = line.strip().split(None, 2)
                        if len(parts) >= 3 and parts[0] == "JOURNAL_ENTRY":
                            kv = parse_kv(parts[2:])
                            if "title" in kv:
                                title = kv["title"]

            # La fecha seleccionada debe estar visible aunque aún no exista su log.
            should_show = d == selected_date
            if path_exists:
                should_show = should_show or bool(title) or os.path.getsize(path) > 0

            if should_show:
                display_text = f"{date_part} - {title if title else '(sin título)'}"
                self.journal_listbox.insert("end", display_text)
                self.journal_list_items.append((d, title))

    def _on_journal_select(self, event) -> None:
        selection = self.journal_listbox.curselection()
        if not selection:
            return
        idx = selection[0]
        selected_date, _ = self.journal_list_items[idx]
        self.edit_date_var.set(selected_date.isoformat())
        # Llamar a refresh_selected_date_views pero sin entrar en bucle infinito
        # _refresh_journal_view llama a _update_journal_list
        self._refresh_today_total()
        self._refresh_today_meds_summary()
        
        # Cargar los datos del diario manualmente para evitar refrescar la lista mientras seleccionamos
        self.journal_text.delete("1.0", "end")
        self.journal_title_var.set("")
        path = log_path_for_date(selected_date)
        if os.path.exists(path):
            last_journal = ""
            last_title = ""
            with open(path, "r", encoding="utf-8") as fh:
                for line in fh:
                    parts = line.strip().split(None, 2)
                    if len(parts) >= 3 and parts[0] == "JOURNAL_ENTRY":
                        # Usar directamente la línea original para parse_kv
                        kv = parse_kv(line.strip().split())
                        if "note" in kv:
                            last_journal = kv["note"].replace("\\n", "\n")
                        if "title" in kv:
                            last_title = kv["title"]
            self.journal_text.insert("1.0", last_journal)
            self.journal_title_var.set(last_title)

    def save_journal(self) -> None:
        target_date = self._get_selected_date(show_error=True)
        if not target_date:
            return

        text = self.journal_text.get("1.0", "end").strip()
        title = self.journal_title_var.get().strip()
        
        clean_note = sanitize_note(text)
        clean_title = title.replace("\t", " ").replace("\n", " ").strip()
        
        self._write_event_for_date(target_date, "JOURNAL_ENTRY", extra=f"title={clean_title} note={clean_note}")
        self._update_journal_list()
        messagebox.showinfo("Guardado", f"Diario guardado para {target_date.isoformat()}.")

    # --------------- Notas ---------------
    def save_day_note(self) -> None:
        target_date = self._get_selected_date(show_error=True)
        if not target_date:
            return

        if not self.workday_active:
            if not messagebox.askyesno(
                "Sin jornada activa",
                f"No hay jornada activa. ¿Guardar nota de todas formas para {target_date.isoformat()}?",
            ):
                return

        text = self.day_notes_text.get("1.0", "end").strip()
        if not text:
            messagebox.showinfo("Sin nota", "Escribe algo antes de guardar.")
            return

        clean = sanitize_note(text)
        self._write_event_for_date(target_date, "DAY_NOTE", extra=f"note={clean}")
        messagebox.showinfo("Guardado", f"Nota de jornada guardada en el log de {target_date.isoformat()}.")

    def save_pomodoro_note(self) -> None:
        if self.current_phase != "pomodoro" or not self.current_pomo_start:
            messagebox.showinfo("No hay pomodoro", "No hay un pomodoro activo para asociar la nota.")
            return

        text = self.pomo_notes_text.get("1.0", "end").strip()
        if not text:
            messagebox.showinfo("Sin nota", "Escribe algo antes de guardar.")
            return

        clean = sanitize_note(text)
        self._log("POMODORO_NOTE", extra=f"pomo_start={self.current_pomo_start.isoformat()} note={clean}")
        messagebox.showinfo("Guardado", "Nota del pomodoro guardada en el log.")

    def _autosave_pomodoro_note_if_any(self) -> None:
        if self.current_phase == "pomodoro" and self.current_pomo_start:
            text = self.pomo_notes_text.get("1.0", "end").strip()
            if text:
                clean = sanitize_note(text)
                self._log("POMODORO_NOTE", extra=f"pomo_start={self.current_pomo_start.isoformat()} note={clean}")

    # --------------- Ajustes manuales (trabajo) ---------------
    def apply_manual_adjustment(self) -> None:
        target_date = self._get_selected_date(show_error=True)
        if not target_date:
            return

        raw = (self.adjust_delta_minutes_var.get() or "").strip()
        if not raw:
            messagebox.showerror("Falta valor", "Pon un número de minutos (ej: 15 o -10).")
            return
        try:
            delta = float(raw)
        except ValueError:
            messagebox.showerror("Valor inválido", "Minutos inválidos. Ejemplos: 15, -10, 7.5")
            return

        reason = sanitize_note(self.adjust_reason_var.get() or "")
        extra = f"amount={delta}"
        if reason:
            extra += f" reason={reason}"

        self._write_event_for_date(target_date, "ADJUST_MINUTES", extra=extra)

        self.adjust_delta_minutes_var.set("")
        self.adjust_reason_var.set("")
        self._refresh_today_total()
        messagebox.showinfo(
            "Ajuste aplicado",
            f"Se registró un ajuste de {delta:+.1f} min para {target_date.isoformat()}.",
        )

    def set_today_total(self) -> None:
        target_date = self._get_selected_date(show_error=True)
        if not target_date:
            return

        raw = (self.adjust_target_today_var.get() or "").strip()
        if not raw:
            messagebox.showerror("Falta valor", "Pon el total objetivo de la fecha en minutos (ej: 180).")
            return
        try:
            target = float(raw)
        except ValueError:
            messagebox.showerror("Valor inválido", "Total inválido. Ej: 180, 210.5")
            return
        if target < 0:
            messagebox.showerror("Valor inválido", "El total objetivo no puede ser negativo.")
            return

        actual = self._compute_total_for_date(target_date)
        delta = target - actual

        if abs(delta) < 0.01:
            messagebox.showinfo("Sin cambio", "Ya estás exactamente en ese total (o la diferencia es despreciable).")
            return

        reason = sanitize_note(self.adjust_reason_var.get() or f"set_total_{target_date.isoformat()}")
        extra = f"amount={delta}"
        if reason:
            extra += f" reason={reason}"

        self._write_event_for_date(target_date, "ADJUST_MINUTES", extra=extra)

        self.adjust_target_today_var.set("")
        self.adjust_reason_var.set("")
        self._refresh_today_total()
        messagebox.showinfo(
            "Total fijado",
            f"Total de {target_date.isoformat()} ajustado a {target:.1f} min (delta {delta:+.1f} min).",
        )

    def _refresh_today_total(self) -> None:
        target_date = self._get_selected_date(show_error=False)
        if not target_date:
            self.today_total_var.set("Trabajo: fecha inválida")
            return
        total = self._compute_total_for_date(target_date)
        self.today_total_var.set(f"Trabajo {target_date.isoformat()}: {total:.1f} min")

    # --------------- Medicinas: guardar + ajustes ---------------
    def save_meds_today(self) -> None:
        """
        Guarda un evento MED_TAKEN con las medicinas que tengan valor.
        Ej:
          MED_TAKEN 2026-02-10T03:20:01 bupropion=150 escitalopram=10 cafeina=200

        Si la fecha seleccionada es pasada, interpreta los valores escritos como
        "total deseado del día" para permitir correcciones sin duplicar dosis.
        En ese caso registra MED_ADJUST por diferencia (delta).
        """
        target_date = self._get_selected_date(show_error=True)
        if not target_date:
            return

        kv_parts: list[str] = []
        values_by_med: dict[str, float] = {}
        any_value = False

        for med in MEDS:
            raw = (self.med_vars[med].get() or "").strip()
            if not raw:
                continue
            try:
                mg = float(raw)
            except ValueError:
                messagebox.showerror("Valor inválido", f"{med}: mg inválidos. Ej: 150, 10, 200")
                return
            if mg < 0:
                messagebox.showerror("Valor inválido", f"{med}: no puede ser negativo.")
                return
            values_by_med[med] = mg
            kv_parts.append(f"{med}={mg}")
            any_value = True

        if not any_value:
            if not messagebox.askyesno(
                "Sin datos",
                f"No pusiste ninguna medicina. ¿Guardar de todas formas el registro vacío para {target_date.isoformat()}?",
            ):
                return
            self._write_event_for_date(target_date, "MED_TAKEN", extra="none=true")
        else:
            today = datetime.now().date()
            if target_date < today:
                current_totals = self._compute_meds_for_date(target_date)
                adjustments: list[tuple[str, float]] = []
                for med, target_total in values_by_med.items():
                    actual_total = float(current_totals.get(med, 0.0))
                    delta = target_total - actual_total
                    if abs(delta) >= 0.01:
                        adjustments.append((med, delta))

                if adjustments:
                    reason = sanitize_note(f"edit_past_date_{target_date.isoformat()}")
                    for med, delta in adjustments:
                        self._write_event_for_date(
                            target_date,
                            "MED_ADJUST",
                            extra=f"med={med} amount={delta} reason={reason}",
                        )
                else:
                    messagebox.showinfo(
                        "Sin cambios",
                        f"Las dosis de {target_date.isoformat()} ya coinciden con los valores escritos.",
                    )
                    return
            else:
                self._write_event_for_date(target_date, "MED_TAKEN", extra=" ".join(kv_parts))

        self._refresh_today_meds_summary()
        if target_date < datetime.now().date():
            messagebox.showinfo("Guardado", f"Dosis de {target_date.isoformat()} actualizadas correctamente.")
        else:
            messagebox.showinfo("Guardado", f"Registro de medicinas guardado en el log de {target_date.isoformat()}.")

    def apply_med_adjustment(self) -> None:
        """
        Ajuste manual (mg +/-) para la fecha seleccionada.
        Registra: MED_ADJUST med=<med> amount=+X reason=...
        Si medicina=todas, aplica el mismo delta a cada medicina.
        """
        target_date = self._get_selected_date(show_error=True)
        if not target_date:
            return

        med = (self.med_adjust_med_var.get() or "").strip()
        apply_to_all = med == MED_ALL_OPTION
        if not apply_to_all and med not in MEDS:
            messagebox.showerror("Medicina inválida", f"Selecciona una medicina válida o '{MED_ALL_OPTION}'.")
            return

        raw = (self.med_adjust_delta_var.get() or "").strip()
        if not raw:
            messagebox.showerror("Falta valor", "Pon un número de mg (ej: 50 o -25).")
            return
        try:
            delta = float(raw)
        except ValueError:
            messagebox.showerror("Valor inválido", "mg inválidos. Ejemplos: 50, -25, 12.5")
            return

        reason = sanitize_note(self.med_adjust_reason_var.get() or "")
        target_meds = MEDS if apply_to_all else [med]
        for target_med in target_meds:
            extra = f"med={target_med} amount={delta}"
            if reason:
                extra += f" reason={reason}"
            self._write_event_for_date(target_date, "MED_ADJUST", extra=extra)

        self.med_adjust_delta_var.set("")
        self.med_adjust_reason_var.set("")
        self._refresh_today_meds_summary()
        if apply_to_all:
            messagebox.showinfo(
                "Ajuste aplicado",
                f"Se registró un ajuste de {delta:+.1f} mg en todas las medicinas para {target_date.isoformat()}.",
            )
        else:
            messagebox.showinfo(
                "Ajuste aplicado",
                f"Se registró un ajuste de {delta:+.1f} mg para {med} en {target_date.isoformat()}.",
            )

    def set_med_today_total(self) -> None:
        """
        Fija total de la fecha seleccionada (mg): target - actual => MED_ADJUST.
        Si medicina=todas, aplica el mismo total objetivo a cada medicina.
        """
        target_date = self._get_selected_date(show_error=True)
        if not target_date:
            return

        med = (self.med_adjust_med_var.get() or "").strip()
        apply_to_all = med == MED_ALL_OPTION
        if not apply_to_all and med not in MEDS:
            messagebox.showerror("Medicina inválida", f"Selecciona una medicina válida o '{MED_ALL_OPTION}'.")
            return

        raw = (self.med_adjust_target_var.get() or "").strip()
        if not raw:
            messagebox.showerror("Falta valor", "Pon el total objetivo de la fecha en mg (ej: 150).")
            return
        try:
            target = float(raw)
        except ValueError:
            messagebox.showerror("Valor inválido", "Total inválido. Ej: 150, 200.5")
            return
        if target < 0:
            messagebox.showerror("Valor inválido", "El total objetivo no puede ser negativo.")
            return

        totals_for_date = self._compute_meds_for_date(target_date)
        target_meds = MEDS if apply_to_all else [med]
        adjustments: list[tuple[str, float]] = []
        for target_med in target_meds:
            actual = float(totals_for_date.get(target_med, 0.0))
            delta = target - actual
            if abs(delta) >= 0.01:
                adjustments.append((target_med, delta))

        if not adjustments:
            messagebox.showinfo("Sin cambio", "Ya estás exactamente en ese total (o la diferencia es despreciable).")
            return

        reason = sanitize_note(self.med_adjust_reason_var.get() or f"set_total_{target_date.isoformat()}")
        for target_med, delta in adjustments:
            extra = f"med={target_med} amount={delta}"
            if reason:
                extra += f" reason={reason}"
            self._write_event_for_date(target_date, "MED_ADJUST", extra=extra)

        self.med_adjust_target_var.set("")
        self.med_adjust_reason_var.set("")
        self._refresh_today_meds_summary()
        if apply_to_all:
            messagebox.showinfo(
                "Total fijado",
                f"Todas las medicinas de {target_date.isoformat()} fueron ajustadas a {target:.1f} mg.",
            )
        else:
            adjusted_med, adjusted_delta = adjustments[0]
            messagebox.showinfo(
                "Total fijado",
                f"{adjusted_med}: total de {target_date.isoformat()} ajustado a {target:.1f} mg (delta {adjusted_delta:+.1f} mg).",
            )

    def _refresh_today_meds_summary(self) -> None:
        target_date = self._get_selected_date(show_error=False)
        if not target_date:
            self.med_today_summary_var.set("Medicinas: fecha inválida")
            return
        totals = self._compute_meds_for_date(target_date)
        if not totals:
            self.med_today_summary_var.set(f"Medicinas {target_date.isoformat()}: (sin datos)")
            return
        parts = [f"{m}={totals.get(m, 0.0):.0f}mg" for m in MEDS if totals.get(m, 0.0) != 0.0]
        summary = ", ".join(parts) if parts else "(0 mg)"
        self.med_today_summary_var.set(f"Medicinas {target_date.isoformat()}: {summary}")

    # --------------- Workday actions ---------------
    def start_workday(self) -> None:
        if self.workday_active:
            messagebox.showinfo("Jornada activa", "Ya tienes una jornada en curso.")
            return
        try:
            pomodoro_minutes = int(self.pomodoro_minutes_var.get())
            break_minutes = int(self.break_minutes_var.get())
            if pomodoro_minutes <= 0 or break_minutes <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("Valores inválidos", "Ingresa minutos positivos para pomodoro y descanso.")
            return

        self.workday_active = True
        self.current_log_path = log_path_for_today()

        if not os.path.exists(self.current_log_path):
            self._log("DAY_START")
        else:
            self._log("DAY_RESUME")

        self.status_var.set("Jornada iniciada. Primer pomodoro en marcha…")
        self.start_day_btn.config(state=tk.DISABLED)
        self.finish_day_btn.config(state=tk.NORMAL)
        self.start_pomo_btn.config(state=tk.DISABLED)

        self.start_pomodoro()

    def finish_workday(self) -> None:
        if not self.workday_active:
            messagebox.showinfo("Sin jornada", "No hay jornada activa.")
            return

        self._autosave_pomodoro_note_if_any()

        if self.after_id:
            self.root.after_cancel(self.after_id)
            self.after_id = None
        if self.current_phase == "pomodoro" and self.current_pomo_start:
            self._log("POMODORO_END", extra="cancelled=true")
        elif self.current_phase == "break" and self.current_break_start:
            self._log("BREAK_END", extra="cancelled=true")

        self._log("DAY_END")
        self._reset_state()
        self._refresh_today_total()
        self.status_var.set("Jornada finalizada. ¡Buen trabajo!")

    def _reset_state(self) -> None:
        self.stop_alarm()
        self.workday_active = False
        self.current_phase = None
        self.remaining_seconds = 0
        self.timer_paused = False
        self.timer_var.set("00:00")
        self.start_day_btn.config(state=tk.NORMAL)
        self.finish_day_btn.config(state=tk.DISABLED)
        self.start_pomo_btn.config(state=tk.DISABLED)
        self.pause_pomo_btn.config(state=tk.DISABLED, text="Pausar")
        self.after_id = None
        self.current_pomo_start = None
        self.current_break_start = None

    # --------------- Pomodoro cycle ---------------
    def start_pomodoro(self) -> None:
        if not self.workday_active:
            messagebox.showinfo("Primero inicia la jornada", "Inicia la jornada antes de comenzar un pomodoro.")
            return
        if self.current_phase is not None:
            return
        try:
            minutes = int(self.pomodoro_minutes_var.get())
            if minutes <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("Valor inválido", "Ingresa minutos positivos para el pomodoro.")
            return

        self.current_phase = "pomodoro"
        self.remaining_seconds = minutes * 60
        self.timer_paused = False
        self.current_pomo_start = datetime.now()

        self.pomo_notes_text.delete("1.0", "end")
        self._log("POMODORO_START", ts=self.current_pomo_start, extra=f"duration={minutes}")

        self.status_var.set("Pomodoro en curso…")
        self.start_pomo_btn.config(state=tk.DISABLED)
        self.pause_pomo_btn.config(state=tk.NORMAL, text="Pausar")
        self._tick()

    def toggle_pomodoro_pause(self) -> None:
        if self.current_phase != "pomodoro" or not self.current_pomo_start:
            return

        if self.timer_paused:
            resume_ts = datetime.now()
            self.timer_paused = False
            self._log("POMODORO_RESUME", ts=resume_ts)
            self.pause_pomo_btn.config(text="Pausar")
            self.status_var.set("Pomodoro en curso…")
            self._tick()
            return

        if self.after_id:
            self.root.after_cancel(self.after_id)
            self.after_id = None

        pause_ts = datetime.now()
        self.timer_paused = True
        self._log("POMODORO_PAUSE", ts=pause_ts)
        self.pause_pomo_btn.config(text="Reanudar")
        self.status_var.set("Pomodoro en pausa.")

    def _end_pomodoro(self) -> None:
        self._autosave_pomodoro_note_if_any()

        end_ts = datetime.now()
        self._log("POMODORO_END", ts=end_ts)

        self.current_phase = None
        self.timer_paused = False
        self.current_pomo_start = None

        self._refresh_today_total()
        self.start_alarm("Pomodoro terminado. Toca descansar.")
        self.start_break()

    def start_break(self) -> None:
        try:
            minutes = int(self.break_minutes_var.get())
            if minutes <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("Valor inválido", "Ingresa minutos positivos para el descanso.")
            return

        self.current_phase = "break"
        self.remaining_seconds = minutes * 60
        self.timer_paused = False
        self.current_break_start = datetime.now()
        self._log("BREAK_START", ts=self.current_break_start, extra=f"duration={minutes}")

        self.status_var.set("Descanso en curso…")
        self.pause_pomo_btn.config(state=tk.DISABLED, text="Pausar")
        self._tick()

    def _end_break(self) -> None:
        end_ts = datetime.now()
        self._log("BREAK_END", ts=end_ts)

        self.current_phase = None
        self.current_break_start = None

        self._refresh_today_total()
        self.start_alarm("Descanso terminado. ¿Listo para otro pomodoro?")
        self.status_var.set("Listo para iniciar siguiente pomodoro.")
        self.start_pomo_btn.config(state=tk.NORMAL)
        self.pause_pomo_btn.config(state=tk.DISABLED, text="Pausar")
        self.timer_var.set("00:00")

    def _update_clock(self) -> None:
        self.clock_var.set(datetime.now().strftime("%H:%M:%S"))
        self.root.after(1000, self._update_clock)

    def _tick(self) -> None:
        mins, secs = divmod(self.remaining_seconds, 60)
        self.timer_var.set(f"{mins:02d}:{secs:02d}")

        if self.remaining_seconds <= 0:
            self.after_id = None
            if self.current_phase == "pomodoro":
                self._end_pomodoro()
            elif self.current_phase == "break":
                self._end_break()
            return

        self.after_id = self.root.after(1000, self._tick_down_one_second)

    def _tick_down_one_second(self) -> None:
        self.remaining_seconds -= 1
        self._tick()

    # --------------- Alarmas ---------------
    def _detect_alarm_player(self) -> Optional[list[str]]:
        if shutil.which("paplay"):
            return ["paplay", "/usr/share/sounds/freedesktop/stereo/alarm-clock-elapsed.oga"]
        if shutil.which("canberra-gtk-play"):
            return ["canberra-gtk-play", "-i", "alarm-clock-elapsed"]
        if shutil.which("aplay"):
            return ["aplay", "/usr/share/sounds/alsa/Front_Center.wav"]
        return None

    def _play_alarm_once(self) -> None:
        if self._alarm_player:
            try:
                if self.alarm_process is not None and self.alarm_process.poll() is None:
                    return
                self.alarm_process = subprocess.Popen(
                    self._alarm_player,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                return
            except Exception:
                self.alarm_process = None
                pass
        for _ in range(3):
            self.root.bell()

    def start_alarm(self, message: str) -> None:
        self.stop_alarm()
        self.alarm_active = True
        self.alarm_message = message
        self.alarm_message_var.set(message)
        self.stop_alarm_btn.config(state=tk.NORMAL)
        self.status_var.set(message + " (alarma sonando)")
        self._show_alarm_window()
        self._ring_alarm()

    def test_alarm(self) -> None:
        self.start_alarm("Alarma de prueba")

    def _show_alarm_window(self) -> None:
        if self.alarm_window is None or not self.alarm_window.winfo_exists():
            self.alarm_window = tk.Toplevel(self.root)
            self.alarm_window.title("Alarma")
            self.alarm_window.configure(bg=COLOR_BG)
            self.alarm_window.resizable(False, False)
            self.alarm_window.protocol("WM_DELETE_WINDOW", self.stop_alarm)
            self.alarm_window.transient(self.root)

            frame = ttk.Frame(self.alarm_window, padding=16)
            frame.grid(row=0, column=0, sticky="nsew")

            ttk.Label(frame, textvariable=self.alarm_message_var, wraplength=320, justify="left").grid(
                row=0, column=0, columnspan=2, sticky="w", pady=(0, 12)
            )
            ttk.Button(
                frame,
                text=f"Snooze {ALARM_SNOOZE_MINUTES} min",
                command=self.snooze_alarm,
            ).grid(row=1, column=0, sticky="w")
            ttk.Button(frame, text="Detener alarma", command=self.stop_alarm).grid(row=1, column=1, sticky="e")

        self.alarm_window.update_idletasks()
        root_x = self.root.winfo_rootx()
        root_y = self.root.winfo_rooty()
        root_w = self.root.winfo_width()
        root_h = self.root.winfo_height()
        win_w = self.alarm_window.winfo_width()
        win_h = self.alarm_window.winfo_height()
        pos_x = root_x + max((root_w - win_w) // 2, 0)
        pos_y = root_y + max((root_h - win_h) // 2, 0)
        self.alarm_window.geometry(f"+{pos_x}+{pos_y}")
        self.alarm_window.deiconify()
        self.alarm_window.lift()
        self.alarm_window.attributes("-topmost", True)
        self.alarm_window.after(250, lambda: self.alarm_window.attributes("-topmost", False))
        self.alarm_window.focus_force()

    def _ring_alarm(self) -> None:
        if not self.alarm_active:
            return
        self._play_alarm_once()
        self.alarm_after_id = self.root.after(700, self._ring_alarm)

    def snooze_alarm(self) -> None:
        if not self.alarm_message:
            return

        message = self.alarm_message
        self._clear_alarm_loop()
        self._close_alarm_window()
        self.status_var.set(f"{message} (pospuesta {ALARM_SNOOZE_MINUTES} min)")
        self.stop_alarm_btn.config(state=tk.DISABLED)
        self.alarm_resume_after_id = self.root.after(
            ALARM_SNOOZE_MINUTES * 60 * 1000,
            lambda: self.start_alarm(message),
        )

    def stop_alarm(self) -> None:
        self._cancel_alarm_resume()
        self._clear_alarm_loop()
        self._close_alarm_window()
        self.alarm_message = ""
        self.alarm_message_var.set("")
        self.stop_alarm_btn.config(state=tk.DISABLED)

    def _cancel_alarm_resume(self) -> None:
        if self.alarm_resume_after_id:
            self.root.after_cancel(self.alarm_resume_after_id)
            self.alarm_resume_after_id = None

    def _clear_alarm_loop(self) -> None:
        if self.alarm_after_id:
            self.root.after_cancel(self.alarm_after_id)
            self.alarm_after_id = None
        self.alarm_active = False
        if self.alarm_process is not None and self.alarm_process.poll() is None:
            try:
                self.alarm_process.terminate()
            except Exception:
                pass
        self.alarm_process = None

    def _close_alarm_window(self) -> None:
        if self.alarm_window is not None and self.alarm_window.winfo_exists():
            self.alarm_window.destroy()
        self.alarm_window = None

    # --------------- Estadísticas ---------------
    def show_stats(self) -> None:
        if self.stats_window is not None and self.stats_window.winfo_exists():
            self.stats_window.deiconify()
            self.stats_window.lift()
            self._refresh_stats_view()
            return

        self.stats_window = tk.Toplevel(self.root)
        self.stats_window.title("Estadísticas")
        self.stats_window.geometry("1180x780")
        self.stats_window.minsize(980, 620)
        self.stats_window.configure(bg=COLOR_BG)
        self.stats_window.protocol("WM_DELETE_WINDOW", self._close_stats_window)

        outer = ttk.Frame(self.stats_window, padding=12)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(2, weight=1)
        outer.rowconfigure(3, weight=1)

        ttk.Label(outer, text="Estadísticas", font=("Helvetica", 14, "bold")).grid(row=0, column=0, sticky="w")

        slicers = ttk.LabelFrame(outer, text="Slicers")
        slicers.grid(row=1, column=0, sticky="ew", pady=(10, 10))

        ttk.Label(slicers, text="Vista:").grid(row=0, column=0, sticky="w", padx=6, pady=8)
        scope_combo = ttk.Combobox(
            slicers,
            textvariable=self.stats_scope_var,
            values=["Trabajo", "Medicinas"],
            state="readonly",
            width=14,
        )
        scope_combo.grid(row=0, column=1, sticky="w", padx=6, pady=8)
        scope_combo.bind("<<ComboboxSelected>>", self._refresh_stats_view)

        ttk.Label(slicers, text="Agrupar por:").grid(row=0, column=2, sticky="w", padx=6, pady=8)
        group_combo = ttk.Combobox(
            slicers,
            textvariable=self.stats_group_var,
            values=["Día", "Semana", "Mes"],
            state="readonly",
            width=12,
        )
        group_combo.grid(row=0, column=3, sticky="w", padx=6, pady=8)
        group_combo.bind("<<ComboboxSelected>>", self._refresh_stats_view)

        ttk.Label(slicers, text="Medicina:").grid(row=0, column=4, sticky="w", padx=6, pady=8)
        self.stats_med_combo = ttk.Combobox(
            slicers,
            textvariable=self.med_stats_selected_med,
            values=MEDS,
            state="readonly",
            width=18,
        )
        self.stats_med_combo.grid(row=0, column=5, sticky="w", padx=6, pady=8)
        self.stats_med_combo.bind("<<ComboboxSelected>>", self._refresh_stats_view)

        ttk.Button(slicers, text="Actualizar", command=self._refresh_stats_view).grid(row=0, column=6, sticky="w", padx=6, pady=8)

        chart_frame = ttk.Frame(outer)
        chart_frame.grid(row=2, column=0, sticky="nsew")
        chart_frame.columnconfigure(0, weight=1)
        chart_frame.rowconfigure(1, weight=1)

        ttk.Label(chart_frame, textvariable=self.stats_note_var, foreground=COLOR_MUTED, justify="left").grid(
            row=0, column=0, sticky="w", pady=(0, 8)
        )

        self.stats_chart_canvas = tk.Canvas(
            chart_frame,
            bg=COLOR_PANEL,
            highlightthickness=1,
            highlightbackground=COLOR_BORDER,
        )
        self.stats_chart_canvas.grid(row=1, column=0, sticky="nsew")
        self.stats_chart_canvas.bind("<Configure>", self._redraw_stats_chart)

        chart_scroll_x = ttk.Scrollbar(chart_frame, orient="horizontal", command=self.stats_chart_canvas.xview)
        chart_scroll_x.grid(row=2, column=0, sticky="ew")
        self.stats_chart_canvas.configure(xscrollcommand=chart_scroll_x.set)

        summary_frame = ttk.LabelFrame(outer, text="Resumen")
        summary_frame.grid(row=3, column=0, sticky="nsew", pady=(10, 0))
        summary_frame.columnconfigure(0, weight=1)
        summary_frame.rowconfigure(0, weight=1)

        self.stats_summary_text = tk.Text(summary_frame, height=10, wrap="word", state="disabled")
        self._apply_text_theme(self.stats_summary_text)
        self.stats_summary_text.grid(row=0, column=0, sticky="nsew")

        summary_scroll = ttk.Scrollbar(summary_frame, orient="vertical", command=self.stats_summary_text.yview)
        summary_scroll.grid(row=0, column=1, sticky="ns")
        self.stats_summary_text.configure(yscrollcommand=summary_scroll.set)

        self._refresh_stats_view()

    def _close_stats_window(self) -> None:
        if self.stats_window is not None and self.stats_window.winfo_exists():
            self.stats_window.destroy()
        self.stats_window = None
        self.stats_chart_canvas = None
        self.stats_summary_text = None
        self.stats_med_combo = None
        self._stats_chart_state = None

    def _refresh_stats_view(self, _event=None) -> None:
        if self.stats_window is None or not self.stats_window.winfo_exists():
            return

        scope = (self.stats_scope_var.get() or "Trabajo").strip()
        group = (self.stats_group_var.get() or "Día").strip()

        if self.stats_med_combo is not None:
            med_state = "readonly" if scope == "Medicinas" else "disabled"
            self.stats_med_combo.config(state=med_state)

        title, summary_lines, series, note, unit = self._build_stats_payload(scope, group)
        self.stats_note_var.set(note)

        if self.stats_summary_text is not None:
            self.stats_summary_text.config(state="normal")
            self.stats_summary_text.delete("1.0", "end")
            self.stats_summary_text.insert("1.0", "\n".join(summary_lines) if summary_lines else "Sin datos")
            self.stats_summary_text.config(state="disabled")

        self._stats_chart_state = (title, series, unit)
        self._draw_stats_chart(title, series, unit)

    def _build_stats_payload(self, scope: str, group: str):
        if scope == "Medicinas":
            med = (self.med_stats_selected_med.get() or MEDS[0]).strip()
            if med not in MEDS:
                med = MEDS[0]

            day_totals = self._collect_med_day_totals(med)
            note = f"Ventana única con slicers. MED_ADJUST ya está incluido para {med}."
            unit = "mg"

            if group == "Semana":
                totals = self._group_by_week(day_totals)
                title = f"{med}: mg por semana"
                summary_lines = self._format_summary_lines(totals, unit, lambda item: f"{item[0]}-W{item[1]:02d}")
                series = self._series_from_totals(totals, lambda item: f"{item[0]}-W{item[1]:02d}")
            elif group == "Mes":
                totals = self._group_by_month(day_totals)
                title = f"{med}: mg por mes"
                summary_lines = self._format_summary_lines(totals, unit, lambda item: f"{item[0]}-{item[1]:02d}")
                series = self._series_from_totals(totals, lambda item: f"{item[0]}-{item[1]:02d}")
            else:
                totals = day_totals
                title = f"{med}: mg por día"
                summary_lines = self._format_summary_lines(totals, unit, lambda item: item.isoformat())
                series = self._series_from_totals(totals, lambda item: item.isoformat())

            return title, summary_lines, series, note, unit

        day_totals, day_sessions = self._collect_day_stats()
        note = "Ventana única con slicers. ADJUST_MINUTES ya está incluido en los totales."
        unit = "min"

        if group == "Semana":
            totals = self._group_by_week(day_totals)
            title = "Trabajo por semana"
            summary_lines = self._format_summary_lines(totals, unit, lambda item: f"{item[0]}-W{item[1]:02d}")
            series = self._series_from_totals(totals, lambda item: f"{item[0]}-W{item[1]:02d}")
        elif group == "Mes":
            totals = self._group_by_month(day_totals)
            title = "Trabajo por mes"
            summary_lines = self._format_summary_lines(totals, unit, lambda item: f"{item[0]}-{item[1]:02d}")
            series = self._series_from_totals(totals, lambda item: f"{item[0]}-{item[1]:02d}")
        else:
            totals = day_totals
            title = "Trabajo por día"
            summary_lines = self._format_summary_lines(
                totals,
                unit,
                lambda item: item.isoformat(),
                sessions_lookup=day_sessions,
            )
            series = self._series_from_totals(totals, lambda item: item.isoformat())

        return title, summary_lines, series, note, unit

    def _format_summary_lines(self, totals_dict, unit: str, label_fmt, sessions_lookup=None) -> list[str]:
        items = list(sorted(totals_dict.items()))
        if not items:
            return ["Sin datos."]

        shown_items = items[-30:]
        lines: list[str] = []
        if len(shown_items) < len(items):
            lines.append(f"Mostrando los últimos {len(shown_items)} de {len(items)} registros.")
            lines.append("")

        for key, value in reversed(shown_items):
            label = label_fmt(key)
            if sessions_lookup is not None and key in sessions_lookup:
                lines.append(f"{label}: {value:.1f} {unit} en {sessions_lookup[key]} sesiones")
            else:
                lines.append(f"{label}: {value:.1f} {unit}")

        return lines

    def _series_from_totals(self, totals_dict, label_fmt):
        items = list(sorted(totals_dict.items()))
        return [(label_fmt(k), v) for k, v in items]

    def _redraw_stats_chart(self, _event=None) -> None:
        if self._stats_chart_state is None:
            return
        title, series, unit = self._stats_chart_state
        self._draw_stats_chart(title, series, unit)

    def _draw_stats_chart(self, title: str, series, unit: str) -> None:
        if self.stats_chart_canvas is None:
            return

        canvas = self.stats_chart_canvas
        canvas.delete("all")

        width = max(canvas.winfo_width(), 960)
        height = max(canvas.winfo_height(), 420)

        if not series:
            canvas.create_text(width / 2, height / 2, text="Sin datos", fill=COLOR_MUTED, font=("Helvetica", 14, "italic"))
            canvas.configure(scrollregion=(0, 0, width, height))
            return

        left_margin = 80
        right_margin = 30
        top_margin = 60
        bottom_margin = 95
        chart_height = max(120, height - top_margin - bottom_margin)
        max_val = max(v for _, v in series) or 1.0

        if len(series) <= 12:
            step = 88
        elif len(series) <= 24:
            step = 68
        else:
            step = 54
        bar_width = max(22, min(40, step - 18))
        total_width = max(width, left_margin + right_margin + len(series) * step)
        baseline_y = top_margin + chart_height

        canvas.create_text(left_margin, 24, anchor="w", text=title, fill=COLOR_TEXT, font=("Helvetica", 14, "bold"))

        for idx in range(5):
            ratio = idx / 4
            y = baseline_y - (ratio * chart_height)
            value = max_val * ratio
            canvas.create_line(left_margin, y, total_width - right_margin, y, fill=COLOR_BORDER)
            canvas.create_text(left_margin - 10, y, anchor="e", text=f"{value:.0f}", fill=COLOR_MUTED, font=("Helvetica", 9))

        for index, (label, value) in enumerate(series):
            x0 = left_margin + index * step + (step - bar_width) / 2
            x1 = x0 + bar_width
            bar_height = 0 if max_val == 0 else (value / max_val) * chart_height
            y0 = baseline_y - bar_height

            canvas.create_rectangle(x0, y0, x1, baseline_y, fill=COLOR_ACCENT_ACTIVE, outline="")
            canvas.create_text((x0 + x1) / 2, y0 - 12, text=f"{value:.1f}", fill=COLOR_TEXT, font=("Helvetica", 9))
            canvas.create_text(
                (x0 + x1) / 2,
                baseline_y + 24,
                text=label,
                width=step,
                justify="center",
                fill=COLOR_MUTED,
                font=("Helvetica", 9),
            )

        canvas.create_text(total_width - right_margin, height - 18, anchor="e", text=f"Unidad: {unit}", fill=COLOR_MUTED)
        canvas.configure(scrollregion=(0, 0, total_width, height))

    def _read_completed_pomodoros(self, path: str) -> list[tuple[datetime, float]]:
        completed: list[tuple[datetime, float]] = []

        with open(path, "r", encoding="utf-8") as fh:
            last_pomo_start: Optional[datetime] = None
            paused_at: Optional[datetime] = None
            paused_seconds = 0.0

            for line in fh:
                parts = line.strip().split()
                if len(parts) < 2:
                    continue
                event, ts_str = parts[0], parts[1]
                try:
                    ts = datetime.fromisoformat(self._clean_ts_token(ts_str))
                except ValueError:
                    continue

                if event == "POMODORO_START":
                    last_pomo_start = ts
                    paused_at = None
                    paused_seconds = 0.0
                elif event == "POMODORO_PAUSE" and last_pomo_start and paused_at is None:
                    paused_at = ts
                elif event == "POMODORO_RESUME" and last_pomo_start and paused_at:
                    paused_seconds += max(0.0, (ts - paused_at).total_seconds())
                    paused_at = None
                elif event == "POMODORO_END" and last_pomo_start:
                    if paused_at:
                        paused_seconds += max(0.0, (ts - paused_at).total_seconds())
                    duration_minutes = max(0.0, ((ts - last_pomo_start).total_seconds() - paused_seconds) / 60.0)
                    completed.append((last_pomo_start, duration_minutes))
                    last_pomo_start = None
                    paused_at = None
                    paused_seconds = 0.0

        return completed

    def _compute_total_for_date(self, d: date) -> float:
        """
        Total en minutos de un día = suma de pomodoros + ajustes manuales.
        """
        ensure_log_dir()
        path = os.path.join(LOG_DIR, d.strftime("%Y-%m-%d.log"))
        if not os.path.exists(path):
            return 0.0

        total_min = sum(duration for start_ts, duration in self._read_completed_pomodoros(path) if start_ts.date() == d)
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                parts = line.strip().split()
                if len(parts) < 2:
                    continue
                event, ts_str = parts[0], parts[1]
                try:
                    ts = datetime.fromisoformat(self._clean_ts_token(ts_str))
                except ValueError:
                    continue

                if event == "ADJUST_MINUTES":
                    kv = parse_kv(parts[2:])
                    try:
                        total_min += float(kv.get("amount", "0"))
                    except ValueError:
                        pass
        return max(0.0, total_min)

    def _compute_meds_for_date(self, d: date) -> dict[str, float]:
        """
        Totales de medicinas (mg) por día:
          total = suma(MED_TAKEN) + suma(MED_ADJUST) por medicina
        """
        ensure_log_dir()
        path = os.path.join(LOG_DIR, d.strftime("%Y-%m-%d.log"))
        if not os.path.exists(path):
            return {}

        totals: dict[str, float] = {m: 0.0 for m in MEDS}

        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                parts = line.strip().split()
                if len(parts) < 2:
                    continue
                event, ts_str = parts[0], parts[1]
                try:
                    ts = datetime.fromisoformat(self._clean_ts_token(ts_str))
                except ValueError:
                    continue
                if ts.date() != d:
                    continue

                if event == "MED_TAKEN":
                    kv = parse_kv(parts[2:])
                    for m in MEDS:
                        if m in kv:
                            try:
                                totals[m] += float(kv[m])
                            except ValueError:
                                pass
                elif event == "MED_ADJUST":
                    kv = parse_kv(parts[2:])
                    med = (kv.get("med", "") or "").strip()
                    if med in MEDS:
                        try:
                            totals[med] += float(kv.get("amount", "0"))
                        except ValueError:
                            pass

        for m in MEDS:
            totals[m] = max(0.0, totals[m])

        if all(abs(totals[m]) < 1e-6 for m in MEDS):
            return {}
        return totals

    def _collect_med_day_totals(self, med: str) -> defaultdict[date, float]:
        """
        Totales por día (mg) de una medicina específica.
        """
        med_day: defaultdict[date, float] = defaultdict(float)
        ensure_log_dir()

        for filename in os.listdir(LOG_DIR):
            if not filename.endswith(".log"):
                continue
            path = os.path.join(LOG_DIR, filename)

            with open(path, "r", encoding="utf-8") as fh:
                for line in fh:
                    parts = line.strip().split()
                    if len(parts) < 2:
                        continue
                    event, ts_str = parts[0], parts[1]
                    try:
                        ts = datetime.fromisoformat(self._clean_ts_token(ts_str))
                    except ValueError:
                        continue

                    if event == "MED_TAKEN":
                        kv = parse_kv(parts[2:])
                        if med in kv:
                            try:
                                med_day[ts.date()] += float(kv[med])
                            except ValueError:
                                pass
                    elif event == "MED_ADJUST":
                        kv = parse_kv(parts[2:])
                        if (kv.get("med", "") or "").strip() == med:
                            try:
                                med_day[ts.date()] += float(kv.get("amount", "0"))
                            except ValueError:
                                pass

        for d in list(med_day.keys()):
            med_day[d] = max(0.0, med_day[d])

        return med_day

    def _collect_day_stats(self):
        day_totals: defaultdict[date, float] = defaultdict(float)
        day_sessions: defaultdict[date, int] = defaultdict(int)

        ensure_log_dir()
        for filename in os.listdir(LOG_DIR):
            if not filename.endswith(".log"):
                continue
            path = os.path.join(LOG_DIR, filename)

            for start_ts, duration in self._read_completed_pomodoros(path):
                day_totals[start_ts.date()] += duration
                day_sessions[start_ts.date()] += 1

            with open(path, "r", encoding="utf-8") as fh:
                for line in fh:
                    parts = line.strip().split()
                    if len(parts) < 2:
                        continue
                    event, ts_str = parts[0], parts[1]
                    try:
                        ts = datetime.fromisoformat(self._clean_ts_token(ts_str))
                    except ValueError:
                        continue

                    if event == "ADJUST_MINUTES":
                        kv = parse_kv(parts[2:])
                        try:
                            day_totals[ts.date()] += float(kv.get("amount", "0"))
                        except ValueError:
                            pass

        for d in list(day_totals.keys()):
            day_totals[d] = max(0.0, day_totals[d])

        return day_totals, day_sessions

    def _group_by_week(self, day_totals):
        week_totals: defaultdict[tuple[int, int], float] = defaultdict(float)
        for d, minutes in day_totals.items():
            year, week, _ = d.isocalendar()
            week_totals[(year, week)] += minutes
        return week_totals

    def _group_by_month(self, day_totals):
        month_totals: defaultdict[tuple[int, int], float] = defaultdict(float)
        for d, minutes in day_totals.items():
            month_totals[(d.year, d.month)] += minutes
        return month_totals

    def on_close(self) -> None:
        if self.after_id:
            self.root.after_cancel(self.after_id)
            self.after_id = None

        self.stop_alarm()

        if self.workday_active:
            if messagebox.askyesno("Cerrar", "¿Quieres finalizar la jornada antes de salir?"):
                self.finish_workday()
                self.root.destroy()
                return

        self.root.destroy()


def main() -> None:
    ensure_log_dir()
    root = tk.Tk()
    app = ElTrakerApp(root)
    root.mainloop()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
