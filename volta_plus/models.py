from collections import namedtuple
from datetime import datetime
import json
import logging
import pytz
import shutil
from threading import Lock
from timezonefinder import TimezoneFinder
from urllib.request import urlopen


ChargingStatus = namedtuple('ChargingStatus', 'state availability')
Location = namedtuple('Location', 'city state zip tz')

def log_warning(msg, data):
    logging.warning("--------------------------------------------------------------")
    logging.warning(msg)
    logging.warning(data)
    logging.warning("--------------------------------------------------------------")


class VoltaMeter:
    available = {ChargingStatus('idle', 'available'), ChargingStatus('pluggedout', 'available')}
    in_use_charging = {ChargingStatus('charging', 'in use'), ChargingStatus('pluggedin', 'plugged in...')}
    in_use_idle = {ChargingStatus('chargestopped', 'in use')}
    unknown = {ChargingStatus('old data', 'unknown'), ChargingStatus('bad data', 'unknown')}

    def __init__(self, name, status, location):
        self.name = name
        self.status = status
        self.location = location

        self.charging_status = None

        self.current_charge_start = None
        self.charge_cnt = 0
        self.avg_charge_duration = 0

        self.in_use_idle_start = None
        self.in_use_idle_cnt = 0
        self.in_use_idle_avg_duration = 0

        self.last_weekly_usage_check = self.utc_to_local_time(datetime.utcnow())
        self.weekly_usage = [0] * 10080
        self.weekly_usage_cnt = 0

        self.mutex = Lock()

    def set_stats(self, stats):
        self.charge_cnt = stats['charge_cnt']
        self.avg_charge_duration = stats['avg_charge_duration']
        self.in_use_idle_cnt = stats['in_use_idle_cnt']
        self.in_use_idle_avg_duration = stats['in_use_idle_avg_duration']
        self.weekly_usage = stats['weekly_usage']
        self.weekly_usage_cnt = stats['weekly_usage_cnt']

    def update(self, new_charging_status):
        with self.mutex:
            if self.charging_status is None and new_charging_status not in self.available:
                return

            utc_time = datetime.utcnow()
            local_time = self.utc_to_local_time(utc_time)

            if not self.is_in_use(self.charging_status) and self.is_in_use(new_charging_status):
                self.current_charge_start = utc_time
            elif self.is_in_use(self.charging_status) and not self.is_in_use(new_charging_status):
                if self.current_charge_start is not None:
                    self.update_charge_duration_avg(utc_time)
                else:
                    log_warning("charge start time is None when it shouldn't be", self.dump())

            if self.charging_status not in self.in_use_idle and new_charging_status in self.in_use_idle:
                self.in_use_idle_start = utc_time
            elif self.charging_status in self.in_use_idle and new_charging_status not in self.in_use_idle:
                if self.in_use_idle_start is not None:
                    self.update_in_use_duration_avg(utc_time)
                else:
                    log_warning("idle start time is None when it shouldn't be", self.dump())

            if self.is_in_use(new_charging_status) and local_time.second < self.last_weekly_usage_check.second:
                self.weekly_usage[(local_time.weekday() * 1440) + (local_time.hour * 60) + local_time.minute] += 1

            if local_time.weekday() < self.last_weekly_usage_check.weekday():
                self.weekly_usage_cnt += 1

            self.last_weekly_usage_check = local_time
            self.charging_status = new_charging_status

    def update_charge_duration_avg(self, curr_time):
        duration = (curr_time - self.current_charge_start).total_seconds()
        self.charge_cnt += 1
        self.avg_charge_duration += (duration - self.avg_charge_duration) / self.charge_cnt

        self.current_charge_start = None

    def update_in_use_duration_avg(self, curr_time):
        duration = (curr_time - self.in_use_idle_start).total_seconds()
        self.in_use_idle_cnt += 1
        self.in_use_idle_avg_duration += (duration - self.in_use_idle_avg_duration) / self.in_use_idle_cnt

        self.in_use_idle_start = None

    def is_in_use(self, charging_status):
        return charging_status in self.in_use_charging or charging_status in self.in_use_idle

    def utc_to_local_time(self, utc_time):
        return self.location.tz.fromutc(utc_time) if self.location.tz is not None else utc_time

    def serialize(self):
        with self.mutex:
            return {
                'charge_cnt': self.charge_cnt,
                'avg_charge_duration': self.avg_charge_duration,
                'in_use_idle_cnt': self.in_use_idle_cnt,
                'in_use_idle_avg_duration': self.in_use_idle_avg_duration,
                'weekly_usage': self.weekly_usage,
                'weekly_usage_cnt': self.weekly_usage_cnt
            }

    def dump(self):
        with self.mutex:
            return {
                'name': self.name,
                'status': self.status,
                'location': self.location,
                'charging_status': self.charging_status,
                'current_charge_start': self.utc_to_local_time(self.current_charge_start),
                'charge_cnt': self.charge_cnt,
                'avg_charge_duration': self.avg_charge_duration,
                'in_use_idle_start': self.utc_to_local_time(self.in_use_idle_start),
                'in_use_idle_cnt': self.in_use_idle_cnt,
                'in_use_idle_avg_duration': self.in_use_idle_avg_duration,
                'last_weekly_usage_check': self.last_weekly_usage_check,
                'weekly_usage': self.weekly_usage,
                'weekly_usage_cnt': self.weekly_usage_cnt
            }

