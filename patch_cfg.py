"""
XILab .cfg Patcher
==================
Патчит .cfg файлы под новую версию протокола XILab без запуска GUI.

Что делает:
  1. Читает .cfg (INI формат)
  2. Добавляет отсутствующие ключи с дефолтными значениями
  3. Пересчитывает MD5 хэш в секции [VERIFICATION]
  4. Сохраняет файл

Использование:
  python xilab_patch_cfg.py
"""

import os
import sys
import glob
import hashlib
import re
import configparser
from datetime import date

# ── Полный список ключей которые пишет XILab 2.0.19 ──────────────────────────
# Формат: { "СЕКЦИЯ": { "ключ": "дефолтное_значение" } }
# Значения взяты из motorsettings.cpp / stagesettings.cpp

EXPECTED_KEYS = {
    "Borders": {
        "Border_is_encoder": "false",
        "Left_border": "0",
        "Left_border_usteps": "0",
        "Right_border": "0",
        "Right_border_usteps": "0",
        "Stop_at_left_border": "false",
        "Stop_at_right_border": "false",
        "Borders_swap_misset_detection": "false",
        "Limit_switch_1_pushed_is_closed": "false",
        "Limit_switch_2_pushed_is_closed": "false",
        "Limit_switch_ender_swap": "false",
    },
    "Maximum_ratings": {
        "Low_voltage_protection": "false",
        "Low_voltage_off": "800",
        "Critical_current": "3000",
        "Critical_voltage": "5000",
        "Critical_usb_current": "450",
        "Critical_usb_voltage": "520",
        "Minimum_usb_voltage": "420",
        "Critical_temperature": "800",
        "Shutdown_on_overheat": "true",
        "H_bridge_alert": "true",
        "Alarm_on_borders_swap_misset": "true",
        "Alarm_flags_sticking": "false",
        "Braking_overvoltage_protection": "false",
        "Alarm_winding_mismatch": "false",
        "Alarm_engine_response": "true",
    },
    "Motor_type": {
        "type": "STEP",
    },
    "Driver_type": {
        "type": "INTEGRATE",
    },
    "Engine": {
        "Max_voltage_enable": "false",
        "Max_current_enable": "false",
        "Limit_speed_enable": "false",
        "Play_compensation_enable": "false",
        "Rated_voltage": "0",
        "Rated_current": "1000",
        "Max_speed_steps": "100",
        "Max_speed_usteps": "0",
        "Play_compensation": "0",
        "Reverse_enable": "false",
        "Use_max_speed": "false",
        "Acceleration_enable": "true",
        "Current_as_RMS_enable": "false",
        "Steps_per_turn": "200",
        "Microstep_mode": "9",
        "Speed_steps": "100",
        "Speed_usteps": "0",
        "Antiplay_speed_steps": "100",
        "Antiplay_speed_usteps": "0",
        "Acceleration": "1000",
        "Deceleration": "1000",
        "Divider_RPM": "false",
        "Feedback_type": "NONE",
        "Encoder_CPT": "0",
        "Encoder_reverse": "false",
        "Feedback_enc_type": "AUTO",
        "Encoder_filter": "NONE",
    },
    "Home_position": {
        "1st_move_direction_right": "true",
        "1st_move_speed": "100",
        "1st_move_speed_usteps": "0",
        "2nd_move_direction_right": "false",
        "2nd_move_speed": "10",
        "2nd_move_speed_usteps": "0",
        "standoff": "0",
        "standoff_usteps": "0",
        "use_second_phase": "false",
        "use_half_movement": "false",
        "use_fast_home": "false",
        "first_stop_after": "REV",
        "second_stop_after": "SYN",
    },
    "PID_control": {
        "Voltage_Kp": "0",
        "Voltage_Ki": "0",
        "Voltage_Kd": "0",
        "Voltage_Kp_float": "0",
        "Voltage_Ki_float": "0",
        "Voltage_Kd_float": "0",
    },
    "EMF_control": {
        "Inductance_L": "0",
        "Resistance_R": "0",
        "EMF_Km": "0",
        "BackEMFFlags": "7",
    },
    "TTL_sync": {
        "Clutter_time": "4",
        "Position": "0",
        "uPosition": "0",
        "Speed": "0",
        "uSpeed": "0",
        "Syncin_enabled": "false",
        "Syncin_invert": "false",
        "Syncin_gotoposition": "false",
        "Syncout_enabled": "false",
        "Syncout_fixed_is_high": "false",
        "Syncout_invert": "false",
        "Syncout_count_in_steps": "false",
        "Syncout_onstart_enabled": "false",
        "Syncout_onstop_enabled": "false",
        "Syncout_onperiod_enabled": "false",
        "Syncout_pulse_steps": "100",
        "Syncout_period": "2000",
        "Accuracy": "0",
        "uAccuracy": "0",
    },
    "Uart": {
        "Speed": "115200",
        "Use_parity": "false",
        "Parity_type": "EVEN",
        "One_stop_bit": "false",
    },
    "Extio": {
        "Extio_as_output": "false",
        "Extio_invert": "false",
        "Mode_in": "IN_NOP",
        "Mode_out": "OUT_OFF",
    },
    "Power": {
        "Hold_current": "100",
        "Current_reduction_delay": "1000",
        "Power_off_delay": "60",
        "Current_set_time": "300",
        "Current_reduction_enabled": "true",
        "Power_off_enabled": "true",
        "Smooth_current_enabled": "true",
    },
    "Brake": {
        "Brake_enabled": "false",
        "Power_off_enabled": "true",
        "t1": "300",
        "t2": "500",
        "t3": "300",
        "t4": "400",
    },
    "Control": {
        "Joy_low_end": "0",
        "Joy_center": "5000",
        "Joy_high_end": "10000",
        "Exp_factor": "100",
        "Dead_zone": "50",
        "Joystick_reverse": "false",
        "timeout_1": "1000", "timeout_2": "1000", "timeout_3": "1000",
        "timeout_4": "1000", "timeout_5": "1000", "timeout_6": "1000",
        "timeout_7": "1000", "timeout_8": "1000", "timeout_9": "1000",
        "speed_1_steps": "100", "speed_1_usteps": "0",
        "speed_2_steps": "1000", "speed_2_usteps": "0",
        "speed_3_steps": "0", "speed_3_usteps": "0",
        "speed_4_steps": "0", "speed_4_usteps": "0",
        "speed_5_steps": "0", "speed_5_usteps": "0",
        "speed_6_steps": "0", "speed_6_usteps": "0",
        "speed_7_steps": "0", "speed_7_usteps": "0",
        "speed_8_steps": "0", "speed_8_usteps": "0",
        "speed_9_steps": "0", "speed_9_usteps": "0",
        "speed_10_steps": "0", "speed_10_usteps": "0",
        "control_mode": "OFF",
        "left_button_pushed": "false",
        "right_button_pushed": "false",
        "MaxClickTime": "300",
        "DeltaPosition": "1",
        "uDeltaPosition": "0",
    },
    "Control_position": {
        "Position_control_enabled": "false",
        "Alarm_on_error_enabled": "true",
        "Error_correction_enabled": "false",
        "Rev_sens_inv_enabled": "false",
        "Based_on_rev_sens": "false",
        "Min_error": "3",
    },
    "Controller_name": {
        "Name": "",
        "EEPROM_precedence": "false",
    },
}

