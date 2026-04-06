#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include "MAX30105.h"
#include <driver/i2s.h>

#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 64
Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, -1);

MAX30105 particleSensor;

// Chân INMP441 của bạn
#define I2S_WS 15
#define I2S_SD 32
#define I2S_SCK 14
#define I2S_PORT I2S_NUM_0

int micHistory[SCREEN_WIDTH];
int irHistory[SCREEN_WIDTH];
int pos = 0;

void setup() {
  display.begin(SSD1306_SWITCHCAPVCC, 0x3C);
  display.clearDisplay();
  
  particleSensor.begin(Wire, I2C_SPEED_FAST);
  particleSensor.setup(0x1F, 4, 2, 400, 411, 4096); 

  const i2s_config_t i2s_config = {
    .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX),
    .sample_rate = 16000,
    .bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT,
    .channel_format = I2S_CHANNEL_FMT_ONLY_LEFT,
    .communication_format = I2S_COMM_FORMAT_STAND_I2S,
    .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,
    .dma_buf_count = 4,
    .dma_buf_len = 128
  };
  const i2s_pin_config_t pin_config = {
    .bck_io_num = I2S_SCK, .ws_io_num = I2S_WS, .data_out_num = -1, .data_in_num = I2S_SD
  };
  i2s_driver_install(I2S_PORT, &i2s_config, 0, NULL);
  i2s_set_pin(I2S_PORT, &pin_config);

  for(int i=0; i<SCREEN_WIDTH; i++) { micHistory[i] = 15; irHistory[i] = 45; }
}

void loop() {
  // 1. Lấy dữ liệu
  long irValue = particleSensor.getIR();
  int32_t micSample = 0;
  size_t bytes_read;
  i2s_read(I2S_PORT, &micSample, sizeof(micSample), &bytes_read, 0);

  // 2. Xử lý làm mượt tín hiệu (Dùng filter đơn giản)
  static float filteredIR = 0;
  filteredIR = (0.9 * filteredIR) + (0.1 * irValue);
  int irGraph = map(irValue - filteredIR, -200, 200, 40, 60); // Phóng to biến thiên nhịp tim
  
  int micGraph = map(constrain(abs(micSample/500), 0, 1000), 0, 1000, 30, 0); // Phóng to âm thanh

  // Lưu vào mảng
  micHistory[pos] = constrain(micGraph, 2, 30);
  irHistory[pos] = constrain(irGraph, 34, 62);

  // 3. Vẽ đồ thị dạng đường (Line) cho rõ nét
  display.clearDisplay();
  
  // Đường phân cách giữa 2 đồ thị
  display.drawFastHLine(0, 32, SCREEN_WIDTH, WHITE);

  for (int i = 1; i < SCREEN_WIDTH; i++) {
    int prev = (pos + i) % SCREEN_WIDTH;
    int curr = (pos + i + 1) % SCREEN_WIDTH;
    
    // Vẽ đồ thị Mic (Nửa trên)
    display.drawLine(i-1, micHistory[prev], i, micHistory[curr], WHITE);
    
    // Vẽ đồ thị IR (Nửa dưới)
    display.drawLine(i-1, irHistory[prev], i, irHistory[curr], WHITE);
  }

  display.display();
  pos = (pos + 1) % SCREEN_WIDTH;
}