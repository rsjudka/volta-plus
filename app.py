
import sys

from volta_plus import create_app


app = create_app(sys.argv[1] if len(sys.argv) > 1 else None)


if __name__ == '__main__':
    app.run()