class VoltaNetwork:
    API_URL = 'https://api.voltaapi.com/v1/public-sites'

    def __init__(self, init_file=None):
        self.meters = dict()

        self.tf = TimezoneFinder(in_memory=True)

        self.init_data = None
        if init_file is not None:
            with open(init_file) as f:
                self.init_data = json.load(f)

    def show_meter_stats(self, oem_id):
        if oem_id in self.meters:
            logging.info(oem_id)
            logging.info(self.meters[oem_id].dump())

    def get_meter(self, oem_id):
        return self.meters.get(oem_id, None)

    def update(self):
        with urlopen(self.API_URL) as url:
            data = json.loads(url.read().decode())
            for charger in data:
                if 'stations' in charger:
                    for station in charger['stations']:
                        self.parse_charger(station)
                else:
                    log_warning("'stations' array not found", charger)

        with open('data.json', 'w') as f:
            json.dump({oem_id:meter.serialize() for oem_id, meter in self.meters.items()}, f)
        shutil.copy('data.json', 'data.json.bak')

    def parse_charger(self, station):
        if 'meters' in station:
            city = station.get('city', None)
            state = station.get('state', None)
            zip_code = int(station['zip_code']) if 'zip_code' in station else None
            timezone = self.find_timezone(station)
            location = Location(city, state, zip_code, timezone)

            for meter in station['meters']:
                name = station.get('name', None)
                status = station.get('status', None)
                self.parse_meter(meter, name, status, location)
        else:
            log_warning("'meters' array not found", station)

    def find_timezone(self, station):
        location = station.get('location', None)
        if location is not None:
            coordinates = location.get('coordinates', None)
            if coordinates is not None:
                return pytz.timezone(self.tf.timezone_at(lng=coordinates[0], lat=coordinates[1]))
            else:
                log_warning("'coordinates' array not found", location)
        else:
            log_warning("'location' object not found", station)

        return None

    def parse_meter(self, meter, name, status, location):
        oem_id = meter.get('oem_id', None)
        if oem_id is not None:
            if oem_id not in self.meters:
                volta_meter = VoltaMeter(name, status, location)
                if self.init_data is not None and oem_id in self.init_data:
                    volta_meter.set_stats(self.init_data[oem_id])

                self.meters[oem_id] = volta_meter

            state = meter['state'].lower() if 'state' in meter else None
            availability = meter['availability'].lower() if 'availability' in meter else None
            self.meters[oem_id].update(ChargingStatus(state, availability))
        else:
            log_warning("'oem_id' not found", meter)
