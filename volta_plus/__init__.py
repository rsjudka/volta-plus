import flask
import logging
import sys
from threading import Thread
import time

from .models import VoltaNetwork


logging.basicConfig(filename='volta_plus.log')

def create_app():
    app = flask.Flask(__name__)

    volta_network = VoltaNetwork()
    def update_volta_network():
        while True:
            try:
                volta_network.update()
                time.sleep(1)
            except Exception as e:
                logging.exception(e)
                time.sleep(5)
    Thread(target=update_volta_network, daemon=True).start()

    @app.route('/', methods=['GET'])
    def index():
        return "Volta API+"

    @app.route('/meter/<oem_id>', methods=['GET'])
    def get_meter(oem_id):
        meter = volta_network.get_meter(oem_id)
        if meter is not None:
            return flask.jsonify(meter.serialize())
        else:
            return "invalid oem id"

    @app.route('/meters/in_use', methods=['GET'])
    def get_meters_in_use():
        meters = dict()
        for oem_id, meter in volta_network.meters.items():
            if meter.current_charge_start is not None:
                meters[oem_id] = meter.serialize()
        
        return meters

    return app
