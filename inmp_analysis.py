import glob
import os
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt


from scipy.signal import (
    butter,
    filtfilt,
    medfilt,
    iirnotch,
    find_peaks,
    correlate
)


# ==================================================
# CONFIG
# ==================================================


file_list = glob.glob("C:/VXL/VXL/inmp441_*.csv")


FS = 8000


RAW_24BIT_MIN = -8388608
RAW_24BIT_MAX = 8388607


# ==================================================
# PREPROCESSING
# ==================================================


def reject_out_of_range(data):


    mask = (
        (data < RAW_24BIT_MIN)
        | (data > RAW_24BIT_MAX)
    )


    cleaned = data.copy()


    if mask.any():


        valid = np.where(~mask)[0]


        cleaned[mask] = np.interp(
            np.where(mask)[0],
            valid,
            data[valid]
        )


    return cleaned




def despike(data):


    med = medfilt(data, 9)


    diff = data - med


    mad = np.median(
        np.abs(diff - np.median(diff))
    )


    sigma = 1.4826 * mad + 1e-9


    spikes = np.abs(diff) > 6 * sigma


    cleaned = data.copy()


    cleaned[spikes] = med[spikes]


    return cleaned




def highpass(data):


    b,a = butter(
        2,
        0.5/(FS/2),
        btype="high"
    )


    return filtfilt(b,a,data)




def notch50(data):


    b,a = iirnotch(
        50/(FS/2),
        20
    )


    return filtfilt(b,a,data)




def bandpass(data):


    b,a = butter(
        4,
        [25/(FS/2),180/(FS/2)],
        btype="band"
    )


    return filtfilt(b,a,data)




# ==================================================
# SHANNON ENVELOPE (SPRINGER STYLE)
# ==================================================


def shannon_envelope(x):


    x = x / (np.max(np.abs(x)) + 1e-12)


    energy = -(x**2) * np.log(x**2 + 1e-12)


    win = int(0.05 * FS)


    env = np.convolve(
        energy,
        np.ones(win)/win,
        mode="same"
    )


    return env




# ==================================================
# SPRINGER RULE BASED S1-S2 DETECTION
# ==================================================


def detect_s1s2(env):


    threshold = (
        np.mean(env)
        + 0.8*np.std(env)
    )


    peaks,_ = find_peaks(
        env,
        height=threshold,
        distance=int(0.05*FS)
    )


    S1 = []
    S2 = []


    i = 0


    while i < len(peaks)-2:


        p1 = peaks[i]
        p2 = peaks[i+1]
        p3 = peaks[i+2]


        dt12 = (p2-p1)/FS
        dt23 = (p3-p2)/FS


        # Springer timing model


        if (
            0.05 <= dt12 <= 0.25
            and
            0.25 <= dt23 <= 0.80
        ):


            S1.append(p1)
            S2.append(p2)


            i += 2


        else:


            i += 1


    return peaks,S1,S2,threshold




# ==================================================
# UOC TINH BPM BANG AUTOCORRELATION (de doi chieu)
# ==================================================
#
# Cach ghep cap S1-S2 o tren rat nhay voi nhung doan bi mat
# dinh / dinh khong on dinh do bien do. Autocorrelation toan
# cuc se tim chu ky lap lai chu dao cua bao tin hieu, it bi
# anh huong hon boi viec thieu/sot mot vai dinh rieng le - dung
# de KIEM TRA CHEO voi so BPM tinh tu ghep cap, khong thay the
# hoan toan vi van co the bat nham bac song hai (sub-harmonic).


def estimate_bpm_autocorr(env, fs, bpm_range=(40, 180)):


    env_c = env - np.mean(env)
    ac = correlate(env_c, env_c, mode="full", method="fft")
    mid = len(ac) // 2


    min_lag = int(fs * 60 / bpm_range[1])
    max_lag = int(fs * 60 / bpm_range[0])


    window_ac = ac[mid + min_lag: mid + max_lag]
    if len(window_ac) == 0 or ac[mid] <= 0:
        return None, 0.0


    peak_pos = np.argmax(window_ac)
    score = window_ac[peak_pos] / ac[mid]   # 0..1, cang gan 1 cang tuan hoan ro
    bpm = 60 * fs / (peak_pos + min_lag)


    return bpm, score




# ==================================================
# MAIN
# ==================================================


