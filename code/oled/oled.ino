#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>

#define SCREEN_WIDTH 128 // Độ rộng màn hình OLED (pixel)
#define SCREEN_HEIGHT 64 // Độ cao màn hình OLED (pixel)

// Khai báo màn hình OLED kết nối qua I2C (chân SDA, SCL mặc định)
Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, -1);

void setup() {
  Serial.begin(115200);

  // Khởi tạo OLED với địa chỉ I2C là 0x3C
  if(!display.begin(SSD1306_SWITCHCAPVCC, 0x3C)) { 
    Serial.println(F("SSD1306 allocation failed"));
    for(;;); // Dừng chương trình nếu không tìm thấy màn hình
  }

  delay(2000); // Đợi 2 giây để màn hình khởi động xong
  display.clearDisplay(); // Xóa bộ nhớ đệm (màn hình sẽ đen xì)

  // Vẽ nội dung
  display.setTextSize(1);      // Cỡ chữ (1 hoặc 2)
  display.setTextColor(WHITE); // Màu chữ
  display.setCursor(20, 0);   // Đặt con trỏ tại tọa độ (x, y)
  display.println("Hello everyone!");
  
  display.setTextSize(1);
  display.setCursor(5, 25);
  display.println("Group 11 are testing");

  display.setTextSize(1);
  display.setCursor(8, 40);
  display.println("ESP32 and oled OK.");

  display.display(); // Lệnh quan trọng: Đẩy dữ liệu từ bộ nhớ lên màn hình
}

void loop() {
  // Không cần làm gì trong vòng lặp ở bài test này
}