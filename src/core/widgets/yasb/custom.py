import json
import re
import shlex
import subprocess
import threading

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QLabel

from core.utils.tooltip import set_tooltip
from core.utils.win32.system_function import function_map
from core.validation.widgets.yasb.custom import CustomConfig
from core.widgets.base import BaseWidget


class CustomWorker(QObject):
    finished = pyqtSignal()
    data_ready = pyqtSignal(object)

    def __init__(
        self,
        cmd: list[str] | None,
        use_shell: bool,
        encoding: str | None,
        return_type: str,
        hide_empty: bool,
    ):
        super().__init__()
        self.cmd = cmd
        self.use_shell = use_shell
        self.encoding = encoding
        self.return_type = return_type
        self.hide_empty = hide_empty
        self._is_running = True

    def stop(self):
        self._is_running = False

    def run(self):
        import os
        exec_data = None
        if self.cmd and self._is_running:
            is_file_read = False
            file_path = None
            
            if isinstance(self.cmd, str):
                cmd_str = self.cmd.strip()
                if cmd_str.lower().startswith("type "):
                    file_path = cmd_str[5:].strip(' "\'')
                    is_file_read = True
                elif cmd_str.lower().startswith("cat "):
                    file_path = cmd_str[4:].strip(' "\'')
                    is_file_read = True
            elif isinstance(self.cmd, list) and len(self.cmd) >= 2:
                if self.cmd[0].lower() in ["type", "cat"]:
                    file_path = self.cmd[1].strip(' "\'')
                    is_file_read = True

            if is_file_read and file_path and os.path.isfile(file_path):
                try:
                    with open(file_path, "r", encoding=self.encoding or "utf-8") as f:
                        output = f.read()
                    
                    if self.return_type == "json":
                        try:
                            exec_data = json.loads(output)
                        except json.JSONDecodeError:
                            exec_data = None
                    else:
                        exec_data = output.strip()
                except Exception:
                    pass
            else:
                proc = subprocess.Popen(
                    self.cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                    shell=self.use_shell,
                    encoding=self.encoding,
                )
                try:
                    output = proc.stdout.read()
                    if self.return_type == "json":
                        try:
                            exec_data = json.loads(output)
                        except json.JSONDecodeError:
                            exec_data = None
                    else:
                        exec_data = output.decode("utf-8").strip() if isinstance(output, bytes) else output.strip()
                finally:
                    proc.stdout.close()
                    proc.wait()

        if self._is_running:
            try:
                self.data_ready.emit(exec_data)
                self.finished.emit()
            except RuntimeError:
                pass


class CustomWidget(BaseWidget):
    validation_schema = CustomConfig

    def __init__(self, config: CustomConfig):
        super().__init__(config.exec_options.run_interval, class_name=f"custom-widget {config.class_name}")
        self.config = config
        self._exec_data: dict | str | None = None
        self._exec_cmd = self._build_exec_cmd(self.config.exec_options.run_cmd, self.config.exec_options.use_shell)
        self._show_alt_label = False
        self._worker = None  # Keep reference to worker for cleanup

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
        active_parsed = self._parsed_label_alt if self._show_alt_label else self._parsed_label
        for widget, parsed in zip(active_widgets, active_parsed):
            if parsed["is_icon"]:
                widget.setText(parsed["text"])
            else:
                part = parsed["text"]
                try:
                    widget.setText(self._truncate_label(part.format(data=self._exec_data)))
                except Exception:
                    widget.setText(self._truncate_label(part))
                    
                if self.config.exec_options.hide_empty:
                    if self._exec_data:
                        self.setVisible(True)
                    else:
                        self.setVisible(False)

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
            if self._worker:
                self._worker.stop()
                try:
                    self._worker.data_ready.disconnect(self._handle_exec_data)
                except (TypeError, RuntimeError):
                    pass

            self._worker = CustomWorker(
                self._exec_cmd,
                self.config.exec_options.use_shell,
                self.config.exec_options.encoding,
                self.config.exec_options.return_format,
                self.config.exec_options.hide_empty,
            )
            worker_thread = threading.Thread(target=self._worker.run, daemon=True)
            self._worker.data_ready.connect(self._handle_exec_data)
            self._worker.finished.connect(self._worker.deleteLater)
            worker_thread.start()
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
