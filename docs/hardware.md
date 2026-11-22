# 生成代码的硬件 API

外设默认关闭。只有通过 `/peripherals` 显式配置并启用后，生成代码、Workspace 脚本和后台任务才能通过受控的全局 `api` 对象访问硬件。

## 配置

```text
/peripherals enable
/peripherals gpio 7,8,9,10              # 允许普通 GPIO（例如 CS/DC/RST/BL）
/peripherals spi 1 12 11 - 20000000 hw  # id sck mosi miso| - [freq] [hw|sw]
/peripherals spi 1 12 11 13 20000000    # 带 MISO 的写法；默认 hw
/peripherals i2c 0 8 9 400000           # id sda scl [freq]
/peripherals uart 1 17 18 115200        # id tx rx [baud]
/peripherals neopixel 48 1
/peripherals adc 1
/peripherals pwm 13                     # 允许 GPIO13 输出 PWM
/peripherals touch 4                    # 允许 GPIO4 电容触摸
/peripherals rtc write off              # 是否允许写 RTC，默认 off
/peripherals transfer 4096              # 单次总线传输上限，1–4096 字节
/peripherals scan 0                     # 扫描已配置 I2C 总线
```

SPI 默认使用硬件 `machine.SPI`（`mode=hw`），适合 ST7735 等显示模块；只有确实需要任意引脚软件时序时才用 `sw`。写入型设备在 `hw` 模式下可以省略 MISO，用 `-` 表示；`sw` 模式通常需要完整的 sck/mosi/miso。硬件 SPI 的 `id` 必须是 1 或 2；`sw` 模式可使用 0–3。

PWM 和电容触摸必须分别加入 `pwm_pins`、`touch_pins`；触摸只接受 ESP32-S3 的 GPIO1–GPIO14 通道。RTC 读取始终可用，写入默认关闭，需要 `/peripherals rtc write on` 显式开启。`api.system` 是只读芯片信息，不占用引脚。

引脚号必须与真实接线一致；Flash/PSRAM、USB、启动 strapping 和控制台 UART 引脚会被拒绝。`/peripherals` 只负责配置校验，不是电气安全层。

## 生成代码中的 `api`

执行器把 `api` 作为全局对象注入，不需要导入：

```python
status = api.status()
if not status["enabled"]:
    print("peripherals disabled")
else:
    api.gpio.setup(4, "out")
    api.gpio.write(4, 1)
    print(api.gpio.read(5))
    api.spi.write(1, b"\x01\x02")       # 单次不超过 peripheral_max_transfer
    print(api.spi.read(1, 2))
    # 只配置了一条 SPI 总线时，可省略 bus 参数：
    api.spi.write(b"\x01\x02")
    print(api.spi.read(2))
```

板载资源示例：

```python
# PWM：GPIO13 输出 1 kHz、50% 占空比
api.pwm.setup(13, 1000, 32768)
api.pwm.write(13, 65535)       # 100%
print(api.pwm.duty(13))        # 返回最近一次写入值
api.pwm.freq(13, 2000)
api.pwm.deinit(13)

# 电容触摸：原始值越小通常表示越接近/按下，阈值按实测校准
print(api.touch.read(4))
api.touch.config(4, 500)

# 只读芯片信息
print(api.system.temperature())  # 内部温度，摄氏度
print(api.system.wake_reason(), api.system.reset_cause())
print(api.system.unique_id(), api.system.freq(), api.system.uptime_ms())

# RTC：读取始终可用；写入需要 /peripherals rtc write on
print(api.rtc.datetime())
# api.rtc.set_datetime((2026, 11, 21, 5, 12, 0, 0, 0))

# 脉冲测量（例如 HC-SR04 的 echo 引脚）
api.gpio.setup(5, "in")
pulse_us = api.pulse.measure(5, 1, 30000)
```

`api.pwm.duty()` 返回本 API 最近一次写入的占空比；该固件的硬件回读不可靠。`api.pulse.measure()` 会阻塞到收到脉冲或超时，超时上限 1 秒，返回 `-1` 表示超时前没有出现指定电平、`-2` 表示没有出现完整脉冲。

可用接口：

