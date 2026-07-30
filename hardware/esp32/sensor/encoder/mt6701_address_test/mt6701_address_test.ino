#include <Arduino.h>
#include <Wire.h>

// ESP32 I2C 핀
constexpr int I2C_SDA_PIN = 21;
constexpr int I2C_SCL_PIN = 22;

// ==========================================================
// [수동 변경 부분]
// 센서별 I2C 주소가 다르다면 여기서 수정합니다.
// ==========================================================
constexpr uint8_t LEFT_ADDRESS  = 0x06;
constexpr uint8_t RIGHT_ADDRESS = 0x46;

bool checkAddress(uint8_t address)
{
    Wire.beginTransmission(address);
    uint8_t error = Wire.endTransmission();

    return error == 0;
}

void printAddressResult(
    const char* sensorName,
    uint8_t address,
    bool detected
)
{
    Serial.print(sensorName);
    Serial.print(" [0x");

    if (address < 0x10)
    {
        Serial.print("0");
    }

    Serial.print(address, HEX);
    Serial.print("]: ");

    if (detected)
    {
        Serial.println("발견됨");
    }
    else
    {
        Serial.println("찾지 못함");
    }
}

void scanAllAddresses()
{
    int foundCount = 0;

    Serial.println();
    Serial.println("전체 I2C 주소 검색:");

    for (uint8_t address = 1; address < 127; address++)
    {
        Wire.beginTransmission(address);
        uint8_t error = Wire.endTransmission();

        if (error == 0)
        {
            Serial.print("  발견된 주소: 0x");

            if (address < 0x10)
            {
                Serial.print("0");
            }

            Serial.println(address, HEX);
            foundCount++;
        }
    }

    if (foundCount == 0)
    {
        Serial.println("  발견된 I2C 장치 없음");
    }
}

void setup()
{
    Serial.begin(115200);
    delay(1000);

    Wire.begin(I2C_SDA_PIN, I2C_SCL_PIN);
    Wire.setClock(100000);
    Wire.setTimeOut(100);

    delay(100);

    Serial.println();
    Serial.println("================================");
    Serial.println("MT6701 두 개 주소 동시 테스트");
    Serial.println("================================");
}

void loop()
{
    bool leftDetected = checkAddress(LEFT_ADDRESS);
    bool rightDetected = checkAddress(RIGHT_ADDRESS);

    Serial.println();

    Serial.print("SDA 유휴 상태: ");
    Serial.println(digitalRead(I2C_SDA_PIN));

    Serial.print("SCL 유휴 상태: ");
    Serial.println(digitalRead(I2C_SCL_PIN));

    printAddressResult(
        "왼쪽 센서",
        LEFT_ADDRESS,
        leftDetected
    );

    printAddressResult(
        "오른쪽 센서",
        RIGHT_ADDRESS,
        rightDetected
    );

    if (leftDetected && rightDetected)
    {
        Serial.println("결과: 두 센서 모두 정상적으로 발견됨");
    }
    else if (leftDetected && !rightDetected)
    {
        Serial.println(
            "결과: 왼쪽만 발견됨. "
            "오른쪽 주소가 0x46인지 확인하세요."
        );
    }
    else if (!leftDetected && rightDetected)
    {
        Serial.println(
            "결과: 오른쪽만 발견됨. "
            "왼쪽 배선과 주소를 확인하세요."
        );
    }
    else
    {
        Serial.println(
            "결과: 두 센서 모두 발견되지 않음. "
            "전원, GND, SDA, SCL을 확인하세요."
        );
    }

    scanAllAddresses();

    Serial.println("--------------------------------");
    delay(3000);
}