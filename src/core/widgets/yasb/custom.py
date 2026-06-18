import json
import re
import shlex
import subprocess
import threading

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QLabel

from core.utils.tooltip import set_tooltip
from core.utils.win32.system_function import function_map
from core.validation.widgets.yasb.custom import CustomConfig
from core.widgets.base import BaseWidget


class CustomWidget(BaseWidget):
    validation_schema = CustomConfig
    _data_ready_signal = pyqtSignal(object)

    def __init__(self, config: CustomConfig):
        super().__init__(config.exec_options.run_interval, class_name=f"custom-widget {config.class_name}")
        self.config = config
        self._exec_data: dict | str | None = None
        self._exec_cmd = self._build_exec_cmd(self.config.exec_options.run_cmd, self.config.exec_options.use_shell)
        self._show_alt_label = False
        self._worker: threading.Thread | None = None

        self._data_ready_signal.connect(self._handle_exec_data)

        # Construct container
        self._init_container()
        self.build_widget_label(
            self.config.label, self.config.label_alt, label_placeholder=self.config.label_placeholder
        )

        self.register_callback("toggle_label", self._toggle_label)
        self.register_callback("exec_custom", self._exec_callback)

        self.callback_left = self.config.callbacks.on_left
        self.callback_right = self.config.callbacks.on_right
        self.callback_middle = self.config.callbacks.on_middle
        self.callback_timer = "exec_custom"

        if self.config.exec_options.run_once:
            self._exec_callback()
        else:
            self.start_timer()

    @staticmethod
    def _build_exec_cmd(run_cmd: str | None, use_shell: bool) -> str | list[str] | None:
        if not run_cmd:
            return None

        if use_shell:
            return run_cmd

        return shlex.split(run_cmd, posix=False)

    def _toggle_label(self):
        self._show_alt_label = not self._show_alt_label
        for widget in self._widgets:
            widget.setVisible(not self._show_alt_label)
        for widget in self._widgets_alt:
            widget.setVisible(self._show_alt_label)
        self._update_label()

    def _truncate_label(self, label):
        if self.config.label_max_length and len(label) > self.config.label_max_length:
            return label[: self.config.label_max_length] + "..."
        return label

    def _update_label(self):
        active_widgets = self._widgets_alt if self._show_alt_label else self._widgets
        active_label_content = self.config.label_alt if self._show_alt_label else self.config.label
        label_parts = re.split("(<span.*?>.*?</span>)", active_label_content)
        widget_index = 0
        try:
            for part in label_parts:
                part = part.strip()
                if part and widget_index < len(active_widgets) and isinstance(active_widgets[widget_index], QLabel):
                    if "<span" in part and "</span>" in part:
                        icon = re.sub(r"<span.*?>|</span>", "", part).strip()
                        active_widgets[widget_index].setText(icon)
                    else:
                        active_widgets[widget_index].setText(self._truncate_label(part.format(data=self._exec_data)))
                    if self.config.exec_options.hide_empty:
                        if self._exec_data:
                            self.setVisible(True)
                            # active_widgets[widget_index].show()
                        else:
                            self.setVisible(False)
                            # active_widgets[widget_index].hide()
                    widget_index += 1
        except Exception:
            active_widgets[widget_index].setText(self._truncate_label(part))

        # Update tooltip if enabled
        self._update_tooltip()

    def _update_tooltip(self):
        """Update the tooltip text based on configuration and data."""
        if not self.config.tooltip or not self._exec_data:
            return

        tooltip_text = None

        # If custom tooltip_label provided, use it with formatting
        if self.config.tooltip_label:
            try:
                tooltip_text = self.config.tooltip_label.format(data=self._exec_data)
            except KeyError, AttributeError, TypeError, IndexError:
                # If formatting fails, fall back to showing raw data
                tooltip_text = str(self._exec_data)
        else:
            tooltip_text = (
                json.dumps(self._exec_data, indent=2) if isinstance(self._exec_data, dict) else str(self._exec_data)
            )

        if tooltip_text:
            set_tooltip(self._widget_container, tooltip_text, delay=400)

    def _exec_callback(self):
        if self._exec_cmd:
            # Skip if a previous execution is still running
            if self._worker and self._worker.is_alive():
                return

            def _run():
                exec_data = None
                try:
                    proc = subprocess.Popen(
                        self._exec_cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                        shell=self.config.exec_options.use_shell,
                        encoding=self.config.exec_options.encoding,
                    )
                    try:
                        output = proc.stdout.read()
                        if self.config.exec_options.return_format == "json":
                            try:
                                exec_data = json.loads(output)
                            except json.JSONDecodeError:
                                exec_data = None
                        else:
                            exec_data = output.decode("utf-8").strip()
                    finally:
                        proc.stdout.close()
                        proc.wait()
                except Exception:
                    pass
                # Deliver result back to main thread via the persistent signal
                try:
                    self._data_ready_signal.emit(exec_data)
                except RuntimeError:
                    pass

            self._worker = threading.Thread(target=_run, daemon=True)
            self._worker.start()
        else:
            self._update_label()

    def _handle_exec_data(self, exec_data):
        self._exec_data = exec_data
        self._update_label()

    def _cb_execute_subprocess(self, cmd: str, *cmd_args: list[str]):
        # Overrides the default 'exec' callback from BaseWidget to allow for data formatting
        if self._exec_data:
            formatted_cmd_args = []
            for cmd_arg in cmd_args:
                try:
                    formatted_cmd_args.append(cmd_arg.format(data=self._exec_data))
                except KeyError:
                    formatted_cmd_args.append(cmd_args)
            cmd_args = formatted_cmd_args
        if cmd in function_map:
            function_map[cmd]()
        else:
            subprocess.Popen(
                [cmd, *cmd_args] if cmd_args else [cmd],
                shell=self.config.exec_options.use_shell,
                encoding=self.config.exec_options.encoding,
            )