# ── MD5 хэш (как в XILab) ──────────────────────────────────────────────────────

# Точно такой же регэксп как в XILab (settingsdlg.cpp VERIFICATION_GROUP_REG_EXP)
VERIFICATION_RE = re.compile(
    r'\n\[VERIFICATION\]\ndate=\d{4}-\d{2}-\d{2}\nhash=\w+\n'
)

def calculate_hash(data: str) -> str:
    """MD5 от текста файла без секции [VERIFICATION] — точно как в XILab.
    Qt QFile::Text нормализует переносы строк к \\n при чтении."""
    data_clean = VERIFICATION_RE.sub('', data)
    return hashlib.md5(data_clean.encode('utf-8')).hexdigest()

def write_verification(filepath: str):
    """Добавляет/обновляет секцию [VERIFICATION] в конце файла."""
    with open(filepath, 'r', encoding='utf-8') as f:
        data = f.read()

    # Нормализуем переносы строк (как Qt QFile::Text)
    data = data.replace('\r\n', '\n')

    # Убираем старую секцию если есть
    data_no_ver = VERIFICATION_RE.sub('', data)

    hash_val = calculate_hash(data_no_ver)
    today = date.today().strftime('%Y-%m-%d')

    result = data_no_ver + f'\n[VERIFICATION]\ndate={today}\nhash={hash_val}\n'

    with open(filepath, 'w', encoding='utf-8', newline='\n') as f:
        f.write(result)


