"""
xilab_cfg_tool.py
=================
GUI утилита для работы с .cfg профилями XILab/mDrive.

Вкладка 1 — Patch: миграция ключей из исходников XILab
Вкладка 2 — Replace: поиск и замена через regex

Зависимости: только стандартная библиотека Python (3.6+)
"""

import os
import re
import sys
import glob
import hashlib
import configparser
import threading
import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext
from typing import Optional


# ════════════════════════════════════════════════════════════════════════════════
#  ЛОГИКА (из patch_cfg.py + cfg_replace.py)
# ════════════════════════════════════════════════════════════════════════════════

SAVE_KEY_DEFAULTS = {
    "feedbackTypeList":      "ENCODER",
    "feedbackEncTypeList":   "AUTO",
    "feedbackEncFilterList": "NONE",
    "homeFirstTypeList":     "REV",
    "homeSecondTypeList":    "REV",
    "engineTypeList":        "DC",
    "driverTypeList":        "INTEGRATE",
    "extioInTypeList":       "IN_NOP",
    "extioOutTypeList":      "OUT_OFF",
    "controlTypeList":       "OFF",
    "uartParityTypeList":    "EVEN",
}

VERIFICATION_RE = re.compile(
    r'\n\[VERIFICATION\]\ndate=\d{4}-\d{2}-\d{2}\nhash=\w+\n'
)


# ── Парсинг исходников ────────────────────────────────────────────────────────

def parse_save_key_lists(source):
    result = {}
    pattern = re.compile(r'(\w+List)\s*\n\s*<<\s*pair\("([^"]+)"', re.MULTILINE)
    for m in pattern.finditer(source):
        if m.group(1) not in result:
            result[m.group(1)] = m.group(2)
    return result


def parse_keys_from_source(filepath):
    with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
        source = f.read()

    save_key_defaults = {**SAVE_KEY_DEFAULTS, **parse_save_key_lists(source)}
    result = {}
    current_section = None

    func_match = re.search(r'FromClassToSettings\s*\(.*?\{(.+?)^}', source,
                           re.DOTALL | re.MULTILINE)
    if not func_match:
        return result

    for line in func_match.group(1).splitlines():
        line = line.strip()
        m = re.search(r'beginGroup\("([^"]+)"\)', line)
        if m:
            current_section = m.group(1)
            result.setdefault(current_section, {})
            continue
        if 'endGroup()' in line:
            current_section = None
            continue
        if current_section is None:
            continue
        m = re.search(r'setValue\("([^"]+)"', line)
        if m:
            key = m.group(1)
            default = "false" if ('!= 0' in line or 'bool' in line.lower()) else "0"
            result[current_section][key] = default
            continue
        m = re.search(r'SAVE_KEY\((\w+),\s*\S+,\s*\S+,\s*"([^"]+)"\)', line)
        if m:
            result[current_section][m.group(2)] = save_key_defaults.get(m.group(1), "")

    return result


def build_expected_keys(xilab_repo, log):
    src_dir = xilab_repo
    for root, dirs, files in os.walk(xilab_repo):
        if "motorsettings.cpp" in files:
            src_dir = root
            break

    expected = {}
    for filename in ["motorsettings.cpp", "stagesettings.cpp"]:
        filepath = os.path.join(src_dir, filename)
        if not os.path.isfile(filepath):
            log(f"  ⚠ Не найден: {filepath}")
            continue
        parsed = parse_keys_from_source(filepath)
        for section, keys in parsed.items():
            expected.setdefault(section, {}).update(keys)
        log(f"  ✓ {filename}: секций={len(parsed)}, ключей={sum(len(v) for v in parsed.values())}")

    return expected


# ── Верификация ───────────────────────────────────────────────────────────────

def compute_cfg_hash(filepath):
    with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
        text = f.read()
    text = re.sub(r'\[VERIFICATION\][^\[]*', '', text, flags=re.IGNORECASE)
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    return hashlib.md5(text.encode('utf-8')).hexdigest()


def get_stored_hash(filepath):
    with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
        text = f.read()
    m = re.search(r'\[VERIFICATION\].*?hash\s*=\s*([0-9a-fA-F]+)', text,
                  re.IGNORECASE | re.DOTALL)
    return m.group(1).lower() if m else None


def is_verified(filepath):
    stored = get_stored_hash(filepath)
    return stored is not None and stored == compute_cfg_hash(filepath)


