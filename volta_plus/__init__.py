from collections import defaultdict
from datetime import datetime, timezone

from flask import Flask, jsonify, request
from flask_caching import Cache
from flask_cors import CORS
from google.cloud import firestore

from volta_plus.models import meters_ref, sites_ref


def create_app():
    app = Flask(__name__)
    cache = Cache(app, config={"CACHE_TYPE": "simple"})
    CORS(app)

    @app.route('/', methods=['GET'])
    def index():
        return "Volta+ API"

    @app.route('/sites', methods=['GET'])
    @cache.cached(timeout=86400)
    def get_sites():
        sites = defaultdict(lambda: defaultdict(list))
        for site in list(sites_ref.stream()):
            data = site.to_dict()
            sites[data['state'].lower()][data['city'].lower()].append((data['name'], data['stations']))
        response = jsonify(sites)
        return response

    @app.route('/meter', methods=['GET'])
    def get_meter():
        meter_id = request.args.get('meter_id', None)
        if meter_id is not None:
            meter = meters_ref.document(meter_id).get().to_dict()
            if meter is not None:
                response = jsonify(meter)
                return response
            else:
                return "invalid id"
        else:
            return "missing parameter 'id'"

    @app.route('/meters', methods=['GET'])
    def get_meters():
        station_id = request.args.get('station_id', None)
        if station_id is not None:
            stations = stations_ref.document(station_id).get(['name', 'status', 'meters', 'timezone']).to_dict()
            meters = [meter.get().to_dict() for meter in stations['meters']]

            new_meters = list()
            for meter in meters:
                curr_charge = 0
                curr_time = datetime.now(timezone.utc)
                start_time = meter['in_use_charging_stats'].get('start')
                if start_time is not None:
                    curr_charge = (curr_time - start_time).seconds
                new_meter = meter
                new_meter['charge_duration'] = curr_charge
                new_meter['weekly_usage'] = [meter['weekly_usage'][(i * 144):((i * 144) + 144)] for i in range(7)]
                new_meters.append(new_meter)

            stations['meters'] = new_meters

            response = jsonify(stations)
            return response
        else:
            return "missing parameter 'station_id'"

    return app
