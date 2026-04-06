#include <SPI.h>
#include <SD.h>

void setup() {
  Serial.begin(115200);
  
  if (!SD.begin(5)) { // Chân CS là GPIO 5
    Serial.println("Thẻ nhớ bị lỗi hoặc không tồn tại!");
    return;
  }

  File dataFile = SD.open("/test.txt", FILE_WRITE);
  if (dataFile) {
    dataFile.println("Hello từ ESP32!");
    dataFile.close();
    Serial.println("Đã ghi file thành công!");
  } else {
    Serial.println("Không mở được file.");
  }
}

void loop() {}