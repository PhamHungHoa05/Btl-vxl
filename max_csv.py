#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RECEIVE_CSV.py - Hung file CSV tu ESP32 qua Serial va luu vao may tinh
Cach dung:
    1. Dong Serial Monitor cua PlatformIO truoc (de khong bi chiem cong)
    2. Sua COM_PORT ben duoi cho dung cong cua ban
    3. Chay: python RECEIVE_CSV.py
    4. Bam nut RST tren board ESP32 -> doi do xong -> file tu luu
"""

import serial
import sys

# ===== CAU HINH - SUA CHO DUNG MAY BAN =====
COM_PORT = "COM4"          # Cong COM cua ESP32 (xem trong PlatformIO)
BAUD_RATE = 921600         # PHAI khop UART_BAUD trong main_combined.c (da doi tu 115200 -> 921600)
OUTPUT_FILE = "max30102_test.csv"

# Dau moc ESP32 gui
MARK_START = "<<<CSV_START>>>"
MARK_END   = "<<<CSV_END>>>"


def is_csv_data(line):
    """Kiem tra dong co phai du lieu CSV khong (khong phai log ESP-IDF)"""
    # Dong log ESP-IDF bat dau bang: "I (", "W (", "E (", "D ("
    if line.startswith("I (") or line.startswith("W (") or line.startswith("E (") or line.startswith("D ("):
        return False
    # Dong trong
    if not line.strip():
        return False
    # Dong CSV hop le: bat dau bang so hoac la header
    if line[0].isdigit() or line.startswith("Time_ms"):
        return True
    return False


def main():
    print(f"Dang mo cong {COM_PORT} @ {BAUD_RATE}...")
    try:
        ser = serial.Serial(COM_PORT, BAUD_RATE, timeout=1)
    except serial.SerialException as e:
        print(f"LOI: Khong mo duoc cong {COM_PORT}")
        print(f"  Chi tiet: {e}")
        print(f"  -> Da dong Serial Monitor cua PlatformIO chua?")
        print(f"  -> Kiem tra cong COM co dung khong?")
        sys.exit(1)

    print("Da ket noi. Dang gui lenh doc file cu tren the SD...")
    print("(Khong can dat ngon tay - chi doc file da co san)")
    print("-" * 50)

    # Cho ESP32 khoi dong xong roi gui phim 'D' (che do doc file cu)
    print("Da ket noi. Dang gui lenh doc file cu (D) lien tuc...")
    print(">>> HAY BAM NUT RST TREN BOARD ESP32 NGAY BAY GIO <<<")
    print("(Khong can dat ngon tay - chi doc file da co san)")
    print("-" * 50)

    import time
    import threading

    # Gui phim 'D' lien tuc trong 12 giay o mot luong rieng
    # De dam bao ESP32 bat duoc lenh du ban bam RST luc nao
    stop_sending = threading.Event()

    def send_D_repeatedly():
        start = time.time()
        while not stop_sending.is_set() and (time.time() - start) < 12:
            try:
                ser.write(b'D')
                ser.flush()
            except:
                pass
            time.sleep(0.1)   # gui moi 100ms

    sender = threading.Thread(target=send_D_repeatedly, daemon=True)
    sender.start()

    capturing = False
    csv_lines = []

    try:
        while True:
            raw = ser.readline()
            if not raw:
                continue
            text = raw.decode("utf-8", errors="ignore").strip()

            if MARK_START in text:
                capturing = True
                csv_lines = []
                stop_sending.set()   # da bat dau nhan -> ngung gui D
                print("[+] Bat dau nhan du lieu CSV...")
                continue

            if MARK_END in text:
                print("[+] Nhan xong!")
                break

            if capturing:
                if is_csv_data(text):
                    csv_lines.append(text)
                    # Hien thi tien trinh
                    if text.startswith("Time_ms"):
                        print(f"  Header: {text}")
                    elif len(csv_lines) % 50 == 0:
                        print(f"  Da nhan {len(csv_lines)} dong...")
                else:
                    # In log ESP de theo doi (nhung khong luu vao CSV)
                    if text:
                        print(f"  [LOG] {text}")
            else:
                if text:
                    print(f"[ESP32] {text}")

    except KeyboardInterrupt:
        print("\nDa dung (Ctrl+C).")
    finally:
        ser.close()

    # Luu file
    if csv_lines:
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            f.write("\n".join(csv_lines) + "\n")
        print("-" * 50)
        print(f"DA LUU: {OUTPUT_FILE}")
        print(f"  So dong du lieu: {len(csv_lines) - 1} mau (+ 1 header)")
        print(f"  Gio chay: python MASTER.py")
    else:
        print("Khong nhan duoc du lieu nao.")


if __name__ == "__main__":
    main()