| 接口 | 方法 |
|---|---|
| `api.status()` / `api.available()` | 查看启用状态、允许 GPIO、已配置 I2C/SPI/UART/NeoPixel/ADC |
| `api.gpio` | `setup(pin, "in"|"out", pull=None)`, `write(pin, value)`, `read(pin)`, `deinit(pin)` |
| `api.spi` | `write(bus, data)`, `read(bus, size)`, `transfer(bus, data)`；只有一条总线时可省略 `bus` |
| `api.i2c` | `scan(bus)`, `write(bus, addr, data)`, `read(bus, addr, size)`, `write_mem`, `read_mem` |
| `api.uart` | `write(bus, data)`, `read(bus, size)`, `any(bus)`, `readline(bus)` |
| `api.neopixel` | `write(pin, pixels)`, `fill(pin, r, g, b)`, `clear(pin)`, `read(pin)` |
| `api.adc` | `read(pin)`，仅限 `adc_pins` |
| `api.pwm` | `setup(pin, freq, duty)`, `write(pin, duty)`, `duty(pin)`, `freq(pin, value=None)`, `deinit(pin)`；占空比 0–65535，仅限 `pwm_pins` |
| `api.touch` | `read(pin)`, `config(pin, threshold)`；仅限 `touch_pins` |
| `api.rtc` | `datetime()`，以及开启 `rtc_write` 后的 `set_datetime((year, month, day, weekday, hour, minute, second, subsecond))` |
| `api.system` | 只读：`temperature()`, `wake_reason()`, `reset_cause()`, `unique_id()`, `freq()`, `uptime_ms()` |
| `api.pulse` | `measure(pin, level=1, timeout_us=1000000)`，引脚需在 `allowed_gpio_pins` 且先设为输入 |

所有方法都会重新检查当前配置。撤销允许引脚、修改总线或禁用外设后，旧对象不会继续工作。SPI/I2C/UART 的 `data` 可为 `str`、`bytes`、`bytearray` 或 `memoryview`。单次总线传输受 `peripheral_max_transfer` 限制（默认 256 字节，可用 `/peripherals transfer 4096` 提高）；更大的缓冲区请分块写入。`import api` 为兼容旧代码返回同一对象，但推荐直接使用全局 `api`。

系统信息通过注入的全局 `device.info()` 获取（版本、平台、堆、文件系统、CPU 频率、唯一 ID、复位原因等），直接调用即可，不需要 `import device`。

`api.spi` 默认走硬件 SPI；`mode=sw` 时才使用 `SoftSPI`。硬件 SPI 明显更适合整屏刷新：在 20 MHz 下 128×160×2 字节约 16 ms 的线上时间，实际耗时取决于分块和 Python 调用开销。`api.status()` 会返回每条总线的 `mode`、频率和引脚。

生成代码不得 `import machine`、直接构造 `Pin`/`SPI`，或访问未在 `/peripherals` 中配置的资源。

## ST7735 示例

以下只是骨架，控制器型号、颜色顺序、偏移和初始化序列必须按实际模块调整。先用 `/peripherals` 配好 SPI 和 CS/DC/RST/BL，例如：

```text
/peripherals gpio 7,8,9,10
/peripherals spi 1 12 11 - 20000000 hw
/peripherals enable
```

代码：

```python
import time

BUS = 1
CS, DC, RST = 4, 5, 6
status = api.status()
print(status)

api.gpio.setup(CS, "out")
api.gpio.setup(DC, "out")
api.gpio.setup(RST, "out")
api.gpio.write(CS, 1)
api.gpio.write(RST, 1)
time.sleep_ms(20)
api.gpio.write(RST, 0)
time.sleep_ms(20)
api.gpio.write(RST, 1)
time.sleep_ms(120)

def send(data, limit=128):
    for index in range(0, len(data), limit):
        api.spi.write(BUS, data[index:index + limit])

def command(value):
    api.gpio.write(DC, 0)
    api.gpio.write(CS, 0)
    send(bytes([value]))
    api.gpio.write(CS, 1)

def data(payload):
    api.gpio.write(DC, 1)
    api.gpio.write(CS, 0)
    send(payload)
    api.gpio.write(CS, 1)

# 这里放入模块对应的 ST7735 初始化序列；不同面板的偏移/伽马可能不同。
# command(0x01); time.sleep_ms(150)
# command(0x11); time.sleep_ms(120)
# ...
```

初始化后可结合 `framebuf`（如固件包含）绘制文本，再按窗口分块写入。长动画或持续刷新适合用 `__workspace run` 的 `background=true` 或 `/workspace run bg <path>`，但短测试应优先前台执行。

如果 `api.status()` 显示外设未启用、没有对应总线，或控制引脚不在 `allowed_gpio_pins` 中，模型应先让用户执行相应的 `/peripherals` 命令并核对接线，不要猜测引脚。

## 限制

- 外设访问不是沙箱；错误接线、错误电压或错误驱动可能损坏设备。
- `api` 只是精简的同步访问层，不提供中断、DMA 或完整驱动生态。
- 后台任务不可硬杀；长时间占用 SPI 的任务可能只能等看门狗恢复。
- 多任务同时访问同一条总线可能交错；避免让多个任务同时驱动同一设备。
- `peripheral_max_transfer` 只限制单次调用的数据量，不限制任务总运行时间。

当前刻意不暴露：`machine.Timer` 回调（中断上下文和并发风险）、`machine.WDT`/`machine.reset`/`deepsleep`（会让 Harness 失去控制）、I2S/SDCard（配置复杂、内存和实时性风险高）、裸 `machine.mem*` 和 flash/NVS 写入。需要这些能力时应作为独立的、显式确认的驱动或维护命令实现，而不是直接放开 `machine`。
