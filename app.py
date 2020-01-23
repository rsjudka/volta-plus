import logging
import sys

from volta_plus import create_app


logging.basicConfig(stream=sys.stdout)

app = create_app()


if __name__ == '__main__':
    app.run()
