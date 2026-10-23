# Verified pinouts and package facts

Verified against official datasheets during the esp_home_hum receiver-node
project (2026-10-23). Use these when generating symbols/footprints so pin
names and packages are correct the first time.

## ATtiny85 (SOIC-8, 8 MHz internal RC)

AVR MCU. The standard KiCad library symbol `MCU_Microchip_ATtiny:ATtiny85-20S`
has **wrong pin names for pins 5–8**; build a custom symbol instead.
Pinout (SOIC-8, pin 1 at top-left):

| SOIC pin | Port/Name |
|---|---|
| 1 | PB5 (RST) |
| 2 | PB3 |
| 3 | PB4 |
| 4 | GND |
| 5 | VCC |
| 6 | PB2 |
| 7 | PB1 |
| 8 | PB0 |

Firmware pin map used for the receiver (bit-bang SPI, no HW SPI on this part):
PB0 = MOSI, PB1 = MISO, PB2 = SCK, PB3 = CSN, PB4 = PUMP (load enable),
PB5 = RST. Deep-sleep wake via the 125 ms watchdog timer (nRF24 IRQ unused).

## IRLZ44N (TO-220, N-channel MOSFET)

Infineon datasheet p.8 pinout:

| TO-220 pin | Function |
|---|---|
| 1 | GATE |
| 2 | DRAIN |
| 3 | SOURCE |
| tab | DRAIN (same as pin 2) |

The KiCad library symbol `Transistor_FET:IRLZ44N` is correct. Used to switch
12 V loads (pump + solenoid, ~2.5 A) from a 5 V logic gate through a series
resistor and a pull-down gate resistor; 1N5819 reverse-protection diodes across
the load.

## nRF24L01+ (module, single-row 8×1.27 mm header)

Pin order on the module header, **GND end first** (left to right when the
module faces the board, antenna on the far side):

| Header pin | Signal |
|---|---|
| 1 | GND |
| 2 | VCC |
| 3 | CE |
| 4 | CSN |
| 5 | SCK |
| 6 | MOSI |
| 7 | MISO |
| 8 | IRQ |

The antenna region is the **far half** of the module — keep it free of copper on
both layers (no components, no traces, no zones).

## Level-shifting note (5 V MCU ↔ 3.3 V radio)

The receiver MCU runs at 5 V; the nRF24L01+ runs at 3.3 V.
- MCU→radio (CSN, SCK, MOSI): 2.2 k/2.2 k voltage dividers.
- Radio→MCU (MISO): direct (3.3 V output is a valid low/high for 5 V AVR inputs).
- CE: hardwired high at 3.3 V (1 kΩ pull-up to 3.3 V rail).
- IRQ: left unconnected (NC); the MCU polls the radio on the WDT wake timer
  instead of using the interrupt.