if len(file_list)==0:


    print("Khong tim thay file CSV")


    quit()


target_file = max(
    file_list,
    key=os.path.getctime
)


print("File:",target_file)


df = pd.read_csv(target_file)


# QUAN TRONG: timestamp_ms chi co do phan giai 1ms, nhung toc do
# lay mau thuc te la 8000Hz (~8 mau / 1ms). Neu sap xep/loai trung
# theo timestamp_ms se xoa mat ~87% du lieu va lam sai toan bo FS
# dung trong cac buoc loc phia duoi. Dung cot "index" (so thu tu
# mau thuc, tang deu 1 don vi/mau) de sap xep va loai trung thay
# the - day la sua loi quan trong nhat trong phien ban nay.


df = df.sort_values(
    by="index"
).reset_index(drop=True)


df = df.drop_duplicates(
    subset=["index"]
)


signal_raw = df["raw_24bit"].astype(float).values


time_sec = (
    df["index"].values
    / FS
)


# ==================================================
# FILTER PIPELINE
# ==================================================


signal = reject_out_of_range(signal_raw)


signal = despike(signal)


signal = highpass(signal)


signal = notch50(signal)


signal_bp = bandpass(signal)


env = shannon_envelope(signal_bp)


# ==================================================
# DETECT S1 S2
# ==================================================


peaks,S1,S2,threshold = detect_s1s2(env)


# ==================================================
# HEART RATE (ghep cap)
# ==================================================


heart_times = np.array(S1)/FS


if len(heart_times) > 2:


    rr = np.diff(heart_times)


    bpm = 60/np.mean(rr)


else:


    bpm = 0
    rr = []


# ==================================================
# HEART RATE (autocorrelation - doi chieu)
# ==================================================


bpm_ac, score_ac = estimate_bpm_autocorr(env, FS)


# ==================================================
# REPORT
# ==================================================


print("\n========= REPORT =========")


print("So mau thuc te dung:", len(signal_raw),
      f"(~{len(signal_raw)/time_sec[-1]:.0f} Hz)")


print("Tong Peak:",len(peaks))


print("Cap S1-S2 (ghep cap):",len(S1))


print("Heart Rate (ghep cap):",round(bpm,1),"BPM")


if len(rr):


    print(
        "RR Mean:",
        round(np.mean(rr),3),
        "s"
    )


if bpm_ac is not None:
    print(f"Heart Rate (autocorrelation, doi chieu): {bpm_ac:.1f} BPM "
          f"(do tuan hoan = {score_ac:.2f}, "
          f"{'dang tin' if score_ac > 0.4 else 'CON YEU - can ghi lau hon / it nhieu hon'})")


# ==================================================
# PLOT
# ==================================================


plt.figure(figsize=(18,10))


# --------------------------------------------------


plt.subplot(3,1,1)


plt.plot(
    time_sec,
    signal_raw,
    linewidth=0.4
)


plt.title("Raw PCG")


plt.grid(True)


# --------------------------------------------------


plt.subplot(3,1,2)


plt.plot(
    time_sec,
    signal_bp,
    color="steelblue",
    linewidth=0.8
)


plt.title("Filtered PCG (25-180Hz)")


plt.grid(True)


# --------------------------------------------------


plt.subplot(3,1,3)


plt.plot(
    time_sec,
    env,
    color="crimson",
    linewidth=1.5,
    label="Shannon Envelope"
)


plt.axhline(
    threshold,
    color="green",
    linestyle="--",
    label="Threshold"
)


plt.scatter(
    np.array(S1)/FS,
    env[S1],
    color="red",
    s=60,
    label="S1"
)


plt.scatter(
    np.array(S2)/FS,
    env[S2],
    color="limegreen",
    s=60,
    label="S2"
)


for s1,s2 in zip(S1,S2):


    plt.axvspan(
        s1/FS,
        s2/FS,
        alpha=0.15
    )


bpm_ac_str = f" | AC={bpm_ac:.1f} BPM (score={score_ac:.2f})" if bpm_ac is not None else ""


plt.title(
    f"S1-S2 Detection | HR={bpm:.1f} BPM{bpm_ac_str}"
)


plt.xlabel("Time (s)")
plt.ylabel("Shannon Energy")


plt.legend()


plt.grid(True)


plt.tight_layout()
plt.show()
