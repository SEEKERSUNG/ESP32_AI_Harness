# 生成代码的硬件访问

生成代码直接运行在 MicroPython 上，可以 `import machine`、`neopixel`、`framebuf`、`esp`、`esp32` 等原生模块。**没有全局 `api` 对象，没有外设 allowlist，也没有 `/peripherals` 前置配置。** 代码必须使用用户提供的真实接线和引脚；引脚号写错、电压不匹配或驱动方式错误都可能损坏外设或开发板。

## 常用模块

| 模块 | 用途 |
|---|---|
| `machine.Pin` | GPIO 输入/输出、上拉/下拉 |
| `machine.SPI` / `SoftSPI` | 硬件/软件 SPI |
| `machine.I2C` / `SoftI2C` | 硬件/软件 I2C |
| `machine.UART` | 串口 |
| `machine.PWM` | PWM 输出 |
| `machine.ADC` | ADC 采样 |
| `machine.TouchPad` | 电容触摸 |
| `machine.RTC` | RTC 读取/写入 |
| `machine.time_pulse_us` | 脉冲宽度测量 |
| `neopixel` | WS2812/NeoPixel 灯带 |
| `framebuf` | 单色/彩色帧缓冲 |
| `esp` / `esp32` | Flash 大小、内部温度、堆信息等 |

`device.info()` 仍然作为只读全局对象注入，用于获取平台、堆、文件系统、CPU 频率、唯一 ID 和复位原因；也可以直接使用 `machine`、`esp`、`esp32`。`import device` 兼容旧代码。

## GPIO

```python
from machine import Pin

led = Pin(4, Pin.OUT)
led.value(1)
led.value(0)

button = Pin(5, Pin.IN, Pin.PULL_UP)
print(button.value())
```

## SPI 与 ST7735

```python
import time
from machine import Pin, SPI

BUS = 1
CS, DC, RST = 4, 5, 6
spi = SPI(
    BUS,
    baudrate=20_000_000,
    polarity=0,
    phase=0,
    sck=Pin(12),
    mosi=Pin(11),
    # 写入型设备省略 miso；带 MISO 的设备再加 miso=Pin(13)
)
cs = Pin(CS, Pin.OUT)
dc = Pin(DC, Pin.OUT)
rst = Pin(RST, Pin.OUT)
cs.value(1)
rst.value(1)
time.sleep_ms(20)
rst.value(0)
time.sleep_ms(20)
rst.value(1)
time.sleep_ms(120)


def send(payload, chunk=128):
    for index in range(0, len(payload), chunk):
        spi.write(payload[index:index + chunk])


def command(value):
    dc.value(0)
    cs.value(0)
    send(bytes([value]))
    cs.value(1)


def data(payload):
    dc.value(1)
    cs.value(0)
    send(payload)
    cs.value(1)


# 不同 ST7735 面板的偏移、颜色顺序和初始化序列必须按模块资料调整。
# command(0x01); time.sleep_ms(150)
# command(0x11); time.sleep_ms(120)
```

写入型设备可以省略 `miso` 参数（不要传 `None`，不同固件的可选参数处理可能不同）。`bus` 必须是 1 或 2（硬件 SPI）；需要任意引脚时改用 `SoftSPI`。20 MHz 下 128×160×2 字节约 16 ms 的线上时间，实际耗时还取决于分块和 Python 调用开销。

## I2C

```python
from machine import I2C, Pin

i2c = I2C(0, scl=Pin(9), sda=Pin(8), freq=400_000)
print(i2c.scan())          # 例如 [60] = 0x3C
i2c.writeto(0x3C, b"\x00\xAF")
print(i2c.readfrom(0x3C, 1))
```

## UART

```python
from machine import UART, Pin

uart = UART(1, baudrate=115200, tx=Pin(17), rx=Pin(18))
uart.write(b"hello\n")
print(uart.any())
print(uart.read())
```

UART0 用于控制台，生成代码应使用 UART1/UART2，避免抢占串口终端。

## NeoPixel

```python
from machine import Pin
from neopixel import NeoPixel

np = NeoPixel(Pin(48), 1)   # 板载 RGB LED：GPIO48，1 颗
np[0] = (0, 32, 0)
np.write()
```

外接灯带注意 5 V 供电、电平和电流，不要由 GPIO 直接供电。

## ADC、PWM、触摸、RTC、脉冲

```python
from machine import ADC, PWM, Pin, RTC, TouchPad, time_pulse_us

adc = ADC(Pin(1))
adc.atten(ADC.ATTN_11DB)
print(adc.read_u16())

pwm = PWM(Pin(13))
pwm.freq(1000)
pwm.duty_u16(32768)

touch = TouchPad(Pin(4))
print(touch.read())        # 阈值按实测校准；原始值越小通常表示越接近

rtc = RTC()
print(rtc.datetime())
# rtc.datetime((2026, 11, 21, 5, 12, 0, 0, 0))

echo = Pin(5, Pin.IN)
pulse_us = time_pulse_us(echo, 1, 30000)
print(pulse_us)            # -2 表示超时
```

ADC 输入电压必须在 0–3.3 V 范围内；不要接入负电压或超过 3.3 V 的信号。触摸只使用 ESP32-S3 的 GPIO1–GPIO14 通道。

## 板载信息

```python
import machine
import esp
import esp32

print(machine.freq())
print(machine.unique_id())
print(machine.reset_cause(), machine.wake_reason())
print(esp.flash_size())
print(esp32.mcu_temperature())
print(device.info())
```

## 引脚与电气注意事项

- **保留引脚**：GPIO26–37 用于 Flash/Octal PSRAM，GPIO43/44 用于控制台 UART0，GPIO19/20 为原生 USB，GPIO0/3/45/46 为启动 strapping。不要在这些引脚上接外设或输出信号，除非你明确知道后果。
- 接线前确认 3.3 V 电平、供电、上拉电阻和设备地址；错误接线或错误驱动可能损坏外设或开发板。
- 多个任务同时访问同一条 SPI/I2C 总线可能交错；不要同时驱动同一设备。
- 生成代码拥有完整 MicroPython 进程权限；`machine.reset()`、文件删除、网络请求等操作都可能中断当前任务。除非用户明确要求，不要主动复位设备或修改 Harness 配置。
- 执行超时和看门狗仍然有效：长循环应调用 `__harness_check()`，`range()` 和 `time.sleep()` 已自动插桩；阻塞在 C/SPI 调用中的代码可能只能等待看门狗复位。

`/peripherals` 命令及相关配置字段已移除。旧 `config.json` 中的 `peripherals_enabled`、`allowed_gpio_pins`、`i2c_buses`、`spi_buses`、`uart_buses`、`neopixel_strips`、`adc_pins`、`pwm_pins`、`touch_pins`、`rtc_write` 和 `peripheral_max_transfer` 会在加载时丢弃。
