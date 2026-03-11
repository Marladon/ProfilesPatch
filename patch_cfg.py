"""
XILab .cfg Patcher
==================
Патчит .cfg файлы профилей под новую версию протокола XILab\mDrive без запуска GUI.

Как работает:
  1. Парсит motorsettings.cpp и stagesettings.cpp из репозитория XILab\mDrive
  2. Извлекает все ключи которые XILab\mDrive пишет при сохранении (setValue + SAVE_KEY)
  3. Для каждого .cfg файла добавляет отсутствующие ключи с дефолтными значениями

При обновлении протокола достаточно указать путь до обновлённого репозитория —
скрипт сам найдёт новые ключи.

Зависимости: только стандартная библиотека Python (3.6+)

Использование:
  python patch_cfg.py
"""

import os
import sys
import re
import glob
import configparser


# ── Парсер motorsettings.cpp / stagesettings.cpp ──────────────────────────────

# Дефолтные значения для SAVE_KEY (enum-списки) — первый элемент каждого списка.
# Ключ = имя списка из исходников, значение = первый/дефолтный элемент.
# Обновляется автоматически при парсинге если список найден в исходнике.
SAVE_KEY_DEFAULTS = {
    "feedbackTypeList":    "ENCODER",
    "feedbackEncTypeList": "AUTO",
    "feedbackEncFilterList": "NONE",
    "homeFirstTypeList":   "REV",
    "homeSecondTypeList":  "REV",
    "engineTypeList":      "DC",
    "driverTypeList":      "INTEGRATE",
    "extioInTypeList":     "IN_NOP",
    "extioOutTypeList":    "OUT_OFF",
    "controlTypeList":     "OFF",
    "uartParityTypeList":  "EVEN",
}


def parse_save_key_lists(source: str):
    """
    Парсит определения списков вида:
        someTypeList
            << pair("VALUE", CONSTANT)
            << pair("VALUE2", CONSTANT2)
            ;
    Возвращает dict { имя_списка: первое_значение (дефолт) }
    """
    result = {}
    # Находим блоки: имяСписка << pair("VAL", ...) << ...;
    pattern = re.compile(
        r'(\w+List)\s*\n\s*<<\s*pair\("([^"]+)"',
        re.MULTILINE
    )
    for m in pattern.finditer(source):
        list_name = m.group(1)
        first_value = m.group(2)
        if list_name not in result:  # берём только первое вхождение (дефолт)
            result[list_name] = first_value
    return result


def parse_keys_from_source(filepath: str) -> dict:
    """
    Парсит один .cpp файл (motorsettings.cpp или stagesettings.cpp).
    Извлекает все секции и ключи из функции FromClassToSettings.
    Возвращает { "Секция": { "ключ": "дефолт" } }
    """
    with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
        source = f.read()

    # Обновляем дефолты SAVE_KEY из исходника
    list_defaults = parse_save_key_lists(source)
    save_key_defaults = {**SAVE_KEY_DEFAULTS, **list_defaults}

    result = {}
    current_section = None

    # Ищем функцию FromClassToSettings
    func_match = re.search(r'FromClassToSettings\s*\(.*?\{(.+?)^}', source,
                           re.DOTALL | re.MULTILINE)
    if not func_match:
        return result

    func_body = func_match.group(1)

    for line in func_body.splitlines():
        line = line.strip()

        # beginGroup("SectionName")
        m = re.search(r'beginGroup\("([^"]+)"\)', line)
        if m:
            current_section = m.group(1)
            if current_section not in result:
                result[current_section] = {}
            continue

        # endGroup()
        if 'endGroup()' in line:
            current_section = None
            continue

        if current_section is None:
            continue

        # setValue("key", ...)  →  дефолт = "0" или "false" в зависимости от типа
        m = re.search(r'setValue\("([^"]+)"', line)
        if m:
            key = m.group(1)
            # Определяем дефолт по типу значения
            if '!= 0' in line or 'bool' in line.lower():
                default = "false"
            elif 'double' in line or 'float' in line.lower():
                default = "0"
            else:
                default = "0"
            result[current_section][key] = default
            continue

        # SAVE_KEY(listName, ..., "key")  →  дефолт = первый элемент списка
        m = re.search(r'SAVE_KEY\((\w+),\s*\S+,\s*\S+,\s*"([^"]+)"\)', line)
        if m:
            list_name = m.group(1)
            key = m.group(2)
            default = save_key_defaults.get(list_name, "")
            result[current_section][key] = default
            continue

    return result


