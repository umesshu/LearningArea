#include <Arduino.h>
#include <homekit/homekit.h>
#include <homekit/characteristics.h>

void my_accessory_identify(homekit_value_t _value) {
    printf("accessory identify\n");
}

// ===== 車庫門:Garage Door Opener 服務(無感測,韌體記憶狀態)=====
// CurrentDoorState: 0=開 1=關 2=開啟中 3=關閉中 4=停止
// TargetDoorState : 0=開 1=關
homekit_characteristic_t cha_current_door_state =
    HOMEKIT_CHARACTERISTIC_(CURRENT_DOOR_STATE, 1);   // 預設關
homekit_characteristic_t cha_target_door_state =
    HOMEKIT_CHARACTERISTIC_(TARGET_DOOR_STATE, 1);
homekit_characteristic_t cha_obstruction =
    HOMEKIT_CHARACTERISTIC_(OBSTRUCTION_DETECTED, false);

// ===== 暫停:獨立 Switch =====
homekit_characteristic_t cha_pause_on =
    HOMEKIT_CHARACTERISTIC_(ON, false);

homekit_accessory_t *accessories[] = {
    HOMEKIT_ACCESSORY(.id = 1, .category = homekit_accessory_category_garage, .services = (homekit_service_t*[]) {
        HOMEKIT_SERVICE(ACCESSORY_INFORMATION, .characteristics = (homekit_characteristic_t*[]) {
            HOMEKIT_CHARACTERISTIC(NAME, "車庫門"),
            HOMEKIT_CHARACTERISTIC(MANUFACTURER, "DIY"),
            HOMEKIT_CHARACTERISTIC(SERIAL_NUMBER, "GD-0001"),
            HOMEKIT_CHARACTERISTIC(MODEL, "GarageDoor"),
            HOMEKIT_CHARACTERISTIC(FIRMWARE_REVISION, "1.0"),
            HOMEKIT_CHARACTERISTIC(IDENTIFY, my_accessory_identify),
            NULL
        }),
        HOMEKIT_SERVICE(GARAGE_DOOR_OPENER, .primary = true, .characteristics = (homekit_characteristic_t*[]) {
            HOMEKIT_CHARACTERISTIC(NAME, "車庫門"),
            &cha_current_door_state,
            &cha_target_door_state,
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
