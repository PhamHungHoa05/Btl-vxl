import serial
import csv
import os
import time
from datetime import datetime


# ==========================================
# CẤU HÌNH KẾT NỐI VÀ LƯU TRỮ
# ==========================================
PORT          = "COM4"
BAUD          = 921600
SAVE_DIR      = "C:/VXL/VXL"


FLUSH_EVERY   = 200     # so dong gom lai truoc khi viet+flush xuong dia
PRINT_EVERY   = 1000    # so dong moi lan in tien trinh (thay vi in tung dong)


# Khoang gia tri hop le cua so 24-bit co dau: -2^23 .. 2^23-1
RAW_24BIT_MIN = -8388608
RAW_24BIT_MAX = 8388607


os.makedirs(SAVE_DIR, exist_ok=True)


ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
filename = os.path.join(SAVE_DIR, f"inmp441_{ts}.csv")


print(f"[INFO] Dang mo ket noi toi cong: {PORT}...")
ser = serial.Serial(PORT, BAUD, timeout=10)


ser.dtr = False
ser.rts = False


print("[INFO] Cho 3 giay de thiet bi khoi dong va dong bo hoa...")
time.sleep(3)


ser.reset_input_buffer()


f      = open(filename, "w", newline="")
writer = csv.writer(f)
writer.writerow(["index", "raw_24bit", "timestamp_ms"])
print(f"[INFO] File ket qua da tao xong: {filename}")


print("[INFO] Phat tin hieu 'r' kich hoat dong bo xuong ESP32...")
ser.write(b'r')
time.sleep(0.1)


count = 0
rejected = 0          # so dong bi loai vi gia tri vuot khoang 24-bit hop le
buffer = []          # gom nhieu dong truoc khi viet xuong dia
t_start = time.time()
print("[INFO] Thiet bi dang ghi am. Vui long giu im lang...")


while True:
    try:
        line = ser.readline().decode("utf-8", errors="ignore").strip()
        if not line:
            continue


        if "##END##" in line:
            if buffer:
                writer.writerows(buffer)
                f.flush()
                buffer.clear()
            elapsed = time.time() - t_start
            print(f"\n[OK] Hoan thanh chu ky! Da ghi nhan thanh cong {count} mau "
                  f"trong {elapsed:.2f} giay (~{count/elapsed:.0f} mau/giay).")
            print(f"[INFO] Da loai {rejected} dong vi gia tri vuot khoang 24-bit hop le.")
            break


        if "," in line:
            parts = line.split(",")
            if len(parts) == 3:
                try:
                    idx_s, raw_s, ts_s = (p.strip() for p in parts)
                    int(idx_s)
                    raw_val = int(raw_s)
                    int(ts_s)


                    # Loai ngay cac gia tri vuot khoang vat ly cua 24-bit
                    # (day la dau hieu dong bi loi do nhieu duong truyen)
                    if raw_val < RAW_24BIT_MIN or raw_val > RAW_24BIT_MAX:
                        rejected += 1
                        continue


                    buffer.append([idx_s, raw_s, ts_s])
                    count += 1


                    # Viet+flush theo lo, khong lam cho moi mau
                    if len(buffer) >= FLUSH_EVERY:
                        writer.writerows(buffer)
                        f.flush()
                        buffer.clear()


                    # Chi in tien trinh dinh ky, khong in tung dong
                    if count % PRINT_EVERY == 0:
                        elapsed = time.time() - t_start
                        print(f"  ... da thu {count} mau ({elapsed:.1f}s, "
                              f"~{count/elapsed:.0f} mau/giay)")


                except ValueError:
                    # Bo qua cac dong rach chu do nhieu xung duong truyen
                    pass
    except KeyboardInterrupt:
        print("\n[WARNING] Da chu dong dung thu thap som bang ban phim.")
        if buffer:
            writer.writerows(buffer)
            f.flush()
        break


f.close()
ser.close()
print(f"[SUCCESS] Tien trinh hoan tat. File luu tai: {filename}")
