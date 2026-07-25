#include <Arduino.h>
#include <homekit/homekit.h>
#include <homekit/characteristics.h>

void my_accessory_identify(homekit_value_t _value) {
    printf("accessory identify\n");
}

// ===== 三個獨立按鈕開關(觸發後自動彈回 Off)=====
homekit_characteristic_t cha_open_on  = HOMEKIT_CHARACTERISTIC_(ON, false);
homekit_characteristic_t cha_close_on = HOMEKIT_CHARACTERISTIC_(ON, false);
homekit_characteristic_t cha_pause_on = HOMEKIT_CHARACTERISTIC_(ON, false);

// ===== 窗簾配件(車庫門位置):由 VL53L1X 回報 0~100% =====
// CURRENT_POSITION：目前開啟百分比(0=全關,100=全開)
// TARGET_POSITION ：使用者拖動滑桿設定的目標百分比
// POSITION_STATE  ：0=關閉中, 1=開啟中, 2=停止
homekit_characteristic_t cha_cover_current = HOMEKIT_CHARACTERISTIC_(CURRENT_POSITION, 0);
homekit_characteristic_t cha_cover_target  = HOMEKIT_CHARACTERISTIC_(TARGET_POSITION, 0);
homekit_characteristic_t cha_cover_state   = HOMEKIT_CHARACTERISTIC_(POSITION_STATE, 2);

homekit_accessory_t *accessories[] = {
    HOMEKIT_ACCESSORY(.id = 1, .category = homekit_accessory_category_switch, .services = (homekit_service_t*[]) {
        HOMEKIT_SERVICE(ACCESSORY_INFORMATION, .characteristics = (homekit_characteristic_t*[]) {
            HOMEKIT_CHARACTERISTIC(NAME, "開門"),
            HOMEKIT_CHARACTERISTIC(MANUFACTURER, "DIY"),
            HOMEKIT_CHARACTERISTIC(SERIAL_NUMBER, "GD-0001"),
            HOMEKIT_CHARACTERISTIC(MODEL, "OpenSwitch"),
            HOMEKIT_CHARACTERISTIC(FIRMWARE_REVISION, "1.0"),
            HOMEKIT_CHARACTERISTIC(IDENTIFY, my_accessory_identify),
            NULL
        }),
        HOMEKIT_SERVICE(SWITCH, .primary = true, .characteristics = (homekit_characteristic_t*[]) {
            HOMEKIT_CHARACTERISTIC(NAME, "開門"),
            &cha_open_on,
            NULL
        }),
        NULL
    }),
    HOMEKIT_ACCESSORY(.id = 2, .category = homekit_accessory_category_switch, .services = (homekit_service_t*[]) {
        HOMEKIT_SERVICE(ACCESSORY_INFORMATION, .characteristics = (homekit_characteristic_t*[]) {
            HOMEKIT_CHARACTERISTIC(NAME, "關門"),
            HOMEKIT_CHARACTERISTIC(MANUFACTURER, "DIY"),
            HOMEKIT_CHARACTERISTIC(SERIAL_NUMBER, "GD-0002"),
            HOMEKIT_CHARACTERISTIC(MODEL, "CloseSwitch"),
            HOMEKIT_CHARACTERISTIC(FIRMWARE_REVISION, "1.0"),
            HOMEKIT_CHARACTERISTIC(IDENTIFY, my_accessory_identify),
            NULL
        }),
        HOMEKIT_SERVICE(SWITCH, .primary = true, .characteristics = (homekit_characteristic_t*[]) {
            HOMEKIT_CHARACTERISTIC(NAME, "關門"),
            &cha_close_on,
            NULL
        }),
        NULL
    }),
    HOMEKIT_ACCESSORY(.id = 3, .category = homekit_accessory_category_switch, .services = (homekit_service_t*[]) {
        HOMEKIT_SERVICE(ACCESSORY_INFORMATION, .characteristics = (homekit_characteristic_t*[]) {
            HOMEKIT_CHARACTERISTIC(NAME, "車庫門暫停"),
            HOMEKIT_CHARACTERISTIC(MANUFACTURER, "DIY"),
            HOMEKIT_CHARACTERISTIC(SERIAL_NUMBER, "GD-0003"),
            HOMEKIT_CHARACTERISTIC(MODEL, "PauseSwitch"),
            HOMEKIT_CHARACTERISTIC(FIRMWARE_REVISION, "1.0"),
            HOMEKIT_CHARACTERISTIC(IDENTIFY, my_accessory_identify),
            NULL
        }),
        HOMEKIT_SERVICE(SWITCH, .primary = true, .characteristics = (homekit_characteristic_t*[]) {
            HOMEKIT_CHARACTERISTIC(NAME, "暫停"),
            &cha_pause_on,
            NULL
        }),
        NULL
    }),
    HOMEKIT_ACCESSORY(.id = 4, .category = homekit_accessory_category_window_covering, .services = (homekit_service_t*[]) {
        HOMEKIT_SERVICE(ACCESSORY_INFORMATION, .characteristics = (homekit_characteristic_t*[]) {
            HOMEKIT_CHARACTERISTIC(NAME, "車庫門"),
            HOMEKIT_CHARACTERISTIC(MANUFACTURER, "DIY"),
            HOMEKIT_CHARACTERISTIC(SERIAL_NUMBER, "GD-0004"),
            HOMEKIT_CHARACTERISTIC(MODEL, "DoorPosition"),
            HOMEKIT_CHARACTERISTIC(FIRMWARE_REVISION, "1.0"),
            HOMEKIT_CHARACTERISTIC(IDENTIFY, my_accessory_identify),
            NULL
        }),
        HOMEKIT_SERVICE(WINDOW_COVERING, .primary = true, .characteristics = (homekit_characteristic_t*[]) {
            HOMEKIT_CHARACTERISTIC(NAME, "車庫門"),
            &cha_cover_current,
            &cha_cover_target,
            &cha_cover_state,
            NULL
        }),
        NULL
    }),
    NULL
};

homekit_server_config_t config = {
    .accessories = accessories,
    .password = "111-11-111"
};