def build_expected_keys(libximc_src_dir: str) -> dict:
    """
    Читает motorsettings.cpp и stagesettings.cpp из директории исходников libximc.
    Возвращает объединённый словарь всех ожидаемых ключей.
    """
    expected = {}
    files_to_parse = ["motorsettings.cpp", "stagesettings.cpp"]

    for filename in files_to_parse:
        filepath = os.path.join(libximc_src_dir, filename)
        if not os.path.isfile(filepath):
            print(f"Файл не найден, пропускаем: {filepath}")
            continue

        parsed = parse_keys_from_source(filepath)
        for section, keys in parsed.items():
            if section not in expected:
                expected[section] = {}
            expected[section].update(keys)
        print(f"Прочитан {filename}: секций={len(parsed)}, "
              f"ключей={sum(len(v) for v in parsed.values())}")

    return expected





# ── Патчер ─────────────────────────────────────────────────────────────────────

class CaseSensitiveConfigParser(configparser.RawConfigParser):
    """ConfigParser который сохраняет регистр ключей."""
    def optionxform(self, optionstr):
        return optionstr


def patch_cfg(filepath: str, expected_keys: dict):
    """
    Патчит один .cfg файл.
    Возвращает список добавленных ключей.
    """
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
        # Сохраняем файл. VERIFICATION не трогаем —
        # хэш станет невалидным, это сигнал что файл нужно верифицировать вручную через XILab\mDrive.
        with open(filepath, 'w', encoding='utf-8') as f:
            config.write(f, space_around_delimiters=False)

    return added


# ── Главная функция ────────────────────────────────────────────────────────────

def find_src_dir(xilab_repo: str) -> str:
    """Ищет папку с motorsettings.cpp.
    Принимает как корень репо, так и путь прямо до папки с исходниками."""
    for root, dirs, files in os.walk(xilab_repo):
        if "motorsettings.cpp" in files:
            return root
    return xilab_repo


def process(xilab_repo: str, cfg_folder: str):
    print(f"\nЧитаем исходники из: {xilab_repo}")
    src_dir = find_src_dir(xilab_repo)
    expected_keys = build_expected_keys(src_dir)

    if not expected_keys:
        print("Не удалось прочитать ключи из исходников!")
        sys.exit(1)

    total_sections = len(expected_keys)
    total_keys = sum(len(v) for v in expected_keys.values())
    print(f"Итого: секций={total_sections}, ключей={total_keys}")

    cfg_files = sorted(glob.glob(os.path.join(cfg_folder, "*.cfg")))
    if not cfg_files:
        print(f"\nВ папке '{cfg_folder}' не найдено .cfg файлов!")
        return

    print(f"\nНайдено .cfg файлов: {len(cfg_files)}\n")

    total_added = 0
    errors = 0

    for cfg_path in cfg_files:
        name = os.path.basename(cfg_path)
        try:
            added = patch_cfg(cfg_path, expected_keys)
            if added:
                print(f"{name} - добавлено ключей: {len(added)}")
                for k in added:
                    print(f"      + {k}")
                total_added += len(added)
            else:
                print(f"  · {name} — актуален")
        except Exception as e:
            print(f"{name} — ошибка: {e}")
            errors += 1

    print(f"\n{'='*50}")
    print(f"Готово! Обработано файлов: {len(cfg_files) - errors}")
    print(f"Добавлено ключей всего:   {total_added}")
    if errors:
        print(f"Ошибок:                   {errors}")
    print(f"{'='*50}")


if __name__ == "__main__":
    print("=" * 50)
    print("  XILab\mDrive .cfg Patcher (автопарсинг исходников)")
    print("=" * 50)
    print()

    xilab_repo = input("Путь до репозитория XILab\mDrive (корень или папка src/, любой вариант):\n  пример: F:\\crzy\\GITLAB\\XILab\n> ").strip().strip('"')
    if not os.path.isdir(xilab_repo):
        print(f"Папка не найдена: {xilab_repo}")
        sys.exit(1)

    cfg_folder = input("\nПуть до папки с .cfg файлами:\n  пример: F:\\crzy\\GITLAB\\xiresource\\profiles\\STANDA\n> ").strip().strip('"')
    if not os.path.isdir(cfg_folder):
        print(f"Папка не найдена: {cfg_folder}")
        sys.exit(1)

    process(xilab_repo, cfg_folder)