"""
Read Modbus RTU registers from a VMS-3001 RS485 4-channel analog input module.

Register map (holding registers, function code 0x03):
  0x0000 (40001) - Channel 1 analog value  (0-4095)
  0x0001 (40002) - Channel 2 analog value  (0-4095)
  0x0002 (40003) - Channel 3 analog value  (0-4095)
  0x0003 (40004) - Channel 4 analog value  (0-4095)
  0x07D0 (42001) - Device address          (1-254, default 1)
  0x07D1 (42002) - Baud rate               (0=2400, 1=4800, 2=9600, 3=19200,
                                            4=38400, 5=57600, 6=115200, default 1)
REF Datasheet provided by P'Ya
"""

import logging
import minimalmodbus
import serial
import time
import threading
import random

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Supported operating modes.
#   "auto"   - read live values from the inverter over Modbus.
#   "mockup" - return randomly generated.
MODE_AUTO = "auto"
MODE_MOCKUP = "mockup"

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


# MARK: TEMPERATURE SENSOR CLASS
class TemperatureSensor:
    def __init__(self,
                mode: str = MODE_AUTO,
                sensor_address: int = 1,
                port: str = "/dev/ttyUSB0",
                baudrate: int = 4800,
                timeout: float = 1.5,
                pre_delay: float = 0.03,
                name: str = ""):

        mode = mode.lower()
        if mode not in (MODE_AUTO, MODE_MOCKUP):
            raise ValueError(
                f"mode must be '{MODE_AUTO}' or '{MODE_MOCKUP}', got {mode!r}")
        
        self.mode = mode
        self.sensor_id = sensor_address
        self.sensor_address = sensor_address
        self.sensor_type = "temperature"
        self.name = name
        self.pre_delay = pre_delay
        self.max_retries = 3    # maximum number of retries for reading
        self.retry_delay = 0.5  # delay between retries in seconds
        self.MODBUS_ADDRESS = self._get_address()

        # Mockup mode never talks to hardware, so no Modbus client is built.
        if mode == MODE_MOCKUP:
            self.client = None
            logger.info(
                f"Temperature sensor (mockup) - returning random values, "
                f"no Modbus client created (slave {sensor_address})")
            return

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

            logger.info(f"Temperature sensor connected on port {port} with address {sensor_address}")

        except Exception as e:
            logger.exception(f"Failed to initialize Temperature sensor on port {port} with address {sensor_address}: {e}")
            self.instrument = None

    def _mock_result(self) -> dict:
        result = {}
        result["temperature"] = round(random.uniform(0, 20), 2) # Random temperature value in mA (0-20mA)
        result["sensor_id"] = self.sensor_id
        result["sensor_type"] = self.sensor_type
        return result

    def read_values(self) -> dict:
        """
        Return a dictionary with CO2, temperature, and humidity readings.
        Returns:
        {
            "temperature": 0.0 ,    # Temperature reading for Temperature sensor (mA output, 4-20mA)
            "sensor_id": int,       # Sensor ID
            "sensor_type": "temperature" # Sensor type
        }
        """
        if self.mode == MODE_MOCKUP:
            result = self._mock_result()
            logger.info(f"Temperature sensor {self.name or 'mockup'} mock readings: {result}")
            return result

        if not self.instrument:
            logger.error("Minimal MODBUS Instrument not initialized.")
            return {"temperature": None, "sensor_id": self.sensor_id, "sensor_type": self.sensor_type}

        retries = 0
        while retries < self.max_retries:
            retries +=1
            try:
                port = self.instrument.serial.port or ""  # type: ignore[union-attr]
                with RS485BusManager.access(port, self.pre_delay):

                    temperature = self.instrument.read_register(
                        registeraddress=self.MODBUS_ADDRESS["ADDR_TEMP"],
                        functioncode=self.MODBUS_ADDRESS["FUNCTION_CODE"])

                    temperature = temperature/4095 * 20 # Convert 0-4095 to 0-20mA
                    temperature = round(temperature, 2)
                    
                logger.info(f"Sensor {self.sensor_id} Readings -")
                logger.info(f"Temperature: {temperature} mA")

                return {
                    "temperature": temperature,
                    "sensor_id": self.sensor_id,
                    "sensor_type": self.sensor_type
                }
            except Exception as e:
                logger.error(f"Attempt {retries} - Failed to read from sensor {self.sensor_id}: {e}")
                time.sleep(self.retry_delay)

        # All attempts failed
        logger.error(f"All {self.max_retries} attempts failed for sensor {self.sensor_id}. Returning None values.")
        return {"temperature": None, "sensor_id": self.sensor_id, "sensor_type": self.sensor_type}

    def _get_address(self) -> dict:
        """Get the Modbus address of the sensor based on sensor type."""
        return {
            "ADDR_HUMID": 0,
            "ADDR_TEMP": 1,
            "FUNCTION_CODE": 3
        }