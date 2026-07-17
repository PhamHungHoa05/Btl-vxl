/*
 * main_combined.c
 * ------------------------------------------------------------
 * Gop MAX30102 (nhip tim/SpO2, ghi SD) va INMP441 (mic I2S) chay
 * chung tren 1 ESP32, dieu khien bang phim goi qua Serial/UART.
 * ------------------------------------------------------------
 */

#include <stdio.h>
#include <string.h>
#include <ctype.h>
#include <math.h>
#include <stdint.h>
#include <stdbool.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "driver/i2c.h"
#include "driver/i2s.h"
#include "driver/spi_common.h"
#include "driver/gptimer.h"
#include "driver/uart.h"
#include "esp_vfs_dev.h"
#include "esp_timer.h"
#include "esp_log.h"
#include "esp_vfs_fat.h"
#include "sdmmc_cmd.h"
#include "driver/sdspi_host.h"

static const char *TAG = "COMBINED";

/* ================================================================
 *  CAU HINH CHAN - MAX30102 (I2C) + SD CARD (SPI)
 * ================================================================ */
#define I2C_PORT        I2C_NUM_0
#define I2C_SDA         21
#define I2C_SCL         22
#define I2C_FREQ_HZ     400000

#define SD_MOSI         23
#define SD_MISO         19
#define SD_SCK          18
#define SD_CS           5
#define MOUNT_POINT     "/sdcard"

/* ================================================================
 *  CAU HINH CHAN - INMP441 (I2S)
 * ================================================================ */
#define I2S_WS          15
#define I2S_SD          32
#define I2S_SCK         14
#define I2S_PORT        I2S_NUM_0

#define AUDIO_SAMPLE_RATE   8000
#define AUDIO_N_SAMPLES     240000
#define DMA_BUF_COUNT       8
#define DMA_BUF_LEN         1024

/* ================================================================
 *  CAU HINH OLED SSD1306 0.96"
 * ================================================================ */
#define OLED_ADDR       0x3C
#define OLED_WIDTH      128
#define OLED_HEIGHT     64
#define OLED_PAGES      (OLED_HEIGHT / 8)

/* ================================================================
 *  CAU HINH UART
 * ================================================================ */
#define UART_PORT       UART_NUM_0
#define UART_BAUD       921600   

/* ================================================================
 *  THANH GHI MAX30102
 * ================================================================ */
#define MAX30102_ADDR           0x57

#define REG_FIFO_WR_PTR         0x04
#define REG_OVF_COUNTER         0x05
#define REG_FIFO_RD_PTR         0x06
#define REG_FIFO_DATA           0x07
#define REG_FIFO_CONFIG         0x08
#define REG_MODE_CONFIG         0x09
#define REG_SPO2_CONFIG         0x0A
#define REG_LED1_PA             0x0C
#define REG_LED2_PA             0x0D
#define REG_PART_ID             0xFF

/* ================================================================
 *  TRANG THAI HE THONG
 * ================================================================ */
typedef enum {
    SYS_IDLE_MAX,        
    SYS_MAX_MEASURING,   
    SYS_AUDIO_RECORDING  
} sys_state_t;

static volatile sys_state_t sys_state = SYS_IDLE_MAX;

/* ================================================================
 *  BIEN TOAN CUC - MAX30102
 * ================================================================ */
static volatile bool sample_ready      = false;
static volatile bool measurement_done  = false;
static int      sample_count           = 0;
static int64_t  start_time_us          = 0;
static gptimer_handle_t gptimer        = NULL;

static FILE *csv_file = NULL;
static sdmmc_card_t *sd_card = NULL;

/* ----- Thuat toan BPM/SpO2 ----- */
#define BPM_AVG_SIZE           4
#define DC_ALPHA               0.995f
#define BEAT_THRESHOLD         500
#define BEAT_MIN_INTERVAL_US   400000
#define BEAT_MAX_INTERVAL_US   2000000
#define REFRACTORY_SAMPLES     50

#define AC_SMOOTH_SIZE         5
static float ac_smooth_buf[AC_SMOOTH_SIZE] = {0};
static int   ac_smooth_idx  = 0;
static bool  ac_smooth_full = false;

static float ir_dc = 0, red_dc = 0;
static bool  dc_initialized = false;
static float prev_ir_smooth = 0, prev2_ir_smooth = 0;
static int   samples_since_beat = 0;

static int64_t last_beat_us = 0;
static float   bpm_buf[BPM_AVG_SIZE] = {0};
static int     bpm_idx = 0, bpm_count = 0;
static int     current_bpm = 0;

static float ir_beat_max, ir_beat_min;
static float red_beat_max, red_beat_min;
static int   current_spo2 = 0;

/* ================================================================
 *  BIEN TOAN CUC - INMP441
 * ================================================================ */
