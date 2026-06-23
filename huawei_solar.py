"""
Poll a Huawei solar inverter over Modbus, selectable between TCP and RTU.

The inverter can be reached either over the LAN through the Huawei WLAN-FE
dongle (Modbus TCP on port 502) or over a direct serial connection (Modbus
RTU). 
The transport is chosen with the `transport` constructor argument
("tcp" or "rtu")

The register map and gains are taken from base_solar.py (the original RTU
implementation). Only the 13 variables actively polled by huaweisolar.py's
modbusAccess() are included here.

REF: Huawei SUN2000 Modbus interface definitions.
"""

import logging
import random
import time

from pymodbus.client import ModbusSerialClient, ModbusTcpClient

logger = logging.getLogger(__name__)

# Placeholder: replace with the dongle's real LAN IP once known.
DEFAULT_INVERTER_HOST = "172.29.247.138"

DEFAULT_SERIAL_PORT = "/dev/ttyUSB0"

# Supported Modbus transports.
TRANSPORT_TCP = "tcp"
TRANSPORT_RTU = "rtu"

# Supported operating modes.
#   "auto"   - read live values from the inverter over Modbus.
#   "mockup" - return randomly generated.
MODE_AUTO = "auto"
MODE_MOCKUP = "mockup"

# MARK: HUAWEI SOLAR SENSOR CLASS
class HuaweiSolarSensor:
    def __init__(self,
                mode: str = MODE_AUTO,
                transport: str = TRANSPORT_TCP,
                # TCP parameters
                host: str = DEFAULT_INVERTER_HOST,
                tcp_port: int = 502,
                # RTU (serial) parameters
                serial_port: str = DEFAULT_SERIAL_PORT,
                baudrate: int = 9600,
                bytesize: int = 8,
                parity: str = "N",
                stopbits: int = 1,
                # common parameters
                slave_id: int = 1,
                timeout: float = 5.0,
                wait: float = 1.0,
                name: str = "",
                max_retries: int = 3,
                retry_delay: float = 0.5):
        
        mode = mode.lower()
        if mode not in (MODE_AUTO, MODE_MOCKUP):
            raise ValueError(
                f"mode must be '{MODE_AUTO}' or '{MODE_MOCKUP}', got {mode!r}")

        transport = transport.lower()
        if transport not in (TRANSPORT_TCP, TRANSPORT_RTU):
            raise ValueError(
                f"transport must be '{TRANSPORT_TCP}' or '{TRANSPORT_RTU}', got {transport!r}")

        self.mode = mode
        self.transport = transport
        self.host = host
        self.tcp_port = tcp_port
        self.serial_port = serial_port
        self.slave_id = slave_id
        self.sensor_id = slave_id
        self.sensor_address = slave_id
        self.sensor_type = "huawei_solar"
        self.name = name
        self.wait = wait
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.connected = False

        if transport == TRANSPORT_TCP:
            self.target = f"{host}:{tcp_port}"
        else:
            self.target = serial_port

        if mode == MODE_MOCKUP:
            self.client = None
            logger.info(
                f"Huawei solar sensor configured (mockup) - returning random values, "
                f"no Modbus client created (slave {slave_id})")
            return

        try:
            if transport == TRANSPORT_TCP:
                self.client = ModbusTcpClient(host=host, port=tcp_port, timeout=timeout)
            else:
                self.client = ModbusSerialClient(
                    port=serial_port, baudrate=baudrate, bytesize=bytesize,
                    parity=parity, stopbits=stopbits, timeout=timeout)
            logger.info(
                f"Huawei solar sensor configured ({transport}) for {self.target} (slave {slave_id})")
        except Exception as e:
            logger.exception(
                f"Failed to initialize Modbus {transport} client for {self.target}: {e}")
            self.client = None

    @staticmethod
    def _decode(registers, dtype, gain):
        """Combine 16-bit registers (big-endian), apply sign, then gain.

        registers: list of 16-bit ints from pymodbus response.registers
        dtype: one of "u16", "i16", "u32", "i32"
        gain: integer divisor (1 means return the raw integer unchanged)
        """
        raw = 0
        for reg in registers:
            raw = (raw << 16) | (reg & 0xFFFF)

        bits = 16 * len(registers)
        if dtype in ("i16", "i32"):
            sign_bit = 1 << (bits - 1)
            if raw & sign_bit:
                raw -= (1 << bits)

        if gain == 1:
            return raw
        return round(raw / gain, 2)

    def _get_registers(self) -> dict:
        # Register map: name -> (dtype, gain, address, length).

        return {
            # Required
            "input_power": ("i32", 1000, 32064, 2),
            "line_voltage": ("u16", 10, 32066, 1),
            "phase_A_voltage": ("u16", 10, 32069, 1),
            "phase_B_voltage": ("u16", 10, 32070, 1),
            "phase_C_voltage": ("u16", 10, 32071, 1),
            "phase_A_current": ("i32", 1000, 32072, 2),
            "phase_B_current": ("i32", 1000, 32074, 2),
            "phase_C_current": ("i32", 1000, 32076, 2),
            "active_power": ("i32", 1000, 32080, 2),
            "efficiency": ("u16", 100, 32086, 1),
            "accumulated_yield_energy": ("u32", 100, 32106, 2),
            "daily_yield_energy": ("u32", 100, 32114, 2),

            # Additional
            "pv_01_voltage": ("i16", 10, 32016, 1),
            "pv_01_current": ("i16", 100, 32017, 1),
            "fault_code": ("u16", 1, 32090, 1),
            "grid_A_voltage": ("i32", 10, 37101, 2),
            "active_grid_A_current": ("i32", 100, 37107, 2),
            "power_meter_active_power": ("i32", 1, 37113, 2),
            "grid_exported_energy": ("i32", 100, 37119, 2),
            "grid_accumulated_energy": ("u32", 100, 37121, 2),
    }

    def _none_result(self) -> dict:
        # Return a dictionary with all register values set to None.
        result = {name: None for name in self._get_registers()}
        result["sensor_id"] = self.sensor_id
        result["sensor_type"] = self.sensor_type
        return result

    def _get_mock_ranges(self) -> dict:
        return {
            "pv_01_voltage": (200.0, 400.0),        # V
            "pv_01_current": (0.0, 11.0),           # A
            "pv_02_voltage": (200.0, 400.0),        # V
            "pv_02_current": (0.0, 11.0),           # A
            "input_power": (0.0, 10.0),             # kW (DC in)
            "line_voltage": (380.0, 420.0),         # V (phase-to-phase)
            "phase_A_voltage": (220.0, 240.0),      # V
            "phase_B_voltage": (220.0, 240.0),      # V
            "phase_C_voltage": (220.0, 240.0),      # V
            "phase_A_current": (0.0, 16.0),         # A
            "phase_B_current": (0.0, 16.0),         # A
            "phase_C_current": (0.0, 16.0),         # A
            "active_power": (0.0, 10.0),            # kW (AC out)
            "efficiency": (95.0, 100.0),            # %
            "fault_code": (0, 0),                   # 0 = healthy
            "accumulated_yield_energy": (1000.0, 50000.0),  # kWh
            "daily_yield_energy": (0.0, 60.0),      # kWh
            "grid_A_voltage": (220.0, 240.0),       # V
            "active_grid_A_current": (0.0, 16.0),   # A
            "power_meter_active_power": (-5000, 5000),      # W
            "grid_exported_energy": (0.0, 30000.0),         # kWh
            "grid_accumulated_energy": (0.0, 30000.0),      # kWh
        }

    def _mock_result(self) -> dict:
        # Build a result dict of random, plausible values for mockup mode.
        
        registers = self._get_registers()
        ranges = self._get_mock_ranges()
        result = {}
        for name, (low, high) in ranges.items():
            gain = registers[name][1]
            if gain == 1:
                result[name] = random.randint(int(low), int(high))
            else:
                result[name] = round(random.uniform(low, high), 2)

        result["sensor_id"] = self.sensor_id
        result["sensor_type"] = self.sensor_type
        return result


    def _ensure_connection(self) -> bool:
        if self.connected:
            return True
        for attempt in range(1, self.max_retries + 1):
            try:
                if self.client.connect():
                    self.connected = True
                    time.sleep(self.wait)  # inverter needs a settle delay
                    return True
            except Exception as e:
                logger.error(f"Connect attempt {attempt} to {self.target} failed: {e}")
            time.sleep(self.retry_delay)
        return False

    def _reconnect(self):
        try:
            self.client.close()
        except Exception:
            pass
        self.connected = False
        try:
            if self.client.connect():
                self.connected = True
                time.sleep(self.wait)  # inverter needs a settle delay
        except Exception as e:
            logger.error(f"Reconnect to {self.target} failed: {e}")

    def _read_register(self, name, dtype, gain, address, length):
        for attempt in range(1, self.max_retries + 1):
            try:
                response = self.client.read_holding_registers(
                    address, count=length, device_id=self.slave_id)
                if response.isError():
                    raise IOError(f"Modbus error response: {response}")
                return self._decode(response.registers, dtype, gain)
            except Exception as e:
                logger.error(f"Attempt {attempt} - failed to read {name}@{address}: {e}")
                self._reconnect()
                time.sleep(self.retry_delay)
        logger.error(f"All {self.max_retries} attempts failed for {name}. Returning None.")
        return None

    def read_values(self) -> dict:
        """Return a dict of the 13 inverter readings plus sensor_id/sensor_type.

        On client-init failure or inability to connect, all readings are None.
        An individual register failure yields None for that key only.
        """

        if self.mode == MODE_MOCKUP:
            result = self._mock_result()
            logger.info(f"Huawei solar {self.name or 'mockup'} mock readings: {result}")
            return result

        if not self.client:
            logger.error("Modbus TCP client not initialized.")
            return self._none_result()

        if not self._ensure_connection():
            logger.error(f"Could not connect to inverter at {self.target}. Returning None values.")
            return self._none_result()

        result = {}
        for name, (dtype, gain, address, length) in self._get_registers().items():
            result[name] = self._read_register(name, dtype, gain, address, length)

        result["sensor_id"] = self.sensor_id
        result["sensor_type"] = self.sensor_type

        logger.info(f"Huawei solar {self.name or self.target} readings: {result}")
        return result
