from collections import namedtuple
from datetime import datetime
from google.cloud import firestore
import json
import logging
import os.path
import pytz
from timezonefinder import TimezoneFinder
from urllib.request import urlopen


def log_warning(msg, data):
    logging.warning("--------------------------------------------------------------")
    logging.warning(msg)
    logging.warning(data)
    logging.warning("--------------------------------------------------------------")


class VoltaMeter:
    idle_states = {'idle', 'pluggedout'}
    idle_availabilities = {'available'}
    in_use_charging_states = {'charging', 'pluggedin'}
    in_use_charging_availabilities = {'in use', 'plugged in...'}
    in_use_idle_states = {'chargestopped'}
    in_use_idle_availabilities = {'in use'}

    class InUseStats:
        def __init__(self):
            self.start = None
            self.cnt = 0
            self.avg = 0

        def update_avg(self, utc_time):
            self.cnt += 1

            duration = (utc_time - self.start).total_seconds()
            self.avg += (duration - self.avg) / self.cnt

            self.start = None

        def serialize(self):
            return {
                'start': self.start,
                'cnt': self.cnt,
                'avg': self.avg
            }

    def __init__(self, state, availability):
        self.state = state
        self.availability = availability

        self.in_use_charging_stats = self.InUseStats()
        self.in_use_idle_stats = self.InUseStats()

        self.weekly_usage_update = -1
        self.weekly_usage = [0] * (144 * 7)
        
        self.stale = True

    @classmethod
    def from_collection(cls, collection, state_updated_at):
        state = collection['state']
        availability = collection['availability']

        in_use_charging_stats_start = collection['in_use_charging_stats']['start']
        in_use_idle_stats_start = collection['in_use_idle_stats']['start']
        if state_updated_at is not None:
            if in_use_charging_stats_start is not None:
                in_use_charging_stats_start = in_use_charging_stats_start.replace(tzinfo=None)
                if state_updated_at > in_use_charging_stats_start:
                    in_use_charging_stats_start, state, availability = None, None, None

            if in_use_idle_stats_start is not None:
                in_use_idle_stats_start = in_use_idle_stats_start.replace(tzinfo=None)
                if state_updated_at > in_use_idle_stats_start:
                    in_use_idle_stats_start, state, availability = None, None, None

        volta_meter = cls(state, availability)
        volta_meter.stale = False
        volta_meter.weekly_usage = collection['weekly_usage']

        volta_meter.in_use_charging_stats.start = in_use_charging_stats_start
        volta_meter.in_use_charging_stats.cnt = collection['in_use_charging_stats']['cnt']
        volta_meter.in_use_charging_stats.avg = collection['in_use_charging_stats']['avg']

        volta_meter.in_use_idle_stats.start = in_use_idle_stats_start
        volta_meter.in_use_idle_stats.cnt = collection['in_use_idle_stats']['cnt']
        volta_meter.in_use_idle_stats.avg = collection['in_use_idle_stats']['avg']

        return volta_meter

    def update(self, new_state, new_availability, timezone):
        utc_time = datetime.utcnow()
        local_time = self.utc_to_local_time(utc_time, timezone)

        self.update_in_use_charging(new_state, new_availability, utc_time)
        self.update_in_use_idle(new_state, new_availability, utc_time)
        self.update_weekly_usage(new_state, new_availability, local_time)

        self.state = new_state
        self.availability = new_availability

    def update_in_use_charging(self, new_state, new_availability, utc_time):
        if not self.is_in_use(self.state, self.availability) and self.is_in_use(new_state, new_availability):
            self.in_use_charging_stats.start = utc_time
            self.stale = True
        elif self.is_in_use(self.state, self.availability) and not self.is_in_use(new_state, new_availability):
            if self.in_use_charging_stats.start is not None:
                self.in_use_charging_stats.update_avg(utc_time)
                self.stale = True
            else:
                log_warning("in use charge start time is None when it should not be", self.serialize())

    def update_in_use_idle(self, new_state, new_availability, utc_time):
        if not self.is_in_use_idle(self.state, self.availability) and self.is_in_use_idle(new_state, new_availability):
            self.in_use_idle_stats.start = utc_time
            self.stale = True
        elif self.is_in_use_idle(self.state, self.availability) and not self.is_in_use_idle(new_state, new_availability):
            if self.in_use_idle_stats.start is not None:
                self.in_use_idle_stats.update_avg(utc_time)
                self.stale = True
            else:
                log_warning("in use idle start time is None when it should not be", self.serialize())

    def update_weekly_usage(self, new_state, new_availability, local_time):
        new_weekly_usage_update = (144 * local_time.weekday()) + (((local_time.hour * 60) + local_time.minute) // 10)
        if self.is_in_use(new_state, new_availability) and new_weekly_usage_update != self.weekly_usage_update:
            self.weekly_usage[new_weekly_usage_update] += 1
            self.weekly_usage_update = new_weekly_usage_update

    def is_idle(self, state, availability):
        return state in self.idle_states and availability in self.idle_availabilities

    def is_in_use_charging(self, state, availability):
        return state in self.in_use_charging_states and availability in self.in_use_charging_availabilities

    def is_in_use_idle(self, state, availability):
        return state in self.in_use_idle_states and availability in self.in_use_idle_availabilities

    def is_in_use(self, state, availability):
        return self.is_in_use_charging(state, availability) or self.is_in_use_idle(state, availability)

    def utc_to_local_time(self, utc_time, timezone):
        return timezone.fromutc(utc_time) if timezone is not None else utc_time

    def serialize(self):
        return {
            'state': self.state,
            'availability': self.availability,
            'in_use_charging_stats': self.in_use_charging_stats.serialize(),
            'in_use_idle_stats': self.in_use_idle_stats.serialize(),
            'weekly_usage': self.weekly_usage
        }

class VoltaStation:
    def __init__(self, name, status, street_address, city, state, zip_code, timezone):
        self.name = name
        self.status = status
        self.street_address = street_address
        self.city = city
        self.state = state
        self.zip_code = zip_code
        self.timezone = timezone

        self.meters = dict()
        
        self.stale = True

    @classmethod
    def from_collection(cls, collection):
        name = collection['name']
        status = collection['status']
        street_address = collection['street_address']
        city = collection['city']
        state = collection['state']
        zip_code = collection['zip_code']
        zone = collection['timezone']
        timezone = pytz.timezone(zone) if zone is not None else None

        volta_station = cls(name, status, street_address, city, state, zip_code, timezone)
        volta_station.stale = False

        return volta_station
    
    def update(self, new_name, new_status, new_street_address, new_city, new_state, new_zip_code, new_timezone):
        if new_name != self.name:
            self.name = new_name
            self.stale =  True
        if new_status != self.status:
            self.status = new_status
            self.stale =  True
        if new_street_address != self.street_address:
            self.street_address = new_street_address
            self.stale =  True
        if new_city != self.city:
            self.city = new_city
            self.stale =  True
        if new_state != self.state:
            self.state = new_state
            self.stale =  True
        if new_zip_code != self.zip_code:
            self.zip_code = new_zip_code
            self.stale =  True
        if new_timezone != self.timezone:
            self.timezone = new_timezone
            self.stale =  True

    def serialize(self, meters_ref):
        return {
            'name': self.name,
            'status': self.status,
            'street_address': self.street_address,
            'city': self.city,
            'state': self.state,
            'zip_code': self.zip_code,
            'timezone': self.timezone.zone,
            'meters': [meters_ref.document(meter_id) for meter_id in self.meters]
        }

class VoltaNetwork:
    API_URL = 'https://api.voltaapi.com/v1/public-sites'

    def __init__(self):
        self.stations = dict()

        self.tf = TimezoneFinder(in_memory=True)

        db = firestore.Client()
        self.stations_ref = db.collection('stations')
        self.meters_ref = db.collection('meters')

    def update(self):
        with urlopen(self.API_URL) as url:
            data = json.loads(url.read().decode())
            for charger in data:
                stations = charger.get('stations', None)
                if stations is not None:
                    for station in stations:
                        self.parse_station(station)
                else:
                    log_warning("'stations' array not found", charger)

    def parse_station(self, station):
        station_id = station.get('id', None)
        if station_id is None:
            log_warning("station 'id' not found", station)
            return

        name = station.get('name', None)
        status = station.get('status', None)
        timezone = self.find_timezone(station)
        street_address = station.get('street_address', None)
        city = station.get('city', None)
        state = station.get('state', None)
        zip_code = int(station['zip_code']) if 'zip_code' in station else None
        
        volta_station = self.stations.get(station_id, None)
        if volta_station is None:
            collection = self.stations_ref.document(station_id).get().to_dict()
            if collection is not None:
                volta_station = VoltaStation.from_collection(collection)
            else:
                volta_station = VoltaStation(name, status, street_address, city, state, zip_code, timezone)

            self.stations[station_id] = volta_station

        volta_station.update(name, status, street_address, city, state, zip_code, timezone)

        meters = station.get('meters', None)
        if meters is not None:
            for meter in meters:
                self.parse_meter(volta_station, meter)
        else:
            log_warning("'meters' array not found", station)

        if volta_station.stale:
            self.stations_ref.document(station_id).set(volta_station.serialize(self.meters_ref))
            volta_station.stale = False

    def find_timezone(self, station):
        location = station.get('location', None)
        if location is not None:
            coordinates = location.get('coordinates', None)
            if coordinates is not None:
                timezone = self.tf.timezone_at(lng=coordinates[0], lat=coordinates[1])
                if timezone is not None:
                    return pytz.timezone(timezone)
                else:
                    log_warning("timezone not found", coordinates)
            else:
                log_warning("'coordinates' array not found", location)
        else:
            log_warning("'location' object not found", station)

        return None

    def parse_meter(self, volta_station, meter):
        meter_id = meter.get('oem_id', None)
        if meter_id is None:
            log_warning("meter 'oem_id' not found", meter)
            return

        state = meter['state'].lower() if 'state' in meter else None
        state_updated_at = meter.get('state_updated_at', None)
        if state_updated_at is not None:
            state_updated_at = datetime.strptime(state_updated_at, '%Y-%m-%dT%H:%M:%S.%f')
        availability = meter['availability'].lower() if 'availability' in meter else None

        volta_meter = volta_station.meters.get(meter_id, None)
        if volta_meter is None:
            collection = self.meters_ref.document(meter_id).get().to_dict()
            if collection is not None:
                volta_meter = VoltaMeter.from_collection(collection, state_updated_at)
            else:
                volta_meter = VoltaMeter(state, availability)

            volta_station.meters[meter_id] = volta_meter

        volta_meter.update(state, availability, volta_station.timezone)
        if volta_meter.stale:
            self.meters_ref.document(meter_id).set(volta_meter.serialize())
            volta_meter.stale = False
