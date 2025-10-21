from sensor_poller import SensorPoller
from queue import Queue
import time

# Create queue for data
queue = Queue()

# Create poller (automatically creates sensors)
poller = SensorPoller(
    polling_interval=5,  # Poll every 60 seconds
    ui_queue=queue
)

# Start polling
poller.start()

# Get data from queue
while True:
    if not queue.empty():
        data = queue.get()
        sensor_data = data['data']
        print(f"Sensor {sensor_data['sensor_id']}: "
              f"CO2={sensor_data['co2']}, "
              f"Temp={sensor_data['temperature']}, "
              f"RH={sensor_data['humidity']}")
    time.sleep(1)

# Stop when done
poller.stop()
