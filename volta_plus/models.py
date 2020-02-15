from collections import namedtuple
from datetime import datetime
import json
import logging
import os.path
from urllib.request import urlopen

from google.api_core.datetime_helpers import DatetimeWithNanoseconds
from google.cloud import firestore
import pytz
from timezonefinder import TimezoneFinder


_db = firestore.Client()
sites_ref = _db.collection('sites')
stations_ref = _db.collection('stations')
meters_ref = _db.collection('meters')

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
    in_use_stopped_states = {'chargestopped'}
    in_use_stopped_availabilities = {'in use'}

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
            if self.start is not None:
                self.start._nanosecond = 0
            return {
                'start': self.start,
                'cnt': self.cnt,
                'avg': self.avg
            }

    def __init__(self):
        self.state = None
        self.availability = None

        self.in_use_charging_stats = self.InUseStats()
        self.in_use_stopped_stats = self.InUseStats()

        self.weekly_usage_update = -1
        self.weekly_usage = [0] * (144 * 7)

        self.stale = True

    @classmethod
    def from_collection(cls, collection):
        volta_meter = cls()
        volta_meter.weekly_usage = collection['weekly_usage']

        volta_meter.in_use_charging_stats.cnt = collection['in_use_charging_stats']['cnt']
        volta_meter.in_use_charging_stats.avg = collection['in_use_charging_stats']['avg']

        volta_meter.in_use_stopped_stats.cnt = collection['in_use_stopped_stats']['cnt']
        volta_meter.in_use_stopped_stats.avg = collection['in_use_stopped_stats']['avg']

        return volta_meter

    def update(self, new_state, new_availability, timezone):
        if not self.is_valid(self.state, self.availability):
            if not self.is_idle(new_state, new_availability):
                return
        else:
            utc_time = DatetimeWithNanoseconds.utcnow()

            self.update_in_use_charging(new_state, new_availability, utc_time)
            self.update_in_use_stopped(new_state, new_availability, utc_time)
            self.update_weekly_usage(new_state, new_availability, self.utc_to_local_time(utc_time, timezone))

        if new_state != self.state:
            logging.debug("updating meter state from {} to {}".format(self.state, new_state))
            self.state = new_state
            self.stale = True
        if new_availability != self.availability:
            logging.debug("updating meter availability from {} to {}".format(self.availability, new_availability))
            self.availability = new_availability
            self.stale = True

    def update_in_use_charging(self, new_state, new_availability, utc_time):
        if not self.is_in_use(self.state, self.availability) and self.is_in_use(new_state, new_availability):
            logging.debug("updating meter in_use_charging_stats.start")
            self.in_use_charging_stats.start = utc_time
        elif self.is_in_use(self.state, self.availability) and not self.is_in_use(new_state, new_availability):
            if self.in_use_charging_stats.start is not None:
                logging.debug("updating meter in_use_charging_stats.avg")
                self.in_use_charging_stats.update_avg(utc_time)
            else:
                log_warning("in use charge start time is None when it should not be", self.serialize())

    def update_in_use_stopped(self, new_state, new_availability, utc_time):
        if not self.is_in_use_stopped(self.state, self.availability) and self.is_in_use_stopped(new_state, new_availability):
            logging.debug("updating meter in_use_stopped_stats.start")
            self.in_use_stopped_stats.start = utc_time
        elif self.is_in_use_stopped(self.state, self.availability) and not self.is_in_use_stopped(new_state, new_availability):
            if self.in_use_stopped_stats.start is not None:
                logging.debug("updating meter in_use_stopped_stats.avg")
                self.in_use_stopped_stats.update_avg(utc_time)
            else:
                log_warning("in use idle start time is None when it should not be", self.serialize())

    def update_weekly_usage(self, new_state, new_availability, local_time):
        new_weekly_usage_update = (144 * local_time.weekday()) + (((local_time.hour * 60) + local_time.minute) // 10)
        if self.is_in_use(new_state, new_availability) and new_weekly_usage_update != self.weekly_usage_update:
            logging.debug("updating meter weekly_usage")
            self.weekly_usage[new_weekly_usage_update] += 1
            self.weekly_usage_update = new_weekly_usage_update

    def is_valid(self, state, availability):
        return state is not None and availability is not None
    
    def is_idle(self, state, availability):
        return state in self.idle_states and availability in self.idle_availabilities

    def is_in_use_charging(self, state, availability):
        return state in self.in_use_charging_states and availability in self.in_use_charging_availabilities

    def is_in_use_stopped(self, state, availability):
        return state in self.in_use_stopped_states and availability in self.in_use_stopped_availabilities

    def is_in_use(self, state, availability):
        return self.is_in_use_charging(state, availability) or self.is_in_use_stopped(state, availability)

    def utc_to_local_time(self, utc_time, timezone):
        return timezone.fromutc(utc_time) if timezone is not None else utc_time

    def serialize(self):
        return {
            'state': self.state,
            'availability': self.availability,
            'in_use_charging_stats': self.in_use_charging_stats.serialize(),
            'in_use_stopped_stats': self.in_use_stopped_stats.serialize(),
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

    def serialize(self):
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

    def poor_serialize(self):
        return {
            'name': self.name,
            'status': self.status,
            'street_address': self.street_address,
            'city': self.city,
            'state': self.state,
            'zip_code': self.zip_code,
            'timezone': self.timezone.zone,
            'meters': [meter_id for meter_id in self.meters]
        }

class VoltaSite:
    def __init__(self, name, street_address, city, state, zip_code, timezone):
        self.name = name
        self.street_address = street_address
        self.city = city
        self.state = state
        self.zip_code = zip_code
        self.timezone = timezone

        self.stations = dict()

        self.stale = True

    @classmethod
    def from_collection(cls, collection):
        name = collection['name']
        street_address = collection['street_address']
        city = collection['city']
        state = collection['state']
        zip_code = collection['zip_code']
        zone = collection['timezone']
        timezone = pytz.timezone(zone) if zone is not None else None

        volta_site = cls(name, street_address, city, state, zip_code, timezone)
        volta_site.stale = False

        return volta_site

    def update(self, new_name, new_street_address, new_city, new_state, new_zip_code, new_timezone):
        if new_name != self.name:
            self.name = new_name
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

    def serialize(self):
        return {
            'name': self.name,
            'street_address': self.street_address,
            'city': self.city,
            'state': self.state,
            'zip_code': self.zip_code,
            'timezone': self.timezone.zone,
            'stations': [stations_ref.document(station_id) for station_id in self.stations]
        }

    def poor_serialize(self):
        return {
            'name': self.name,
            'street_address': self.street_address,
            'city': self.city,
            'state': self.state,
            'zip_code': self.zip_code,
            'timezone': self.timezone.zone,
            'stations': [station.poor_serialize() for station in self.stations.values()]
        }

class VoltaNetwork:
    API_URL = 'https://api.voltaapi.com/v1/public-sites'

    def __init__(self, poor=False):
        self.poor = poor

        self.sites = dict()
        self.tf = TimezoneFinder(in_memory=True)

    def update(self):
        with urlopen(self.API_URL) as url:
            data = json.loads(url.read().decode())
            for site in data:
                self.parse_site(site)

    def parse_site(self, site):
        site_id = site.get('id', None)
        if site_id is None:
            log_warning("site 'id' not found", site)
            return

        name = site.get('name', None)
        street_address = site.get('street_address', None)
        city = site.get('city', None)
        state = site.get('state', None)
        zip_code = int(site['zip_code']) if 'zip_code' in site else None
        timezone = self.find_timezone(site)

        volta_site = self.sites.get(site_id, None)
        if volta_site is None:
            if not self.poor:
                field_paths = ['name', 'street_address', 'city', 'state', 'zip_code', 'timezone', 'stations']
                collection = sites_ref.document(site_id).get(field_paths).to_dict()
                if collection is not None:
                    volta_site = VoltaSite.from_collection(collection)

            if volta_site is None:
                logging.info("creating new site {}".format(site_id))
                volta_site = VoltaSite(name, street_address, city, state, zip_code, timezone)

            self.sites[site_id] = volta_site

        volta_site.update(name, street_address, city, state, zip_code, timezone)

        stations = site.get('stations', None)
        if stations is not None:
            for station in stations:
                self.parse_station(volta_site, station)
        else:
            log_warning("'stations' array not found", site)

        if volta_site.stale:
            logging.debug("writing site {} to sites_ref".format(site_id))
            if self.poor:
                sites_ref.document(site_id).set(volta_site.poor_serialize())
            else:
                sites_ref.document(site_id).set(volta_site.serialize())
            volta_site.stale = False

    def parse_station(self, volta_site, station):
        station_id = station.get('id', None)
        if station_id is None:
            log_warning("station 'id' not found", station)
            return

        name = station.get('name', None)
        status = station.get('status', None)
        street_address = station.get('street_address', None)
        city = station.get('city', None)
        state = station.get('state', None)
        zip_code = int(station['zip_code']) if 'zip_code' in station else None
        timezone = self.find_timezone(station)

        volta_station = volta_site.stations.get(station_id, None)
        if volta_station is None:
            if not self.poor:
                field_paths = ['name', 'status', 'street_address', 'city', 'state', 'zip_code', 'timezone', 'meters']
                collection = stations_ref.document(station_id).get(field_paths).to_dict()
                if collection is not None:
                    volta_station = VoltaStation.from_collection(collection)

            if volta_station is None:
                logging.info("creating new station {}".format(station_id))
                volta_station = VoltaStation(name, status, street_address, city, state, zip_code, timezone)

            volta_site.stations[station_id] = volta_station

        volta_station.update(name, status, street_address, city, state, zip_code, timezone)

        meters = station.get('meters', None)
        if meters is not None:
            for meter in meters:
                self.parse_meter(volta_station, meter)
        else:
            log_warning("'meters' array not found", station)

        if volta_station.stale:
            if self.poor:
                volta_site.stale = True
            else:
                logging.debug("writing station {} to stations_ref".format(station_id))
                stations_ref.document(station_id).set(volta_station.serialize())
            volta_station.stale = False

    def parse_meter(self, volta_station, meter):
        meter_id = meter.get('oem_id', None)
        if meter_id is None:
            log_warning("meter 'oem_id' not found", meter)
            return

        state = meter['state'].lower() if 'state' in meter else None
        availability = meter['availability'].lower() if 'availability' in meter else None

        volta_meter = volta_station.meters.get(meter_id, None)
        if volta_meter is None:
            field_paths = ['weekly_usage', 'in_use_charging_stats', 'in_use_stopped_stats']
            collection = meters_ref.document(meter_id).get(field_paths).to_dict()
            if collection is not None:
                volta_meter = VoltaMeter.from_collection(collection)
            else:
                logging.info("creating new meter {}".format(meter_id))
                volta_meter = VoltaMeter()

            volta_station.meters[meter_id] = volta_meter

        volta_meter.update(state, availability, volta_station.timezone)
        if volta_meter.stale:
            logging.debug("writing meter {} to meters_ref".format(meter_id))
            meters_ref.document(meter_id).set(volta_meter.serialize())
            volta_meter.stale = False

    def find_timezone(self, station):
        location = station.get('location', None)
        if location is not None:
            coordinates = location.get('coordinates', None)
            if coordinates is not None:
                timezone = self.tf.timezone_at(lng=coordinates[0], lat=coordinates[1])
                if timezone is not None:
                    try:
                        return pytz.timezone(timezone)
                    except Exception as e:
                        logging.exception(e)
                else:
                    log_warning("timezone not found", coordinates)
            else:
                log_warning("'coordinates' array not found", location)
        else:
            log_warning("'location' object not found", station)

        return None
