from collections import defaultdict
from datetime import datetime, timezone

from flask import Flask, jsonify, request
from flask_caching import Cache
from flask_cors import CORS
from google.cloud import firestore

from volta_plus.models import meters_ref, sites_ref


def create_app(poor=False):
    app = Flask(__name__)

    if poor:
        app.config['CACHE_TYPE'] = 'filesystem'
        app.config['CACHE_DIR'] = '.cache'
    else:
        app.config['CACHE_TYPE'] = 'simple'

    CORS(app)
    cache = Cache(app)

    @app.route('/', methods=['GET'])
    def index():
        return "Volta+ API"

    @app.route('/sites', methods=['GET'])
    @cache.cached(timeout=86400)
    def get_sites():
        sites = defaultdict(lambda: defaultdict(list))
        for site in list(sites_ref.stream()):
            data = site.to_dict()
            if poor:
                stations = list()
                for station in data['stations']:
                    stations.append({
                        'name': station['name'],
                        'status': station['status'],
                        'meters': station['meters']
                    })
            else:
                stations = data['stations']

            # ignore site if state or city is unknown
            if data['state'] is not None and data['city'] is not None:
                sites[data['state'].lower()][data['city'].lower()].append((data['name'], stations))

        return jsonify(sites)

    @app.route('/meters/<meter_ids>', methods=['GET'])
    def get_meter(meter_ids):
        meters = list()
        for meter_id in meter_ids.split(','):
            meter = meters_ref.document(meter_id).get().to_dict()
            if meter is not None:
                charge_duration = 0
                charge_start_time = meter['in_use_charging_stats']['start']
                if charge_start_time is not None:
                    charge_duration = (datetime.now(timezone.utc) - charge_start_time).seconds
                meter['charge_duration'] = charge_duration
                meter['weekly_usage'] = [meter['weekly_usage'][(i * 144):((i * 144) + 144)] for i in range(7)]
                meters.append(meter)
            else:
                return "invalid id {}".format(meter_id)

        return jsonify(meters)

    return app
