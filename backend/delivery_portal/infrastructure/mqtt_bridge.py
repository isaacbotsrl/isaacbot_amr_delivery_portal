"""
VDA 5050 over MQTT (paho-mqtt 2.x): publishes orders / instantActions with the protocol header,
and feeds the robots' state / connection messages to the delivery service.

Topics: uagv/v2/<manufacturer>/<serialNumber>/{order,instantActions,state,connection}.
QoS 0 for state (high rate), 1 for orders and instant actions, as VDA 5050 recommends.
"""

from __future__ import annotations

import itertools
import json
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Callable, Dict

import paho.mqtt.client as mqtt

from ..domain.order_builder import with_header
from ..domain.ports import OrderPublisher
from ..domain.robots import RobotIdentity

log = logging.getLogger(__name__)

Handler = Callable[[str, str, Dict], None]   # (manufacturer, serial, payload)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


class MqttBridge(OrderPublisher):
    def __init__(self, host: str, port: int = 1883, username: str = '', password: str = '',
                 client_id: str = 'isaacbot-delivery-portal'):
        self.host, self.port = host, port
        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
        if username:
            self._client.username_pw_set(username, password)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message
        self._header_ids: Dict[str, itertools.count] = {}
        self._ids_lock = threading.Lock()
        self.on_state: Handler = lambda *a: None
        self.on_connection: Handler = lambda *a: None
        self.connected = False
        self.last_error = ''

    def start(self) -> None:
        self._client.reconnect_delay_set(1, 30)
        self._client.connect_async(self.host, self.port, keepalive=30)
        self._client.loop_start()

    def stop(self) -> None:
        self._client.loop_stop()
        self._client.disconnect()

    # --- inbound ---------------------------------------------------------------------------

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code.is_failure:
            self.connected, self.last_error = False, f'broker refused: {reason_code}'
            log.error(self.last_error)
            return
        self.connected, self.last_error = True, ''
        client.subscribe([('uagv/v2/+/+/state', 0), ('uagv/v2/+/+/connection', 1)])
        log.info('MQTT connected to %s:%s', self.host, self.port)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties=None):
        self.connected = False
        if reason_code != 0:
            self.last_error = f'disconnected: {reason_code}'
            log.warning('MQTT %s', self.last_error)

    def _on_message(self, client, userdata, msg):
        parts = msg.topic.split('/')
        if len(parts) != 5:
            return
        _, _, manufacturer, serial, kind = parts
        try:
            payload = json.loads(msg.payload.decode())
        except (UnicodeDecodeError, json.JSONDecodeError):
            log.warning('not JSON on %s', msg.topic)
            return
        if not isinstance(payload, dict):
            return
        try:
            if kind == 'state':
                self.on_state(manufacturer, serial, payload)
            elif kind == 'connection':
                self.on_connection(manufacturer, serial, payload)
        except Exception:  # noqa: B902 one bad message must not kill the MQTT thread
            log.exception('handling %s', msg.topic)

    # --- outbound --------------------------------------------------------------------------

    def _next_header(self, topic: str) -> int:
        with self._ids_lock:
            # Seeded with the clock so ids keep increasing across portal restarts (uint32).
            return next(self._header_ids.setdefault(
                topic, itertools.count(int(time.time()) % 4_000_000_000)))

    def _publish(self, robot: RobotIdentity, kind: str, body: Dict) -> None:
        if not self.connected:
            raise ConnectionError(f'not connected to the MQTT broker {self.host}:{self.port} '
                                  f'{self.last_error}'.strip())
        topic = f'{robot.topic_prefix}/{kind}'
        message = with_header(body, robot.manufacturer, robot.serial_number,
                              self._next_header(topic), _now())
        info = self._client.publish(topic, json.dumps(message), qos=1)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            raise ConnectionError(f'publish to {topic} failed: {mqtt.error_string(info.rc)}')

    def publish_order(self, robot: RobotIdentity, order: Dict) -> None:
        self._publish(robot, 'order', order)

    def publish_instant_actions(self, robot: RobotIdentity, actions: Dict) -> None:
        self._publish(robot, 'instantActions', actions)