# ── Патчер ─────────────────────────────────────────────────────────────────────

class CaseSensitiveConfigParser(configparser.RawConfigParser):
    """ConfigParser который сохраняет регистр ключей."""
    def optionxform(self, optionstr):
        return optionstr


def patch_cfg(filepath: str):
    """
    Патчит один .cfg файл.
    Возвращает (количество_добавленных_ключей, список_добавленных).
    """
    config = CaseSensitiveConfigParser()
    config.read(filepath, encoding='utf-8')

    added = []

    for section, keys in EXPECTED_KEYS.items():
        if not config.has_section(section):
            config.add_section(section)
            for key, default in keys.items():
                config.set(section, key, default)
                added.append(f"[{section}] {key} = {default}")
        else:
            for key, default in keys.items():
                if not config.has_option(section, key):
                    config.set(section, key, default)
                    added.append(f"[{section}] {key} = {default}")

    if added:
        # Убираем секцию VERIFICATION перед записью (пересчитаем потом)
        if config.has_section('VERIFICATION'):
            config.remove_section('VERIFICATION')

        with open(filepath, 'w', encoding='utf-8') as f:
            config.write(f, space_around_delimiters=False)

        write_verification(filepath)
    
    return len(added), added


# ── Главная функция ────────────────────────────────────────────────────────────

def process_folder(cfg_folder: str):
    cfg_files = sorted(glob.glob(os.path.join(cfg_folder, "*.cfg")))

    if not cfg_files:
        print(f"В папке '{cfg_folder}' не найдено .cfg файлов!")
        return

    print(f"Найдено файлов: {len(cfg_files)}\n")

    total_added = 0
    errors = 0

    for cfg_path in cfg_files:
        name = os.path.basename(cfg_path)
        try:
            count, added_keys = patch_cfg(cfg_path)
            if count > 0:
                print(f"  ✓ {name} — добавлено ключей: {count}")
                for k in added_keys:
                    print(f"      + {k}")
                total_added += count
            else:
                print(f"  · {name} — актуален, изменений нет")
        except Exception as e:
            print(f"  ✗ {name} — ошибка: {e}")
            errors += 1

    print(f"\n{'='*50}")
    print(f"Готово! Файлов обработано: {len(cfg_files) - errors}")
    print(f"Добавлено ключей всего:    {total_added}")
    if errors:
        print(f"Ошибок:                    {errors}")
    print(f"{'='*50}")


if __name__ == "__main__":
    print("=" * 50)
    print("  XILab .cfg Patcher (без GUI)")
    print("=" * 50)
    print()

    cfg_folder = input("Путь до папки с .cfg файлами:\n> ").strip().strip('"')
    if not os.path.isdir(cfg_folder):
        print(f"✗ Папка не найдена: {cfg_folder}")
        sys.exit(1)

    print()
    process_folder(cfg_folder)
