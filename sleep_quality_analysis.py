#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sleep_quality_analysis.py
--------------------------------------------------------------
Phân tích chất lượng giấc ngủ và sàng lọc bất thường tim mạch.
"""

import glob
import os
import sys
import time
import numpy as np
import pandas as pd

from scipy.signal import butter, filtfilt, medfilt, iirnotch, find_peaks

# ==================================================
# CONFIG
# ==================================================

MAX_CSV_PATTERN   = "max30102_test*.csv"
AUDIO_CSV_PATTERN = "inmp441_*.csv"
REFERENCE_CSV     = "reference_data.csv"

AUDIO_FS = 8000  

RAW_24BIT_MIN = -8388608
RAW_24BIT_MAX = 8388607

SEND_TO_ESP32 = True
SERIAL_PORT   = "COM4"
SERIAL_BAUD   = 921600   


def find_latest(pattern, required=True):
    files = glob.glob(pattern)
    if not files:
        if required:
            print(f"[LOI] Khong tim thay file khop mau: {pattern}")
            sys.exit(1)
        return None
    return max(files, key=os.path.getctime)

# ==================================================
# PHAN 1: PHAN TICH MAX30102 (SpO2 / BPM)
# ==================================================

def analyze_max30102(path):
    df = pd.read_csv(path)
    df_valid = df[df["Valid"] == 1]

    bpm_vals  = df_valid.loc[df_valid["HR_bpm"] > 0, "HR_bpm"].astype(float)
    spo2_vals = df_valid.loc[df_valid["SpO2_percent"] > 0, "SpO2_percent"].astype(float)

    result = {
        "n_samples":   len(df),
        "n_valid":     len(df_valid),
        "avg_bpm":     float(bpm_vals.mean())  if len(bpm_vals)  else 0.0,
        "bpm_std":     float(bpm_vals.std())   if len(bpm_vals) > 1 else 0.0,
        "avg_spo2":    float(spo2_vals.mean()) if len(spo2_vals) else 0.0,
        "min_spo2":    float(spo2_vals.min())  if len(spo2_vals) else 0.0,
        "has_bpm_data":  len(bpm_vals) > 0,
        "has_spo2_data": len(spo2_vals) > 0,
    }
    return result

# ==================================================
# PHAN 2: PHAN TICH AM THANH (ngay / khoang lang)
# ==================================================

def reject_out_of_range(data):
    mask = (data < RAW_24BIT_MIN) | (data > RAW_24BIT_MAX)
    cleaned = data.copy()
    if mask.any():
        valid = np.where(~mask)[0]
        if len(valid) > 0:
            cleaned[mask] = np.interp(np.where(mask)[0], valid, data[valid])
    return cleaned

def despike(data):
    if len(data) < 9:
        return data
    med = medfilt(data, 9)
    diff = data - med
    mad = np.median(np.abs(diff - np.median(diff)))
    sigma = 1.4826 * mad + 1e-9
    spikes = np.abs(diff) > 6 * sigma
    cleaned = data.copy()
    cleaned[spikes] = med[spikes]
    return cleaned

def bandpass_snore(data, fs):
    nyq = fs / 2
    low = min(50 / nyq, 0.99)
    high = min(1500 / nyq, 0.99)
    if high <= low:
        return data
    b, a = butter(4, [low, high], btype="band")
    return filtfilt(b, a, data)

def rms_envelope(data, fs, win_sec=0.5, hop_sec=0.25):
    win = max(1, int(win_sec * fs))
    hop = max(1, int(hop_sec * fs))
    n = len(data)
    env = []
    times = []
    for start in range(0, n - win, hop):
        seg = data[start:start + win]
        env.append(np.sqrt(np.mean(seg.astype(np.float64) ** 2)))
        times.append((start + win / 2) / fs)
    return np.array(env), np.array(times)

def count_events(mask, times, min_gap_sec=0.5):
    if len(mask) == 0:
        return 0, []
    events = []
    in_event = False
    start_t = None
    for i, v in enumerate(mask):
        if v and not in_event:
            in_event = True
            start_t = times[i]
        elif not v and in_event:
            in_event = False
            events.append((start_t, times[i - 1]))
    if in_event:
        events.append((start_t, times[-1]))

    merged = []
    for ev in events:
        if merged and ev[0] - merged[-1][1] < min_gap_sec:
            merged[-1] = (merged[-1][0], ev[1])
        else:
            merged.append(ev)
    return len(merged), merged

def analyze_audio(path, fs=AUDIO_FS):
    df = pd.read_csv(path)
    df = df.sort_values(by="index").drop_duplicates(subset=["index"]).reset_index(drop=True)
    raw = df["raw_24bit"].astype(float).values
    duration_sec = len(raw) / fs if len(raw) else 0.0

    if len(raw) < fs: 
        return {
            "duration_sec": duration_sec,
            "too_short": True,
            "snoring_events": 0,
            "snoring_rate_per_min": 0.0,
            "long_pause_events": 0,
        }

    sig = reject_out_of_range(raw)
    sig = despike(sig)
    sig = bandpass_snore(sig, fs)

    env, times = rms_envelope(sig, fs)
    if len(env) == 0:
        return {"duration_sec": duration_sec, "too_short": True, "snoring_events": 0, "snoring_rate_per_min": 0.0, "long_pause_events": 0}

    env_mean = np.mean(env)
    env_std  = np.std(env)

    snore_thresh = env_mean + 1.5 * env_std
    snore_mask = env > snore_thresh
    n_snore, _ = count_events(snore_mask, times, min_gap_sec=0.3)

    silence_thresh = 0.15 * env_mean
    silence_mask = env < silence_thresh
    _, silence_events = count_events(silence_mask, times, min_gap_sec=0.3)
    long_pauses = [ev for ev in silence_events if (ev[1] - ev[0]) >= 3.0]

    snoring_rate_per_min = (n_snore / duration_sec * 60) if duration_sec > 0 else 0.0

    return {
        "duration_sec": duration_sec,
        "too_short": False,
        "snoring_events": n_snore,
        "snoring_rate_per_min": snoring_rate_per_min,
        "long_pause_events": len(long_pauses),
    }

# ==================================================
# PHAN 3: PHAN TICH AM TIM (NGHI NGO BAT THUONG / MURMUR)
# ==================================================

def highpass(data, fs):
    b, a = butter(2, 0.5 / (fs / 2), btype="high")
    return filtfilt(b, a, data)

def notch50(data, fs):
    b, a = iirnotch(50 / (fs / 2), 20)
    return filtfilt(b, a, data)

def bandpass_heart(data, fs):
    b, a = butter(4, [25 / (fs / 2), 180 / (fs / 2)], btype="band")
    return filtfilt(b, a, data)

def shannon_envelope(x, fs):
    x = x / (np.max(np.abs(x)) + 1e-12)
    energy = -(x ** 2) * np.log(x ** 2 + 1e-12)
    win = max(1, int(0.05 * fs))
    return np.convolve(energy, np.ones(win) / win, mode="same")

def detect_s1s2(env, fs):
    threshold = np.mean(env) + 0.8 * np.std(env)
    peaks, _ = find_peaks(env, height=threshold, distance=int(0.05 * fs))

    S1, S2 = [], []
    i = 0
    while i < len(peaks) - 2:
        p1, p2, p3 = peaks[i], peaks[i + 1], peaks[i + 2]
        dt12 = (p2 - p1) / fs
        dt23 = (p3 - p2) / fs
        if 0.05 <= dt12 <= 0.25 and 0.25 <= dt23 <= 0.80:
            S1.append(p1)
            S2.append(p2)
            i += 2
        else:
            i += 1
    return peaks, S1, S2, threshold

def analyze_heart_murmur(path, fs=AUDIO_FS):
    df = pd.read_csv(path)
    df = df.sort_values(by="index").drop_duplicates(subset=["index"]).reset_index(drop=True)
    raw = df["raw_24bit"].astype(float).values
    duration_sec = len(raw) / fs if len(raw) else 0.0

    if len(raw) < fs * 2:   
        return {"enough_data": False, "duration_sec": duration_sec, "n_pairs": 0}

    sig = reject_out_of_range(raw)
    sig = despike(sig)
    sig = highpass(sig, fs)
    sig = notch50(sig, fs)
    sig_bp = bandpass_heart(sig, fs)

    env = shannon_envelope(sig_bp, fs)
    _, S1, S2, _ = detect_s1s2(env, fs)

    if len(S1) < 2:
        return {"enough_data": False, "duration_sec": duration_sec, "n_pairs": len(S1)}

    systolic_energies = []
    diastolic_energies = []

    for i in range(len(S1)):
        s1, s2 = S1[i], S2[i]
        if s2 > s1:
            systolic_energies.append(np.mean(env[s1:s2]))
        if i + 1 < len(S1):
            next_s1 = S1[i + 1]
            if next_s1 > s2:
                diastolic_energies.append(np.mean(env[s2:next_s1]))

    avg_systolic  = float(np.mean(systolic_energies))  if systolic_energies  else 0.0
    avg_diastolic = float(np.mean(diastolic_energies)) if diastolic_energies else 0.0
    ratio = (avg_diastolic / avg_systolic) if avg_systolic > 1e-9 else 0.0

    if ratio > 0.5:
        flag, flag_code = "NGHI NGO CAO", "CAO"
    elif ratio > 0.3:
        flag, flag_code = "NGHI NGO NHE", "NHE"
    else:
        flag, flag_code = "BINH THUONG", "OK"

    return {
        "enough_data": True,
        "duration_sec": duration_sec,
        "n_pairs": len(S1),
        "avg_systolic_energy": avg_systolic,
        "avg_diastolic_energy": avg_diastolic,
        "ratio": ratio,
        "flag": flag,
        "flag_code": flag_code,
    }


# ==================================================
# PHAN 4: DANH GIA TONG HOP CHAT LUONG
# ==================================================

def score_sleep_quality(max_result, audio_result, murmur_result):
    score = 100.0
    notes = []

    if max_result["has_spo2_data"]:
        avg_spo2 = max_result["avg_spo2"]
        min_spo2 = max_result["min_spo2"]
        if avg_spo2 < 90:
            score -= 25
            notes.append(f"SpO2 trung binh thap ({avg_spo2:.1f}% < 90%)")
        elif avg_spo2 < 95:
            score -= 10
            notes.append(f"SpO2 trung binh hoi thap ({avg_spo2:.1f}% < 95%)")

        if min_spo2 < 90:
            score -= 15
            notes.append(f"Co thoi diem SpO2 tut xuong {min_spo2:.1f}% (nghi ngo desaturation)")
    else:
        notes.append("Khong co du lieu SpO2 hop le")

    if max_result["has_bpm_data"]:
        avg_bpm  = max_result["avg_bpm"]
        bpm_std  = max_result["bpm_std"]
        if avg_bpm < 50 or avg_bpm > 100:
            score -= 10
            notes.append(f"Nhip tim trung binh bat thuong ({avg_bpm:.0f} bpm)")
        elif avg_bpm < 60 or avg_bpm > 90:
            score -= 5
            notes.append(f"Nhip tim trung binh hoi le ({avg_bpm:.0f} bpm)")

        if bpm_std > 15:
            score -= 10
            notes.append(f"Nhip tim dao dong manh (std={bpm_std:.1f})")
    else:
        notes.append("Khong co du lieu BPM hop le")

    if not audio_result.get("too_short", True):
        rate = audio_result["snoring_rate_per_min"]
        if rate > 10:
            score -= 15
            notes.append(f"Ngay nhieu ({rate:.1f} lan/phut)")
        elif rate > 5:
            score -= 7
            notes.append(f"Co ngay ({rate:.1f} lan/phut)")

        pauses = audio_result["long_pause_events"]
        if pauses > 0:
            penalty = min(pauses, 2) * 15
            score -= penalty
            notes.append(f"Phat hien {pauses} khoang lang keo dai (nghi ngo ngung tho)")

    if murmur_result.get("enough_data", False):
        ratio = murmur_result["ratio"]
        if murmur_result["flag_code"] == "CAO":
            score -= 30
            notes.append(f"CANH BAO TIM MACH: Ty le am thanh Tam truong/Tam thu RAT CAO ({ratio:.2f}). Nghi ngo tieng am ram tam truong (VD: Hep van hai la).")
        elif murmur_result["flag_code"] == "NHE":
            score -= 10
            notes.append(f"Luu y tim mach: Ty le am thanh Tam truong/Tam thu hoi cao ({ratio:.2f}). Co the co tap am tim.")

    score = max(0.0, min(100.0, score))

    if score >= 85:
        grade = "TOT"
    elif score >= 65:
        grade = "KHA"
    elif score >= 45:
        grade = "TB"
    else:
        grade = "KEM"

    return score, grade, notes

def load_reference(path):
    if not os.path.exists(path):
        return None
    df = pd.read_csv(path)
    return {
        "ref_bpm":  float(df["Reference_BPM"].mean()),
        "ref_spo2": float(df["Reference_SpO2"].mean()),
    }

# ==================================================
# PHAN 5: GUI KET QUA CHO ESP32 (OLED)
# ==================================================

def send_to_esp32(avg_bpm, avg_spo2, grade, score, murmur_flag_code, port, baud):
    try:
        import serial
    except ImportError:
        print("[CANH BAO] Chua cai pyserial (pip install pyserial), bo qua gui OLED.")
        return

    # Sửa phím 'S' thành 's', nối thêm SCORE và MURMUR
    line = f"s;BPM:{avg_bpm:.0f};SPO2:{avg_spo2:.0f};GRADE:{grade};SCORE:{score:.0f};MURMUR:{murmur_flag_code}\n"
    
    try:
        ser = serial.Serial(port, baud, timeout=2)
        print("[INFO] Cho ESP32 khoi dong lai (do mo cong COM moi)...")
        time.sleep(3)
        ser.reset_input_buffer()

        ser.write(line.encode("utf-8"))
        ser.flush()
        ser.close()
        print(f"[OK] Da gui ket qua cho ESP32 ({port}@{baud}): {line.strip()}")
    except Exception as e:
        print(f"[LOI] Khong gui duoc cho ESP32: {e}")

# ==================================================
# MAIN
# ==================================================

def main():
    max_path   = find_latest(MAX_CSV_PATTERN)
    audio_path = find_latest(AUDIO_CSV_PATTERN, required=False)

    print("=" * 55)
    print("  PHAN TICH CHAT LUONG GIAC NGU")
    print("=" * 55)
    print(f"File SpO2/BPM : {max_path}")
    print(f"File am thanh : {audio_path if audio_path else '(khong co - bo qua tieu chi ngay/tho)'}")
    print("-" * 55)

    max_result = analyze_max30102(max_path)
    audio_result = analyze_audio(audio_path) if audio_path else {"too_short": True}
    murmur_result = analyze_heart_murmur(audio_path) if audio_path else {"enough_data": False}

    ref = load_reference(REFERENCE_CSV)

    score, grade, notes = score_sleep_quality(max_result, audio_result, murmur_result)

    grade_text = {
        "TOT": "TOT (Good)",
        "KHA": "KHA (Fair)",
        "TB":  "TRUNG BINH (Average)",
        "KEM": "KEM (Poor)",
    }[grade]

    print(f"Nhip tim (BPM)      : trung binh {max_result['avg_bpm']:.1f}"
          f" (do lech chuan {max_result['bpm_std']:.1f}), "
          f"{max_result['n_valid']}/{max_result['n_samples']} mau hop le")
    print(f"SpO2 (%)            : trung binh {max_result['avg_spo2']:.1f}%,"
          f" thap nhat {max_result['min_spo2']:.1f}%")

    if ref:
        print(f"So voi reference    : BPM ~{ref['ref_bpm']:.1f}, SpO2 ~{ref['ref_spo2']:.1f}%")

    if not audio_result.get("too_short", True):
        print(f"Am thanh tho/ngay   : {audio_result['duration_sec']:.1f}s, "
              f"{audio_result['snoring_events']} lan ngay "
              f"(~{audio_result['snoring_rate_per_min']:.1f} lan/phut), "
              f"{audio_result['long_pause_events']} khoang lang bat thuong")
    else:
        print("Am thanh tho/ngay   : Khong du du lieu de phan tich")

    if murmur_result.get("enough_data", False):
        print(f"Am thanh tim mach   : Ty le Tam truong/Tam thu = {murmur_result['ratio']:.3f} -> {murmur_result['flag']}")

    print("-" * 55)
    print(f"DIEM CHAT LUONG GIAC NGU : {score:.0f}/100  ->  {grade_text}")
    
    if notes:
        print("Chi tiet:")
        for n in notes:
            print(f"  - {n}")
    else:
        print("Khong phat hien bat thuong dang chu y.")
    print("=" * 55)

    if SEND_TO_ESP32:
        murmur_code = murmur_result.get("flag_code", "N/A") if murmur_result else "N/A"
        send_to_esp32(max_result["avg_bpm"], max_result["avg_spo2"], grade, score, murmur_code, SERIAL_PORT, SERIAL_BAUD)

if __name__ == "__main__":
    main()