static int32_t  audio_dma_buf[DMA_BUF_LEN];
static uint32_t audio_sample_count = 0;

/* ================================================================
 *  UART INIT
 * ================================================================ */
static void uart_init(void)
{
    uart_config_t cfg = {
        .baud_rate  = UART_BAUD,
        .data_bits  = UART_DATA_8_BITS,
        .parity     = UART_PARITY_DISABLE,
        .stop_bits  = UART_STOP_BITS_1,
        .flow_ctrl  = UART_HW_FLOWCTRL_DISABLE,
        .source_clk = UART_SCLK_APB,
    };
    uart_driver_install(UART_PORT, 256, 0, 0, NULL, 0);
    uart_param_config(UART_PORT, &cfg);
    esp_vfs_dev_uart_use_driver(UART_PORT);
}

/* ================================================================
 *  MAX30102 - THUẬT TOÁN
 * ================================================================ */
static float smooth_ac(float new_val) {
    ac_smooth_buf[ac_smooth_idx] = new_val;
    ac_smooth_idx = (ac_smooth_idx + 1) % AC_SMOOTH_SIZE;
    if (!ac_smooth_full && ac_smooth_idx == 0) ac_smooth_full = true;
    int count = ac_smooth_full ? AC_SMOOTH_SIZE : ac_smooth_idx;
    if (count == 0) return new_val;
    float sum = 0;
    for (int i = 0; i < count; i++) sum += ac_smooth_buf[i];
    return sum / count;
}

static void process_sample(uint32_t ir, uint32_t red) {
    if (!dc_initialized) {
        ir_dc = (float)ir;
        red_dc = (float)red;
        ir_beat_max = (float)ir;  ir_beat_min = (float)ir;
        red_beat_max = (float)red; red_beat_min = (float)red;
        dc_initialized = true;
        return;
    }

    if (fabsf((float)ir - ir_dc) > ir_dc * 0.3f) {
        ir_dc = (float)ir;
        red_dc = (float)red;
        ir_beat_max = (float)ir;  ir_beat_min = (float)ir;
        red_beat_max = (float)red; red_beat_min = (float)red;
    }

    ir_dc = ir_dc * DC_ALPHA + (float)ir * (1.0f - DC_ALPHA);
    red_dc = red_dc * DC_ALPHA + (float)red * (1.0f - DC_ALPHA);

    float ir_ac_raw = (float)ir - ir_dc;
    float ir_ac = smooth_ac(ir_ac_raw);

    if ((float)ir > ir_beat_max) ir_beat_max = (float)ir;
    if ((float)ir < ir_beat_min) ir_beat_min = (float)ir;
    if ((float)red > red_beat_max) red_beat_max = (float)red;
    if ((float)red < red_beat_min) red_beat_min = (float)red;

    samples_since_beat++;

    bool is_peak = (prev_ir_smooth > prev2_ir_smooth) &&
                   (prev_ir_smooth > ir_ac) &&
                   (prev_ir_smooth > BEAT_THRESHOLD) &&
                   (samples_since_beat > REFRACTORY_SAMPLES);

    if (is_peak) {
        samples_since_beat = 0;
        int64_t now = esp_timer_get_time();
        if (last_beat_us > 0) {
            int64_t interval = now - last_beat_us;
            if (interval > BEAT_MIN_INTERVAL_US && interval < BEAT_MAX_INTERVAL_US) {
                float bpm = 60000000.0f / (float)interval;
                bpm_buf[bpm_idx] = bpm;
                bpm_idx = (bpm_idx + 1) % BPM_AVG_SIZE;
                if (bpm_count < BPM_AVG_SIZE) bpm_count++;
                float sum = 0;
                for (int i = 0; i < bpm_count; i++) sum += bpm_buf[i];
                current_bpm = (int)(sum / bpm_count);

                float ir_ac_amp = ir_beat_max - ir_beat_min;
                float red_ac_amp = red_beat_max - red_beat_min;
                if (ir_ac_amp > 100 && ir_dc > 1000 && red_dc > 1000) {
                    float R = (red_ac_amp / red_dc) / (ir_ac_amp / ir_dc);
                    int spo2 = (int)(110.0f - 25.0f * R);
                    if (spo2 > 100) spo2 = 100;
                    if (spo2 < 70) spo2 = 70;
                    current_spo2 = spo2;
                }

                ir_beat_max = (float)ir;  ir_beat_min = (float)ir;
                red_beat_max = (float)red; red_beat_min = (float)red;
            }
        }
        last_beat_us = now;
    }

    prev2_ir_smooth = prev_ir_smooth;
    prev_ir_smooth = ir_ac;
}