def update_verification(filepath):
    new_hash = compute_cfg_hash(filepath)
    with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
        text = f.read()
    if re.search(r'\[VERIFICATION\]', text, re.IGNORECASE):
        text = re.sub(
            r'(\[VERIFICATION\].*?hash\s*=\s*)[0-9a-fA-F]+',
            lambda m: m.group(1) + new_hash,
            text, flags=re.IGNORECASE | re.DOTALL
        )
    else:
        text = text.rstrip('\n') + '\n\n[VERIFICATION]\nhash=' + new_hash + '\n'
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(text)


# ── Patch ─────────────────────────────────────────────────────────────────────

class CaseSensitiveConfigParser(configparser.RawConfigParser):
    def optionxform(self, s): return s


def patch_cfg(filepath, expected_keys, rehash=False):
    was_verified = is_verified(filepath)
    config = CaseSensitiveConfigParser()
    config.read(filepath, encoding='utf-8')

    added = []
    for section, keys in expected_keys.items():
        if not config.has_section(section):
            config.add_section(section)
        for key, default in keys.items():
            if not config.has_option(section, key):
                config.set(section, key, default)
                added.append(f"[{section}] {key} = {default}")

    if added:
        with open(filepath, 'w', encoding='utf-8') as f:
            config.write(f, space_around_delimiters=False)

    hash_status = None
    if rehash:
        update_verification(filepath)
        hash_status = 'forced'
    elif was_verified and added:
        update_verification(filepath)
        hash_status = 'updated'
    elif not was_verified and added:
        hash_status = 'skipped'

    return added, hash_status


def run_patch(xilab_repo, cfg_folder, rehash, log):
    log(f"Читаем исходники из: {xilab_repo}\n")
    expected_keys = build_expected_keys(xilab_repo, log)
    if not expected_keys:
        log("\n✗ Не удалось прочитать ключи из исходников!")
        return

    total_keys = sum(len(v) for v in expected_keys.values())
    log(f"  Итого: секций={len(expected_keys)}, ключей={total_keys}\n")

    cfg_files = sorted(glob.glob(os.path.join(cfg_folder, "*.cfg")))
    if not cfg_files:
        log(f"\n✗ Нет .cfg файлов в: {cfg_folder}")
        return

    log(f"Найдено файлов: {len(cfg_files)}\n")
    total_added = errors = 0

    for cfg_path in cfg_files:
        name = os.path.basename(cfg_path)
        try:
            added, hash_status = patch_cfg(cfg_path, expected_keys, rehash=rehash)
            if added:
                log(f"  ✓ {name} — добавлено: {len(added)}")
                for k in added:
                    log(f"      + {k}")
                total_added += len(added)
            else:
                log(f"  · {name} — актуален")
            if hash_status == 'updated': log(f"      # хэш пересчитан")
            elif hash_status == 'skipped': log(f"      # хэш не тронут (не был верифицирован)")
            elif hash_status == 'forced': log(f"      # хэш пересчитан (--rehash)")
        except Exception as e:
            log(f"  ✗ {name} — ошибка: {e}")
            errors += 1

    log(f"\n{'='*48}")
    log(f"Обработано: {len(cfg_files) - errors}  |  Добавлено ключей: {total_added}")
    if errors:
        log(f"Ошибок: {errors}")


# ── Replace ───────────────────────────────────────────────────────────────────

def preview_matches(cfg_folder, pattern, log):
    cfg_files = sorted(glob.glob(os.path.join(cfg_folder, "*.cfg")))
    total = 0
    for filepath in cfg_files:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        matches = list(re.finditer(pattern, content))
        if not matches:
            continue
        log(f"\n  {os.path.basename(filepath)}:")
        for m in matches:
            line_start = content.rfind('\n', 0, m.start()) + 1
            line_end = content.find('\n', m.end())
            line = content[line_start:line_end if line_end != -1 else None].strip()
            log(f"    {line}  →  [{m.group()}]")
            total += 1
    if total == 0:
        log("  Совпадений не найдено.")
    else:
        log(f"\n  Итого: {total}")


