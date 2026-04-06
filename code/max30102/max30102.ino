#include <Wire.h>
#include "MAX30105.h" // Thư viện dùng chung cho cả dòng 3010x

MAX30105 particleSensor;

void setup() {
  Serial.begin(115200);
  Serial.println("Đang khởi tạo MAX30102...");

  // Khởi tạo I2C với chân mặc định (21, 22)
  if (!particleSensor.begin(Wire, I2C_SPEED_FAST)) {
    Serial.println("Không tìm thấy cảm biến MAX30102. Vui lòng kiểm tra dây nối!");
    while (1);
  }

  // Cấu hình cảm biến với các thông số mặc định
  particleSensor.setup(); 
}

void loop() {
  // Đọc giá trị IR (Hồng ngoại)
  // Giá trị này sẽ tăng vọt khi có vật thể (ngón tay) che lên cảm biến
  long irValue = particleSensor.getIR();

  if (irValue < 50000) {
    Serial.println("Vui lòng đặt ngón tay lên cảm biến!");
  } else {
    Serial.print("Giá trị IR: ");
    Serial.println(irValue);
  }

  delay(100); 
}