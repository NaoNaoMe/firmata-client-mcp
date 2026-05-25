# firmata-client-mcp

An [MCP (Model Context Protocol)](https://modelcontextprotocol.io) server that lets an LLM
control an Arduino's GPIO over the [Firmata](https://github.com/firmata/protocol) protocol.

Built on [FastMCP](https://github.com/modelcontextprotocol/python-sdk) and the
[`firmata-client`](https://pypi.org/project/firmata-client/) library. It exposes digital/analog
I/O, PWM, and basic servo control as MCP tools so an agent can read sensors and drive actuators
on a connected board.

> ?? **This server lets an AI agent drive physical hardware.** Please read the
> [Safety](#safety) section before connecting a board.

---

## Features

- Discover serial ports and connect to a board running **StandardFirmata**
- Query board capabilities (firmware, pin modes, analog mapping)
- Digital read / write
- Analog (ADC) read with sample averaging
- PWM (analog) write
- Servo control with a settable "origin" reference point
- Structured JSON responses and actionable error messages

## Requirements

- Python **3.13+** (required by the `firmata-client` dependency)
- An Arduino (or compatible board) flashed with **StandardFirmata**
  (`File ? Examples ? Firmata ? StandardFirmata` in the Arduino IDE)
- Python dependencies:
  - `mcp[cli]`
  - `firmata-client`
  - `pyserial` (installed with `firmata-client`)
  - `pydantic` (installed with `mcp`)


## Installation

Using `uv` (recommended):

```bash
uv pip install firmata-client-mcp
```

Or via `pip`:

```bash
pip install firmata-client-mcp
```

Or from source:

```bash
git clone https://github.com/NaoNaoMe/firmata-client-mcp.git
cd firmata-client-mcp
uv sync
```


## Configuration

### Claude Desktop Config

Add the server to your `claude_desktop_config.json`:


```json
{
  "mcpServers": {
    "firmata-client-mcp": {
      "command": "uvx",
      "args": [
        "firmata-client-mcp"
      ]
    }
  }
}
```

A typical first interaction:

1. `arduino_list_ports` – find your board's serial port
2. `arduino_connect` – connect using that port
3. `arduino_get_board_info` – inspect supported modes per pin
4. `arduino_set_pin_mode` – configure a pin (e.g. pin 13 ? `OUTPUT`)
5. `arduino_digital_write` – turn it on

## Tools

| Tool | Description | Read-only |
|------|-------------|-----------|
| `arduino_list_ports` | List available serial ports | ? |
| `arduino_connect` | Connect to a board on a given port | |
| `arduino_disconnect` | Disconnect from the current board | |
| `arduino_get_board_info` | Firmware info and per-pin capabilities | ? |
| `arduino_set_pin_mode` | Set a pin to INPUT/OUTPUT/ANALOG/PWM/SERVO | |
| `arduino_digital_write` | Set a digital pin HIGH/LOW | |
| `arduino_digital_read` | Read a digital pin (majority of 5 samples) | ? |
| `arduino_analog_write` | Write a PWM value (0–255) | |
| `arduino_analog_read` | Read an analog channel (averaged) | ? |
| `arduino_set_servo_origin` | Register the current angle as a reference | |
| `arduino_servo_move` | Move a servo to an angle offset from origin | |

### A note on pin vs. analog channel

`arduino_analog_read` takes an **analog channel** number (`0` = A0, `1` = A1, …),
but `arduino_set_pin_mode` expects the **digital pin** number. Use the
`analog_mapping` returned by `arduino_get_board_info` to translate between them.

### A note on servo movement

`arduino_servo_move` moves the servo to `origin + value` degrees (clamped to 0–180),
where `origin` is whatever you registered with `arduino_set_servo_origin`
(default `0`). The move is **relative to the origin, not cumulative** — calling it
twice with the same `value` results in the same physical position.

## Safety

This server gives an autonomous agent direct control of electrical outputs and
moving parts. Before use:

- **Supervise it.** Do not run it unattended while a board with actuators is powered.
- **Mind your wiring.** An agent can set any configured pin HIGH or drive PWM/servos;
  verify current limits, and mechanical end-stops yourself.
- **Setting SERVO mode does not define a position.** The physical angle right after a
  mode change is undefined and hardware-dependent.
- The author(s) accept no liability for damage to hardware or surroundings.
  Use at your own risk.

## Limitations

- Single board / single client at a time (module-level connection state, no locking —
  intended for local stdio use).
- Pin numbers are accepted in the range 0–69 (covers boards up to Arduino Mega);
  the actual valid range depends on your board.
- Reads block briefly (~100 ms) while sampling.

## License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.