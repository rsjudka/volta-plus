from collections import namedtuple
from datetime import datetime
from google.cloud import firestore
import json
import logging
import os.path
import pytz
from timezonefinder import TimezoneFinder
from urllib.request import urlopen


ChargingStatus = namedtuple('ChargingStatus', 'state availability')
Location = namedtuple('Location', 'city state zip tz')

db_cnt = 0

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

    class InUseStats:
        def __init__(self, start=None, cnt=0, avg=0):
            self.start = start
            self.cnt = cnt
            self.avg = avg

        def update_start(self, utc_time):
            self.start = utc_time

        def update_avg(self, utc_time):
            self.cnt += 1

            duration = (utc_time - self.start).total_seconds()
            self.avg += (duration - self.avg) / self.cnt

            self.start = None
        
        @property
        def is_charging(self):
            return self.start is not None

        def serialize(self):
            return {
                'start': self.start,
                'cnt': self.cnt,
                'avg': self.avg
            }

    def __init__(self, name, status, location):
        self.name = name
        self.status = status
        self.location = location

        self.charging_status = None

        self.in_use_charging_stats = self.InUseStats()
        self.in_use_idle_stats = self.InUseStats()

        self.last_weekly_usage_update = (0, 0)
        self.weekly_usage = [[0] * 144 for i in range(7)]

    # this is only for data from text file
    def set_stats(self, stats):
        self.in_use_charging_stats.cnt = stats['charge_cnt']
        self.in_use_charging_stats.avg = stats['avg_charge_duration']
        self.in_use_idle_stats.cnt = stats['in_use_idle_cnt']
        self.in_use_idle_stats.avg = stats['in_use_idle_avg_duration']
        for i in range(7):
            self.weekly_usage[i] = [max(stats['weekly_usage'][z*1440:(z*1440)+1440][y:y+10]) for y in range(0,1440,10)]

    def update(self, new_charging_status):
        updated = False

        if self.charging_status is None and new_charging_status not in self.available:
            return updated
        
        utc_time = datetime.utcnow()

        updated |= self.update_in_use_charging(new_charging_status, utc_time)
        updated |= self.update_in_use_idle(new_charging_status, utc_time)

        self.update_weekly_usage(new_charging_status, self.utc_to_local_time(utc_time))

        self.charging_status = new_charging_status

        return updated

    def update_in_use_charging(self, new_charging_status, utc_time):
        if not self.status_in_use(self.charging_status) and self.status_in_use(new_charging_status):
            self.in_use_charging_stats.update_start(utc_time)
            return True
        elif self.status_in_use(self.charging_status) and not self.status_in_use(new_charging_status):
            if self.in_use_charging_stats.is_charging:
                self.in_use_charging_stats.update_avg(utc_time)
                return True
            else:
                log_warning("charge start time is None when it shouldn't be", self.dump())
        
        return False

    def update_in_use_idle(self, new_charging_status, utc_time):
        if self.charging_status not in self.in_use_idle and new_charging_status in self.in_use_idle:
            self.in_use_idle_stats.update_start(utc_time)
            return True
        elif self.charging_status in self.in_use_idle and new_charging_status not in self.in_use_idle:
            if self.in_use_idle_stats.is_charging:
                self.in_use_idle_stats.update_avg(utc_time)
                return True
            else:
                log_warning("idle start time is None when it shouldn't be", self.dump())

        return False

    def update_weekly_usage(self, new_charging_status, local_time):
        weekly_usage_update = (local_time.weekday(), ((local_time.hour * 60) + local_time.minute) // 10)
        if self.status_in_use(new_charging_status) and weekly_usage_update != self.last_weekly_usage_update:
            self.weekly_usage[weekly_usage_update[0]][weekly_usage_update[1]] += 1
            self.last_weekly_usage_update = weekly_usage_update

    def status_in_use(self, charging_status):
        return charging_status in self.in_use_charging or charging_status in self.in_use_idle

    def utc_to_local_time(self, utc_time):
        return self.location.tz.fromutc(utc_time) if self.location.tz is not None else utc_time

    def serialize(self):
        return {
            'in_use_charging_stats': self.in_use_charging_stats.serialize(),
            'in_use_idle_stats': self.in_use_idle_stats.serialize(),
            'weekly_usage': self.weekly_usage
        }

    def dump(self):
        return {
            'name': self.name,
            'status': self.status,
            'location': self.location,
            'charging_status': self.charging_status,
            'in_use_charging_stats': self.in_use_charging_stats.serialize(),
            'in_use_idle_stats': self.in_use_idle_stats.serialize(),
            'last_weekly_usage_update': self.last_weekly_usage_update,
            'weekly_usage': self.weekly_usage,
        }

class VoltaStation:
    pass

class VoltaNetwork:
    API_URL = 'https://api.voltaapi.com/v1/public-sites'
    DATA_FILE = 'data.json'

    def __init__(self):
        self.meters = dict()

        self.tf = TimezoneFinder(in_memory=True)

        db = firestore.Client()
        self.conn = db.collection('meters')

        # self.init_data = None
        # if os.path.isfile(self.DATA_FILE):
        #     with open(self.DATA_FILE) as f:
        #         self.init_data = json.load(f)

    def get_meter(self, oem_id):
        return self.meters.get(oem_id, None)

    def update(self):
        with urlopen(self.API_URL) as url:
            data = json.loads(url.read().decode())
            for charger in data:
                stations = charger.get('stations', None)
                if stations is not None:
                    for station in stations:
                        self.parse_charger(station)
                else:
                    log_warning("'stations' array not found", charger)

    def parse_charger(self, station):
        meters = station.get('meters', None)
        if meters is not None:
            city = station.get('city', None)
            state = station.get('state', None)
            zip_code = int(station['zip_code']) if 'zip_code' in station else None
            timezone = self.find_timezone(station)
            location = Location(city, state, zip_code, timezone)

            for meter in meters:
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
            volta_meter = self.meters.get(oem_id, None)
            if volta_meter is None:
                # datetime.strptime(meter['state_updated_at'], '%Y-%m-%dT%H:%M:%S.%f')
                volta_meter = VoltaMeter(name, status, location)
                # **** new firestore way (need to update set_stats for this to work)
                # init_data = self.conn.document(oem_id).get().to_dict()
                # if init_data is not None:
                #     volta_meter.set_stats(init_data)
                # **** old text file way
                # if self.init_data is not None and oem_id in self.init_data:
                #     volta_meter.set_stats(self.init_data[oem_id])

                self.meters[oem_id] = volta_meter

            state = meter['state'].lower() if 'state' in meter else None
            availability = meter['availability'].lower() if 'availability' in meter else None

            if volta_meter.update(ChargingStatus(state, availability)):
                global db_cnt
                print("would have wrote to db {} for {}".format(db_cnt, oem_id))
                db_cnt += 1
                # self.conn.document(oem_id).set(volta_meter.serialize())

            self.meters[oem_id] = volta_meter
        else:
            log_warning("'oem_id' not found", meter)
