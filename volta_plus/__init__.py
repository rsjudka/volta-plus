from flask import Flask, request
from google.cloud import firestore


def create_app():
    app = Flask(__name__)

    db = firestore.Client()
    stations_ref = db.collection('stations')
    meters_ref = db.collection('meters')

    @app.route('/', methods=['GET'])
    def index():
        return "Volta API+"

    @app.route('/meter', methods=['GET'])
    def get_meter():
        meter_id = request.args.get('id', None)
        if meter_id is not None:
            meter = meters_ref.document(meter_id).get().to_dict()
            if meter is not None:
                return meter
            else:
                return "invalid id"
        else:
            return "missing parameter 'id'"

    @app.route('/meters', methods=['GET'])
    def get_meters():
        city = request.args.get('city', None)
        state = request.args.get('state', None)
        if state is not None:
            query = stations_ref.where('state', '==', state)
            if city is not None:
                query = query.where('city', '==', city)

                stations = {station.id:station.to_dict() for station in query.stream()}
                for station in stations:
                    stations[station]['meters'] = [meter.get().to_dict() for meter in stations[station]['meters']]

                return stations
        else:
            return "missing parameter 'state'"

    return app
