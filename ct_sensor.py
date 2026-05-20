"""
Read Modbus RTU current registers from a ME231 three-phase multifunctional smart meter.

Register map (holding registers, function code 0x03):
  0x03E8 (1000) - Phase L1 current value    Float32, 2 registers, unit: A
  0x03EA (1002) - Phase L2 current value    Float32, 2 registers, unit: A
  0x03EC (1004) - Phase L3 current value    Float32, 2 registers, unit: A
  0x03EE (1006) - Average current value     Float32, 2 registers, unit: A
  0x03F0 (1008) - Neutral phase current IN  Float32, 2 registers, unit: A

Notes:
  - Use function code 0x03 to read holding registers.
  - Each current value is a 32-bit floating point value.
  - Each Float32 value occupies 2 Modbus registers.
  - To read L1, L2, and L3 currents together, start at address 1000 and read 6 registers.
  - Register addresses are decimal actual Modbus addresses from the datasheet.

REF Datasheet https://assets.temcocontrols.com/products/three_phase_multifunction_smart_meter/me231/ME231-Manual-1.pdf
"""

import logging
import minimalmodbus
import serial
import time
import threading

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# MARK: RS485 BUS MANAGER
class RS485BusManager:
    """
    Bus manager for managing bus access (fixing timeout)
    Magic shenanigans from the depths of threading
    """

    _locks = {}
    _last_access = {}
    _global_lock = threading.Lock()

    @classmethod
    def _ensure_port(cls, port: str):
        with cls._global_lock:
            if port not in cls._locks:
                cls._locks[port] = threading.Lock()
                cls._last_access[port] = 0.0
            return cls._locks[port]

    @classmethod
    def access(cls, port: str, pre_delay: float = 0.03):
        lock = cls._ensure_port(port)

        class _Ctx:
            def __enter__(self):
                lock.acquire()
                now = time.time()
                last = cls._last_access.get(port, 0.0)
                wait = pre_delay - (now - last)
                if wait > 0:
                    time.sleep(wait)
                return self

            def __exit__(self, exc_type, exc, tb):
                cls._last_access[port] = time.time()
                lock.release()
                return False

        return _Ctx()


# MARK: CT SENSOR CLASS
class CTSensor:
    def __init__(self,
                 sensor_address: int,
                 port: str = "/dev/ttyUSB0",
                 baudrate: int = 4800,
                 timeout: float = 1.5,
                 pre_delay: float = 0.03,
                 name: str = ""):

        self.sensor_id = sensor_address
        self.sensor_address = sensor_address
        self.sensor_type = "ct_sensor"
        self.name = name
        self.pre_delay = pre_delay
        self.max_retries = 3    # maximum number of retries for reading
        self.retry_delay = 0.5  # delay between retries in seconds
        self.MODBUS_ADDRESS = self._get_address()

        try:
            self.instrument = minimalmodbus.Instrument(port=port, slaveaddress=sensor_address)
            self.instrument.serial.baudrate = baudrate  # type: ignore[union-attr]
            self.instrument.serial.bytesize = 8  # type: ignore[union-attr]
            self.instrument.serial.parity = serial.PARITY_NONE  # type: ignore[union-attr]
            self.instrument.serial.stopbits = 1  # type: ignore[union-attr]
            self.instrument.serial.timeout = timeout  # type: ignore[union-attr]
            self.instrument.mode = minimalmodbus.MODE_RTU
            self.instrument.clear_buffers_before_each_transaction = True
            self.instrument.close_port_after_each_call = False

            logger.info(f"CT sensor connected on port {port} with address {sensor_address}")

        except Exception as e:
            logger.exception(f"Failed to initialize CT sensor on port {port} with address {sensor_address}: {e}")
            self.instrument = None

    def read_values(self) -> dict:
        """
        Return a dictionary with current readings.
        Returns:
        {
            "current_phase1": 0.00,        # Phase 1 Current reading for CT sensor (A output)
            "current_phase2": 0.00,        # Phase 2 Current reading for CT sensor (A output)
            "current_phase3": 0.00,        # Phase 3 Current reading for CT sensor (A output)
            "sensor_id": int,       # Sensor ID
            "sensor_type": "ct_sensor" # Sensor type
        }
        """
        if not self.instrument:
            logger.error("Minimal MODBUS Instrument not initialized.")
            return {"current_phase1": None, "current_phase2": None, "current_phase3": None, "sensor_id": self.sensor_id, "sensor_type": self.sensor_type}

        retries = 0
        while retries < self.max_retries:
            retries +=1
            try:
                port = self.instrument.serial.port or ""  # type: ignore[union-attr]
                with RS485BusManager.access(port, self.pre_delay):

                    current_phase1 = self.instrument.read_float(
                        registeraddress=self.MODBUS_ADDRESS["ADDR_PHASE1"],
                        functioncode=self.MODBUS_ADDRESS["FUNCTION_CODE"],
                        number_of_registers=2)

                    current_phase2 = self.instrument.read_float(
                        registeraddress=self.MODBUS_ADDRESS["ADDR_PHASE2"],
                        functioncode=self.MODBUS_ADDRESS["FUNCTION_CODE"],
                        number_of_registers=2)

                    current_phase3 = self.instrument.read_float(
                        registeraddress=self.MODBUS_ADDRESS["ADDR_PHASE3"],
                        functioncode=self.MODBUS_ADDRESS["FUNCTION_CODE"],
                        number_of_registers=2)

                    current_phase1 = round(current_phase1, 2)
                    current_phase2 = round(current_phase2, 2)
                    current_phase3 = round(current_phase3, 2)
                    
                logger.info(f"Sensor {self.sensor_id} Readings -")
                logger.info(f"Current Phase 1: {current_phase1} A")
                logger.info(f"Current Phase 2: {current_phase2} A")
                logger.info(f"Current Phase 3: {current_phase3} A")

                return {
                    "current_phase1": current_phase1,
                    "current_phase2": current_phase2,
                    "current_phase3": current_phase3,
                    "sensor_id": self.sensor_id,
                    "sensor_type": self.sensor_type
                }
            except Exception as e:
                logger.error(f"Attempt {retries} - Failed to read from sensor {self.sensor_id}: {e}")
                time.sleep(self.retry_delay)

        # All attempts failed
        logger.error(f"All {self.max_retries} attempts failed for sensor {self.sensor_id}. Returning None values.")
        return {"current_phase1": None, "current_phase2": None, "current_phase3": None, "sensor_id": self.sensor_id, "sensor_type": self.sensor_type}

    def _get_address(self) -> dict:
        """Get the Modbus address of the sensor based on sensor type."""
        return {
            "ADDR_PHASE1": 1000,
            "ADDR_PHASE2": 1002,
            "ADDR_PHASE3": 1004,
            "FUNCTION_CODE": 3
        }