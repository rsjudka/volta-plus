import logging
import time

from volta_plus.models import VoltaNetwork


logging.basicConfig(filename='volta_plus.log')


if __name__ == '__main__':
    volta_network = VoltaNetwork()

    while True:
        try:
            volta_network.update()
            time.sleep(15)
        except Exception as e:
            logging.exception(e)
            time.sleep(30)