static void reset_max_algorithm_state(void) {
    memset(ac_smooth_buf, 0, sizeof(ac_smooth_buf));
    ac_smooth_idx = 0;
    ac_smooth_full = false;

    ir_dc = 0; red_dc = 0;
    dc_initialized = false;
    prev_ir_smooth = 0; prev2_ir_smooth = 0;
    samples_since_beat = 0;

    last_beat_us = 0;
    memset(bpm_buf, 0, sizeof(bpm_buf));
    bpm_idx = 0; bpm_count = 0;
    current_bpm = 0;

    current_spo2 = 0;
}

/* ================================================================
 *  MAX30102 - I2C DRIVER
 * ================================================================ */
static esp_err_t max30102_write_reg(uint8_t reg, uint8_t value) {
    uint8_t buf[2] = { reg, value };
    return i2c_master_write_to_device(I2C_PORT, MAX30102_ADDR, buf, 2,
                                      1000 / portTICK_PERIOD_MS);
}

static esp_err_t max30102_read_regs(uint8_t reg, uint8_t *data, size_t len) {
    return i2c_master_write_read_device(I2C_PORT, MAX30102_ADDR,
                                        &reg, 1, data, len,
                                        1000 / portTICK_PERIOD_MS);
}

static esp_err_t i2c_init(void) {
    i2c_config_t conf = {
        .mode = I2C_MODE_MASTER,
        .sda_io_num = I2C_SDA,
        .scl_io_num = I2C_SCL,
        .sda_pullup_en = GPIO_PULLUP_ENABLE,
        .scl_pullup_en = GPIO_PULLUP_ENABLE,
        .master.clk_speed = I2C_FREQ_HZ,
    };
    esp_err_t err = i2c_param_config(I2C_PORT, &conf);
    if (err != ESP_OK) return err;
    return i2c_driver_install(I2C_PORT, conf.mode, 0, 0, 0);
}

static esp_err_t max30102_init(void) {
    uint8_t part_id = 0;
    if (max30102_read_regs(REG_PART_ID, &part_id, 1) != ESP_OK) {
        ESP_LOGE(TAG, "Khong doc duoc Part ID (loi I2C)");
        return ESP_FAIL;
    }
    ESP_LOGI(TAG, "Part ID = 0x%02X (mong doi 0x15)", part_id);

    max30102_write_reg(REG_MODE_CONFIG, 0x40);   
    vTaskDelay(100 / portTICK_PERIOD_MS);

    max30102_write_reg(REG_FIFO_WR_PTR, 0x00);
    max30102_write_reg(REG_OVF_COUNTER, 0x00);
    max30102_write_reg(REG_FIFO_RD_PTR, 0x00);

    max30102_write_reg(REG_FIFO_CONFIG, 0x10);
    max30102_write_reg(REG_MODE_CONFIG, 0x03);   
    max30102_write_reg(REG_SPO2_CONFIG, 0x27);
    max30102_write_reg(REG_LED1_PA, 0x24);
    max30102_write_reg(REG_LED2_PA, 0x24);

    ESP_LOGI(TAG, "MAX30102 da cau hinh xong");
    return ESP_OK;
}

static void max30102_shutdown(void) {
    max30102_write_reg(REG_MODE_CONFIG, 0x80);
    ESP_LOGI(TAG, "MAX30102: da SHUTDOWN");
}

static void max30102_wakeup(void) {
    max30102_write_reg(REG_MODE_CONFIG, 0x03);   
    max30102_write_reg(REG_FIFO_WR_PTR, 0x00);
    max30102_write_reg(REG_OVF_COUNTER, 0x00);
    max30102_write_reg(REG_FIFO_RD_PTR, 0x00);
    ESP_LOGI(TAG, "MAX30102: da BAT LAI (wake up)");
}

static esp_err_t max30102_read_fifo(uint32_t *red, uint32_t *ir) {
    uint8_t data[6];
    esp_err_t err = max30102_read_regs(REG_FIFO_DATA, data, 6);
    if (err != ESP_OK) return err;
    *red = (((uint32_t)data[0] << 16) | ((uint32_t)data[1] << 8) | data[2]) & 0x03FFFF;
    *ir  = (((uint32_t)data[3] << 16) | ((uint32_t)data[4] << 8) | data[5]) & 0x03FFFF;
    return ESP_OK;
}

static int max30102_fifo_available(void) {
    uint8_t wr_ptr = 0, rd_ptr = 0;
    max30102_read_regs(REG_FIFO_WR_PTR, &wr_ptr, 1);
    max30102_read_regs(REG_FIFO_RD_PTR, &rd_ptr, 1);
    int count = (int)wr_ptr - (int)rd_ptr;
    if (count < 0) count += 32;
    return count;
}

/* ================================================================
 *  SD CARD
 * ================================================================ */
