#include <Arduino.h>
#include <homekit/homekit.h>
#include <homekit/characteristics.h>

void my_accessory_identify(homekit_value_t _value) {
    printf("accessory identify\n");
}

// ================= 車庫門:用 Window Covering 服務(可顯示百分比) =================
// CurrentPosition / TargetPosition: 0=全關, 100=全開
// PositionState: 0=關閉中(遞減), 1=開啟中(遞增), 2=靜止
homekit_characteristic_t cha_current_position =
    HOMEKIT_CHARACTERISTIC_(CURRENT_POSITION, 0);
homekit_characteristic_t cha_target_position =
    HOMEKIT_CHARACTERISTIC_(TARGET_POSITION, 0);
homekit_characteristic_t cha_position_state =
    HOMEKIT_CHARACTERISTIC_(POSITION_STATE, 2);
homekit_characteristic_t cha_obstruction =
    HOMEKIT_CHARACTERISTIC_(OBSTRUCTION_DETECTED, false);

// ================= 暫停:獨立 Switch =================
homekit_characteristic_t cha_pause_on =
    HOMEKIT_CHARACTERISTIC_(ON, false);

homekit_accessory_t *accessories[] = {
    HOMEKIT_ACCESSORY(.id = 1, .category = homekit_accessory_category_window_covering, .services = (homekit_service_t*[]) {
        HOMEKIT_SERVICE(ACCESSORY_INFORMATION, .characteristics = (homekit_characteristic_t*[]) {
            HOMEKIT_CHARACTERISTIC(NAME, "車庫門"),
            HOMEKIT_CHARACTERISTIC(MANUFACTURER, "DIY"),
            HOMEKIT_CHARACTERISTIC(SERIAL_NUMBER, "GD-0001"),
            HOMEKIT_CHARACTERISTIC(MODEL, "GarageDoor"),
            HOMEKIT_CHARACTERISTIC(FIRMWARE_REVISION, "1.0"),
            HOMEKIT_CHARACTERISTIC(IDENTIFY, my_accessory_identify),
            NULL
        }),
        HOMEKIT_SERVICE(WINDOW_COVERING, .primary = true, .characteristics = (homekit_characteristic_t*[]) {
            HOMEKIT_CHARACTERISTIC(NAME, "車庫門"),
            &cha_current_position,
            &cha_target_position,
            &cha_position_state,
            &cha_obstruction,
            NULL
        }),
        NULL
    }),
    HOMEKIT_ACCESSORY(.id = 2, .category = homekit_accessory_category_switch, .services = (homekit_service_t*[]) {
        HOMEKIT_SERVICE(ACCESSORY_INFORMATION, .characteristics = (homekit_characteristic_t*[]) {
            HOMEKIT_CHARACTERISTIC(NAME, "車庫門暫停"),
            HOMEKIT_CHARACTERISTIC(MANUFACTURER, "DIY"),
            HOMEKIT_CHARACTERISTIC(SERIAL_NUMBER, "GD-0002"),
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
    NULL
};

homekit_server_config_t config = {
    .accessories = accessories,
    .password = "111-11-111"
};
