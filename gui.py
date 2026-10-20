"""Десктопный GUI (customtkinter) для пайплайна TikTok/Shorts (Python + FFmpeg).

Запуск из исходников:  python gui.py
Пайплайн и генерация озвучки исполняются в отдельных потоках, логи идут в текстовое поле.
"""

import os
import subprocess
import sys
import threading

import customtkinter as ctk
from tkinter import Menu, filedialog

import cancellation
import config
from build_timeline import run_pipeline

ENV_PATH = os.path.join(config.BASE_DIR, ".env")

WHISPER_MODELS = ["base (быстро)", "small (баланс)", "medium (качество)"]
VOICES = ["ru-RU-DmitryNeural", "ru-RU-SvetlanaNeural", "ru-RU-DariyaNeural"]

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


# ---------------------------------------------------------------------------
# .env (хранение настроек между запусками)
# ---------------------------------------------------------------------------
def load_env_file() -> dict:
    data: dict = {}
    if os.path.isfile(ENV_PATH):
        with open(ENV_PATH, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                data[key.strip()] = value.strip().strip('"')
    return data


def save_env_file(values: dict) -> None:
    lines = ["# Автосохранённые настройки GUI"]
    lines += [f'{key}="{value}"' for key, value in values.items()]
    lines.append("")
    with open(ENV_PATH, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines))


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------
class PipelineApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("TikTok Movie Cutter")
        self.geometry("920x820")
        self.minsize(780, 700)

        self._output_dir = ctk.StringVar(value=config.OUTPUT_DIR)
        self._whisper_model = ctk.StringVar(value=WHISPER_MODELS[1])
        self._voice = ctk.StringVar(value=config.TTS_VOICE)
        self._apply_zoom = ctk.BooleanVar(value=config.APPLY_ZOOM)
        self._burn_subs = ctk.BooleanVar(value=config.BURN_SUBTITLES)

        self.is_cancelled = False
        self._running = False
        self._voice_running = False

        self._text_widgets: list = []
        self._menu_target = None

        self._build_layout()
        self._load_saved_settings()

        self._text_widgets = [
            self.query_entry, self.url_entry, self.key_entry,
            self.output_entry, self.voice_text, self.log_box,
        ]
        self._setup_clipboard()

    # -- разметка --
    def _build_layout(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(6, weight=1)

        ctk.CTkLabel(self, text="Генератор вертикальных видео",
                     font=ctk.CTkFont(size=22, weight="bold")).grid(
            row=0, column=0, padx=20, pady=(16, 4), sticky="w")

        self._build_form()
        self._build_voice_block()
        self._build_actions()
        self._build_progress_and_log()

    def _build_form(self) -> None:
        form = ctk.CTkFrame(self)
        form.grid(row=1, column=0, padx=20, pady=6, sticky="ew")
        form.grid_columnconfigure(1, weight=1)
        form.grid_columnconfigure(3, weight=1)

        ctk.CTkLabel(form, text="Фильм / запрос:").grid(
            row=0, column=0, padx=(12, 8), pady=(12, 6), sticky="w")
        self.query_entry = ctk.CTkEntry(form, placeholder_text="например: Matrix")
        self.query_entry.grid(row=0, column=1, padx=(0, 12), pady=(12, 6), sticky="ew")

        ctk.CTkLabel(form, text="Ссылка YouTube:").grid(
            row=0, column=2, padx=(8, 8), pady=(12, 6), sticky="w")
        self.url_entry = ctk.CTkEntry(
            form, placeholder_text="https://youtu.be/... (необязательно)")
        self.url_entry.grid(row=0, column=3, padx=(0, 12), pady=(12, 6), sticky="ew")

        ctk.CTkLabel(form, text="API ключ Clip.Cafe:").grid(
            row=1, column=0, padx=(12, 8), pady=6, sticky="w")
        self.key_entry = ctk.CTkEntry(form, show="*",
                                      placeholder_text="пусто = локальные клипы")
        self.key_entry.grid(row=1, column=1, columnspan=3,
                            padx=(0, 12), pady=6, sticky="ew")

        ctk.CTkLabel(form, text="Папка вывода:").grid(
            row=2, column=0, padx=(12, 8), pady=6, sticky="w")
        path_row = ctk.CTkFrame(form, fg_color="transparent")
        path_row.grid(row=2, column=1, columnspan=3, padx=(0, 12), pady=6, sticky="ew")
        path_row.grid_columnconfigure(0, weight=1)
        self.output_entry = ctk.CTkEntry(path_row, textvariable=self._output_dir)
        self.output_entry.grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(path_row, text="Выбрать папку", width=130,
                      command=self._choose_dir).grid(row=0, column=1, padx=(8, 0))

        ctk.CTkLabel(form, text="Модель Whisper:").grid(
            row=3, column=0, padx=(12, 8), pady=(6, 6), sticky="w")
        ctk.CTkOptionMenu(form, values=WHISPER_MODELS,
                          variable=self._whisper_model).grid(
            row=3, column=1, padx=(0, 12), pady=(6, 6), sticky="ew")
        ctk.CTkCheckBox(form, text="Добавить зум (Punch-in)",
                        variable=self._apply_zoom).grid(
            row=3, column=2, columnspan=2, padx=(8, 12), pady=(6, 6), sticky="w")

        ctk.CTkCheckBox(form, text="Встроить субтитры в видео",
                        variable=self._burn_subs).grid(
            row=4, column=1, columnspan=3, padx=(0, 12), pady=(0, 12), sticky="w")

    def _build_voice_block(self) -> None:
        frame = ctk.CTkFrame(self)
        frame.grid(row=2, column=0, padx=20, pady=6, sticky="ew")
        frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(frame, text="Озвучка (Edge TTS)",
                     font=ctk.CTkFont(size=15, weight="bold")).grid(
            row=0, column=0, columnspan=3, padx=12, pady=(10, 4), sticky="w")

        self.voice_text = ctk.CTkTextbox(frame, height=80, wrap="word")
        self.voice_text.grid(row=1, column=0, columnspan=3,
                             padx=12, pady=(0, 6), sticky="ew")
        self.voice_text.insert("1.0", "Вставь сюда текст для озвучки...")

        ctk.CTkLabel(frame, text="Голос:").grid(
            row=2, column=0, padx=(12, 8), pady=(0, 12), sticky="w")
        ctk.CTkOptionMenu(frame, values=VOICES, variable=self._voice,
                          width=230).grid(row=2, column=1, padx=(0, 12),
                                          pady=(0, 12), sticky="w")
        self.voice_btn = ctk.CTkButton(
            frame, text="Сгенерировать озвучку", width=200,
            fg_color="#7C3AED", hover_color="#6D28D9",
            command=self._on_generate_voice)
        self.voice_btn.grid(row=2, column=2, padx=(0, 12), pady=(0, 12), sticky="e")

    def _build_actions(self) -> None:
        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.grid(row=3, column=0, padx=20, pady=8, sticky="ew")
        actions.grid_columnconfigure(0, weight=1)

        self.generate_btn = ctk.CTkButton(
            actions, text="СГЕНЕРИРОВАТЬ ВИДЕО", height=52,
            font=ctk.CTkFont(size=18, weight="bold"),
            fg_color="#E11D48", hover_color="#BE123C",
            command=self._on_generate)
        self.generate_btn.grid(row=0, column=0, sticky="ew")

        self.cancel_btn = ctk.CTkButton(
            actions, text="ОТМЕНА", height=52, width=160,
            font=ctk.CTkFont(size=18, weight="bold"),
            fg_color="#374151", hover_color="#4B5563",
            command=self._on_cancel)
        self.cancel_btn.grid(row=0, column=0, sticky="ew")
        self.cancel_btn.grid_remove()

        self.open_btn = ctk.CTkButton(
            actions, text="Открыть папку", height=52, width=150,
            fg_color="#0F766E", hover_color="#0D9488",
            command=self._open_output_dir)
        self.open_btn.grid(row=0, column=1, padx=(10, 0))

    def _build_progress_and_log(self) -> None:
        self.progress = ctk.CTkProgressBar(self, mode="determinate")
        self.progress.grid(row=4, column=0, padx=20, pady=(4, 2), sticky="ew")
        self.progress.set(0)

        self.status = ctk.CTkLabel(self, text="Готов к запуску.", anchor="w")
        self.status.grid(row=5, column=0, padx=24, pady=(2, 6), sticky="w")

        self.log_box = ctk.CTkTextbox(self, wrap="word", font=ctk.CTkFont(size=12))
        self.log_box.grid(row=6, column=0, padx=20, pady=(6, 16), sticky="nsew")
        self.log_box.configure(state="disabled")

    # ------------------------------------------------------------------
    # БУФЕР ОБМЕНА / ГОРЯЧИЕ КЛАВИШИ / КОНТЕКСТНОЕ МЕНЮ
    # ------------------------------------------------------------------
    @staticmethod
    def _inner_widget(widget):
        return (getattr(widget, "_entry", None)
                or getattr(widget, "_textbox", None)
                or widget)

    def _setup_clipboard(self) -> None:
        ws = self.tk.call("tk", "windowingsystem")
        mod = "Command" if ws == "aqua" else "Control"

        self._clipboard_bindings = {
            "paste": [f"<{mod}-v>", f"<{mod}-V>", "<Shift-Insert>"],
            "copy": [f"<{mod}-c>", f"<{mod}-C>"],
            "cut": [f"<{mod}-x>", f"<{mod}-X>", "<Shift-Delete>"],
            "select_all": [f"<{mod}-a>", f"<{mod}-A>"],
            "undo": [f"<{mod}-z>", f"<{mod}-Z>"],
        }

        self._context_menu = Menu(self, tearoff=0)
        self._context_menu.add_command(label="Вырезать", command=self._ctx_cut)
        self._context_menu.add_command(label="Копировать", command=self._ctx_copy)
        self._context_menu.add_command(label="Вставить", command=self._ctx_paste)
        self._context_menu.add_separator()
        self._context_menu.add_command(label="Выделить всё",
                                       command=self._ctx_select_all)

        for widget in self._text_widgets:
            self._bind_text_widget(widget)

    def _bind_text_widget(self, widget) -> None:
        inner = self._inner_widget(widget)
        for action, sequences in self._clipboard_bindings.items():
            for seq in sequences:
                inner.bind(seq, lambda e, a=action, w=inner: self._on_shortcut(a, w))
        for seq in ("<Button-3>", "<Button-2>", "<Control-Button-1>"):
            inner.bind(seq, lambda e, w=inner: self._show_context_menu(e, w))

    def _on_shortcut(self, action: str, widget):
        if action == "select_all":
            return self._select_all(widget)
        if action == "undo":
            try:
                widget.event_generate("<<Undo>>")
            except Exception:  # noqa: BLE001
                pass
            return "break"
        virtual = {"paste": "<<Paste>>", "copy": "<<Copy>>", "cut": "<<Cut>>"}[action]
        try:
            widget.event_generate(virtual)
        except Exception:  # noqa: BLE001
            pass
        return "break"

    @staticmethod
    def _select_all(widget):
        try:  # Text
            widget.tag_add("sel", "1.0", "end")
            widget.mark_set("insert", "end")
        except Exception:  # noqa: BLE001
            try:  # Entry
                widget.selection_range(0, "end")
                widget.icursor("end")
            except Exception:  # noqa: BLE001
                pass
        return "break"

    def _show_context_menu(self, event, widget):
        self._menu_target = widget
        try:
            widget.focus_set()
            self._context_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self._context_menu.grab_release()
        return "break"

    def _ctx_paste(self):
        if self._menu_target:
            self._menu_target.event_generate("<<Paste>>")

    def _ctx_copy(self):
        if self._menu_target:
            self._menu_target.event_generate("<<Copy>>")

    def _ctx_cut(self):
        if self._menu_target:
            self._menu_target.event_generate("<<Cut>>")

    def _ctx_select_all(self):
        if self._menu_target:
            self._select_all(self._menu_target)

    # -- настройки --
    def _load_saved_settings(self) -> None:
        data = load_env_file()
        if data.get("CLIP_CAFE_API_KEY"):
            self.key_entry.insert(0, data["CLIP_CAFE_API_KEY"])
        if data.get("TIKTOK_OUTPUT_DIR"):
            self._output_dir.set(data["TIKTOK_OUTPUT_DIR"])
        if data.get("LAST_QUERY") and not self.query_entry.get().strip():
            self.query_entry.insert(0, data["LAST_QUERY"])
        if data.get("YT_VIDEO_URL") and not self.url_entry.get().strip():
            self.url_entry.insert(0, data["YT_VIDEO_URL"])
        if data.get("TTS_VOICE") in VOICES:
            self._voice.set(data["TTS_VOICE"])
        if data.get("APPLY_ZOOM") is not None:
            self._apply_zoom.set(data["APPLY_ZOOM"] != "0")
        if data.get("BURN_SUBTITLES") is not None:
            self._burn_subs.set(data["BURN_SUBTITLES"] != "0")
        if data.get("WHISPER_MODEL"):
            for label in WHISPER_MODELS:
                if label.startswith(data["WHISPER_MODEL"]):
                    self._whisper_model.set(label)

    def _choose_dir(self) -> None:
        chosen = filedialog.askdirectory(title="Куда сохранить видео")
        if chosen:
            self._output_dir.set(chosen)

    # -- логи/статус/прогресс (из рабочих потоков) --
    def append_log(self, text: str) -> None:
        self.after(0, self._append_log_main, text)

    def _append_log_main(self, text: str) -> None:
        self.log_box.configure(state="normal")
        self.log_box.insert("end", text + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _set_status(self, text: str) -> None:
        self.after(0, lambda: self.status.configure(text=text))

    def update_progress(self, step: int, total: int, message: str) -> None:
        def apply() -> None:
            self.progress.set(step / max(1, total))
            self.status.configure(text=f"Шаг {step} из {total}: {message}...")
        self.after(0, apply)

    # -- открыть папку --
    def _open_output_dir(self) -> None:
        path = self._output_dir.get().strip() or config.OUTPUT_DIR
        try:
            os.makedirs(path, exist_ok=True)
            if os.name == "nt":
                os.startfile(path)  # noqa: S606
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as exc:  # noqa: BLE001
            self.append_log(f"Не удалось открыть папку: {exc}")

    # -- озвучка --
    def _on_generate_voice(self) -> None:
        if self._voice_running:
            return
        text = self.voice_text.get("1.0", "end").strip()
        if not text:
            self.append_log("Введите текст для озвучки.")
            return
        speaker = self._voice.get()
        self._voice_running = True
        self.voice_btn.configure(state="disabled", text="Генерирую...")
        self._set_status("Генерация озвучки...")
        threading.Thread(target=self._voice_worker, args=(text, speaker),
                         daemon=True).start()

    def _voice_worker(self, text: str, speaker: str) -> None:
        try:
            from tts_generator import generate_voice
            path = generate_voice(text, speaker, config.AUDIO_PATH,
                                  log_callback=self.append_log)
            self.append_log(f"Озвучка готова: {path}. Можно запускать видео.")
            self._set_status("Озвучка готова.")
        except Exception as exc:  # noqa: BLE001
            self.append_log(f"Ошибка озвучки: {exc}")
            self._set_status("Ошибка озвучки.")
        finally:
            self.after(0, self._reset_voice_ui)

    def _reset_voice_ui(self) -> None:
        self._voice_running = False
        self.voice_btn.configure(state="normal", text="Сгенерировать озвучку")

    # -- пайплайн --
    def _on_cancel(self) -> None:
        self.is_cancelled = True
        cancellation.request_cancel()
        self.cancel_btn.configure(state="disabled")
        self.append_log("Запрошена отмена — останавливаю текущий шаг...")

    def _on_generate(self) -> None:
        if self._running:
            return

        query = self.query_entry.get().strip()
        video_url = self.url_entry.get().strip()
        api_key = self.key_entry.get().strip()
        output_dir = self._output_dir.get().strip() or config.OUTPUT_DIR
        whisper_model = self._whisper_model.get().split()[0]
        apply_zoom = bool(self._apply_zoom.get())
        burn_subs = bool(self._burn_subs.get())

        try:
            os.makedirs(output_dir, exist_ok=True)
            save_env_file({
                "CLIP_CAFE_API_KEY": api_key,
                "TIKTOK_OUTPUT_DIR": output_dir,
                "WHISPER_MODEL": whisper_model,
                "TTS_VOICE": self._voice.get(),
                "APPLY_ZOOM": "1" if apply_zoom else "0",
                "BURN_SUBTITLES": "1" if burn_subs else "0",
                "LAST_QUERY": query,
                "YT_VIDEO_URL": video_url,
            })
            self.append_log(f"Настройки сохранены в {ENV_PATH}")
        except OSError as exc:
            self.append_log(f"Не удалось сохранить настройки: {exc}")

        self.is_cancelled = False
        self._running = True
        self.generate_btn.grid_remove()
        self.cancel_btn.grid()
        self.cancel_btn.configure(state="normal")
        self.progress.set(0)
        self._set_status("Запуск...")

        threading.Thread(
            target=self._worker,
            args=(query, video_url, api_key, output_dir, whisper_model,
                  apply_zoom, burn_subs),
            daemon=True,
        ).start()

    def _worker(self, query, video_url, api_key, output_dir,
                whisper_model, apply_zoom, burn_subs) -> None:
        try:
            ok = run_pipeline(
                query=query,
                api_key=api_key,
                output_dir=output_dir,
                log_callback=self.append_log,
                whisper_model=whisper_model,
                cancel_check=lambda: self.is_cancelled,
                progress_callback=self.update_progress,
                video_url=video_url,
                apply_zoom=apply_zoom,
                burn_subtitles=burn_subs,
            )
            if self.is_cancelled:
                self._set_status("Отменено пользователем.")
            else:
                self._set_status("Готово! Видео сохранено." if ok
                                 else "Завершено с ошибками — см. лог.")
        except Exception as exc:  # noqa: BLE001
            self.append_log(f"Критическая ошибка: {exc}")
            self._set_status("Ошибка выполнения.")
        finally:
            self.after(0, self._reset_ui)

    def _reset_ui(self) -> None:
        self._running = False
        self.cancel_btn.grid_remove()
        self.generate_btn.grid()
        self.generate_btn.configure(state="normal")


def main() -> None:
    PipelineApp().mainloop()


if __name__ == "__main__":
    main()