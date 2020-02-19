import logging
import sys

from volta_plus import create_app


logging.basicConfig(
    stream=sys.stdout,
    format='[%(levelname)s][%(asctime)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

app = create_app(poor=True)


if __name__ == '__main__':
    app.run()
