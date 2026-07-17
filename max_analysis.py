#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MASTER.py - PHAN TICH TONG HOP NANG CAO MAX30102
=================================================
Bao gom:
  1. Thong ke nang cao (mean, median, std, CV%, CI 95%, IQR, outlier removal)
  2. Phan tich tin hieu (FFT, peak detection, SNR)
  3. Heart Rate Variability (HRV): SDNN, RMSSD
  4. Bland-Altman analysis (chuan y te)
  5. Do thi nang cao (histogram, heatmap, rolling average)
  6. Diem chat luong tong hop (0-100)
  7. Xuat bao cao PDF tu dong
"""

import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')  # backend khong can GUI, xuat file truc tiep
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from scipy import stats, signal
import os
import sys
from datetime import datetime

# ========================================================================
# CAU HINH
# ========================================================================
CSV_FILE_1 = "max30102_test.csv"
CSV_FILE_2 = "max30102_test_2.csv"
REF_FILE   = "reference_data.csv"
PDF_OUTPUT = "MAX30102_REPORT.pdf"
TXT_OUTPUT = "MAX30102_RESULT.txt"

# Tieu chuan danh gia
BPM_TOLERANCE = 5       # sai so BPM cho phep
SPO2_TOLERANCE = 2      # sai so SpO2 cho phep (%)
SAMPLE_RATE = 100       # Hz (10ms/mau)

# ========================================================================
# HAM HO TRO
# ========================================================================
def load_data():
    """Doc du lieu tu cac file CSV"""
    data = {}
    for name, path in [("test_1", CSV_FILE_1), ("test_2", CSV_FILE_2)]:
        if os.path.exists(path):
            df = pd.read_csv(path)
            # Loai bo khoang trang thua trong ten cot
            df.columns = df.columns.str.strip()
            data[name] = df
            print(f"  OK - {path}: {len(df)} mau")
        else:
            print(f"  SKIP - {path} khong ton tai")
    
    if os.path.exists(REF_FILE):
        ref = pd.read_csv(REF_FILE)
        ref.columns = ref.columns.str.strip()
        data["ref"] = ref
        print(f"  OK - {REF_FILE}: {len(ref)} mau tham chieu")
    else:
        # Tao reference mac dinh neu khong co file
        data["ref"] = pd.DataFrame({
            "Reference_BPM": [72.0, 72.0, 72.0, 72.0, 72.0],
            "Reference_SpO2": [98.0, 98.0, 98.0, 98.0, 98.0]
        })
        print(f"  INFO - Dung gia tri tham chieu mac dinh (BPM=72, SpO2=98)")
    
    return data


def get_valid_data(df):
    """Lay du lieu hop le (Valid=1, gia tri > 0)"""
    if 'Valid' in df.columns:
        mask = (df['Valid'] == 1) & (df['HR_bpm'] > 0) & (df['SpO2_percent'] > 0)
    else:
        mask = (df['HR_bpm'] > 0) & (df['SpO2_percent'] > 0)
    return df[mask].copy()


def remove_outliers_iqr(series, factor=1.5):
    """Loai bo outlier bang phuong phap IQR"""
    Q1 = series.quantile(0.25)
    Q3 = series.quantile(0.75)
    IQR = Q3 - Q1
    lower = Q1 - factor * IQR
    upper = Q3 + factor * IQR
    return series[(series >= lower) & (series <= upper)]


def compute_stats(series, name=""):
    """Tinh toan thong ke day du"""
    clean = remove_outliers_iqr(series)
    n = len(clean)
    mean = clean.mean()
    std = clean.std()
    median = clean.median()
    cv = (std / mean * 100) if mean > 0 else 0
    ci95 = 1.96 * std / np.sqrt(n) if n > 0 else 0
    
    return {
        "name": name,
        "n": n,
        "n_outliers": len(series) - n,
        "mean": mean,
        "std": std,
        "median": median,
        "min": clean.min(),
        "max": clean.max(),
        "Q1": clean.quantile(0.25),
        "Q3": clean.quantile(0.75),
        "IQR": clean.quantile(0.75) - clean.quantile(0.25),
        "CV%": cv,
        "CI95_lower": mean - ci95,
        "CI95_upper": mean + ci95,
    }


def compute_snr(ir_signal):
    """Tinh Signal-to-Noise Ratio (SNR) tu tin hieu IR"""
    if len(ir_signal) < 10:
        return 0
    # Loc thong thap de lay tin hieu
    b, a = signal.butter(3, 5 / (SAMPLE_RATE / 2), btype='low')
    filtered = signal.filtfilt(b, a, ir_signal)
    noise = ir_signal - filtered
    power_signal = np.mean(filtered ** 2)
    power_noise = np.mean(noise ** 2)
    if power_noise == 0:
        return 100
    snr = 10 * np.log10(power_signal / power_noise)
    return snr


def detect_peaks_ir(ir_signal, sample_rate=SAMPLE_RATE):
    """Phat hien dinh nhip tim tu tin hieu IR bang Python"""
    if len(ir_signal) < 20:
        return np.array([]), 0
    
    # Loc DC
    b, a = signal.butter(2, [0.5 / (sample_rate / 2), 8 / (sample_rate / 2)], btype='band')
    try:
        filtered = signal.filtfilt(b, a, ir_signal.astype(float))
    except:
        return np.array([]), 0
    
    # Tim dinh
    min_distance = int(sample_rate * 0.4)  # toi thieu 0.4s giua cac dinh (150 BPM max)
    height_threshold = np.std(filtered) * 0.3
    peaks, properties = signal.find_peaks(filtered, distance=min_distance, height=height_threshold)
    
    # Tinh BPM tu cac dinh
    if len(peaks) >= 2:
        intervals = np.diff(peaks) / sample_rate  # giay
        avg_bpm = 60 / np.mean(intervals)
    else:
        avg_bpm = 0
    
    return peaks, avg_bpm


def compute_hrv(peaks, sample_rate=SAMPLE_RATE):
    """Tinh Heart Rate Variability (HRV) tu vi tri cac dinh"""
    if len(peaks) < 3:
        return {"SDNN": 0, "RMSSD": 0, "pNN50": 0, "mean_IBI": 0}
    
    # Inter-Beat Intervals (ms)
    ibi_ms = np.diff(peaks) / sample_rate * 1000
    
    # SDNN: do lech chuan cua cac khoang RR (ms)
    sdnn = np.std(ibi_ms)
    
    # RMSSD: can bac hai trung binh cua binh phuong hieu cac khoang RR lien tiep
    successive_diff = np.diff(ibi_ms)
    rmssd = np.sqrt(np.mean(successive_diff ** 2)) if len(successive_diff) > 0 else 0
    
    # pNN50: ty le cac khoang RR chenh > 50ms
    pnn50 = (np.sum(np.abs(successive_diff) > 50) / len(successive_diff) * 100) if len(successive_diff) > 0 else 0
    
    return {
        "SDNN": sdnn,
        "RMSSD": rmssd,
        "pNN50": pnn50,
        "mean_IBI": np.mean(ibi_ms),
    }


def compute_quality_score(stats_bpm, stats_spo2, snr, ref_bpm, ref_spo2):
    """Tinh diem chat luong tong hop (0-100)"""
    score = 0
    
    # 1. Do chinh xac BPM (30 diem)
    bpm_error = abs(stats_bpm["mean"] - ref_bpm)
    if bpm_error <= 2:
        score += 30
    elif bpm_error <= 5:
        score += 25
    elif bpm_error <= 10:
        score += 15
    else:
        score += 5
    
    # 2. Do chinh xac SpO2 (30 diem)
    spo2_error = abs(stats_spo2["mean"] - ref_spo2)
    if spo2_error <= 1:
        score += 30
    elif spo2_error <= 2:
        score += 25
    elif spo2_error <= 3:
        score += 15
    else:
        score += 5
    
    # 3. Do on dinh BPM - CV% (15 diem)
    if stats_bpm["CV%"] < 3:
        score += 15
    elif stats_bpm["CV%"] < 5:
        score += 12
    elif stats_bpm["CV%"] < 10:
        score += 8
    else:
        score += 3
    
    # 4. Do on dinh SpO2 - CV% (15 diem)
    if stats_spo2["CV%"] < 1:
        score += 15
    elif stats_spo2["CV%"] < 2:
        score += 12
    elif stats_spo2["CV%"] < 5:
        score += 8
    else:
        score += 3
    
    # 5. SNR (10 diem)
    if snr > 30:
        score += 10
    elif snr > 20:
        score += 8
    elif snr > 10:
        score += 5
    else:
        score += 2
    
    return score


def get_grade(score):
    """Xep loai tu diem"""
    if score >= 90: return "EXCELLENT"
    if score >= 80: return "VERY GOOD"
    if score >= 70: return "GOOD"
    if score >= 60: return "ACCEPTABLE"
    return "NEEDS IMPROVEMENT"


# ========================================================================
# DANH GIA SUC KHOE THEO TIEU CHUAN Y TE
# (Tham chieu: WHO, AHA, ISO 80601-2-61, Bo Y te Viet Nam)
# ========================================================================
def assess_spo2(spo2_mean, spo2_min):
    """Phan loai SpO2 theo tieu chuan WHO/Bo Y te"""
    result = {"level": "", "color": "", "description": "", "risk": "", "advice": ""}
    
    if spo2_mean >= 95:
        result["level"] = "BINH THUONG"
        result["color"] = "green"
        result["description"] = "Nong do oxy trong mau binh thuong"
        result["risk"] = "THAP"
        result["advice"] = "Khong can can thiep. Tiep tuc duy tri loi song lanh manh."
    elif spo2_mean >= 90:
        result["level"] = "THIEU OXY NHE (Mild Hypoxemia)"
        result["color"] = "orange"
        result["description"] = "Nong do oxy giam nhe, co the do ho hap nong hoac o do cao"
        result["risk"] = "TRUNG BINH"
        result["advice"] = "Nen theo doi them. Neu keo dai, can kham bac si chuyen khoa ho hap."
    elif spo2_mean >= 85:
        result["level"] = "THIEU OXY TRUNG BINH (Moderate Hypoxemia)"
        result["color"] = "red"
        result["description"] = "Nong do oxy giam ro ret, co the anh huong den hoat dong co the"
        result["risk"] = "CAO"
        result["advice"] = "Can kham bac si NGAY. Co the can ho tro oxy."
    else:
        result["level"] = "THIEU OXY NANG (Severe Hypoxemia)"
        result["color"] = "darkred"
        result["description"] = "Nong do oxy nguy hiem, de doa tinh mang"
        result["risk"] = "RAT CAO - CAN CAP CUU"
        result["advice"] = "GOI CAP CUU NGAY LAP TUC. Can ho tro oxy khan cap."
    
    # Kiem tra SpO2 tut dot ngot (dau hieu ngung tho khi ngu)
    if spo2_min < 90 and spo2_mean >= 95:
        result["note_apnea"] = f"CANH BAO: SpO2 tut xuong {spo2_min:.0f}% (< 90%). " \
                                "Day co the la dau hieu ngung tho khi ngu (Sleep Apnea). " \
                                "Nen kham bac si chuyen khoa giac ngu."
    else:
        result["note_apnea"] = ""
    
    return result


def assess_heart_rate(bpm_mean, bpm_std):
    """Phan loai nhip tim theo tieu chuan AHA (American Heart Association)"""
    result = {"level": "", "description": "", "risk": "", "advice": "", "stability": ""}
    
    if bpm_mean < 40:
        result["level"] = "NHIP TIM RAT CHAM (Severe Bradycardia)"
        result["description"] = "Nhip tim qua cham, co the gay chong mat, ngat xiu"
        result["risk"] = "CAO"
        result["advice"] = "Can kham tim mach NGAY. Co the can may tao nhip."
    elif bpm_mean < 60:
        result["level"] = "NHIP TIM CHAM (Bradycardia)"
        result["description"] = "Nhip tim duoi muc binh thuong. Binh thuong o nguoi tap the duc nhieu."
        result["risk"] = "THAP - TRUNG BINH"
        result["advice"] = "Neu khong tap the duc thuong xuyen, nen kham bac si. " \
                          "Neu la van dong vien, day la binh thuong."
    elif bpm_mean <= 100:
        result["level"] = "BINH THUONG"
        result["description"] = "Nhip tim nam trong khoang binh thuong (60-100 BPM)"
        result["risk"] = "THAP"
        result["advice"] = "Nhip tim on dinh, khong can can thiep."
    elif bpm_mean <= 120:
        result["level"] = "NHIP TIM NHANH NHE (Mild Tachycardia)"
        result["description"] = "Nhip tim hoi nhanh, co the do cang thang, caffeine, hoac van dong"
        result["risk"] = "TRUNG BINH"
        result["advice"] = "Nghi ngoi, giam caffeine. Neu keo dai khi nghi ngoi, nen kham bac si."
    else:
        result["level"] = "NHIP TIM NHANH (Tachycardia)"
        result["description"] = "Nhip tim nhanh bat thuong khi nghi ngoi"
        result["risk"] = "CAO"
        result["advice"] = "Can kham bac si tim mach. Co the la dau hieu roi loan nhip tim."
    
    # Danh gia do on dinh
    cv = (bpm_std / bpm_mean * 100) if bpm_mean > 0 else 0
    if cv < 3:
        result["stability"] = "RAT ON DINH (CV < 3%)"
    elif cv < 5:
        result["stability"] = "ON DINH (CV < 5%)"
    elif cv < 10:
        result["stability"] = "BIEN DONG NHE (CV < 10%)"
    else:
        result["stability"] = "BIEN DONG LON (CV >= 10%) - Can theo doi them"
    
    return result


def assess_hrv(hrv_data):
    """Danh gia HRV theo tieu chuan Task Force ESC/NASPE"""
    result = {"level": "", "description": "", "risk": "", "sleep_quality": ""}
    
    sdnn = hrv_data.get("SDNN", 0)
    rmssd = hrv_data.get("RMSSD", 0)
    
    # SDNN - chi so suc khoe tim mach tong quat
    if sdnn > 100:
        result["level"] = "HRV CAO - Tim mach khoe manh"
        result["description"] = "He than kinh tu dong hoat dong tot, kha nang phuc hoi cao"
        result["risk"] = "THAP"
        result["sleep_quality"] = "Giac ngu co the tot"
    elif sdnn > 50:
        result["level"] = "HRV TRUNG BINH - Binh thuong"
        result["description"] = "He than kinh tu dong hoat dong o muc trung binh"
        result["risk"] = "TRUNG BINH"
        result["sleep_quality"] = "Giac ngu binh thuong"
    else:
        result["level"] = "HRV THAP - Can chu y"
        result["description"] = "He than kinh tu dong co the dang chiu ap luc (stress, met moi, benh ly)"
        result["risk"] = "CAO"
        result["sleep_quality"] = "Giac ngu co the bi anh huong. Nen cai thien giac ngu."
    
    # RMSSD - chi so hoat dong pho giao cam (lien quan giac ngu)
    if rmssd > 40:
        result["parasympathetic"] = "Hoat dong pho giao cam tot (thu gian, phuc hoi)"
    elif rmssd > 20:
        result["parasympathetic"] = "Hoat dong pho giao cam trung binh"
    else:
        result["parasympathetic"] = "Hoat dong pho giao cam yeu - Co the dang stress cao"
    
    return result


def assess_overall_health(spo2_assess, hr_assess, hrv_assess, bpm_mean, spo2_mean):
    """Danh gia suc khoe tong hop"""
    result = {
        "overall_level": "",
        "overall_risk": "",
        "summary": "",
        "recommendations": []
    }
    
    # Dem so chi so nguy co
    risk_count = 0
    if spo2_assess["risk"] in ["CAO", "RAT CAO - CAN CAP CUU"]:
        risk_count += 2
    elif spo2_assess["risk"] == "TRUNG BINH":
        risk_count += 1
    
    if hr_assess["risk"] == "CAO":
        risk_count += 2
    elif hr_assess["risk"] == "TRUNG BINH" or hr_assess["risk"] == "THAP - TRUNG BINH":
        risk_count += 1
    
    if hrv_assess["risk"] == "CAO":
        risk_count += 1
    
    # Phan loai tong hop
    if risk_count == 0:
        result["overall_level"] = "SUC KHOE TIM MACH TOT"
        result["overall_risk"] = "NGUY CO THAP"
        result["summary"] = (
            f"Nhip tim {bpm_mean:.0f} BPM va SpO2 {spo2_mean:.0f}% deu nam trong "
            f"khoang binh thuong. Cac chi so cho thay he tim mach hoat dong on dinh."
        )
    elif risk_count <= 2:
        result["overall_level"] = "CAN THEO DOI"
        result["overall_risk"] = "NGUY CO TRUNG BINH"
        result["summary"] = (
            f"Mot so chi so (BPM={bpm_mean:.0f}, SpO2={spo2_mean:.0f}%) chua hoan toan "
            f"trong khoang ly tuong. Nen theo doi them va cai thien loi song."
        )
    else:
        result["overall_level"] = "CAN KHAM BAC SI"
        result["overall_risk"] = "NGUY CO CAO"
        result["summary"] = (
            f"Nhieu chi so bat thuong (BPM={bpm_mean:.0f}, SpO2={spo2_mean:.0f}%). "
            f"Nen di kham bac si chuyen khoa de duoc tu van cu the."
        )
    
    # Khuyen nghi cu the
    result["recommendations"] = []
    if spo2_mean < 95:
        result["recommendations"].append("- Kiem tra chuc nang ho hap. Tranh moi truong thieu oxy.")
    if bpm_mean > 100:
        result["recommendations"].append("- Giam caffeine, nghi ngoi du. Neu nhip tim nhanh keo dai, kham bac si.")
    if bpm_mean < 60:
        result["recommendations"].append("- Theo doi nhip tim. Neu chong mat/ngat xiu, kham bac si ngay.")
    if spo2_mean >= 95 and 60 <= bpm_mean <= 100:
        result["recommendations"].append("- Tiep tuc duy tri loi song lanh manh, tap the duc deu dan.")
        result["recommendations"].append("- Ngu du 7-8 tieng/dem, han che stress.")
    
    return result


def plot_health_assessment(pdf, health_data, test_name):
    """Ve trang danh gia suc khoe"""
    fig = plt.figure(figsize=(11, 8))
    fig.suptitle(f'DANH GIA SUC KHOE - {test_name}', fontsize=16, fontweight='bold', y=0.98)
    
    spo2_a = health_data["spo2"]
    hr_a = health_data["hr"]
    hrv_a = health_data["hrv"]
    overall = health_data["overall"]
    
    lines = []
    lines.append("=" * 65)
    lines.append("  DANH GIA SUC KHOE THEO TIEU CHUAN Y TE")
    lines.append("  (WHO, AHA, ISO 80601, Task Force ESC/NASPE)")
    lines.append("=" * 65)
    
    lines.append(f"\n1. OXY-HOA MAU (SpO2):")
    lines.append(f"   Phan loai: {spo2_a['level']}")
    lines.append(f"   Mo ta: {spo2_a['description']}")
    lines.append(f"   Nguy co: {spo2_a['risk']}")
    lines.append(f"   Khuyen nghi: {spo2_a['advice']}")
    if spo2_a.get("note_apnea"):
        lines.append(f"   *** {spo2_a['note_apnea']}")
    
    lines.append(f"\n2. NHIP TIM (BPM):")
    lines.append(f"   Phan loai: {hr_a['level']}")
    lines.append(f"   Mo ta: {hr_a['description']}")
    lines.append(f"   Nguy co: {hr_a['risk']}")
    lines.append(f"   Do on dinh: {hr_a['stability']}")
    lines.append(f"   Khuyen nghi: {hr_a['advice']}")
    
    lines.append(f"\n3. BIEN THIEN NHIP TIM (HRV):")
    lines.append(f"   Phan loai: {hrv_a['level']}")
    lines.append(f"   Mo ta: {hrv_a['description']}")
    lines.append(f"   Pho giao cam: {hrv_a.get('parasympathetic', 'N/A')}")
    lines.append(f"   Chat luong giac ngu: {hrv_a['sleep_quality']}")
    
    lines.append(f"\n{'=' * 65}")
    lines.append(f"  DANH GIA TONG HOP: {overall['overall_level']}")
    lines.append(f"  MUC NGUY CO: {overall['overall_risk']}")
    lines.append(f"{'=' * 65}")
    lines.append(f"\n  {overall['summary']}")
    
    if overall["recommendations"]:
        lines.append(f"\n  KHUYEN NGHI:")
        for rec in overall["recommendations"]:
            lines.append(f"  {rec}")
    
    lines.append(f"\n{'=' * 65}")
    lines.append("  LUU Y: Day la danh gia so bo tu thiet bi do tu che.")
    lines.append("  KHONG thay the chan doan cua bac si chuyen khoa.")
    lines.append("  Neu co bat thuong, hay den co so y te de kham.")
    lines.append(f"{'=' * 65}")
    
    fig.text(0.05, 0.90, '\n'.join(lines), fontsize=9, fontfamily='monospace',
             verticalalignment='top',
             bbox=dict(boxstyle='round', facecolor='#F0F8FF', alpha=0.9))
    
    pdf.savefig(fig)
    plt.close(fig)


# ========================================================================
# HAM VE DO THI
# ========================================================================
def plot_signal_overview(pdf, df, test_name):
    """Trang 1: Tong quan tin hieu"""
    fig, axes = plt.subplots(3, 1, figsize=(11, 8))
    fig.suptitle(f'TONG QUAN TIN HIEU - {test_name}', fontsize=14, fontweight='bold')
    
    t = df['Time_ms'].values / 1000  # giay
    
    # IR & Red
    axes[0].plot(t, df['IR_Value'], color='#8B0000', linewidth=0.8, label='IR', alpha=0.8)
    axes[0].plot(t, df['Red_Value'], color='#FF6347', linewidth=0.8, label='Red', alpha=0.8)
    axes[0].set_ylabel('ADC (LSB)')
    axes[0].set_title('Tin hieu IR & Red')
    axes[0].legend(loc='upper right', fontsize=8)
    axes[0].grid(True, alpha=0.3)
    
    # BPM
    axes[1].plot(t, df['HR_bpm'], color='#1E90FF', linewidth=1.2, label='BPM')
    valid_bpm = df[df['HR_bpm'] > 0]['HR_bpm']
    if len(valid_bpm) > 0:
        axes[1].axhline(y=valid_bpm.mean(), color='#1E90FF', linestyle='--', alpha=0.5,
                       label=f'TB: {valid_bpm.mean():.1f}')
    axes[1].set_ylabel('BPM')
    axes[1].set_title('Nhip tim (BPM)')
    axes[1].legend(loc='upper right', fontsize=8)
    axes[1].grid(True, alpha=0.3)
    
    # SpO2
    axes[2].plot(t, df['SpO2_percent'], color='#228B22', linewidth=1.2, label='SpO2')
    valid_spo2 = df[df['SpO2_percent'] > 0]['SpO2_percent']
    if len(valid_spo2) > 0:
        axes[2].axhline(y=valid_spo2.mean(), color='#228B22', linestyle='--', alpha=0.5,
                       label=f'TB: {valid_spo2.mean():.1f}%')
    axes[2].set_ylabel('SpO2 (%)')
    axes[2].set_xlabel('Thoi gian (s)')
    axes[2].set_title('Oxy-hoa mau (SpO2)')
    axes[2].legend(loc='upper right', fontsize=8)
    axes[2].grid(True, alpha=0.3)
    
    plt.tight_layout()
    pdf.savefig(fig)
    plt.close(fig)


def plot_fft_analysis(pdf, df, test_name):
    """Trang 2: Phan tich FFT"""
    valid = df[df['Valid'] == 1] if 'Valid' in df.columns else df
    ir = valid['IR_Value'].values.astype(float)
    
    if len(ir) < 20:
        return
    
    # Bo DC
    ir_ac = ir - np.mean(ir)
    
    # FFT
    n = len(ir_ac)
    freqs = np.fft.rfftfreq(n, d=1.0/SAMPLE_RATE)
    fft_mag = np.abs(np.fft.rfft(ir_ac)) / n
    
    # Chi hien 0.5 - 4 Hz (30 - 240 BPM)
    mask = (freqs >= 0.5) & (freqs <= 4.0)
    
    fig, axes = plt.subplots(2, 1, figsize=(11, 7))
    fig.suptitle(f'PHAN TICH TAN SO (FFT) - {test_name}', fontsize=14, fontweight='bold')
    
    # Tin hieu AC
    t = np.arange(len(ir_ac)) / SAMPLE_RATE
    axes[0].plot(t, ir_ac, color='#8B0000', linewidth=0.8)
    axes[0].set_title('Tin hieu IR (da bo DC)')
    axes[0].set_xlabel('Thoi gian (s)')
    axes[0].set_ylabel('Bien do AC')
    axes[0].grid(True, alpha=0.3)
    
    # Pho tan so
    if np.any(mask):
        axes[1].plot(freqs[mask], fft_mag[mask], color='#1E90FF', linewidth=1.5)
        axes[1].fill_between(freqs[mask], fft_mag[mask], alpha=0.3, color='#87CEEB')
        
        # Tim dinh chinh (tan so nhip tim)
        peak_idx = np.argmax(fft_mag[mask])
        peak_freq = freqs[mask][peak_idx]
        peak_bpm = peak_freq * 60
        axes[1].axvline(x=peak_freq, color='red', linestyle='--', alpha=0.7,
                       label=f'Dinh: {peak_freq:.2f} Hz = {peak_bpm:.0f} BPM')
        axes[1].legend(fontsize=10)
    
    axes[1].set_title('Pho tan so (FFT)')
    axes[1].set_xlabel('Tan so (Hz)')
    axes[1].set_ylabel('Bien do')
    axes[1].grid(True, alpha=0.3)
    
    plt.tight_layout()
    pdf.savefig(fig)
    plt.close(fig)


def plot_peak_detection(pdf, df, test_name):
    """Trang 3: Phat hien dinh + HRV"""
    valid = df[df['Valid'] == 1] if 'Valid' in df.columns else df
    ir = valid['IR_Value'].values.astype(float)
    
    if len(ir) < 20:
        return
    
    peaks, python_bpm = detect_peaks_ir(ir, SAMPLE_RATE)
    hrv = compute_hrv(peaks, SAMPLE_RATE)
    
    fig, axes = plt.subplots(2, 1, figsize=(11, 7))
    fig.suptitle(f'PHAT HIEN DINH & HRV - {test_name}', fontsize=14, fontweight='bold')
    
    # Tin hieu voi cac dinh
    t = np.arange(len(ir)) / SAMPLE_RATE
    axes[0].plot(t, ir, color='#8B0000', linewidth=0.8, label='IR Signal')
    if len(peaks) > 0:
        axes[0].plot(t[peaks], ir[peaks], 'v', color='blue', markersize=8, label=f'Dinh ({len(peaks)} nhip)')
    axes[0].set_title(f'Phat hien dinh nhip tim (Python) | BPM = {python_bpm:.0f}')
    axes[0].set_xlabel('Thoi gian (s)')
    axes[0].set_ylabel('IR (LSB)')
    axes[0].legend(fontsize=9)
    axes[0].grid(True, alpha=0.3)
    
    # IBI & HRV
    if len(peaks) >= 3:
        ibi = np.diff(peaks) / SAMPLE_RATE * 1000  # ms
        axes[1].bar(range(len(ibi)), ibi, color='#4682B4', alpha=0.8)
        axes[1].axhline(y=hrv["mean_IBI"], color='red', linestyle='--',
                       label=f'TB: {hrv["mean_IBI"]:.0f} ms')
        axes[1].set_title(f'Khoang RR | SDNN={hrv["SDNN"]:.1f}ms | RMSSD={hrv["RMSSD"]:.1f}ms | pNN50={hrv["pNN50"]:.1f}%')
        axes[1].set_xlabel('Nhip thu')
        axes[1].set_ylabel('Khoang RR (ms)')
        axes[1].legend(fontsize=9)
        axes[1].grid(True, alpha=0.3)
    else:
        axes[1].text(0.5, 0.5, 'Khong du dinh de phan tich HRV', 
                    ha='center', va='center', fontsize=14, transform=axes[1].transAxes)
    
    plt.tight_layout()
    pdf.savefig(fig)
    plt.close(fig)


def plot_statistics(pdf, stats_bpm, stats_spo2, ref_bpm, ref_spo2, test_name):
    """Trang 4: Thong ke chi tiet + box plot"""
    fig, axes = plt.subplots(1, 2, figsize=(11, 5))
    fig.suptitle(f'THONG KE CHI TIET - {test_name}', fontsize=14, fontweight='bold')
    
    # Box plot BPM
    bpm_data = [stats_bpm["mean"]]
    axes[0].boxplot([[stats_bpm["min"], stats_bpm["Q1"], stats_bpm["median"],
                      stats_bpm["Q3"], stats_bpm["max"]]],
                   tick_labels=[test_name], patch_artist=True,
                   boxprops=dict(facecolor='#87CEEB'))
    axes[0].axhline(y=ref_bpm, color='red', linestyle='--', label=f'Ref: {ref_bpm:.0f}')
    axes[0].set_title(f'BPM: {stats_bpm["mean"]:.1f} ± {stats_bpm["std"]:.2f}\n'
                      f'CV={stats_bpm["CV%"]:.1f}% | CI95=[{stats_bpm["CI95_lower"]:.1f}, {stats_bpm["CI95_upper"]:.1f}]')
    axes[0].set_ylabel('BPM')
    axes[0].legend(fontsize=9)
    axes[0].grid(True, alpha=0.3, axis='y')
    
    # Box plot SpO2
    axes[1].boxplot([[stats_spo2["min"], stats_spo2["Q1"], stats_spo2["median"],
                      stats_spo2["Q3"], stats_spo2["max"]]],
                   tick_labels=[test_name], patch_artist=True,
                   boxprops=dict(facecolor='#98FB98'))
    axes[1].axhline(y=ref_spo2, color='red', linestyle='--', label=f'Ref: {ref_spo2:.1f}%')
    axes[1].set_title(f'SpO2: {stats_spo2["mean"]:.2f} ± {stats_spo2["std"]:.3f}%\n'
                      f'CV={stats_spo2["CV%"]:.2f}% | CI95=[{stats_spo2["CI95_lower"]:.2f}, {stats_spo2["CI95_upper"]:.2f}]')
    axes[1].set_ylabel('SpO2 (%)')
    axes[1].legend(fontsize=9)
    axes[1].grid(True, alpha=0.3, axis='y')
    
    plt.tight_layout()
    pdf.savefig(fig)
    plt.close(fig)


def plot_bland_altman(pdf, measured_values, ref_value, param_name, unit, test_name):
    """Trang 5: Bland-Altman plot (chuan y te)"""
    if len(measured_values) == 0:
        return
    
    # Bland-Altman: x = trung binh (do + ref), y = hieu (do - ref)
    means = (measured_values + ref_value) / 2
    diffs = measured_values - ref_value
    
    mean_diff = np.mean(diffs)  # Bias
    std_diff = np.std(diffs)
    loa_upper = mean_diff + 1.96 * std_diff  # Limits of Agreement
    loa_lower = mean_diff - 1.96 * std_diff
    
    fig, ax = plt.subplots(figsize=(11, 5))
    fig.suptitle(f'BLAND-ALTMAN: {param_name} - {test_name}', fontsize=14, fontweight='bold')
    
    ax.scatter(means, diffs, color='#4682B4', alpha=0.6, s=30)
    ax.axhline(y=mean_diff, color='red', linestyle='-', linewidth=2,
               label=f'Bias: {mean_diff:+.2f} {unit}')
    ax.axhline(y=loa_upper, color='gray', linestyle='--', linewidth=1.5,
               label=f'+1.96SD: {loa_upper:+.2f}')
    ax.axhline(y=loa_lower, color='gray', linestyle='--', linewidth=1.5,
               label=f'-1.96SD: {loa_lower:+.2f}')
    ax.fill_between(ax.get_xlim(), loa_lower, loa_upper, alpha=0.1, color='green')
    
    ax.set_xlabel(f'Trung binh ({param_name})')
    ax.set_ylabel(f'Hieu (Do - Ref) ({unit})')
    ax.set_title(f'Bias = {mean_diff:+.2f} | LoA = [{loa_lower:.2f}, {loa_upper:.2f}]')
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.3)
    
    plt.tight_layout()
    pdf.savefig(fig)
    plt.close(fig)


def plot_histograms(pdf, valid_df, test_name):
    """Trang 6: Histogram phan bo"""
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5))
    fig.suptitle(f'PHAN BO DU LIEU - {test_name}', fontsize=14, fontweight='bold')
    
    bpm = valid_df['HR_bpm']
    spo2 = valid_df['SpO2_percent']
    
    # BPM histogram
    axes[0].hist(bpm, bins=15, color='#87CEEB', edgecolor='#4682B4', alpha=0.8)
    axes[0].axvline(x=bpm.mean(), color='red', linestyle='--', label=f'Mean: {bpm.mean():.1f}')
    axes[0].axvline(x=bpm.median(), color='green', linestyle=':', label=f'Median: {bpm.median():.1f}')
    axes[0].set_title('Phan bo BPM')
    axes[0].set_xlabel('BPM')
    axes[0].set_ylabel('Tan suat')
    axes[0].legend(fontsize=8)
    axes[0].grid(True, alpha=0.3)
    
    # SpO2 histogram
    axes[1].hist(spo2, bins=15, color='#98FB98', edgecolor='#228B22', alpha=0.8)
    axes[1].axvline(x=spo2.mean(), color='red', linestyle='--', label=f'Mean: {spo2.mean():.2f}%')
    axes[1].axvline(x=spo2.median(), color='green', linestyle=':', label=f'Median: {spo2.median():.2f}%')
    axes[1].set_title('Phan bo SpO2')
    axes[1].set_xlabel('SpO2 (%)')
    axes[1].set_ylabel('Tan suat')
    axes[1].legend(fontsize=8)
    axes[1].grid(True, alpha=0.3)
    
    plt.tight_layout()
    pdf.savefig(fig)
    plt.close(fig)


def plot_rolling_average(pdf, df, test_name):
    """Trang 7: Trung binh truot"""
    valid = df[(df['HR_bpm'] > 0) & (df['SpO2_percent'] > 0)].copy()
    if len(valid) < 10:
        return
    
    window = min(20, len(valid) // 3)
    if window < 3:
        window = 3
    
    valid['BPM_rolling'] = valid['HR_bpm'].rolling(window=window, center=True).mean()
    valid['SpO2_rolling'] = valid['SpO2_percent'].rolling(window=window, center=True).mean()
    
    fig, axes = plt.subplots(2, 1, figsize=(11, 6))
    fig.suptitle(f'TRUNG BINH TRUOT (window={window}) - {test_name}', fontsize=14, fontweight='bold')
    
    t = valid['Time_ms'].values / 1000
    
    axes[0].plot(t, valid['HR_bpm'], color='#87CEEB', linewidth=0.8, alpha=0.5, label='BPM goc')
    axes[0].plot(t, valid['BPM_rolling'], color='#1E90FF', linewidth=2, label='Trung binh truot')
    axes[0].set_title('Nhip tim')
    axes[0].set_ylabel('BPM')
    axes[0].legend(fontsize=9)
    axes[0].grid(True, alpha=0.3)
    
    axes[1].plot(t, valid['SpO2_percent'], color='#98FB98', linewidth=0.8, alpha=0.5, label='SpO2 goc')
    axes[1].plot(t, valid['SpO2_rolling'], color='#228B22', linewidth=2, label='Trung binh truot')
    axes[1].set_title('Oxy-hoa mau')
    axes[1].set_ylabel('SpO2 (%)')
    axes[1].set_xlabel('Thoi gian (s)')
    axes[1].legend(fontsize=9)
    axes[1].grid(True, alpha=0.3)
    
    plt.tight_layout()
    pdf.savefig(fig)
    plt.close(fig)


def plot_correlation_heatmap(pdf, df, test_name):
    """Trang 8: Heatmap tuong quan"""
    cols = ['IR_Value', 'Red_Value', 'HR_bpm', 'SpO2_percent']
    available = [c for c in cols if c in df.columns]
    
    if len(available) < 2:
        return
    
    valid = df[df['HR_bpm'] > 0][available] if 'HR_bpm' in available else df[available]
    
    if len(valid) < 5:
        return
    
    corr = valid.corr()
    
    fig, ax = plt.subplots(figsize=(8, 6))
    fig.suptitle(f'MA TRAN TUONG QUAN - {test_name}', fontsize=14, fontweight='bold')
    
    im = ax.imshow(corr, cmap='RdBu_r', vmin=-1, vmax=1, aspect='auto')
    plt.colorbar(im)
    
    ax.set_xticks(range(len(available)))
    ax.set_yticks(range(len(available)))
    ax.set_xticklabels([c.replace('_', '\n') for c in available], fontsize=9)
    ax.set_yticklabels([c.replace('_', '\n') for c in available], fontsize=9)
    
    # Ghi so len o
    for i in range(len(available)):
        for j in range(len(available)):
            color = 'white' if abs(corr.iloc[i, j]) > 0.7 else 'black'
            ax.text(j, i, f'{corr.iloc[i, j]:.3f}', ha='center', va='center',
                   fontsize=11, color=color, fontweight='bold')
    
    plt.tight_layout()
    pdf.savefig(fig)
    plt.close(fig)


def plot_summary_page(pdf, results_list):
    """Trang cuoi: Tom tat ket qua"""
    fig = plt.figure(figsize=(11, 8))
    fig.suptitle('TOM TAT KET QUA DANH GIA', fontsize=16, fontweight='bold', y=0.98)
    
    text_lines = []
    text_lines.append(f"Ngay phan tich: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    text_lines.append("=" * 70)
    
    for r in results_list:
        text_lines.append(f"\n{r['test_name']}:")
        text_lines.append(f"  BPM:  {r['bpm_mean']:.1f} +/- {r['bpm_std']:.2f}  |  Sai so: {r['bpm_error']:.1f}  |  {'PASS' if r['bpm_pass'] else 'FAIL'}")
        text_lines.append(f"  SpO2: {r['spo2_mean']:.2f} +/- {r['spo2_std']:.3f}%  |  Sai so: {r['spo2_error']:.2f}%  |  {'PASS' if r['spo2_pass'] else 'FAIL'}")
        text_lines.append(f"  SNR:  {r['snr']:.1f} dB")
        text_lines.append(f"  BPM (FFT): {r['fft_bpm']:.0f}  |  BPM (Peak): {r['peak_bpm']:.0f}")
        text_lines.append(f"  HRV: SDNN={r['hrv']['SDNN']:.1f}ms  RMSSD={r['hrv']['RMSSD']:.1f}ms  pNN50={r['hrv']['pNN50']:.1f}%")
        text_lines.append(f"  Diem chat luong: {r['score']}/100 ({r['grade']})")
        text_lines.append("-" * 70)
    
    # Tong ket
    if len(results_list) > 0:
        avg_score = np.mean([r['score'] for r in results_list])
        text_lines.append(f"\nDIEM TONG: {avg_score:.0f}/100 ({get_grade(avg_score)})")
    
    fig.text(0.05, 0.88, '\n'.join(text_lines), fontsize=10, fontfamily='monospace',
             verticalalignment='top',
             bbox=dict(boxstyle='round', facecolor='lightyellow', alpha=0.8))
    
    pdf.savefig(fig)
    plt.close(fig)


# ========================================================================
# HAM CHINH
# ========================================================================
def main():
    print("\n" + "=" * 80)
    print(" " * 10 + "MAX30102 - PHAN TICH TONG HOP NANG CAO")
    print(" " * 10 + "FFT | HRV | Bland-Altman | Quality Score | PDF Report")
    print("=" * 80)
    
    # 1. Doc du lieu
    print("\n[1/5] Doc du lieu...")
    data = load_data()
    
    if not any(k.startswith("test") for k in data):
        print("LOI: Khong tim thay file CSV nao!")
        sys.exit(1)
    
    ref_bpm = data["ref"]["Reference_BPM"].mean()
    ref_spo2 = data["ref"]["Reference_SpO2"].mean()
    print(f"  Tham chieu: BPM={ref_bpm:.1f}, SpO2={ref_spo2:.1f}%")
    
    # 2. Phan tich tung test
    print("\n[2/5] Phan tich...")
    results_list = []
    test_data_for_pdf = []
    
    for test_key in sorted([k for k in data if k.startswith("test")]):
        df = data[test_key]
        test_name = test_key.upper().replace("_", " ")
        print(f"\n  --- {test_name} ---")
        
        valid = get_valid_data(df)
        print(f"  Mau hop le: {len(valid)}/{len(df)}")
        
        if len(valid) < 5:
            print(f"  CANH BAO: Qua it mau hop le, bo qua {test_name}")
            continue
        
        # Thong ke
        stats_bpm = compute_stats(valid['HR_bpm'], "BPM")
        stats_spo2 = compute_stats(valid['SpO2_percent'], "SpO2")
        
        # SNR
        ir_valid = df[df['Valid'] == 1]['IR_Value'].values if 'Valid' in df.columns else df['IR_Value'].values
        snr = compute_snr(ir_valid.astype(float))
        
        # Peak detection + HRV
        peaks, peak_bpm = detect_peaks_ir(ir_valid, SAMPLE_RATE)
        hrv = compute_hrv(peaks, SAMPLE_RATE)
        
        # FFT BPM
        if len(ir_valid) > 20:
            ir_ac = ir_valid.astype(float) - np.mean(ir_valid)
            n = len(ir_ac)
            freqs = np.fft.rfftfreq(n, d=1.0/SAMPLE_RATE)
            fft_mag = np.abs(np.fft.rfft(ir_ac))
            mask = (freqs >= 0.5) & (freqs <= 4.0)
            if np.any(mask):
                fft_bpm = freqs[mask][np.argmax(fft_mag[mask])] * 60
            else:
                fft_bpm = 0
        else:
            fft_bpm = 0
        
        # Sai so
        bpm_error = abs(stats_bpm["mean"] - ref_bpm)
        spo2_error = abs(stats_spo2["mean"] - ref_spo2)
        bpm_pass = bpm_error <= BPM_TOLERANCE
        spo2_pass = spo2_error <= SPO2_TOLERANCE
        
        # Diem chat luong
        score = compute_quality_score(stats_bpm, stats_spo2, snr, ref_bpm, ref_spo2)
        grade = get_grade(score)
        
        # === DANH GIA SUC KHOE THEO TIEU CHUAN Y TE ===
        spo2_min = valid['SpO2_percent'].min()
        spo2_assess = assess_spo2(stats_spo2["mean"], spo2_min)
        hr_assess = assess_heart_rate(stats_bpm["mean"], stats_bpm["std"])
        hrv_assess = assess_hrv(hrv)
        overall_assess = assess_overall_health(spo2_assess, hr_assess, hrv_assess,
                                               stats_bpm["mean"], stats_spo2["mean"])
        health_data = {
            "spo2": spo2_assess, "hr": hr_assess,
            "hrv": hrv_assess, "overall": overall_assess
        }
        
        # In ket qua
        print(f"  BPM:    {stats_bpm['mean']:.1f} +/- {stats_bpm['std']:.2f} (CV={stats_bpm['CV%']:.1f}%)")
        print(f"          Sai so: {bpm_error:.1f} -> {'PASS' if bpm_pass else 'FAIL'}")
        print(f"  SpO2:   {stats_spo2['mean']:.2f} +/- {stats_spo2['std']:.3f}% (CV={stats_spo2['CV%']:.2f}%)")
        print(f"          Sai so: {spo2_error:.2f}% -> {'PASS' if spo2_pass else 'FAIL'}")
        print(f"  SNR:    {snr:.1f} dB")
        print(f"  FFT:    {fft_bpm:.0f} BPM | Peak: {peak_bpm:.0f} BPM")
        print(f"  HRV:    SDNN={hrv['SDNN']:.1f}ms RMSSD={hrv['RMSSD']:.1f}ms pNN50={hrv['pNN50']:.1f}%")
        print(f"  Diem:   {score}/100 ({grade})")
        print(f"  --- DANH GIA SUC KHOE ---")
        print(f"  SpO2:   {spo2_assess['level']} (nguy co: {spo2_assess['risk']})")
        print(f"  Nhip tim: {hr_assess['level']} (nguy co: {hr_assess['risk']})")
        print(f"  HRV:    {hrv_assess['level']}")
        print(f"  >>> TONG HOP: {overall_assess['overall_level']} - {overall_assess['overall_risk']}")
        
        result = {
            "test_name": test_name,
            "bpm_mean": stats_bpm["mean"], "bpm_std": stats_bpm["std"],
            "bpm_error": bpm_error, "bpm_pass": bpm_pass,
            "spo2_mean": stats_spo2["mean"], "spo2_std": stats_spo2["std"],
            "spo2_error": spo2_error, "spo2_pass": spo2_pass,
            "snr": snr, "fft_bpm": fft_bpm, "peak_bpm": peak_bpm,
            "hrv": hrv, "score": score, "grade": grade,
            "stats_bpm": stats_bpm, "stats_spo2": stats_spo2,
            "health": health_data,
        }
        results_list.append(result)
        test_data_for_pdf.append((test_key, test_name, df, valid, result))
    
    # 3. Tao bao cao PDF
    print(f"\n[3/5] Tao bao cao PDF: {PDF_OUTPUT}...")
    
    with PdfPages(PDF_OUTPUT) as pdf:
        # Trang bia
        fig = plt.figure(figsize=(11, 8))
        fig.text(0.5, 0.65, 'MAX30102', fontsize=40, fontweight='bold',
                ha='center', va='center', color='#1E90FF')
        fig.text(0.5, 0.55, 'BÁO CÁO PHÂN TÍCH NÂNG CAO', fontsize=20,
                ha='center', va='center', color='#333333')
        fig.text(0.5, 0.45, 'Heart Rate & SpO2 Measurement System', fontsize=14,
                ha='center', va='center', color='#666666')
        fig.text(0.5, 0.30, f'ESP-IDF | GPTimer Interrupt | PlatformIO', fontsize=11,
                ha='center', va='center', color='#888888')
        fig.text(0.5, 0.20, f'Ngay: {datetime.now().strftime("%d/%m/%Y %H:%M")}', fontsize=11,
                ha='center', va='center', color='#888888')
        fig.text(0.5, 0.10, f'FFT | HRV | Bland-Altman | Quality Score', fontsize=10,
                ha='center', va='center', color='#AAAAAA')
        pdf.savefig(fig)
        plt.close(fig)
        
        # Cac trang phan tich cho tung test
        for test_key, test_name, df, valid, result in test_data_for_pdf:
            plot_signal_overview(pdf, df, test_name)
            plot_fft_analysis(pdf, df, test_name)
            plot_peak_detection(pdf, df, test_name)
            plot_statistics(pdf, result["stats_bpm"], result["stats_spo2"],
                          ref_bpm, ref_spo2, test_name)
            plot_bland_altman(pdf, valid['HR_bpm'].values, ref_bpm, "BPM", "BPM", test_name)
            plot_bland_altman(pdf, valid['SpO2_percent'].values, ref_spo2, "SpO2", "%", test_name)
            plot_histograms(pdf, valid, test_name)
            plot_rolling_average(pdf, df, test_name)
            plot_correlation_heatmap(pdf, df, test_name)
            plot_health_assessment(pdf, result["health"], test_name)
        
        # Trang tom tat cuoi
        plot_summary_page(pdf, results_list)
    
    print(f"  OK - Da tao {PDF_OUTPUT}")
    
    # 4. Luu file txt
    print(f"\n[4/5] Luu ket qua: {TXT_OUTPUT}...")
    
    with open(TXT_OUTPUT, 'w', encoding='utf-8') as f:
        f.write("=" * 80 + "\n")
        f.write(" " * 15 + "MAX30102 ANALYSIS REPORT (ADVANCED)\n")
        f.write("=" * 80 + "\n\n")
        f.write(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        
        for r in results_list:
            f.write(f"{r['test_name']}\n")
            f.write("-" * 60 + "\n")
            f.write(f"  BPM:   {r['bpm_mean']:.1f} +/- {r['bpm_std']:.2f}\n")
            f.write(f"         vs Ref {ref_bpm:.1f} | Error: {r['bpm_error']:.1f} | {'PASS' if r['bpm_pass'] else 'FAIL'}\n")
            f.write(f"  SpO2:  {r['spo2_mean']:.2f} +/- {r['spo2_std']:.3f}%\n")
            f.write(f"         vs Ref {ref_spo2:.1f}% | Error: {r['spo2_error']:.2f}% | {'PASS' if r['spo2_pass'] else 'FAIL'}\n")
            f.write(f"  SNR:   {r['snr']:.1f} dB\n")
            f.write(f"  FFT:   {r['fft_bpm']:.0f} BPM\n")
            f.write(f"  Peak:  {r['peak_bpm']:.0f} BPM\n")
            f.write(f"  HRV:   SDNN={r['hrv']['SDNN']:.1f}ms RMSSD={r['hrv']['RMSSD']:.1f}ms pNN50={r['hrv']['pNN50']:.1f}%\n")
            f.write(f"  Score: {r['score']}/100 ({r['grade']})\n")
            # Danh gia suc khoe
            h = r['health']
            f.write(f"\n  --- DANH GIA SUC KHOE (tieu chuan y te) ---\n")
            f.write(f"  SpO2:     {h['spo2']['level']}\n")
            f.write(f"            Nguy co: {h['spo2']['risk']} | {h['spo2']['advice']}\n")
            if h['spo2'].get('note_apnea'):
                f.write(f"            {h['spo2']['note_apnea']}\n")
            f.write(f"  Nhip tim: {h['hr']['level']}\n")
            f.write(f"            Nguy co: {h['hr']['risk']} | On dinh: {h['hr']['stability']}\n")
            f.write(f"            {h['hr']['advice']}\n")
            f.write(f"  HRV:      {h['hrv']['level']}\n")
            f.write(f"            Giac ngu: {h['hrv']['sleep_quality']}\n")
            f.write(f"  >>> TONG HOP: {h['overall']['overall_level']} ({h['overall']['overall_risk']})\n")
            f.write(f"      {h['overall']['summary']}\n")
            if h['overall']['recommendations']:
                f.write(f"      Khuyen nghi:\n")
                for rec in h['overall']['recommendations']:
                    f.write(f"      {rec}\n")
            f.write("\n")
        
        if results_list:
            avg = np.mean([r['score'] for r in results_list])
            f.write("=" * 60 + "\n")
            f.write(f"OVERALL: {avg:.0f}/100 ({get_grade(avg)})\n")
            f.write("=" * 60 + "\n")
    
    print(f"  OK - Da luu {TXT_OUTPUT}")
    
    # 5. Ket thuc
    print(f"\n[5/5] HOAN THANH!")
    print("\n" + "=" * 80)
    print("KET QUA:")
    for r in results_list:
        print(f"  {r['test_name']}: {r['score']}/100 ({r['grade']})")
        print(f"    BPM={r['bpm_mean']:.1f} (err {r['bpm_error']:.1f}) | SpO2={r['spo2_mean']:.1f}% (err {r['spo2_error']:.2f}%)")
    
    print(f"\nFile da tao:")
    print(f"  1. {PDF_OUTPUT} - Bao cao PDF day du ({len(test_data_for_pdf)} test x 9 trang)")
    print(f"  2. {TXT_OUTPUT} - Ket qua chi tiet")
    print("=" * 80 + "\n")


if __name__ == "__main__":
    main()
