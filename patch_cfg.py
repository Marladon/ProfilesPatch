"""
XILab .cfg Patcher
==================
Патчит .cfg файлы профилей под новую версию протокола XILab\mDrive без запуска GUI.

Как работает:
  1. Парсит motorsettings.cpp и stagesettings.cpp из репозитория XILab\mDrive
  2. Извлекает все ключи которые XILab\mDrive пишет при сохранении (setValue + SAVE_KEY)
  3. Для каждого .cfg файла добавляет отсутствующие ключи с дефолтными значениями
  4. Если файл был верифицирован до патча — хэш пересчитывается, статус сохраняется.
     Если файл был намеренно невалидным — хэш не трогается.
     Флаг --rehash форсирует пересчёт хэша для всех файлов включая невалидные.

При обновлении протокола достаточно указать путь до обновлённого репозитория —
скрипт сам найдёт новые ключи.

Зависимости: только стандартная библиотека Python (3.6+)

Использование:
  python patch_cfg.py               # сохраняет статус верификации каждого файла
  python patch_cfg.py --rehash      # форсирует пересчёт хэша для всех файлов
"""

import os
import sys
import re
import glob
import hashlib
import configparser
from typing import Optional


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


# ── Хэш (VERIFICATION) ────────────────────────────────────────────────────────

def compute_cfg_hash(filepath: str) -> str:
    """
    Считает MD5 хэш содержимого .cfg файла точно как XILab:
      - читает файл как текст (UTF-8)
      - убирает секцию [VERIFICATION] и всё её содержимое
      - нормализует переносы строк до \n
      - считает MD5 от результата в UTF-8
    Возвращает хэш в нижнем регистре.
    """
    with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
        text = f.read()

    # Убираем секцию [VERIFICATION] целиком (до следующей секции или конца файла)
    text_without_verification = re.sub(
        r'\[VERIFICATION\][^\[]*',
        '',
        text,
        flags=re.IGNORECASE
    )

    # Нормализуем переносы строк → \n (как QFile::Text)
    normalized = text_without_verification.replace('\r\n', '\n').replace('\r', '\n')

    md5 = hashlib.md5(normalized.encode('utf-8')).hexdigest()
    return md5


def get_stored_hash(filepath: str) -> Optional[str]:
    """
    Извлекает хэш из секции [VERIFICATION] файла.
    Возвращает строку хэша или None если секции/ключа нет.
    """
    with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
        text = f.read()

    m = re.search(r'\[VERIFICATION\].*?hash\s*=\s*([0-9a-fA-F]+)', text,
                  re.IGNORECASE | re.DOTALL)
    return m.group(1).lower() if m else None


def is_verified(filepath: str) -> bool:
    """
    Проверяет верифицирован ли файл: хэш в [VERIFICATION] совпадает с реальным.
    Если секции или ключа нет — считаем файл не верифицированным.
    """
    stored = get_stored_hash(filepath)
    if stored is None:
        return False
    return stored == compute_cfg_hash(filepath)


def update_verification(filepath: str) -> str:
    """
    Пересчитывает MD5 хэш файла и обновляет (или создаёт) секцию [VERIFICATION].
    Возвращает новый хэш.
    """
    new_hash = compute_cfg_hash(filepath)

    with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
        text = f.read()

    if re.search(r'\[VERIFICATION\]', text, re.IGNORECASE):
        new_text = re.sub(
            r'(\[VERIFICATION\].*?hash\s*=\s*)[0-9a-fA-F]+',
            lambda m: m.group(1) + new_hash,
            text,
            flags=re.IGNORECASE | re.DOTALL
        )
        if new_text == text:
            # Ключ hash не найден внутри секции — добавляем
            new_text = re.sub(
                r'(\[VERIFICATION\])',
                r'\1\nhash=' + new_hash,
                new_text,
                flags=re.IGNORECASE
            )
    else:
        # Секции нет вообще — добавляем в конец
        new_text = text.rstrip('\n') + '\n\n[VERIFICATION]\nhash=' + new_hash + '\n'

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(new_text)

    return new_hash


# ── Патчер ─────────────────────────────────────────────────────────────────────

class CaseSensitiveConfigParser(configparser.RawConfigParser):
    """ConfigParser который сохраняет регистр ключей."""
    def optionxform(self, optionstr):
        return optionstr