static esp_err_t sd_init(void) {
    esp_err_t ret;
    esp_vfs_fat_sdmmc_mount_config_t mount_config = {
        .format_if_mount_failed = false,
        .max_files = 5,
        .allocation_unit_size = 16 * 1024
    };

    sdmmc_host_t host = SDSPI_HOST_DEFAULT();
    host.max_freq_khz = 4000;

    spi_bus_config_t bus_cfg = {
        .mosi_io_num = SD_MOSI,
        .miso_io_num = SD_MISO,
        .sclk_io_num = SD_SCK,
        .quadwp_io_num = -1,
        .quadhd_io_num = -1,
        .max_transfer_sz = 4000,
    };
    ret = spi_bus_initialize(host.slot, &bus_cfg, SPI_DMA_CH_AUTO);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Loi khoi tao SPI bus");
        return ret;
    }

    sdspi_device_config_t slot_config = SDSPI_DEVICE_CONFIG_DEFAULT();
    slot_config.gpio_cs = SD_CS;
    slot_config.host_id = host.slot;

    ret = esp_vfs_fat_sdspi_mount(MOUNT_POINT, &host, &slot_config,
                                  &mount_config, &sd_card);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Khong mount duoc SD card (%s)", esp_err_to_name(ret));
        return ret;
    }
    ESP_LOGI(TAG, "SD card da mount tai %s", MOUNT_POINT);
    return ESP_OK;
}

static void dump_csv_to_serial(void) {
    FILE *f = fopen(MOUNT_POINT "/max30102_test_1.csv", "r");
    if (!f) {
        printf("<<<CSV_ERROR>>>\n");
        return;
    }
    printf("<<<CSV_START>>>\n");

    char line[256];
    while (fgets(line, sizeof(line), f) != NULL) {
        printf("%s", line);
    }
    fclose(f);

    printf("<<<CSV_END>>>\n");
    fflush(stdout);
}

/* ================================================================
 *  OLED SSD1306 128x64
 * ================================================================ */

static esp_err_t oled_cmd(uint8_t cmd) {
    uint8_t buf[2] = { 0x00, cmd };
    return i2c_master_write_to_device(I2C_PORT, OLED_ADDR, buf, 2, 1000 / portTICK_PERIOD_MS);
}

static esp_err_t oled_data(const uint8_t *data, size_t len) {
    uint8_t buf[17];
    size_t sent = 0;
    while (sent < len) {
        size_t chunk = (len - sent) > 16 ? 16 : (len - sent);
        buf[0] = 0x40;  
        memcpy(&buf[1], data + sent, chunk);
        esp_err_t err = i2c_master_write_to_device(I2C_PORT, OLED_ADDR, buf, chunk + 1,
                                                    1000 / portTICK_PERIOD_MS);
        if (err != ESP_OK) return err;
        sent += chunk;
    }
    return ESP_OK;
}

static bool oled_init(void) {
    static const uint8_t init_cmds[] = {
        0xAE, 0xD5, 0x80, 0xA8, 0x3F, 0xD3, 0x00, 0x40,
        0x8D, 0x14, 0x20, 0x00, 0xA1, 0xC8, 0xDA, 0x12,
        0x81, 0xCF, 0xD9, 0xF1, 0xDB, 0x40, 0xA4, 0xA6, 0xAF
    };
    for (size_t i = 0; i < sizeof(init_cmds); i++) {
        if (oled_cmd(init_cmds[i]) != ESP_OK) {
            ESP_LOGW(TAG, "OLED: khong phan hoi");
            return false;
        }
    }
    return true;
}

static void oled_set_cursor(uint8_t page, uint8_t col) {
    oled_cmd(0xB0 | (page & 0x07));
    oled_cmd(0x00 | (col & 0x0F));
    oled_cmd(0x10 | ((col >> 4) & 0x0F));
}

static void oled_clear(void) {
    uint8_t zeros[16] = {0};
    for (uint8_t page = 0; page < OLED_PAGES; page++) {
        oled_set_cursor(page, 0);
        for (int c = 0; c < OLED_WIDTH; c += 16) {
            oled_data(zeros, 16);
        }
    }
}

#define FONT_FIRST_CHAR ' '
#define FONT_LAST_CHAR  'Z'
#define FONT_CHAR_COUNT (FONT_LAST_CHAR - FONT_FIRST_CHAR + 1)

