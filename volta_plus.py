import logging
from logging.handlers import RotatingFileHandler
import time

from volta_plus.models import VoltaNetwork


log_name = 'volta_plus.log'
logging.basicConfig(
    filename=log_name,
    level=logging.WARNING,
    format='[%(levelname)s][%(asctime)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
log = logging.getLogger()
handler = RotatingFileHandler(log_name, maxBytes=524288, backupCount=1)
log.addHandler(handler)


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
