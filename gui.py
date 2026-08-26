"""Tkinter GUI for the guarded SIS Shopping Cart scheduler."""

from __future__ import annotations

import argparse
import logging
import os
import queue
import shutil
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk

import yaml

from config import ConfigError, Settings, load_settings
from errors import EnrollClickerError
from logging_setup import IsoMillisFormatter, configure_logging
from main import run_with_settings
from scheduler import TargetSchedule


SOURCE_DIR = Path(__file__).resolve().parent
APP_DIR = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else SOURCE_DIR
)
BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", SOURCE_DIR))
DEFAULT_CONFIG = APP_DIR / "config.yaml"
EXAMPLE_CONFIG = BUNDLE_DIR / "config.example.yaml"


def ensure_config(path: Path) -> None:
    """Create an editable config from the non-sensitive example if missing."""

    if path.exists():
        return
    if not EXAMPLE_CONFIG.is_file():
        raise ConfigError(f"Example config not found: {EXAMPLE_CONFIG}")
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(EXAMPLE_CONFIG, path)


def update_config_file(path: Path, values: dict[str, object]) -> Settings:
    """Validate a same-directory temporary file before replacing config.yaml."""

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"Cannot read config: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("Config root must be a mapping")
    raw.update(values)
    raw.pop("site_policy_confirmed", None)
    raw.pop("button_wait_window_seconds", None)
    temporary = path.with_name(path.name + ".gui-tmp")
    temporary.write_text(
        yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    try:
        validated = load_settings(temporary)
        os.replace(temporary, path)
        return validated
    finally:
        temporary.unlink(missing_ok=True)


class QueueLogHandler(logging.Handler):
    def __init__(self, events: queue.Queue) -> None:
        super().__init__()
        self.events = events

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.events.put(("log", self.format(record)))
        except Exception:
            self.handleError(record)


class EnrollClickerGui:
    def __init__(self, root: tk.Tk, config_path: Path) -> None:
        self.root = root
        self.config_path = config_path.resolve()
        self.events: queue.Queue = queue.Queue()
        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.running = False
        ensure_config(self.config_path)
        self.settings = load_settings(self.config_path)

        self.root.title("SIS 购物车定时助手")
        self.root.geometry("920x760")
        self.root.minsize(820, 650)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_ui()
        self._load_fields(self.settings)
        self._attach_logger()
        self.root.after(100, self._poll_events)
        self.root.after(250, self._update_clock)

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill="both", expand=True)

        title = ttk.Label(
            outer,
            text="SIS 购物车定时助手",
            font=("Segoe UI", 16, "bold"),
        )
        title.pack(anchor="w")
        ttk.Label(
            outer,
            text=(
                "先点“检查页面”确认课程，再设置时间并点“开始定时”。"
                "登录和二次验证由你手动完成；程序只点一次 Enrol，后续步骤由你处理。"
            ),
            foreground="#444444",
        ).pack(anchor="w", pady=(2, 10))

        config_frame = ttk.LabelFrame(outer, text="配置", padding=10)
        config_frame.pack(fill="x")
        config_frame.columnconfigure(1, weight=1)

        self.target_var = tk.StringVar()
        self.timezone_var = tk.StringVar()
        self.url_var = tk.StringVar()
        self.pre_refresh_var = tk.BooleanVar()
        self.refresh_seconds_var = tk.StringVar()
        self.login_timeout_var = tk.StringVar()
        self.keep_browser_var = tk.BooleanVar()

        self._row_entry(config_frame, 0, "目标时间", self.target_var)
        self._row_entry(config_frame, 1, "时区", self.timezone_var, readonly=True)
        self._row_entry(config_frame, 2, "购物车网址", self.url_var, readonly=True)
        ttk.Label(config_frame, text="页面识别", width=19).grid(
            row=3, column=0, sticky="w", pady=3
        )
        ttk.Label(
            config_frame,
            text="已配置：自动全选课程并识别 Enrol 按钮",
            foreground="#137333",
        ).grid(
            row=3, column=1, sticky="w", pady=3
        )

        options = ttk.Frame(config_frame)
        options.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Checkbutton(
            options, text="到点前刷新一次", variable=self.pre_refresh_var
        ).grid(row=0, column=0, sticky="w", padx=(0, 12))
        ttk.Label(options, text="提前多久（秒）").grid(row=0, column=1)
        self.refresh_spinbox = ttk.Spinbox(
            options,
            from_=1,
            to=3,
            increment=1,
            textvariable=self.refresh_seconds_var,
            width=6,
        )
        self.refresh_spinbox.grid(row=0, column=2, padx=(4, 14))
        ttk.Label(options, text="等待登录（分钟）").grid(row=0, column=3)
        ttk.Spinbox(
            options,
            from_=1,
            to=60,
            increment=1,
            textvariable=self.login_timeout_var,
            width=6,
        ).grid(row=0, column=4, padx=(4, 0))
        ttk.Checkbutton(
            options,
            text="运行后不关闭购物车窗口",
            variable=self.keep_browser_var,
        ).grid(row=1, column=0, columnspan=4, sticky="w", pady=(7, 0))

        button_frame = ttk.Frame(outer)
        button_frame.pack(fill="x", pady=10)
        self.save_button = ttk.Button(
            button_frame, text="保存", command=self._save_fields
        )
        self.save_button.pack(side="left")
        self.dry_button = ttk.Button(
            button_frame, text="检查页面", command=lambda: self._start(False)
        )
        self.dry_button.pack(side="left", padx=(8, 0))
        self.arm_button = ttk.Button(
            button_frame, text="开始定时", command=lambda: self._start(True)
        )
        self.arm_button.pack(side="left", padx=(8, 0))
        self.stop_button = ttk.Button(
            button_frame, text="停止", command=self._request_stop, state="disabled"
        )
        self.stop_button.pack(side="left", padx=(8, 0))

        status_frame = ttk.LabelFrame(outer, text="运行状态", padding=10)
        status_frame.pack(fill="x")
        self.mode_var = tk.StringVar(value="待机")
        self.status_var = tk.StringVar(value="尚未运行")
        self.clock_var = tk.StringVar(value="")
        self.countdown_var = tk.StringVar(value="")
        self.arm_hint_var = tk.StringVar(value="")
        self.selection_var = tk.StringVar(value="全选状态：尚未检查")
        ttk.Label(status_frame, textvariable=self.mode_var, width=12).grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(status_frame, textvariable=self.status_var).grid(
            row=0, column=1, sticky="w"
        )
        self.clock_label = ttk.Label(status_frame, textvariable=self.clock_var)
        self.clock_label.grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(5, 0)
        )
        ttk.Label(
            status_frame,
            textvariable=self.countdown_var,
            font=("Consolas", 11, "bold"),
            foreground="#0b5aa6",
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))
        self.arm_hint_label = ttk.Label(
            status_frame, textvariable=self.arm_hint_var, foreground="#555555"
        )
        self.arm_hint_label.grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(4, 0)
        )
        self.selection_label = ttk.Label(
            status_frame, textvariable=self.selection_var, foreground="#555555"
        )
        self.selection_label.grid(
            row=4, column=0, columnspan=2, sticky="w", pady=(4, 0)
        )

        summary_frame = ttk.LabelFrame(outer, text="已识别课程（只读）", padding=8)
        summary_frame.pack(fill="x", pady=(10, 0))
        self.summary_text = tk.Text(summary_frame, height=4, wrap="word")
        self.summary_text.pack(fill="x")
        self.summary_text.configure(state="disabled")

        log_frame = ttk.LabelFrame(outer, text="实时日志", padding=8)
        log_frame.pack(fill="both", expand=True, pady=(10, 0))
        self.log_text = scrolledtext.ScrolledText(
            log_frame, height=15, wrap="word", font=("Consolas", 9)
        )
        self.log_text.pack(fill="both", expand=True)
        self.log_text.configure(state="disabled")

        self.target_var.trace_add("write", self._refresh_start_availability)
        self.pre_refresh_var.trace_add("write", self._refresh_pre_refresh_availability)
        self._set_summary([])
        self._update_pre_refresh_availability()

    def _row_entry(
        self,
        parent: ttk.LabelFrame,
        row: int,
        label: str,
        variable: tk.StringVar,
        *,
        readonly: bool = False,
    ) -> None:
        ttk.Label(parent, text=label, width=19).grid(
            row=row, column=0, sticky="w", pady=3
        )
        entry = ttk.Entry(parent, textvariable=variable)
        if readonly:
            entry.configure(state="readonly")
        entry.grid(row=row, column=1, sticky="ew", pady=3)

    def _load_fields(self, settings: Settings) -> None:
        self.target_var.set(settings.target_datetime)
        self.timezone_var.set(settings.timezone)
        self.url_var.set(settings.shopping_cart_url)
        self.pre_refresh_var.set(settings.enable_pre_refresh)
        self.refresh_seconds_var.set(f"{settings.refresh_before_seconds:g}")
        self.login_timeout_var.set(f"{settings.manual_login_timeout_seconds / 60:g}")
        self.keep_browser_var.set(settings.keep_browser_open_after_run)

    def _values_from_fields(self) -> dict[str, object]:
        return {
            "target_datetime": self.target_var.get().strip(),
            "timezone": "Asia/Shanghai",
            "shopping_cart_url": "https://sisn.hkust-gz.edu.cn/student/student-my-cart",
            "browser_channel": "msedge",
            "enroll_button_selector": "button.btn-cart-enrol",
            "select_all_checkbox_selector": (
                'div.mine-select input.el-checkbox__original[type="checkbox"]'
                '[value="Select All"]'
            ),
            "select_all_click_target_selector": (
                'div.mine-select label.el-checkbox:has('
                'input.el-checkbox__original[type="checkbox"]'
                '[value="Select All"])'
            ),
            "enable_pre_refresh": bool(self.pre_refresh_var.get()),
            "refresh_before_seconds": float(self.refresh_seconds_var.get()),
            "manual_login_timeout_seconds": float(self.login_timeout_var.get()) * 60,
            "keep_browser_open_after_run": bool(self.keep_browser_var.get()),
            "require_arm_phrase_confirmation": False,
            "click_confirmation_button": False,
            "confirmation_button_selector": "",
        }

    def _save_fields(self, *, show_success: bool = True) -> Settings | None:
        try:
            settings = update_config_file(
                self.config_path, self._values_from_fields()
            )
            TargetSchedule.parse(settings.target_datetime, settings.timezone)
        except (ValueError, ConfigError) as exc:
            messagebox.showerror("配置错误", str(exc), parent=self.root)
            return None
        self.settings = settings
        if show_success:
            messagebox.showinfo(
                "配置已保存", f"已保存到：\n{self.config_path}", parent=self.root
            )
        return settings

    def _attach_logger(self) -> None:
        logger = configure_logging(self.settings.log_file)
        if not any(isinstance(handler, QueueLogHandler) for handler in logger.handlers):
            handler = QueueLogHandler(self.events)
            handler.setFormatter(IsoMillisFormatter("%(asctime)s %(levelname)s %(message)s"))
            logger.addHandler(handler)
        self.logger = logger

    def _start(self, armed: bool) -> None:
        if self.running:
            messagebox.showwarning("正在运行", "已有任务正在运行。", parent=self.root)
            return
        settings = self._save_fields(show_success=False)
        if settings is None:
            return
        try:
            schedule = TargetSchedule.parse(settings.target_datetime, settings.timezone)
            if armed:
                schedule.ensure_future()
        except EnrollClickerError as exc:
            messagebox.showerror("无法启动", str(exc), parent=self.root)
            return

        self.running = True
        self.stop_event = threading.Event()
        self._set_running_controls(True)
        self.mode_var.set("定时" if armed else "检查")
        self.status_var.set("正在启动专用 Edge…")
        self.countdown_var.set("")
        self.selection_var.set("全选状态：正在检查…")
        self.selection_label.configure(foreground="#555555")
        self._set_summary([])
        self.worker = threading.Thread(
            target=self._worker_main,
            args=(settings, armed),
            daemon=True,
        )
        self.worker.start()

    def _worker_main(self, settings: Settings, armed: bool) -> None:
        def callback(kind: str, payload: object) -> None:
            self.events.put((kind, payload))

        try:
            result = run_with_settings(
                settings,
                armed=armed,
                logger=self.logger,
                confirmation_provider=None,
                stop_event=self.stop_event,
                event_callback=callback,
            )
            self.events.put(("finished", result, None))
        except Exception as exc:
            self.logger.exception("GUI run stopped")
            self.events.put(("finished", 2, str(exc)))

    def _poll_events(self) -> None:
        try:
            while True:
                event = self.events.get_nowait()
                kind = event[0]
                if kind == "log":
                    self._append_log(str(event[1]))
                elif kind == "mode":
                    self.mode_var.set(self._translate_mode(str(event[1])))
                elif kind == "status":
                    self.status_var.set(self._translate_status(str(event[1])))
                elif kind == "button":
                    self._append_log("BUTTON " + str(event[1]))
                elif kind == "courses":
                    self._set_summary(list(event[1]))
                elif kind == "selection":
                    self._set_selection(dict(event[1]))
                elif kind == "cart_state":
                    self._set_cart_state(dict(event[1]))
                elif kind == "screenshot":
                    self._append_log("SCREENSHOT " + str(event[1]))
                elif kind == "progress":
                    data = dict(event[1])
                    self.countdown_var.set(
                        f"{self._translate_progress(str(data['label']))}｜"
                        f"还剩 {float(data['remaining_seconds']):.1f} 秒｜"
                        f"目标 {data['target']}"
                    )
                elif kind == "finished":
                    self._finish_run(int(event[1]), event[2])
        except queue.Empty:
            pass
        self.root.after(100, self._poll_events)

    def _request_stop(self) -> None:
        if not self.running:
            return
        self.stop_event.set()
        self.status_var.set("正在安全停止；不会重试点击…")
        self.stop_button.configure(state="disabled")

    def _finish_run(self, code: int, error: object) -> None:
        self.running = False
        self._set_running_controls(False)
        if error:
            self.status_var.set("已安全停止")
            messagebox.showerror("任务停止", str(error), parent=self.root)
        elif code == 0:
            self.status_var.set("任务完成")
            messagebox.showinfo("任务完成", "运行已结束，请查看下方日志。", parent=self.root)
        else:
            self.status_var.set(f"任务结束，代码 {code}")

    def _set_running_controls(self, running: bool) -> None:
        normal = "disabled" if running else "normal"
        self.save_button.configure(state=normal)
        self.dry_button.configure(state=normal)
        self.stop_button.configure(state="normal" if running else "disabled")
        if running:
            self.arm_button.configure(state="disabled")
        else:
            self._update_start_availability()

    def _append_log(self, line: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", line + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _set_summary(self, items: list[object]) -> None:
        self.summary_text.configure(state="normal")
        self.summary_text.delete("1.0", "end")
        if items:
            self.summary_text.insert(
                "end", f"已读取 {len(items)} 项：\n" + "\n".join(f"• {item}" for item in items)
            )
        else:
            self.summary_text.insert(
                "end",
                "未识别到课程。请确认专用 Edge 已停留在 Shopping Cart；"
                "这不会影响按钮检查，也不会修改课程。",
            )
        self.summary_text.configure(state="disabled")

    def _set_selection(self, data: dict[str, object]) -> None:
        if bool(data.get("cart_empty")):
            self.selection_var.set("全选状态：购物车为空")
            self.selection_label.configure(foreground="#b00020")
        elif int(data.get("count", 0)) != 1 or int(
            data.get("click_target_count", 0)
        ) != 1:
            self.selection_var.set("全选状态：定位异常，已停止")
            self.selection_label.configure(foreground="#b00020")
        elif bool(data.get("checked")):
            self.selection_var.set("全选状态：已选中")
            self.selection_label.configure(foreground="#137333")
        else:
            self.selection_var.set("全选状态：未选中；正式运行时会自动选择")
            self.selection_label.configure(foreground="#9a5b00")

    def _set_cart_state(self, data: dict[str, object]) -> None:
        status = str(data.get("status", "unknown"))
        count = int(data.get("summary_count", 0))
        if status == "ready":
            self.status_var.set(f"购物车加载完成，识别到 {count} 项")
        elif status == "empty":
            self.status_var.set("购物车加载完成，但当前为空")
            self.selection_var.set("全选状态：购物车为空")
            self.selection_label.configure(foreground="#b00020")
        else:
            self.status_var.set(f"购物车状态异常：{status}")

    @staticmethod
    def _translate_mode(value: str) -> str:
        return {"ARMED": "定时", "DRY-RUN": "检查", "IDLE": "待机"}.get(
            value, value
        )

    @staticmethod
    def _translate_progress(value: str) -> str:
        return {
            "Waiting for one pre-refresh": "等待刷新",
            "Waiting for target": "等待目标时间",
        }.get(value, value)

    @staticmethod
    def _translate_status(value: str) -> str:
        translations = {
            "Opening dedicated Edge for dry-run": "正在打开购物车…",
            "Opening dedicated Edge for armed run": "正在打开购物车…",
            "Dry-run complete; no click performed": "检查完成，未点击",
            "Dry-run complete; Shopping Cart is empty; no click performed": (
                "检查完成：购物车为空，未点击"
            ),
            "Dry-run complete; Select All is checked; no click performed": (
                "检查完成：课程已全选，未点击 Enrol"
            ),
            "Dry-run complete; Select All is unchecked; no click performed": (
                "检查完成：课程未全选，未执行页面点击"
            ),
            "Select All checked once": "已自动全选购物车课程",
            "Select All already checked": "购物车课程已经全选",
            "Dry-run complete; Shopping Cart kept open until Safe Stop/manual close": (
                "检查完成，购物车窗口已保留"
            ),
            "Single pre-refresh completed": "定时前刷新完成",
            "One Enrol click attempted; follow-up remains manual": (
                "已尝试点击一次，后续请手动处理"
            ),
            "Shopping Cart kept open for manual follow-up; use Safe Stop when done": (
                "购物车已保留，完成后请点“停止”"
            ),
            "Click attempt failed/timed out; Shopping Cart kept open": (
                "点击失败或超时，购物车窗口已保留"
            ),
        }
        return translations.get(value, value)

    def _refresh_start_availability(self, *_args: object) -> None:
        """Schedule a safe button-state refresh after a Tk variable changes."""

        self.root.after_idle(self._update_start_availability)

    def _refresh_pre_refresh_availability(self, *_args: object) -> None:
        self.root.after_idle(self._update_pre_refresh_availability)

    def _update_pre_refresh_availability(self) -> None:
        self.refresh_spinbox.configure(
            state="normal" if self.pre_refresh_var.get() else "disabled"
        )

    def _update_start_availability(self) -> None:
        if self.running:
            self.arm_button.configure(state="disabled")
            self.arm_hint_var.set("任务运行中；正式点击不会重试。")
            self.arm_hint_label.configure(foreground="#555555")
            return

        try:
            schedule = TargetSchedule.parse(self.target_var.get(), "Asia/Shanghai")
            remaining = schedule.seconds_remaining()
        except Exception:
            self.arm_button.configure(state="disabled")
            self.arm_hint_var.set("请填写有效目标时间。")
            self.arm_hint_label.configure(foreground="#b00020")
            return

        if remaining <= 0:
            self.arm_button.configure(state="disabled")
            self.arm_hint_var.set("目标时间已过期；修改为未来时间后才能开始定时。")
            self.arm_hint_label.configure(foreground="#b00020")
        else:
            self.arm_button.configure(state="normal")
            self.arm_hint_var.set("可以开始定时；只尝试一次 Enrol。")
            self.arm_hint_label.configure(foreground="#137333")

    def _update_clock(self) -> None:
        try:
            schedule = TargetSchedule.parse(self.target_var.get(), "Asia/Shanghai")
            now = schedule.now()
            remaining = schedule.seconds_remaining(now)
            difference = (
                f"已过期 {abs(remaining):.1f} 秒"
                if remaining <= 0
                else f"还剩 {remaining:.1f} 秒"
            )
            self.clock_var.set(
                f"当前：{now.isoformat(timespec='seconds')}    "
                f"目标：{schedule.target.isoformat(timespec='seconds')}    "
                f"{difference}"
            )
            self.clock_label.configure(
                foreground="#b00020" if remaining <= 0 else "#222222"
            )
        except Exception:
            self.clock_var.set("目标时间格式无效；示例：2026-09-01T15:30:00+08:00")
            self.clock_label.configure(foreground="#b00020")
        self._update_start_availability()
        self.root.after(250, self._update_clock)

    def _on_close(self) -> None:
        if self.running:
            if not messagebox.askyesno(
                "停止任务？",
                "任务仍在运行。是否先安全停止并关闭界面？",
                parent=self.root,
            ):
                return
            self.stop_event.set()
            self.status_var.set("正在安全停止…")
            self._wait_then_close()
            return
        self.root.destroy()

    def _wait_then_close(self) -> None:
        if self.worker is not None and self.worker.is_alive():
            self.root.after(200, self._wait_then_close)
        else:
            self.root.destroy()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Tkinter GUI for SIS Enrol scheduler")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root = tk.Tk()
    try:
        EnrollClickerGui(root, Path(args.config))
    except Exception as exc:
        root.withdraw()
        messagebox.showerror("启动失败", str(exc), parent=root)
        root.destroy()
        raise SystemExit(2) from exc
    root.mainloop()


if __name__ == "__main__":
    main()