static const uint8_t font5x7[FONT_CHAR_COUNT][5] = {
    [' ' - FONT_FIRST_CHAR] = {0x00,0x00,0x00,0x00,0x00},
    ['%' - FONT_FIRST_CHAR] = {0x23,0x13,0x08,0x64,0x62},
    ['(' - FONT_FIRST_CHAR] = {0x00,0x1C,0x22,0x41,0x00},
    [')' - FONT_FIRST_CHAR] = {0x00,0x41,0x22,0x1C,0x00},
    ['-' - FONT_FIRST_CHAR] = {0x08,0x08,0x08,0x08,0x08},
    ['.' - FONT_FIRST_CHAR] = {0x00,0x60,0x60,0x00,0x00},
    ['/' - FONT_FIRST_CHAR] = {0x20,0x10,0x08,0x04,0x02},
    ['0' - FONT_FIRST_CHAR] = {0x3E,0x51,0x49,0x45,0x3E},
    ['1' - FONT_FIRST_CHAR] = {0x00,0x42,0x7F,0x40,0x00},
    ['2' - FONT_FIRST_CHAR] = {0x42,0x61,0x51,0x49,0x46},
    ['3' - FONT_FIRST_CHAR] = {0x21,0x41,0x45,0x4B,0x31},
    ['4' - FONT_FIRST_CHAR] = {0x18,0x14,0x12,0x7F,0x10},
    ['5' - FONT_FIRST_CHAR] = {0x27,0x45,0x45,0x45,0x39},
    ['6' - FONT_FIRST_CHAR] = {0x3C,0x4A,0x49,0x49,0x30},
    ['7' - FONT_FIRST_CHAR] = {0x01,0x71,0x09,0x05,0x03},
    ['8' - FONT_FIRST_CHAR] = {0x36,0x49,0x49,0x49,0x36},
    ['9' - FONT_FIRST_CHAR] = {0x06,0x49,0x49,0x29,0x1E},
    [':' - FONT_FIRST_CHAR] = {0x00,0x36,0x36,0x00,0x00},
    ['A' - FONT_FIRST_CHAR] = {0x7E,0x11,0x11,0x11,0x7E},
    ['B' - FONT_FIRST_CHAR] = {0x7F,0x49,0x49,0x49,0x36},
    ['C' - FONT_FIRST_CHAR] = {0x3E,0x41,0x41,0x41,0x22},
    ['D' - FONT_FIRST_CHAR] = {0x7F,0x41,0x41,0x22,0x1C},
    ['E' - FONT_FIRST_CHAR] = {0x7F,0x49,0x49,0x49,0x41},
    ['F' - FONT_FIRST_CHAR] = {0x7F,0x09,0x09,0x09,0x01},
    ['G' - FONT_FIRST_CHAR] = {0x3E,0x41,0x49,0x49,0x7A},
    ['H' - FONT_FIRST_CHAR] = {0x7F,0x08,0x08,0x08,0x7F},
    ['I' - FONT_FIRST_CHAR] = {0x00,0x41,0x7F,0x41,0x00},
    ['J' - FONT_FIRST_CHAR] = {0x20,0x40,0x41,0x3F,0x01},
    ['K' - FONT_FIRST_CHAR] = {0x7F,0x08,0x14,0x22,0x41},
    ['L' - FONT_FIRST_CHAR] = {0x7F,0x40,0x40,0x40,0x40},
    ['M' - FONT_FIRST_CHAR] = {0x7F,0x02,0x0C,0x02,0x7F},
    ['N' - FONT_FIRST_CHAR] = {0x7F,0x04,0x08,0x10,0x7F},
    ['O' - FONT_FIRST_CHAR] = {0x3E,0x41,0x41,0x41,0x3E},
    ['P' - FONT_FIRST_CHAR] = {0x7F,0x09,0x09,0x09,0x06},
    ['Q' - FONT_FIRST_CHAR] = {0x3E,0x41,0x51,0x21,0x5E},
    ['R' - FONT_FIRST_CHAR] = {0x7F,0x09,0x19,0x29,0x46},
    ['S' - FONT_FIRST_CHAR] = {0x46,0x49,0x49,0x49,0x31},
    ['T' - FONT_FIRST_CHAR] = {0x01,0x01,0x7F,0x01,0x01},
    ['U' - FONT_FIRST_CHAR] = {0x3F,0x40,0x40,0x40,0x3F},
    ['V' - FONT_FIRST_CHAR] = {0x1F,0x20,0x40,0x20,0x1F},
    ['W' - FONT_FIRST_CHAR] = {0x3F,0x40,0x38,0x40,0x3F},
    ['X' - FONT_FIRST_CHAR] = {0x63,0x14,0x08,0x14,0x63},
    ['Y' - FONT_FIRST_CHAR] = {0x07,0x08,0x70,0x08,0x07},
    ['Z' - FONT_FIRST_CHAR] = {0x61,0x51,0x49,0x45,0x43},
};

static const uint8_t *get_char_glyph(char c) {
    c = (char)toupper((unsigned char)c);
    if (c < FONT_FIRST_CHAR || c > FONT_LAST_CHAR) {
        c = ' ';
    }
    return font5x7[c - FONT_FIRST_CHAR];
}

static void oled_draw_char(uint8_t page, uint8_t col, char c) {
    const uint8_t *glyph = get_char_glyph(c);
    uint8_t buf[6];
    memcpy(buf, glyph, 5);
    buf[5] = 0x00;
    oled_set_cursor(page, col);
    oled_data(buf, 6);
}

