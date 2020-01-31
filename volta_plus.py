import logging
import time

from volta_plus.models import VoltaNetwork


logging.basicConfig(filename='volta_plus.log')







# dumb testing stuff #
from threading import Thread
# dumb testing stuff #

if __name__ == '__main__':
    volta_network = VoltaNetwork()

    # dumb testing stuff #
    def take_input():
        while True:
            oem_id = input()
            if oem_id in volta_network.meters:
                print(volta_network.meters[oem_id].dump())
    Thread(target=take_input, daemon=True).start()
    # dumb testing stuff #

    while True:
        try:
            volta_network.update()
            print("sleeping")
            time.sleep(5)
        except Exception as e:
            logging.exception(e)
            print("something broke")
            time.sleep(10)
