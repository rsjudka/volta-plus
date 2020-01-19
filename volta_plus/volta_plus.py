import flask
import logging
import sys
from threading import Thread
import time

from volta import VoltaNetwork


logging.basicConfig(filename='volta_plus.log')

def create_app(volta_network):
    app = flask.Flask(__name__)

    @app.route('/', methods=['GET'])
    def index():
        return "Volta API+"

    @app.route('/meter/<oem_id>', methods=['GET'])
    def get_meter(oem_id=None):
        if oem_id is not None:
            meter = volta_network.get_meter(oem_id)
            if meter is not None:
                return flask.jsonify(meter.serialize())
            else:
                return "invalid oem id"
        else:
            return "missing 'oem_id' field"

    @app.route('/meters/in_use', methods=['GET'])
    def get_meters_in_use():
        meters = dict()
        for oem_id, meter in volta_network.meters.items():
            if meter.current_charge_start is not None:
                meters[oem_id] = meter.serialize()
        
        return meters

    return app


if __name__ == '__main__':
    volta_network = VoltaNetwork(sys.argv[1] if len(sys.argv) > 1 else None)
    def update_volta_network():
        while True:
            try:
                volta_network.update()
                time.sleep(15)
            except Exception as e:
                logging.exception(e)
                time.sleep(30)
    Thread(target=update_volta_network, daemon=True).start()

    try:
        app = create_app(volta_network)
        app.run()
    except Exception as e:
        logging.exception(e)
        while True: continue