static void oled_draw_text(uint8_t page, uint8_t col, const char *text) {
    while (*text && col <= (OLED_WIDTH - 6)) {
        oled_draw_char(page, col, *text);
        col += 6;
        text++;
    }
}

static void oled_display_sleep_result(int bpm, int spo2, const char *grade, int score, const char *murmur) {
    char line2[22], line3[22], line4[22], line5[22];

    snprintf(line2, sizeof(line2), "BPM: %d", bpm);
    snprintf(line3, sizeof(line3), "SPO2: %d%%", spo2);
    snprintf(line4, sizeof(line4), "SCORE: %d (%s)", score, grade);

    if (strcmp(murmur, "OK") == 0) {
        snprintf(line5, sizeof(line5), "HEART: OK");
    } else if (strcmp(murmur, "CAO") == 0 || strcmp(murmur, "NHE") == 0) {
        snprintf(line5, sizeof(line5), "HEART: MURMUR");
    } else {
        snprintf(line5, sizeof(line5), "HEART: %s", murmur);
    }

    oled_clear();
    oled_draw_text(0, 0, "SLEEP REPORT");
    oled_draw_text(2, 0, line2);
    oled_draw_text(3, 0, line3);
    oled_draw_text(5, 0, line4);
    oled_draw_text(6, 0, line5);
}

static void parse_and_display_sleep_result(const char *line) {
    int bpm = 0, spo2 = 0, score = 0;
    char grade[8] = "?";
    char murmur[8] = "?";
    const char *p;

    if ((p = strstr(line, "BPM:"))) bpm = atoi(p + 4);
    if ((p = strstr(line, "SPO2:"))) spo2 = atoi(p + 5);
    if ((p = strstr(line, "GRADE:"))) sscanf(p + 6, "%7[^;]", grade);
    if ((p = strstr(line, "SCORE:"))) score = atoi(p + 6);
    if ((p = strstr(line, "MURMUR:"))) sscanf(p + 7, "%7s", murmur);

    ESP_LOGI(TAG, "OLED Updated: BPM=%d SPO2=%d GRADE=%s SCORE=%d MURMUR=%s", bpm, spo2, grade, score, murmur);
    oled_display_sleep_result(bpm, spo2, grade, score, murmur);
}

/* ================================================================
 *  GPTIMER
 * ================================================================ */
static bool IRAM_ATTR on_gptimer_alarm(gptimer_handle_t timer,
                                       const gptimer_alarm_event_data_t *edata,
                                       void *user_ctx) {
    sample_ready = true;
    return false;
}

static void gptimer_setup(void) {
    gptimer_config_t timer_config = {
        .clk_src = GPTIMER_CLK_SRC_DEFAULT,
        .direction = GPTIMER_COUNT_UP,
        .resolution_hz = 1000000,
    };
    ESP_ERROR_CHECK(gptimer_new_timer(&timer_config, &gptimer));

    gptimer_event_callbacks_t cbs = {
        .on_alarm = on_gptimer_alarm,
    };
    ESP_ERROR_CHECK(gptimer_register_event_callbacks(gptimer, &cbs, NULL));
    ESP_ERROR_CHECK(gptimer_enable(gptimer));

    gptimer_alarm_config_t alarm_config = {
        .alarm_count = 10000,   // 10ms -> 100Hz
        .reload_count = 0,
        .flags.auto_reload_on_alarm = true,
    };
    ESP_ERROR_CHECK(gptimer_set_alarm_action(gptimer, &alarm_config));
    ESP_ERROR_CHECK(gptimer_start(gptimer));
    ESP_LOGI(TAG, "GPTIMER da chay (ngat moi 10ms)");
}

static void do_one_max_sample(void) {
    if (max30102_fifo_available() == 0) return;

    uint32_t red = 0, ir = 0;
    if (max30102_read_fifo(&red, &ir) != ESP_OK) return;

    int valid = (ir > 50000) ? 1 : 0;

    if (valid) {
        process_sample(ir, red);
    } else {
        current_bpm = 0;
        current_spo2 = 0;
        last_beat_us = 0;
        bpm_count = 0;
        dc_initialized = false;
    }

    int64_t now_us = esp_timer_get_time();
    unsigned long t_ms = (unsigned long)((now_us - start_time_us) / 1000);
    sample_count++;

    if (csv_file) {
        fprintf(csv_file, "%lu,%u,%u,%d,%d,%d\n",
                t_ms, (unsigned)ir, (unsigned)red, valid, current_bpm, current_spo2);
        if (sample_count % 10 == 0) fflush(csv_file);
    }

    if (sample_count % 10 == 0) {
        ESP_LOGI(TAG, "Sample %d | IR=%u | BPM=%d | SpO2=%d%% | Valid=%d",
                 sample_count, (unsigned)ir, current_bpm, current_spo2, valid);
    }

    if (sample_count >= 500) {
        ESP_LOGI(TAG, "=== Do xong! Tong %d mau. Da luu %s/max30102_test_1.csv ===",
                 sample_count, MOUNT_POINT);
        if (csv_file) {
            fclose(csv_file);
            csv_file = NULL;
        }
        measurement_done = true;
    }
}

