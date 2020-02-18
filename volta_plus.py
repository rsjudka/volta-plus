import logging
from logging.handlers import TimedRotatingFileHandler
import time

from volta_plus.models import VoltaNetwork


logging.basicConfig(
    level=logging.WARNING,
    format='[%(levelname)s][%(asctime)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[TimedRotatingFileHandler('volta_plus.log', when='midnight', backupCount=3, utc=True)]
)


if __name__ == '__main__':
    volta_network = VoltaNetwork(poor=True)

    while True:
        try:
            volta_network.update()
            logging.debug("updated Volta Network")
            time.sleep(15)
        except Exception as e:
            logging.exception(e)
            time.sleep(30)
