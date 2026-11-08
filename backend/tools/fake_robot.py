#!/usr/bin/env python3
"""
A fake VDA 5050 2.0 robot for trying the portal without hardware.

It drives every order's nodes in a straight line at --speed, publishing `state` (agvPosition in
the shared ENU frame, as the real adapter does once aligned, plus a noisy gpsFix information
entry) and `connection`. It honours cancelOrder, startPause / stopPause and startTrip (FINISHED
after 3 s, as the real one once its fresh SLAM map is aligned to GPS), and finishes the cargo
actions (confirmPickup after --pickup-s seconds) when an order carries them.

    python tools/fake_robot.py --host 127.0.0.1 --serial SIM001 --datum 44.4268,26.1025
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import threading
import time
from datetime import datetime, timezone

import paho.mqtt.client as mqtt

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from delivery_portal.domain.geo import EnuDatum, EnuPoint, GeoPoint  # noqa: E402


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


class FakeRobot:
    def __init__(self, args):
        lat, lon = (float(v) for v in args.datum.split(','))
        self.datum = EnuDatum(GeoPoint(lat, lon), args.map_id)
        self.args = args
        self.prefix = f'uagv/v2/{args.manufacturer}/{args.serial}'
        self.lock = threading.Lock()
        self.x, self.y, self.theta = args.start_x, args.start_y, 0.0
        self.order_id, self.nodes, self.last_node, self.last_seq = '', [], '', 0
        self.actions = {}            # actionId -> [type, status, started_at]
        self.paused, self.header = False, 0
        self.battery = 95.0
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f'fake-{args.serial}')
        if args.user:
            self.client.username_pw_set(args.user, args.password)
        self.client.will_set(f'{self.prefix}/connection',
                             json.dumps(self._conn('CONNECTIONBROKEN')), qos=1, retain=True)
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message

    def _conn(self, state):
        self.header += 1
        return {'headerId': self.header, 'timestamp': now(), 'version': '2.0.0',
                'manufacturer': self.args.manufacturer, 'serialNumber': self.args.serial,
                'connectionState': state}

    def on_connect(self, client, userdata, flags, rc, properties=None):
        client.subscribe([(f'{self.prefix}/order', 1), (f'{self.prefix}/instantActions', 1)])
        client.publish(f'{self.prefix}/connection', json.dumps(self._conn('ONLINE')), 1, True)
        print(f'{self.args.serial}: online at ENU ({self.x:.1f}, {self.y:.1f})', flush=True)

    def on_message(self, client, userdata, msg):
        body = json.loads(msg.payload)
        with self.lock:
            if msg.topic.endswith('/order'):
                self.order_id = body['orderId']
                self.nodes = [n for n in body['nodes'] if n['sequenceId'] > 0]
                self.last_node, self.last_seq = body['nodes'][0]['nodeId'], 0
                self.actions = {k: v for k, v in self.actions.items() if v[0] == 'startTrip'}
                self.actions.update({a['actionId']: [a['actionType'], 'WAITING', 0.0]
                                     for n in body['nodes'] for a in n.get('actions', [])})
                print(f'{self.args.serial}: order {self.order_id}, {len(self.nodes)} nodes',
                      flush=True)
            else:
                for a in body.get('actions', []):
                    t = a['actionType']
                    if t == 'startTrip':
                        self.actions[a['actionId']] = [t, 'RUNNING', time.time()]
                    if t == 'cancelOrder':
                        self.nodes = []
                    elif t == 'startPause':
                        self.paused = True
                    elif t == 'stopPause':
                        self.paused = False
                    print(f'{self.args.serial}: {t}', flush=True)

    def step(self, dt):
        with self.lock:
            for entry in self.actions.values():
                if entry[0] == 'startTrip' and entry[1] == 'RUNNING' and time.time() - entry[2] > 3:
                    entry[1] = 'FINISHED'

            if self.nodes and not self.paused:
                target = self.nodes[0]
                tx, ty = target['nodePosition']['x'], target['nodePosition']['y']
                d = math.hypot(tx - self.x, ty - self.y)
                move = self.args.speed * dt
                if d > 1e-3:
                    self.theta = math.atan2(ty - self.y, tx - self.x)
                if d <= move:
                    self.x, self.y = tx, ty
                    self.last_node, self.last_seq = target['nodeId'], target['sequenceId']
                    if target.get('actions'):
                        self.run_actions(target)
                        if any(self.actions[a['actionId']][1] != 'FINISHED'
                               for a in target['actions']):
                            return  # hold at the node until its actions finish
                    self.nodes.pop(0)
                else:
                    self.x += (tx - self.x) / d * move
                    self.y += (ty - self.y) / d * move
                self.battery = max(5.0, self.battery - 0.002)

    def run_actions(self, node):
        t = time.time()
        for a in node['actions']:
            entry = self.actions[a['actionId']]
            if entry[1] == 'FINISHED':
                continue
            if entry[1] == 'WAITING':
                entry[1], entry[2] = 'RUNNING', t
            wait = self.args.pickup_s if entry[0] == 'confirmPickup' else 1.0
            if t - entry[2] >= wait:
                entry[1] = 'FINISHED'
            return  # actions run one after another

    def state(self):
        with self.lock:
            gps = self.datum.to_geo(EnuPoint(self.x + random.gauss(0, 1.5),
                                             self.y + random.gauss(0, 1.5)))
            self.header += 1
            return {
                'headerId': self.header, 'timestamp': now(), 'version': '2.0.0',
                'manufacturer': self.args.manufacturer, 'serialNumber': self.args.serial,
                'orderId': self.order_id, 'orderUpdateId': 0,
                'lastNodeId': self.last_node, 'lastNodeSequenceId': self.last_seq,
                'driving': bool(self.nodes) and not self.paused, 'paused': self.paused,
                'operatingMode': 'AUTOMATIC', 'newBaseRequest': False,
                'nodeStates': [{'nodeId': n['nodeId'], 'sequenceId': n['sequenceId'],
                                'released': True} for n in self.nodes],
                'edgeStates': [],
                'agvPosition': {'x': round(self.x, 3), 'y': round(self.y, 3),
                                'theta': round(self.theta, 4), 'mapId': self.datum.map_id,
                                'positionInitialized': True, 'localizationScore': 0.95},
                'velocity': {'vx': self.args.speed if self.nodes else 0.0},
                'batteryState': {'batteryCharge': round(self.battery, 1), 'charging': False},
                'safetyState': {'eStop': 'NONE', 'fieldViolation': False},
                'actionStates': [{'actionId': k, 'actionType': v[0], 'actionStatus': v[1]}
                                 for k, v in self.actions.items()],
                'errors': [],
                'information': [{'infoType': 'gpsFix', 'infoLevel': 'INFO', 'infoReferences': [
                    {'referenceKey': 'lat', 'referenceValue': f'{gps.lat:.7f}'},
                    {'referenceKey': 'lon', 'referenceValue': f'{gps.lon:.7f}'},
                    {'referenceKey': 'fixType', 'referenceValue': 'GPS'},
                    {'referenceKey': 'hdop', 'referenceValue': '1.2'}]}],
            }

    def run(self):
        self.client.connect(self.args.host, self.args.port)
        self.client.loop_start()
        period = 1.0 / self.args.rate
        while True:
            self.step(period * self.args.time_scale)
            self.client.publish(f'{self.prefix}/state', json.dumps(self.state()), 0)
            time.sleep(period)


def main():
    p = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    p.add_argument('--host', default=os.environ.get('MQTT_HOST', '127.0.0.1'))
    p.add_argument('--port', type=int, default=int(os.environ.get('MQTT_PORT', '1883')))
    p.add_argument('--user', default=os.environ.get('MQTT_USER', ''))
    p.add_argument('--password', default=os.environ.get('MQTT_PASSWORD', ''))
    p.add_argument('--manufacturer', default='IsaacBot')
    p.add_argument('--serial', default='SIM001')
    p.add_argument('--datum', default=f"{os.environ.get('DATUM_LAT', '44.4268')},"
                                      f"{os.environ.get('DATUM_LON', '26.1025')}")
    p.add_argument('--map-id', default=os.environ.get('ENU_MAP_ID', 'enu_site'))
    p.add_argument('--start-x', type=float, default=0.0)
    p.add_argument('--start-y', type=float, default=0.0)
    p.add_argument('--speed', type=float, default=0.8)
    p.add_argument('--time-scale', type=float, default=1.0, help='>1 drives faster than real time')
    p.add_argument('--rate', type=float, default=2.0, help='state messages per second')
    p.add_argument('--pickup-s', type=float, default=20.0)
    FakeRobot(p.parse_args()).run()


if __name__ == '__main__':
    main()