static void start_new_max_measurement(void) {
    csv_file = fopen(MOUNT_POINT "/max30102_test_1.csv", "w");
    if (!csv_file) {
        ESP_LOGE(TAG, "Khong tao duoc file CSV");
        return;
    }
    fprintf(csv_file, "Time_ms,IR_Value,Red_Value,Valid,HR_bpm,SpO2_percent\n");
    fflush(csv_file);

    ESP_LOGI(TAG, ">>> CHE DO DO MOI: dat ngon tay len cam bien! (bat dau sau 2s) <<<");
    vTaskDelay(2000 / portTICK_PERIOD_MS);

    sample_count = 0;
    measurement_done = false;
    reset_max_algorithm_state();

    start_time_us = esp_timer_get_time();
    gptimer_setup();
    sys_state = SYS_MAX_MEASURING;
}

static void max30102_task(void *arg) {
    while (true) {
        if (sys_state == SYS_MAX_MEASURING) {
            if (measurement_done) {
                gptimer_stop(gptimer);
                gptimer_disable(gptimer);
                gptimer_del_timer(gptimer);
                gptimer = NULL;

                ESP_LOGI(TAG, "Dang gui file CSV ra Serial...");
                vTaskDelay(500 / portTICK_PERIOD_MS);
                dump_csv_to_serial();

                measurement_done = false;
                sys_state = SYS_IDLE_MAX;
                ESP_LOGI(TAG, ">>> San sang lenh moi (d/m/r) <<<");
            } else if (sample_ready) {
                sample_ready = false;
                do_one_max_sample();
            }
        }
        vTaskDelay(1);
    }
}

/* ================================================================
 *  INMP441 - I2S
 * ================================================================ */
static bool i2s_init(void)
{
    i2s_config_t cfg = {
        .mode                 = I2S_MODE_MASTER | I2S_MODE_RX,
        .sample_rate          = AUDIO_SAMPLE_RATE,
        .bits_per_sample      = I2S_BITS_PER_SAMPLE_32BIT,
        .channel_format       = I2S_CHANNEL_FMT_ONLY_LEFT,
        .communication_format = I2S_COMM_FORMAT_STAND_I2S,
        .intr_alloc_flags     = 0,
        .dma_buf_count        = DMA_BUF_COUNT,
        .dma_buf_len          = DMA_BUF_LEN,
        .use_apll             = true,
        .tx_desc_auto_clear   = false,
        .fixed_mclk           = 0
    };

    esp_err_t err = i2s_driver_install(I2S_PORT, &cfg, 0, NULL);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "[I2S] Driver install loi: %d", err);
        return false;
    }

    i2s_pin_config_t pins = {
        .bck_io_num   = I2S_SCK,
        .ws_io_num    = I2S_WS,
        .data_out_num = I2S_PIN_NO_CHANGE,
        .data_in_num  = I2S_SD
    };

    err = i2s_set_pin(I2S_PORT, &pins);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "[I2S] Set pin loi: %d", err);
        return false;
    }

    i2s_zero_dma_buffer(I2S_PORT);
    return true;
}

static void audio_task(void *arg)
{
    while (true)
    {
        if (sys_state != SYS_AUDIO_RECORDING) {
            vTaskDelay(pdMS_TO_TICKS(10));
            continue;
        }

        size_t bytes_read = 0;
        esp_err_t err = i2s_read(I2S_PORT, audio_dma_buf, sizeof(audio_dma_buf),
                                  &bytes_read, pdMS_TO_TICKS(100));

        if (err != ESP_OK || bytes_read == 0) continue;

        size_t samples_got = bytes_read / sizeof(int32_t);

        for (size_t i = 0; i < samples_got && audio_sample_count < AUDIO_N_SAMPLES; i++)
        {
            int32_t clean_signal = ((int32_t)audio_dma_buf[i]) >> 8;
            uint32_t t_ms = (uint32_t)(((uint64_t)audio_sample_count * 1000) / AUDIO_SAMPLE_RATE);

            printf("%u,%ld,%u\n",
                   (unsigned)audio_sample_count,
                   (long)clean_signal,
                   (unsigned)t_ms);

            audio_sample_count++;
        }

        if (audio_sample_count >= AUDIO_N_SAMPLES) {
            printf("##END##\n");
            fflush(stdout);

            max30102_wakeup();
            sys_state = SYS_IDLE_MAX;
            ESP_LOGI(TAG, "Thu am xong. Da bat lai MAX30102. San sang lenh moi (d/m/r).");
        }
    }
}

