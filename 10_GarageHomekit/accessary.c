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
    NULL
};

homekit_server_config_t config = {
    .accessories = accessories,
    .password = "111-11-111"
};