def apply_replacement(cfg_folder, pattern, replacement, log):
    cfg_files = sorted(glob.glob(os.path.join(cfg_folder, "*.cfg")))
    changed = 0
    for filepath in cfg_files:
        with open(filepath, 'r', encoding='utf-8') as f:
            original = f.read()
        had_verification = bool(VERIFICATION_RE.search(original))
        text = re.sub(pattern, replacement, original)
        if text == original:
            continue
        if had_verification:
            text = text.replace('\r\n', '\n')
            text_no_ver = VERIFICATION_RE.sub('', text)
            from datetime import date
            hash_val = hashlib.md5(text_no_ver.encode('utf-8')).hexdigest()
            text = text_no_ver + f'\n[VERIFICATION]\ndate={date.today().strftime("%Y-%m-%d")}\nhash={hash_val}\n'
        with open(filepath, 'w', encoding='utf-8', newline='\n') as f:
            f.write(text)
        ver = "верификация пересчитана" if had_verification else "верификации не было"
        log(f"  ✓ {os.path.basename(filepath)}  [{ver}]")
        changed += 1
    if changed == 0:
        log("  Ни один файл не изменён.")
    else:
        log(f"\n  Изменено файлов: {changed}/{len(cfg_files)}")


# ════════════════════════════════════════════════════════════════════════════════
#  GUI
# ════════════════════════════════════════════════════════════════════════════════

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("XILab / mDrive .cfg Tool")
        self.resizable(True, True)
        self.minsize(680, 520)

        nb = ttk.Notebook(self)
        nb.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        self.patch_tab = PatchTab(nb)
        self.replace_tab = ReplaceTab(nb)

        nb.add(self.patch_tab, text="  Patch  ")
        nb.add(self.replace_tab, text="  Replace  ")


# ── Общие виджеты ─────────────────────────────────────────────────────────────

def make_path_row(parent, label, placeholder, row):
    tk.Label(parent, text=label, anchor='w').grid(
        row=row, column=0, sticky='w', pady=(6, 0))
    var = tk.StringVar()
    entry = tk.Entry(parent, textvariable=var, width=52)
    entry.grid(row=row+1, column=0, sticky='ew', padx=(0, 4))
    entry.insert(0, placeholder)
    entry.config(foreground='gray')

    def on_focus_in(e):
        if entry.get() == placeholder:
            entry.delete(0, tk.END)
            entry.config(foreground='black')

    def on_focus_out(e):
        if not entry.get():
            entry.insert(0, placeholder)
            entry.config(foreground='gray')

    entry.bind('<FocusIn>', on_focus_in)
    entry.bind('<FocusOut>', on_focus_out)

    def browse():
        path = filedialog.askdirectory()
        if path:
            var.set(path)
            entry.config(foreground='black')

    tk.Button(parent, text="…", width=3, command=browse).grid(
        row=row+1, column=1, sticky='w')

    return var


def make_log(parent, row, colspan=2):
    log = scrolledtext.ScrolledText(parent, height=14, font=("Consolas", 9),
                                     state='disabled', wrap=tk.WORD)
    log.grid(row=row, column=0, columnspan=colspan, sticky='nsew', pady=(8, 0))
    return log


def log_append(widget, text):
    widget.config(state='normal')
    widget.insert(tk.END, text + '\n')
    widget.see(tk.END)
    widget.config(state='disabled')


def log_clear(widget):
    widget.config(state='normal')
    widget.delete('1.0', tk.END)
    widget.config(state='disabled')


# ── Вкладка Patch ─────────────────────────────────────────────────────────────

class PatchTab(tk.Frame):
    def __init__(self, parent):
        super().__init__(parent, padx=12, pady=8)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(6, weight=1)

        self.xilab_var = make_path_row(
            self, "Репозиторий XILab / mDrive (корень или папка src/):",
            "F:\\crzy\\GITLAB\\XILab", row=0)

        self.cfg_var = make_path_row(
            self, "Папка с .cfg файлами:",
            "F:\\crzy\\GITLAB\\xiresource\\profiles\\STANDA", row=2)

        ctrl = tk.Frame(self)
        ctrl.grid(row=4, column=0, columnspan=2, sticky='w', pady=(10, 0))

        self.rehash_var = tk.BooleanVar()
        tk.Checkbutton(ctrl, text="--rehash (пересчитать хэш для всех файлов)",
                       variable=self.rehash_var).pack(side=tk.LEFT)

        tk.Button(ctrl, text="Запустить", width=14,
                  command=self.run).pack(side=tk.RIGHT, padx=(12, 0))

        tk.Button(ctrl, text="Очистить лог",
                  command=lambda: log_clear(self.log)).pack(side=tk.RIGHT)

        self.log = make_log(self, row=6, colspan=2)

    def run(self):
        xilab = self.xilab_var.get()
        cfg = self.cfg_var.get()
        rehash = self.rehash_var.get()

        if not os.path.isdir(xilab):
            log_clear(self.log)
            log_append(self.log, f"✗ Папка не найдена: {xilab}")
            return
        if not os.path.isdir(cfg):
            log_clear(self.log)
            log_append(self.log, f"✗ Папка не найдена: {cfg}")
            return

        log_clear(self.log)
        log_append(self.log, f"Папка профилей: {cfg}")

        def worker():
            run_patch(xilab, cfg, rehash, lambda t: self.after(0, log_append, self.log, t))

        threading.Thread(target=worker, daemon=True).start()


