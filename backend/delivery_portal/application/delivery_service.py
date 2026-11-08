"""
The delivery use cases: tickets, route planning, dispatch to robots and following their progress.

Every mutation goes through one lock: MQTT state callbacks arrive on the MQTT thread, HTTP
requests on the server's.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

from ..domain.eta import EtaEstimator
from ..domain.geo import EnuDatum, GeoPoint
from ..domain.order_builder import (BuiltOrder, OrderSettings, build_order, instant_action,
                                    with_header)
from ..domain.ports import (FleetSource, GeocodeResult, Geocoder, OrderPublisher, RoutedPath,
                            RoutingError, RoutingProvider, SettingsRepository, TicketRepository)
from ..domain.robots import Robot, RobotStatus, parse_state
from ..domain.settings import DeliverySettings
from ..domain.tickets import Ticket, TicketError, TicketStatus

log = logging.getLogger(__name__)

STALE_S = 15.0   # no state for this long: the robot counts as offline


class NotFound(TicketError):
    """No such ticket or robot."""


@dataclass(frozen=True)
class Plan:
    robot: str
    start: GeoPoint
    start_source: str            # 'slam' (aligned pose) / 'gps' / 'shop'
    route: RoutedPath
    built: BuiltOrder
    waypoints: List[GeoPoint]
    eta_s: float
    order_preview: Dict          # the full VDA 5050 message as it would be sent


@dataclass(frozen=True)
class Settings:
    order: OrderSettings = OrderSettings()
    eta: EtaEstimator = EtaEstimator()
    auto_return: bool = True     # send the robot home as soon as the ticket is delivered
    # Send startTrip first (the robot maps afresh and aligns to GPS) and the order once it
    # finished. Off for robots without the delivery adapter (map-frame orders, simulation).
    start_trip: bool = True


class DeliveryService:
    def __init__(self, datum: EnuDatum, shop: GeoPoint, router: RoutingProvider,
                 geocoder: Optional[Geocoder], fleet: FleetSource, publisher: OrderPublisher,
                 tickets: TicketRepository, settings: Settings = Settings(),
                 clock: Callable[[], float] = time.time,
                 parked_heading_deg: Optional[float] = None,
                 settings_store: Optional[SettingsRepository] = None,
                 enu_map_id: Optional[str] = None):
        self.datum, self.shop = datum, shop
        # The configured base name (the datum's map id carries the location fingerprint).
        self.enu_map_id = enu_map_id or datum.map_id
        self.parked_heading_deg = parked_heading_deg
        self.settings_store = settings_store
        self.router, self.geocoder, self.fleet = router, geocoder, fleet
        self.publisher, self.tickets = publisher, tickets
        self.settings, self.clock = settings, clock
        self._lock = threading.RLock()
        self._robots: Dict[str, Robot] = {}
        self.fleet_error = ''

    # --- settings ------------------------------------------------------------------------

    def delivery_settings(self) -> DeliverySettings:
        with self._lock:
            return DeliverySettings(
                datum=self.datum.origin, shop=self.shop, enu_map_id=self.enu_map_id,
                parked_heading_deg=self.parked_heading_deg,
                cargo_actions=self.settings.order.cargo_actions,
                auto_return=self.settings.auto_return)

    def update_settings(self, new: DeliverySettings) -> DeliverySettings:
        """
        Change the pizzeria location and delivery options. Refused while deliveries run: their
        orders are in the old frame. Robots get the new frame with their next startTrip.
        """
        with self._lock:
            active = [t for t in self.tickets.list() if t.active]
            if active:
                raise TicketError(f'finish or cancel the {len(active)} active '
                                  f'deliver{"y" if len(active) == 1 else "ies"} first: '
                                  'they run in the old location\'s frame')
            self.datum, self.shop = new.enu, new.shop
            self.enu_map_id = new.enu_map_id
            self.parked_heading_deg = new.parked_heading_deg
            self.settings = replace(
                self.settings, order=replace(self.settings.order, cargo_actions=new.cargo_actions),
                auto_return=new.auto_return)
            if self.settings_store is not None:
                self.settings_store.save(new.to_dict())
            log.info('settings changed: pizzeria %s, frame %s', new.datum, new.frame_id)
            return new

    def _trip_parameters(self) -> Dict:
        """startTrip's parameters: the frame the robot aligns its fresh map to."""
        params = {'datum_lat': self.datum.origin.lat, 'datum_lon': self.datum.origin.lon,
                  'enu_map_id': self.datum.map_id}
        if self.parked_heading_deg is not None:
            params['parked_heading_deg'] = self.parked_heading_deg
        return params

    # --- fleet ---------------------------------------------------------------------------

    def refresh_fleet(self) -> None:
        """Re-read the robot list (site manager); robots keep their live state."""
        try:
            identities = self.fleet.robots()
        except Exception as e:  # noqa: B902 site manager down: keep the last list
            self.fleet_error = f'{type(e).__name__}: {e}'
            log.warning('fleet refresh failed: %s', self.fleet_error)
            return
        with self._lock:
            self.fleet_error = ''
            self._robots = {
                i.name: replace(self._robots[i.name], identity=i) if i.name in self._robots
                else Robot(i)
                for i in identities}

    def robots(self) -> List[Robot]:
        now = self.clock()
        with self._lock:
            out = []
            for r in self._robots.values():
                if r.status.received_at and now - r.status.received_at > STALE_S \
                        and r.status.connection == 'ONLINE':
                    r = r.with_connection('CONNECTIONBROKEN')
                out.append(r)
            return sorted(out, key=lambda r: r.name)

    def robot(self, name: str) -> Robot:
        with self._lock:
            robot = self._robots.get(name)
        if robot is None:
            raise NotFound(f'no robot {name!r}')
        return robot

    def _by_serial(self, manufacturer: str, serial: str) -> Optional[Robot]:
        return next((r for r in self._robots.values()
                     if r.identity.serial_number == serial
                     and r.identity.manufacturer == manufacturer), None)

    # --- robot messages (MQTT thread) ------------------------------------------------------

    def on_state(self, manufacturer: str, serial: str, state: Dict) -> None:
        with self._lock:
            robot = self._by_serial(manufacturer, serial)
            if robot is None:
                return
            status = parse_state(state, self.datum, self.clock(), robot.status)
            robot = robot.with_status(status)
            self._robots[robot.name] = robot
            for ticket in self.tickets.list():
                if ticket.robot == robot.name and ticket.active:
                    self._follow(ticket, robot)

    def on_connection(self, manufacturer: str, serial: str, connection: Dict) -> None:
        with self._lock:
            robot = self._by_serial(manufacturer, serial)
            if robot is not None:
                state = str(connection.get('connectionState') or '')
                self._robots[robot.name] = replace(
                    robot.with_connection(state),
                    status=replace(robot.status, connection=state,
                                   received_at=robot.status.received_at or self.clock()))

    def _follow(self, ticket: Ticket, robot: Robot) -> None:
        """Advance a ticket from its robot's state."""
        s: RobotStatus = robot.status
        if not ticket.order_sent:
            self._follow_trip_start(ticket, robot)
            return
        if s.order_id != ticket.order_id or not ticket.order_id:
            return
        fatal = [e for e in s.errors if e['level'] == 'FATAL']
        if fatal and ticket.error != fatal[0]['description']:
            ticket = replace(ticket, error=f"{fatal[0]['type']}: {fatal[0]['description']}")
            self.tickets.save(ticket)
        outbound = ticket.order_id == ticket.outbound_order_id
        if outbound:
            arrived = (s.last_node_id == ticket.destination_node_id)
            if ticket.status == TicketStatus.ASSIGNED and (s.driving or s.last_node_sequence_id
                                                           > 0 or arrived):
                ticket = self._move(ticket, TicketStatus.EN_ROUTE if not arrived
                                    else TicketStatus.ARRIVED)
            if ticket.status == TicketStatus.EN_ROUTE and arrived:
                ticket = self._move(ticket, TicketStatus.ARRIVED)
            pickup = s.action(f'{ticket.order_id}-confirmPickup')
            if pickup and ticket.status == TicketStatus.ARRIVED:
                if pickup.status == 'FINISHED':
                    ticket = self._move(ticket, TicketStatus.DELIVERED, 'customer confirmed')
                    if self.settings.auto_return:
                        self._send_return(ticket, robot)
                elif pickup.status == 'FAILED':
                    ticket = replace(ticket, error=f'confirmPickup failed: {pickup.result}')
                    self.tickets.save(ticket)
        elif ticket.status == TicketStatus.RETURNING:
            if not s.remaining_node_ids and s.last_node_id == 'shop':
                self._move(ticket, TicketStatus.DONE, 'back at the shop')

    def _follow_trip_start(self, ticket: Ticket, robot: Robot) -> None:
        """startTrip finished: the robot maps afresh and is aligned, so the order goes out now."""
        action = robot.status.action(ticket.trip_action_id)
        if action is None or action.status not in ('FINISHED', 'FAILED'):
            return
        if action.status == 'FAILED':
            self._move(ticket, TicketStatus.FAILED, 'the trip could not start',
                       error=f'startTrip failed: {action.result or "no reason given"}')
            return
        try:
            plan = self._plan_to(robot, ticket.destination, ticket.order_id)
            self.publisher.publish_order(robot.identity, plan.built.order)
        except Exception as e:  # noqa: B902 routing or broker: the ticket shows why
            self._move(ticket, TicketStatus.FAILED, 'the order could not be sent',
                       error=f'{type(e).__name__}: {e}')
            return
        ticket = replace(ticket, order_sent=True, route=plan.waypoints,
                         destination_node_id=plan.built.destination_node_id,
                         distance_m=plan.built.distance_m, eta_s=plan.eta_s)
        self.tickets.save(ticket)
        log.info('ticket %s: trip started, order %s sent', ticket.id, ticket.order_id)

    def _move(self, ticket: Ticket, status: TicketStatus, note: str = '', **changes) -> Ticket:
        ticket = ticket.transition(status, note, now=self.clock(), **changes)
        self.tickets.save(ticket)
        log.info('ticket %s -> %s %s', ticket.id, status.value, note)
        return ticket

    # --- tickets ---------------------------------------------------------------------------

    def create_ticket(self, customer: str, destination: GeoPoint, address: str = '',
                      phone: str = '', notes: str = '') -> Ticket:
        ticket = Ticket.create(customer, destination, address, phone, notes, now=self.clock())
        ticket = self._with_shop_route(ticket)
        with self._lock:
            self.tickets.save(ticket)
        return ticket

    def _with_shop_route(self, ticket: Ticket) -> Ticket:
        """The walking route from the pizzeria, so the map shows it right away (best effort)."""
        try:
            route = self.router.route(self.shop, ticket.destination)
            points = list(route.points) or [ticket.destination]
            if (points[-1].lat, points[-1].lon) != (ticket.destination.lat,
                                                    ticket.destination.lon):
                points.append(ticket.destination)
            built = build_order('preview', self.datum, self.datum.to_enu(self.shop), points,
                                self.settings.order, with_cargo=False)
        except Exception as e:  # noqa: B902 router down: the ticket still exists, no route yet
            log.warning('no route for new ticket %s: %s', ticket.id, e)
            return replace(ticket, error=f'No walking route yet: {e}')
        return replace(ticket, route=[self.datum.to_geo(p) for p in built.waypoints],
                       distance_m=built.distance_m,
                       eta_s=self.settings.eta.trip_s(built.distance_m))

    def ticket(self, ticket_id: str) -> Ticket:
        ticket = self.tickets.get(ticket_id)
        if ticket is None:
            raise NotFound(f'no ticket {ticket_id!r}')
        return ticket

    def list_tickets(self, closed_hours: float = 24.0) -> List[Ticket]:
        return sorted(self.tickets.list(self.clock() - closed_hours * 3600),
                      key=lambda t: t.created_at, reverse=True)

    def geocode(self, query: str) -> List[GeocodeResult]:
        if not self.geocoder:
            raise TicketError('no geocoder configured: click the map instead')
        query = (query or '').strip()
        if len(query) < 3:
            raise TicketError('type at least 3 characters')
        return self.geocoder.search(query, near=self.shop)

    # --- planning and dispatch -------------------------------------------------------------

    def _start_of(self, robot: Robot):
        """(ENU start, lat/lon start, source, theta) for a robot's next order."""
        s = robot.status
        if s.position_enu is not None:
            return s.position_enu, s.position, 'slam', s.theta
        if s.gps is not None:
            return self.datum.to_enu(s.gps), s.gps, 'gps', None
        return self.datum.to_enu(self.shop), self.shop, 'shop', None

    def _plan_to(self, robot: Robot, destination: GeoPoint, order_id: str,
                 cargo: Optional[bool] = None, end_node_id: str = '') -> Plan:
        start_enu, start_geo, source, theta = self._start_of(robot)
        try:
            route = self.router.route(start_geo, destination)
        except RoutingError:
            raise
        except Exception as e:  # noqa: B902 any adapter failure is a routing failure here
            raise RoutingError(f'{type(e).__name__}: {e}') from e
        points = list(route.points) or [destination]
        if (points[-1].lat, points[-1].lon) != (destination.lat, destination.lon):
            points.append(destination)  # the router snaps to the path; the door is the goal
        built = build_order(order_id, self.datum, start_enu, points, self.settings.order,
                            start_theta=theta, with_cargo=cargo)
        if end_node_id:
            built = _rename_last(built, end_node_id)
        waypoints = [self.datum.to_geo(p) for p in built.waypoints]
        preview = with_header(built.order, robot.identity.manufacturer,
                              robot.identity.serial_number, 0,
                              datetime.now(timezone.utc).isoformat(timespec='milliseconds'))
        return Plan(robot=robot.name, start=start_geo, start_source=source, route=route,
                    built=built, waypoints=waypoints,
                    eta_s=self.settings.eta.trip_s(built.distance_m), order_preview=preview)

    def plan(self, ticket_id: str, robot_name: str) -> Plan:
        ticket = self.ticket(ticket_id)
        robot = self.robot(robot_name)
        return self._plan_to(robot, ticket.destination, _order_id(ticket, 'o'))

    def _check_dispatchable(self, robot: Robot, ticket_id: str = '') -> None:
        if not robot.identity.delivery:
            raise TicketError(f'{robot.name} is not a delivery robot (RMF commands it)')
        if not robot.online:
            raise TicketError(f'{robot.name} is offline')
        if robot.stopped_by_safety:
            raise TicketError(f'{robot.name} is emergency-stopped ({robot.status.e_stop})')
        busy = [t for t in self.tickets.list() if t.robot == robot.name and t.active
                and t.id != ticket_id]
        if busy:
            raise TicketError(f'{robot.name} is busy with ticket {busy[0].id}')

    def assign(self, ticket_id: str, robot_name: str) -> Ticket:
        with self._lock:
            ticket = self.ticket(ticket_id)
            if ticket.status != TicketStatus.NEW:
                raise TicketError(f'ticket {ticket.id} is {ticket.status.value}, not NEW')
            robot = self.robot(robot_name)
            self._check_dispatchable(robot, ticket.id)
            # Planned now (route check, ETA); with startTrip, re-planned once the robot is ready.
            plan = self._plan_to(robot, ticket.destination, _order_id(ticket, 'o'))
            order_id = plan.built.order['orderId']
            trip_action_id = ''
            if self.settings.start_trip:
                trip_action_id = f'{order_id}-startTrip'
                self.publisher.publish_instant_actions(
                    robot.identity,
                    instant_action('startTrip', trip_action_id, self._trip_parameters()))
                note = f'{robot.name}: starting a fresh map'
            else:
                self.publisher.publish_order(robot.identity, plan.built.order)
                note = f'{robot.name}, {plan.built.distance_m:.0f} m'
            return self._move(
                ticket, TicketStatus.ASSIGNED, note,
                robot=robot.name, order_id=order_id, outbound_order_id=order_id,
                destination_node_id=plan.built.destination_node_id, route=plan.waypoints,
                distance_m=plan.built.distance_m, eta_s=plan.eta_s, error='',
                trip_action_id=trip_action_id, order_sent=not self.settings.start_trip)

    def _send_return(self, ticket: Ticket, robot: Robot) -> Ticket:
        order_id = _order_id(ticket, 'r')
        try:
            plan = self._plan_to(robot, self.shop, order_id, cargo=False, end_node_id='shop')
            order = plan.built.order
        except RoutingError as e:  # retrace the outbound route instead
            log.warning('return route for %s failed (%s): retracing', ticket.id, e)
            back = list(reversed(ticket.route))[1:] + [self.shop]
            start_enu = self._start_of(robot)[0]
            order = _rename_last(build_order(order_id, self.datum, start_enu, back,
                                             self.settings.order, with_cargo=False),
                                 'shop').order
        self.publisher.publish_order(robot.identity, order)
        return self._move(ticket, TicketStatus.RETURNING, 'returning to the shop',
                          order_id=order_id, return_order_id=order_id)

    def mark_delivered(self, ticket_id: str) -> Ticket:
        """Staff confirm the hand-off (robots without a cargo box, or an override)."""
        with self._lock:
            ticket = self.ticket(ticket_id)
            if ticket.status != TicketStatus.ARRIVED:
                raise TicketError(f'ticket {ticket.id} is {ticket.status.value}, not ARRIVED')
            ticket = self._move(ticket, TicketStatus.DELIVERED, 'marked delivered by staff')
            if self.settings.auto_return:
                ticket = self._send_return(ticket, self.robot(ticket.robot))
            return ticket

    def send_back(self, ticket_id: str) -> Ticket:
        """Return to the shop now (after DELIVERED when auto-return is off)."""
        with self._lock:
            ticket = self.ticket(ticket_id)
            if ticket.status != TicketStatus.DELIVERED:
                raise TicketError(f'ticket {ticket.id} is {ticket.status.value}, not DELIVERED')
            return self._send_return(ticket, self.robot(ticket.robot))

    def cancel(self, ticket_id: str, reason: str = 'cancelled by staff') -> Ticket:
        with self._lock:
            ticket = self.ticket(ticket_id)
            ticket.transition(TicketStatus.CANCELLED)  # raises before anything is sent
            if ticket.active and ticket.robot:
                self._instant(ticket.robot, 'cancelOrder')
            return self._move(ticket, TicketStatus.CANCELLED, reason)

    def fail(self, ticket_id: str, reason: str) -> Ticket:
        with self._lock:
            ticket = self.ticket(ticket_id)
            ticket.transition(TicketStatus.FAILED)  # raises before anything is sent
            if ticket.active and ticket.robot:
                self._instant(ticket.robot, 'cancelOrder')
            return self._move(ticket, TicketStatus.FAILED, reason or 'failed', error=reason)

    def retry(self, ticket_id: str) -> Ticket:
        with self._lock:
            return self._move(self.ticket(ticket_id), TicketStatus.NEW, 'retry')

    # --- robot commands --------------------------------------------------------------------

    def _instant(self, robot_name: str, action_type: str) -> None:
        robot = self.robot(robot_name)
        self.publisher.publish_instant_actions(
            robot.identity, instant_action(action_type, f'{action_type}-{uuid.uuid4().hex[:8]}'))

    def pause(self, robot_name: str) -> None:
        self._instant(robot_name, 'startPause')

    def resume(self, robot_name: str) -> None:
        self._instant(robot_name, 'stopPause')

    def return_robot(self, robot_name: str) -> Dict:
        """Send an idle robot back to the shop (no ticket)."""
        with self._lock:
            robot = self.robot(robot_name)
            self._check_dispatchable(robot)
            plan = self._plan_to(robot, self.shop, f'home-{uuid.uuid4().hex[:8]}', cargo=False,
                                 end_node_id='shop')
            self.publisher.publish_order(robot.identity, plan.built.order)
            return plan.built.order

    # --- monitoring ------------------------------------------------------------------------

    def remaining_s(self, ticket: Ticket) -> Optional[float]:
        """Time left to the customer (outbound) or the shop (return); None when unknown."""
        if not ticket.active or not ticket.robot:
            return None
        robot = self._robots.get(ticket.robot)
        if robot is None or robot.status.order_id != ticket.order_id:
            return ticket.eta_s if ticket.status == TicketStatus.ASSIGNED else None
        if ticket.order_id != ticket.outbound_order_id:
            return None  # the return route is not stored; the UI shows the trail
        waypoints = [self.datum.to_enu(p) for p in ticket.route]
        reached = _node_index(robot.status.last_node_id, len(waypoints))
        return self.settings.eta.remaining_s(waypoints, reached, robot.status.position_enu)


def _order_id(ticket: Ticket, leg: str) -> str:
    attempt = sum(1 for e in ticket.history if e.status == TicketStatus.ASSIGNED) + (
        1 if leg == 'o' else 0)
    return f'pizza-{ticket.id}-{attempt}{leg}'


def _node_index(node_id: str, count: int) -> int:
    if node_id == 'destination':
        return count - 1
    if node_id.startswith('w') and node_id[1:].isdigit():
        return int(node_id[1:])
    return 0


def _rename_last(built: BuiltOrder, node_id: str) -> BuiltOrder:
    """The order with its last node renamed (e.g. 'shop' for return orders)."""
    order = dict(built.order)
    nodes = [dict(n) for n in order['nodes']]
    edges = [dict(e) for e in order['edges']]
    old = nodes[-1]['nodeId']
    nodes[-1]['nodeId'] = node_id
    if edges and edges[-1]['endNodeId'] == old:
        edges[-1]['endNodeId'] = node_id
    order.update(nodes=nodes, edges=edges)
    return replace(built, order=order, destination_node_id=node_id)

