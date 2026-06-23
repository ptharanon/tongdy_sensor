from sensor_poller import SensorPoller
from mock_sensor_poller import create_mock_poller

from queue import Queue
import time

# Create queue for data
queue = Queue()

# Create poller
poller = SensorPoller(
    polling_interval=5,
    ui_queue=queue
)
# poller = create_mock_poller(
#     ui_queue=queue,
#     polling_interval=5,
#     sensor_type='stable',
#     include_interlock=True
# )

# Start polling
poller.start()
time.sleep(6)

# Get data from queue
while True:
    if not queue.empty():
        data = queue.get()
        # print(f"\n--- Full Data Object ---")
        # print(data)
        # print(f"--- Parsed ---")
        # print(f"Sensor {data['sensor_id']} ({data['sensor_type']}): "
        #       f"CO2={data['payload']['co2']}, "
        #       f"Temp={data['payload']['temperature']}, "
        #       f"RH={data['payload']['humid']}")
    time.sleep(1)

# Stop when done
poller.stop()
