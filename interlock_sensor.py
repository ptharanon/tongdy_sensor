import logging
import minimalmodbus
import serial
import time
import threading

# Configure logging
logging.basicConfig(
    level=logging.ERROR,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

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
            def __enter__(self_inner):
                lock.acquire()
                now = time.time()
                last = cls._last_access.get(port, 0.0)
                wait = pre_delay - (now - last)
                if wait > 0:
                    time.sleep(wait)
                return self_inner

            def __exit__(self_inner, exc_type, exc, tb):
                cls._last_access[port] = time.time()
                lock.release()
                return False

        return _Ctx()

# MARK: INTERLOCK SENSOR CLASS
class InterlockSensor:
    def __init__(self,
                 sensor_address: int, 
                 port: str = "/dev/ttyUSB0",
                 baudrate: int = 19200,
                 timeout: float = 1.5,
                 pre_delay: float = 0.03,
                 name: str = "interlock"):

        self.sensor_id = sensor_address
        self.sensor_address = sensor_address
        self.sensor_type = "interlock"
        self.name = name
        self.pre_delay = pre_delay
        self.max_retries = 3    # maximum number of retries for reading
        self.retry_delay = 0.5  # delay between retries in seconds
        self.MODBUS_ADDRESS = self._get_address()

        try:
            self.instrument = minimalmodbus.Instrument(port=port, slaveaddress=sensor_address)
            self.instrument.serial.baudrate = baudrate
            self.instrument.serial.bytesize = 8
            self.instrument.serial.parity = serial.PARITY_NONE
            self.instrument.serial.stopbits = 1
            self.instrument.serial.timeout = timeout  # seconds
            self.instrument.mode = minimalmodbus.MODE_RTU
            self.instrument.clear_buffers_before_each_transaction = True
            self.instrument.close_port_after_each_call = False

            logger.info(f"Type K sensor connected on port {port} with address {sensor_address}")

        except Exception as e:
            logger.exception(f"Failed to initialize Type K sensor on port {port} with address {sensor_address}: {e}")
            self.instrument = None

    def read_values(self) -> dict:
        """
        Return a dictionary with HLR sensor readings.
        Returns:
        {
            "sensor_id": int,
            "sensor_type": str,
            "payload": JSON object
        }
        
        Payload format : {
            "temperature_before_filter": float,  # Temperature in °C
            "fan_speed": float,                   # Fan speed in %
            "temperature": float,            # Duct temperature in °C
            "humid": float,               # Duct humidity in %RH
            "co2": int,                      # Duct CO2 in ppm
            "voc": float,                    # Duct VOC in %LV
            "operation_mode": int             # 0-5 (Manual/Standby/Scrubbing/Regeneration/Cooldown/Alarming)
        }
        """
        data = {
            "sensor_id" : self.name,
            "sensor_type" : self.sensor_type,
            "payload" : {}
        }

        payload = {}

        if not self.instrument:
            logger.error("Minimal MODBUS Instrument not initialized.")
            payload = {
                "temperature_before_filter": None,
                "fan_speed": None,
                "temperature": None,
                "humid": None,
                "co2": None,
                "voc": None,
                "operation_mode": None
            }
            
            data["payload"] = payload
            return data

        retries = 0
        while retries < self.max_retries:
            retries += 1
            try:
                with RS485BusManager.access(self.instrument.serial.port, self.pre_delay):

                    # Read Temperature Before Filter Value (40001) - INT16 with 0.1x multiplier
                    temp_before_filter_raw = self.instrument.read_register(
                        registeraddress=self.MODBUS_ADDRESS["ADDR_TEMP_BEFORE_FILTER"],
                        functioncode=self.MODBUS_ADDRESS["FUNCTION_CODE"],
                        signed=True)
                    temperature_before_filter = temp_before_filter_raw * 0.1

                    # Read Fan Speed (40003) - INT16 with 1.0x multiplier
                    fan_speed = self.instrument.read_register(
                        registeraddress=self.MODBUS_ADDRESS["ADDR_FAN_SPEED"],
                        functioncode=self.MODBUS_ADDRESS["FUNCTION_CODE"],
                        signed=True) * 1.0

                    # Read Duct Temperature (40004) - INT16 with 0.1x multiplier
                    duct_temp_raw = self.instrument.read_register(
                        registeraddress=self.MODBUS_ADDRESS["ADDR_DUCT_TEMP"],
                        functioncode=self.MODBUS_ADDRESS["FUNCTION_CODE"],
                        signed=True)
                    duct_temperature = duct_temp_raw * 0.1

                    # Read Duct Humidity (40005) - INT16 with 0.1x multiplier
                    duct_humid_raw = self.instrument.read_register(
                        registeraddress=self.MODBUS_ADDRESS["ADDR_DUCT_HUMID"],
                        functioncode=self.MODBUS_ADDRESS["FUNCTION_CODE"],
                        signed=True)
                    duct_humidity = duct_humid_raw * 0.1

                    # Read Duct CO2 (40006) - INT16 with 1.0x multiplier
                    duct_co2 = self.instrument.read_register(
                        registeraddress=self.MODBUS_ADDRESS["ADDR_DUCT_CO2"],
                        functioncode=self.MODBUS_ADDRESS["FUNCTION_CODE"],
                        signed=True) * 1.0

                    # Read Duct VOC (40007) - INT16 with 0.1x multiplier
                    duct_voc_raw = self.instrument.read_register(
                        registeraddress=self.MODBUS_ADDRESS["ADDR_DUCT_VOC"],
                        functioncode=self.MODBUS_ADDRESS["FUNCTION_CODE"],
                        signed=True)
                    duct_voc = duct_voc_raw * 0.1

                    # Read HLR Operation Mode (40009) - INT16 with 1.0x multiplier
                    hlr_operation_mode = self.instrument.read_register(
                        registeraddress=self.MODBUS_ADDRESS["ADDR_HLR_OPERATION_MODE"],
                        functioncode=self.MODBUS_ADDRESS["FUNCTION_CODE"],
                        signed=True) * 1.0

                logger.info(f"Sensor {self.sensor_id} Readings -")
                logger.info(f"Temp Before Filter: {temperature_before_filter}°C, Fan Speed: {fan_speed}%, "
                           f"Duct Temp: {duct_temperature}°C, Duct Humidity: {duct_humidity}%RH, "
                           f"Duct CO2: {duct_co2}ppm, Duct VOC: {duct_voc}%LV, "
                           f"Operation Mode: {hlr_operation_mode}")

                payload =  {
                    "temp_before_filter": round(temperature_before_filter, 2),
                    "fan_speed": round(fan_speed, 2),
                    "temperature": round(duct_temperature, 2),
                    "humid": round(duct_humidity, 2),
                    "co2": int(duct_co2),
                    "voc": round(duct_voc, 2),
                    "operation_mode": int(hlr_operation_mode),
                }

                data["payload"] = payload
                return data
            
            except Exception as e:
                logger.error(f"Attempt {retries} - Failed to read from sensor {self.sensor_id}: {e}")
                time.sleep(self.retry_delay)

        # All attempts failed
        logger.error(f"All {self.max_retries} attempts failed for sensor {self.sensor_id}. Returning None values.")
                
        payload = {
            "temperature_before_filter": None,
            "fan_speed": None,
            "temperature": None,
            "humid": None,
            "co2": None,
            "voc": None,
            "operation_mode": None
        }
        data["payload"] = payload

        return data

    def _get_address(self) -> dict:
        """Get the Modbus address of the interlock sensor registers."""
        return {
            "ADDR_TEMP_BEFORE_FILTER": 0,   # 40001
            "ADDR_FAN_SPEED": 2,            # 40003
            "ADDR_DUCT_TEMP": 3,            # 40004
            "ADDR_DUCT_HUMID": 4,           # 40005
            "ADDR_DUCT_CO2": 5,             # 40006
            "ADDR_DUCT_VOC": 6,             # 40007
            "ADDR_HLR_OPERATION_MODE": 8,   # 40009
            "FUNCTION_CODE": 3              # 03: HOLDING register
        }