def patch_cfg(filepath: str, expected_keys: dict, rehash: bool = False):
    """
    Патчит один .cfg файл.

    Логика хэша:
      - Если файл верифицирован до патча → пересчитываем хэш после, статус сохраняется.
      - Если файл не верифицирован → хэш не трогаем, статус сохраняется.
      - --rehash → пересчитываем хэш для всех файлов независимо от статуса.

    Возвращает (список добавленных ключей, статус хэша: 'updated' | 'forced' | 'skipped' | None).
    """
    # Проверяем статус верификации ДО изменений
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
        # Форсируем пересчёт для всех файлов
        update_verification(filepath)
        hash_status = 'forced'
    elif was_verified and added:
        # Файл был верифицирован — восстанавливаем верификацию после патча
        update_verification(filepath)
        hash_status = 'updated'
    elif not was_verified and added:
        # Файл был невалидным — оставляем хэш нетронутым
        hash_status = 'skipped'

    return added, hash_status


# ── Главная функция ────────────────────────────────────────────────────────────

def find_src_dir(xilab_repo: str) -> str:
    """Ищет папку с motorsettings.cpp.
    Принимает как корень репо, так и путь прямо до папки с исходниками."""
    for root, dirs, files in os.walk(xilab_repo):
        if "motorsettings.cpp" in files:
            return root
    return xilab_repo


def process(xilab_repo: str, cfg_folder: str, rehash: bool):
    print(f"\nЧитаем исходники из: {xilab_repo}")
    src_dir = find_src_dir(xilab_repo)
    expected_keys = build_expected_keys(src_dir)

    if not expected_keys:
        print("Не удалось прочитать ключи из исходников!")
        sys.exit(1)

    total_sections = len(expected_keys)
    total_keys = sum(len(v) for v in expected_keys.values())
    print(f"Итого: секций={total_sections}, ключей={total_keys}")

    if rehash:
        print("Режим: --rehash, хэш пересчитывается для всех файлов")
    else:
        print("Режим: хэш верифицированных файлов сохраняется, невалидных — не трогается")

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
            added, hash_status = patch_cfg(cfg_path, expected_keys, rehash=rehash)

            if added:
                print(f"  ✓ {name} — добавлено ключей: {len(added)}")
                for k in added:
                    print(f"      + {k}")
                total_added += len(added)
            else:
                print(f"  · {name} — актуален")

            if hash_status == 'updated':
                print(f"      # хэш пересчитан (файл был верифицирован)")
            elif hash_status == 'skipped':
                print(f"      # хэш не тронут (файл не верифицирован)")
            elif hash_status == 'forced':
                print(f"      # хэш пересчитан (--rehash)")

        except Exception as e:
            print(f"  ✗ {name} — ошибка: {e}")
            errors += 1

    print(f"\n{'='*50}")
    print(f"Готово! Обработано файлов: {len(cfg_files) - errors}")
    print(f"Добавлено ключей всего:   {total_added}")
    if errors:
        print(f"Ошибок:                   {errors}")
    print(f"{'='*50}")


if __name__ == "__main__":
    rehash = "--rehash" in sys.argv

    print("=" * 50)
    print("  XILab\\mDrive .cfg Patcher (автопарсинг исходников)")
    print("=" * 50)
    if rehash:
        print("  [--rehash] хэш пересчитывается для всех файлов")
    else:
        print("  Статус верификации каждого файла сохраняется автоматически")
    print()

    xilab_repo = input("Путь до репозитория XILab\\mDrive (корень или папка src/, любой вариант):\n  пример: F:\\crzy\\GITLAB\\XILab\n> ").strip().strip('"')
    if not os.path.isdir(xilab_repo):
        print(f"Папка не найдена: {xilab_repo}")
        sys.exit(1)

    cfg_folder = input("\nПуть до папки с .cfg файлами:\n  пример: F:\\crzy\\GITLAB\\xiresource\\profiles\\STANDA\n> ").strip().strip('"')
    if not os.path.isdir(cfg_folder):
        print(f"Папка не найдена: {cfg_folder}")
        sys.exit(1)

    process(xilab_repo, cfg_folder, rehash=rehash)