# ── Вкладка Replace ───────────────────────────────────────────────────────────

class ReplaceTab(tk.Frame):
    def __init__(self, parent):
        super().__init__(parent, padx=12, pady=8)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(8, weight=1)

        self.cfg_var = make_path_row(
            self, "Папка с .cfg файлами:",
            "F:\\crzy\\GITLAB\\xiresource\\profiles\\STANDA", row=0)

        tk.Label(self, text="Регулярное выражение:", anchor='w').grid(
            row=2, column=0, sticky='w', pady=(10, 0))
        self.pattern_var = tk.StringVar()
        tk.Entry(self, textvariable=self.pattern_var, width=52, font=("Consolas", 10)).grid(
            row=3, column=0, columnspan=2, sticky='ew')

        tk.Label(self, text="Замена (\\g<1>, \\1 и т.д.):", anchor='w').grid(
            row=4, column=0, sticky='w', pady=(6, 0))
        self.replace_var = tk.StringVar()
        tk.Entry(self, textvariable=self.replace_var, width=52, font=("Consolas", 10)).grid(
            row=5, column=0, columnspan=2, sticky='ew')

        btn_frame = tk.Frame(self)
        btn_frame.grid(row=6, column=0, columnspan=2, sticky='w', pady=(10, 0))

        tk.Button(btn_frame, text="Preview", width=14,
                  command=self.preview).pack(side=tk.LEFT, padx=(0, 6))
        self.replace_btn = tk.Button(btn_frame, text="Replace", width=14,
                                      command=self.replace, state='disabled')
        self.replace_btn.pack(side=tk.LEFT, padx=(0, 6))
        tk.Button(btn_frame, text="Очистить",
                  command=lambda: log_clear(self.log)).pack(side=tk.LEFT)

        self.log = make_log(self, row=8, colspan=2)

        # После Preview разблокируем Replace
        self._last_pattern = None
        self._last_folder = None

    def _validate(self):
        folder = self.cfg_var.get()
        pattern = self.pattern_var.get().strip()
        if not os.path.isdir(folder):
            log_clear(self.log)
            log_append(self.log, f"✗ Папка не найдена: {folder}")
            return None, None
        if not pattern:
            log_clear(self.log)
            log_append(self.log, "✗ Введите регулярное выражение.")
            return None, None
        try:
            re.compile(pattern)
        except re.error as e:
            log_clear(self.log)
            log_append(self.log, f"✗ Ошибка в регулярном выражении: {e}")
            return None, None
        return folder, pattern

    def preview(self):
        folder, pattern = self._validate()
        if not folder:
            return
        log_clear(self.log)
        log_append(self.log, f"Поиск: {pattern}\n")

        def worker():
            preview_matches(folder, pattern, lambda t: self.after(0, log_append, self.log, t))
            self._last_pattern = pattern
            self._last_folder = folder
            self.after(0, self.replace_btn.config, {'state': 'normal'})

        threading.Thread(target=worker, daemon=True).start()

    def replace(self):
        folder, pattern = self._validate()
        if not folder:
            return
        replacement = self.replace_var.get()
        log_clear(self.log)
        log_append(self.log, f"Замена: {pattern}  →  {replacement}\n")
        self.replace_btn.config(state='disabled')

        def worker():
            apply_replacement(folder, pattern, replacement,
                              lambda t: self.after(0, log_append, self.log, t))

        threading.Thread(target=worker, daemon=True).start()


# ════════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    app = App()
    app.mainloop()