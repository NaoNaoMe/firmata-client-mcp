"""
server.py
~~~~~~~~~
FastMCP server for Arduino GPIO via Firmata.
Dependency: mcp[cli], pyserial

Usage:
    uv run mcp dev server.py       # MCP Inspector for development
    uv run mcp run server.py       # stdio (Claude Desktop etc.)
"""

import json
import time
import logging
from functools import wraps
from typing import Optional, Callable, TypeVar

import serial.tools.list_ports
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field, ConfigDict

from firmata_client import FirmataClient, PinMode

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SAMPLES       = 5     # Number of samples for read stabilization
SAMPLE_DELAY  = 0.01  # Seconds between samples
REPORT_SETTLE = 0.05  # Seconds to wait after enabling reporting

log = logging.getLogger("arduino_mcp")

# ---------------------------------------------------------------------------
# Device state
# ---------------------------------------------------------------------------

class DeviceState:
    """Manages FirmataClient connection state."""

    def __init__(self) -> None:
        self._client:         Optional[FirmataClient] = None
        self._port:           Optional[str]           = None
        self._servo_origins:  dict[int, int]          = {}  # pin -> origin angle

    def set_servo_origin(self, pin: int, angle: int) -> int:
        """Register angle as origin for the pin. Returns the origin angle."""
        self._servo_origins[pin] = angle
        return angle

    def servo_move_relative(self, pin: int, value: int) -> dict:
        """Resolve an angle offset from the pin's origin. Returns origin and target angle."""
        origin  = self._servo_origins.get(pin, 0)  # default origin is 0
        current = max(0, min(180, origin + value))
        return {"origin": origin, "current": current}

    def clear_servo_state(self) -> None:
        """Reset servo state on disconnect."""
        self._servo_origins.clear()

    @property
    def is_connected(self) -> bool:
        return self._client is not None

    @property
    def port(self) -> Optional[str]:
        return self._port

    def connect(self, port: str) -> dict:
        if self._client is not None:
            self._client.close()
        self._client = FirmataClient(port)
        self._port   = port
        return self._client.query_board_info()

    def disconnect(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
            self._port   = None
            self.clear_servo_state()

    def get_client(self) -> FirmataClient:
        if self._client is None:
            raise DeviceNotConnectedError(
                "Not connected. Call 'arduino_connect' first with a port from 'arduino_list_ports'."
            )
        return self._client


class DeviceNotConnectedError(Exception):
    pass


_state = DeviceState()

# ---------------------------------------------------------------------------
# Error handling decorator
# ---------------------------------------------------------------------------

T = TypeVar("T", bound=Callable[..., str])

def handle_errors(func: T) -> T:
    @wraps(func)
    def wrapper(*args, **kwargs) -> str:
        try:
            return func(*args, **kwargs)
        except DeviceNotConnectedError as e:
            return json.dumps({"status": "error", "message": str(e)}, indent=2)
        except Exception as e:
            return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"}, indent=2)
    return wrapper  # type: ignore

# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------

mcp = FastMCP("arduino_mcp")

# ---------------------------------------------------------------------------
# Input models
# ---------------------------------------------------------------------------

class ArduinoBaseModel(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")


class ConnectInput(ArduinoBaseModel):
    port: str = Field(..., description="Serial port name (e.g. 'COM9', '/dev/ttyUSB0')", min_length=1)


class PinModeInput(ArduinoBaseModel):
    pin:  int = Field(..., description="Digital pin number (e.g. 13)", ge=0, le=69)
    mode: str = Field(..., description="Pin mode: 'INPUT', 'OUTPUT', 'ANALOG', 'PWM', 'SERVO'")


class DigitalWriteInput(ArduinoBaseModel):
    pin:   int  = Field(..., description="Digital pin number", ge=0, le=69)
    value: bool = Field(..., description="True = HIGH, False = LOW")


class DigitalReadInput(ArduinoBaseModel):
    pin: int = Field(..., description="Digital pin number", ge=0, le=69)


class AnalogWriteInput(ArduinoBaseModel):
    pin:   int = Field(..., description="Digital pin number", ge=0, le=69)
    value: int = Field(..., description="PWM value (0-255)", ge=0, le=255)


class AnalogReadInput(ArduinoBaseModel):
    channel: int = Field(..., description="Analog channel number (0=A0, 1=A1, ...)", ge=0, le=15)


class ServoOriginInput(ArduinoBaseModel):
    pin:   int = Field(..., description="Digital pin number configured as SERVO", ge=0, le=69)
    angle: int = Field(..., description="Current servo angle to register as origin (0-180)", ge=0, le=180)


class ServoMoveInput(ArduinoBaseModel):
    pin:   int = Field(..., description="Digital pin number configured as SERVO", ge=0, le=69)
    value: int = Field(..., description="Angle (negative = reverse direction)", ge=-180, le=180)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool(
    name="arduino_list_ports",
    annotations={
        "title": "List available serial ports",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
@handle_errors
def arduino_list_ports() -> str:
    """List available serial ports on this machine.

    Call this first to discover which port your Arduino is connected to,
    then pass the port name to 'arduino_connect'.

    Returns:
        str: JSON list of objects, each containing:
            - port (str): Port name (e.g. 'COM9', '/dev/ttyUSB0')
            - description (str): Human-readable description
            - hwid (str): Hardware ID string
    """
    ports = [
        {"port": p.device, "description": p.description, "hwid": p.hwid}
        for p in serial.tools.list_ports.comports()
    ]
    return json.dumps(ports, indent=2)


@mcp.tool(
    name="arduino_connect",
    annotations={
        "title": "Connect to Arduino",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
@handle_errors
def arduino_connect(params: ConnectInput) -> str:
    """Connect to an Arduino running StandardFirmata on the specified serial port.

    Automatically queries board info (firmware, pin capabilities, analog mapping)
    after connecting.

    Args:
        params (ConnectInput):
            - port (str): Serial port name (e.g. 'COM9', '/dev/ttyUSB0')

    Returns:
        str: JSON with connection status and firmware information.
    """
    info = _state.connect(params.port)
    return json.dumps({
        "status":    "connected",
        "port":      params.port,
        "firmware":  info["firmware"],
        "version":   list(info["version"]),
        "pin_count": len(info["capabilities"]),
    }, indent=2)


@mcp.tool(
    name="arduino_disconnect",
    annotations={
        "title": "Disconnect from Arduino",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def arduino_disconnect() -> str:
    """Disconnect from the currently connected Arduino.

    Returns:
        str: JSON with disconnection status.
    """
    if not _state.is_connected:
        return json.dumps({"status": "not_connected"})
    _state.disconnect()
    return json.dumps({"status": "disconnected"})


@mcp.tool(
    name="arduino_get_board_info",
    annotations={
        "title": "Get board capabilities",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
@handle_errors
def arduino_get_board_info() -> str:
    """Return firmware info and pin capabilities of the connected Arduino.

    Returns:
        str: JSON object containing:
            - firmware (str): Firmware name (e.g. 'StandardFirmata.ino')
            - version (list): [major, minor] version numbers
            - analog_mapping (dict): analog channel -> digital pin number
            - capabilities (dict): pin number -> list of supported modes
              Each mode: {"mode": int, "resolution": int}
              Mode values: 0=INPUT, 1=OUTPUT, 2=ANALOG, 3=PWM, 4=SERVO, 6=I2C
    """
    client = _state.get_client()
    info   = client.query_board_info()
    return json.dumps({
        "firmware":       info["firmware"],
        "version":        list(info["version"]),
        "analog_mapping": {str(k): v for k, v in info["analog_mapping"].items()},
        "capabilities":   {str(k): v for k, v in info["capabilities"].items()},
    }, indent=2)


@mcp.tool(
    name="arduino_set_pin_mode",
    annotations={
        "title": "Set pin mode",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
@handle_errors
def arduino_set_pin_mode(params: PinModeInput) -> str:
    """Set the operating mode of a digital pin.

    Must be called before reading or writing a pin.
    Use 'arduino_get_board_info' to check which modes each pin supports.

    NOTE on SERVO mode:
        Setting a pin to SERVO mode does not move the servo to any defined position.
        The physical position after mode change is undefined (hardware-dependent).
    Args:
        params (PinModeInput):
            - pin  (int): Digital pin number (0-69)
            - mode (str): One of 'INPUT', 'OUTPUT', 'ANALOG', 'PWM', 'SERVO'

    Returns:
        str: JSON with status and the pin/mode that was set.
    """
    mode_map = {
        "INPUT":  PinMode.INPUT,
        "OUTPUT": PinMode.OUTPUT,
        "ANALOG": PinMode.ANALOG,
        "PWM":    PinMode.PWM,
        "SERVO":  PinMode.SERVO,
    }
    mode_upper = params.mode.upper()
    if mode_upper not in mode_map:
        return json.dumps({
            "status":  "error",
            "message": f"Unknown mode '{params.mode}'. Valid: {list(mode_map.keys())}",
        })

    _state.get_client().set_pin_mode(params.pin, mode_map[mode_upper])
    return json.dumps({"status": "ok", "pin": params.pin, "mode": mode_upper})


@mcp.tool(
    name="arduino_digital_write",
    annotations={
        "title": "Write digital output",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
@handle_errors
def arduino_digital_write(params: DigitalWriteInput) -> str:
    """Set a digital output pin HIGH or LOW.

    Args:
        params (DigitalWriteInput):
            - pin   (int):  Digital pin number (0-69)
            - value (bool): True = HIGH, False = LOW

    Returns:
        str: JSON with status, pin, and value written.
    """
    _state.get_client().digital_write(params.pin, params.value)
    return json.dumps({"status": "ok", "pin": params.pin, "value": params.value})


@mcp.tool(
    name="arduino_digital_read",
    annotations={
        "title": "Read digital input",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
@handle_errors
def arduino_digital_read(params: DigitalReadInput) -> str:
    """Read the current state of a digital input pin.

    Args:
        params (DigitalReadInput):
            - pin (int): Digital pin number (0-69)

    Returns:
        str: JSON with:
            - pin (int): Pin number read
            - value (int): 1 = HIGH, 0 = LOW
    """
    client = _state.get_client()
    port   = params.pin // 8

    client.report_digital(port, True)
    time.sleep(REPORT_SETTLE)

    samples = []
    for _ in range(SAMPLES):
        v = client.digital_read(params.pin)
        samples.append(v if v is not None else 0)
        time.sleep(SAMPLE_DELAY)

    client.report_digital(port, False)

    majority = 1 if samples.count(1) > len(samples) / 2 else 0
    return json.dumps({"pin": params.pin, "value": majority})


@mcp.tool(
    name="arduino_analog_write",
    annotations={
        "title": "Write PWM output",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
@handle_errors
def arduino_analog_write(params: AnalogWriteInput) -> str:
    """Write a PWM value to a pin.
    The pin must be configured as PWM mode first with 'arduino_set_pin_mode'.
    For servo control, use 'arduino_servo_move' instead.

    Args:
        params (AnalogWriteInput):
            - pin   (int): Digital pin number (0-69)
            - value (int): PWM value 0-255

    Returns:
        str: JSON with status, pin, and value written.
    """
    _state.get_client().analog_write(params.pin, params.value)
    return json.dumps({"status": "ok", "pin": params.pin, "value": params.value})


@mcp.tool(
    name="arduino_analog_read",
    annotations={
        "title": "Read analog input",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
@handle_errors
def arduino_analog_read(params: AnalogReadInput) -> str:
    """Read the current value of an analog input channel.

    The pin must be set to ANALOG mode first via
    'arduino_set_pin_mode' (use analog_mapping from 'arduino_get_board_info'
    to find the corresponding digital pin number).

    Args:
        params (AnalogReadInput):
            - channel (int): Analog channel number (0=A0, 1=A1, ...)

    Returns:
        str: JSON with:
            - channel (int): Channel number read
            - value (float): Averaged reading (0-1023 for 10-bit ADC)
    """
    client = _state.get_client()

    client.report_analog(params.channel, True)
    time.sleep(REPORT_SETTLE)

    samples = []
    for _ in range(SAMPLES):
        v = client.analog_read(params.channel)
        samples.append(v if v is not None else 0)
        time.sleep(SAMPLE_DELAY)

    client.report_analog(params.channel, False)

    average = sum(samples) / len(samples)
    return json.dumps({
        "channel": params.channel,
        "value":   round(average, 2),
    })


@mcp.tool(
    name="arduino_set_servo_origin",
    annotations={
        "title": "Set servo origin position",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
@handle_errors
def arduino_set_servo_origin(params: ServoOriginInput) -> str:
    """Register a servo angle as the origin (reference point) for relative moves.
    Call this to establish a known reference for 'arduino_servo_move'.
    Args:
        params (ServoOriginInput):
            - pin   (int): Digital pin number configured as SERVO
            - angle (int): Current servo angle to register as origin (0-180)

    Returns:
        str: JSON with:
            - status (str): ok
            - pin (int): Pin number
            - origin (int): Angle registered as origin
    """
    _state.get_client()   # ensure connected
    origin = _state.set_servo_origin(params.pin, params.angle)
    return json.dumps({"status": "ok", "pin": params.pin, "origin": origin})


@mcp.tool(
    name="arduino_servo_move",
    annotations={
        "title": "Move servo",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
@handle_errors
def arduino_servo_move(params: ServoMoveInput) -> str:
    """Move a servo to an angle offset from the registered origin.
    If no origin has been set via 'arduino_set_servo_origin', the origin
    defaults to 0 degrees. For example, passing value=90 will move the
    servo to 90 degrees from the origin.
    Args:
        params (ServoMoveInput):
            - pin   (int): Digital pin number configured as SERVO
            - value (int): Angle (-180 to +180)

    Returns:
        str: JSON with:
            - status (str): ok
            - pin (int): Pin number
            - value (int): Angle
    """
    client = _state.get_client()
    move   = _state.servo_move_relative(params.pin, params.value)
    client.analog_write(params.pin, move["current"])
    return json.dumps({"status": "ok", "pin": params.pin, "value": params.value})

# ============================================================
# Entry Point
# ============================================================
def main() -> None:
    """Entry point for the MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()