/* ================================================================
 *  LANG NGHE PHIM DIEU KHIEN QUA UART
 * ================================================================ */
static void key_listener_task(void *arg)
{
    uint8_t ch;
    while (true)
    {
        int len = uart_read_bytes(UART_PORT, &ch, 1, pdMS_TO_TICKS(50));
        if (len <= 0) continue;

        switch (ch) {
            case 'd':
            case 'D':
                if (sys_state == SYS_IDLE_MAX) {
                    ESP_LOGI(TAG, ">>> Doc lai file cu tren SD <<<");
                    dump_csv_to_serial();
                } else {
                    ESP_LOGW(TAG, "Bo qua 'd': he thong dang ban.");
                }
                break;

            case 'm':
            case 'M':
                if (sys_state == SYS_IDLE_MAX) {
                    start_new_max_measurement();
                } else {
                    ESP_LOGW(TAG, "Bo qua 'm': he thong dang ban.");
                }
                break;

            case 'r':
            case 'R':
                if (sys_state == SYS_IDLE_MAX) {
                    ESP_LOGI(TAG, ">>> Tat MAX30102, chuyen sang che do thu am <<<");
                    max30102_shutdown();

                    audio_sample_count = 0;
                    i2s_zero_dma_buffer(I2S_PORT);
                    sys_state = SYS_AUDIO_RECORDING;

                    printf("##START##\n");
                    fflush(stdout);
                } else {
                    ESP_LOGW(TAG, "Bo qua 'r': he thong dang ban (dang do MAX hoac dang thu am).");
                }
                break;

            case 's': {
                char line_buf[128];
                int idx = 0;
                line_buf[idx++] = 's';

                while (idx < (int)sizeof(line_buf) - 1) {
                    uint8_t c2;
                    int l2 = uart_read_bytes(UART_PORT, &c2, 1, pdMS_TO_TICKS(2000));
                    if (l2 <= 0) break;
                    if (c2 == '\n' || c2 == '\r') break;
                    line_buf[idx++] = (char)c2;
                }
                line_buf[idx] = '\0';

                parse_and_display_sleep_result(line_buf);
                break;
            }

            default:
                break;
        }
    }
}

/* ================================================================
 *  HAM CHINH
 * ================================================================ */
void app_main(void) {
    uart_init();
    vTaskDelay(pdMS_TO_TICKS(500));

    ESP_LOGI(TAG, "========================================");
    ESP_LOGI(TAG, "  ESP32 - MAX30102 + INMP441 (gop chung)");
    ESP_LOGI(TAG, "========================================");

    if (i2c_init() != ESP_OK) {
        ESP_LOGE(TAG, "Loi I2C, dung lai.");
        return;
    }
    ESP_LOGI(TAG, "[1/4] I2C OK");

    if (max30102_init() != ESP_OK) {
        ESP_LOGE(TAG, "Loi MAX30102, dung lai.");
        return;
    }
    ESP_LOGI(TAG, "[2/4] MAX30102 OK");

    if (sd_init() != ESP_OK) {
        ESP_LOGE(TAG, "Loi SD card, dung lai.");
        return;
    }
    ESP_LOGI(TAG, "[3/4] SD card OK");

    if (!i2s_init()) {
        ESP_LOGE(TAG, "Loi I2S (INMP441), dung lai.");
        return;
    }
    ESP_LOGI(TAG, "[4/5] I2S (INMP441) OK");

    if (oled_init()) {
        oled_clear();
        oled_draw_text(0, 0, "SLEEP QUALITY");
        oled_draw_text(3, 0, "CHO DU LIEU...");
        ESP_LOGI(TAG, "[5/5] OLED OK");
    } else {
        ESP_LOGW(TAG, "[5/5] OLED khong ket noi duoc");
    }

    ESP_LOGI(TAG, "========================================");
    ESP_LOGI(TAG, "  San sang. Go phim (qua Serial):");
    ESP_LOGI(TAG, "    d = Doc lai file CSV cu tren SD");
    ESP_LOGI(TAG, "    m = Do MOI MAX30102 (500 mau)");
    ESP_LOGI(TAG, "    r = Tat MAX30102, thu am INMP441");
    ESP_LOGI(TAG, "    s = (tu may tinh) nhan ket qua giac ngu, hien OLED");
    ESP_LOGI(TAG, "========================================");

    printf("##READY##\n");
    fflush(stdout);

    xTaskCreatePinnedToCore(key_listener_task, "keys",  3072, NULL, 6, NULL, 0);
    xTaskCreatePinnedToCore(max30102_task,      "max",  4096, NULL, 5, NULL, 0);
    xTaskCreatePinnedToCore(audio_task,         "audio",4096, NULL, 5, NULL, 1);
}