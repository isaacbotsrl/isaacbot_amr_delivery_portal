"""Delivery tickets and their life cycle."""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Dict, FrozenSet, List, Optional

from .geo import GeoPoint


class TicketError(ValueError):
    """A request that breaks the ticket rules; the message goes back to the UI as is."""


class TicketStatus(str, Enum):
    NEW = 'NEW'                # created, no robot yet
    ASSIGNED = 'ASSIGNED'      # order sent to a robot
    EN_ROUTE = 'EN_ROUTE'      # the robot accepted it and is driving
    ARRIVED = 'ARRIVED'        # at the customer, waiting for the hand-off
    DELIVERED = 'DELIVERED'    # handed off (customer confirmed or staff marked it)
    RETURNING = 'RETURNING'    # driving back to the shop
    DONE = 'DONE'              # back at the shop
    FAILED = 'FAILED'
    CANCELLED = 'CANCELLED'


ACTIVE: FrozenSet[TicketStatus] = frozenset({
    TicketStatus.ASSIGNED, TicketStatus.EN_ROUTE, TicketStatus.ARRIVED,
    TicketStatus.DELIVERED, TicketStatus.RETURNING})
CLOSED: FrozenSet[TicketStatus] = frozenset({
    TicketStatus.DONE, TicketStatus.FAILED, TicketStatus.CANCELLED})

_ALLOWED: Dict[TicketStatus, FrozenSet[TicketStatus]] = {
    TicketStatus.NEW: frozenset({TicketStatus.ASSIGNED, TicketStatus.CANCELLED}),
    TicketStatus.ASSIGNED: frozenset({TicketStatus.EN_ROUTE, TicketStatus.ARRIVED,
                                      TicketStatus.FAILED, TicketStatus.CANCELLED}),
    TicketStatus.EN_ROUTE: frozenset({TicketStatus.ARRIVED, TicketStatus.FAILED,
                                      TicketStatus.CANCELLED}),
    TicketStatus.ARRIVED: frozenset({TicketStatus.DELIVERED, TicketStatus.FAILED,
                                     TicketStatus.CANCELLED}),
    TicketStatus.DELIVERED: frozenset({TicketStatus.RETURNING, TicketStatus.DONE,
                                       TicketStatus.FAILED}),
    TicketStatus.RETURNING: frozenset({TicketStatus.DONE, TicketStatus.FAILED}),
    TicketStatus.DONE: frozenset(),
    TicketStatus.FAILED: frozenset({TicketStatus.NEW}),       # retry with another robot
    TicketStatus.CANCELLED: frozenset({TicketStatus.NEW}),
}

_PHONE = re.compile(r'[0-9+()\- ]{0,32}')


@dataclass(frozen=True)
class TicketEvent:
    at: float
    status: TicketStatus
    note: str = ''


@dataclass(frozen=True)
class Ticket:
    id: str
    customer: str
    destination: GeoPoint
    address: str = ''
    phone: str = ''
    notes: str = ''
    status: TicketStatus = TicketStatus.NEW
    robot: str = ''                 # RMF/site-manager robot name
    order_id: str = ''              # current VDA 5050 order (outbound or return)
    outbound_order_id: str = ''
    return_order_id: str = ''
    destination_node_id: str = ''   # the outbound order's last node
    trip_action_id: str = ''        # the startTrip instant action sent before the order
    order_sent: bool = True         # False while startTrip runs (the order waits for it)
    route: List[GeoPoint] = field(default_factory=list)   # planned outbound route
    distance_m: float = 0.0
    eta_s: float = 0.0
    created_at: float = 0.0
    updated_at: float = 0.0
    history: List[TicketEvent] = field(default_factory=list)
    error: str = ''

    @staticmethod
    def create(customer: str, destination: GeoPoint, address: str = '', phone: str = '',
               notes: str = '', now: Optional[float] = None) -> 'Ticket':
        customer = (customer or '').strip()
        if not customer:
            raise TicketError('the customer name is required')
        if len(customer) > 120 or len(address) > 300 or len(notes) > 1000:
            raise TicketError('a field is too long')
        phone = (phone or '').strip()
        if not _PHONE.fullmatch(phone):
            raise TicketError(f'{phone!r} is not a phone number')
        now = time.time() if now is None else now
        return Ticket(id=uuid.uuid4().hex[:10], customer=customer, destination=destination,
                      address=address.strip(), phone=phone, notes=notes.strip(),
                      created_at=now, updated_at=now,
                      history=[TicketEvent(now, TicketStatus.NEW)])

    @property
    def active(self) -> bool:
        return self.status in ACTIVE

    def transition(self, status: TicketStatus, note: str = '',
                   now: Optional[float] = None, **changes) -> 'Ticket':
        if status == self.status:
            return replace(self, **changes) if changes else self
        if status not in _ALLOWED[self.status]:
            raise TicketError(f'ticket {self.id} cannot go from {self.status.value} '
                              f'to {status.value}')
        now = time.time() if now is None else now
        if status == TicketStatus.NEW:  # a retry: forget the previous robot and orders
            changes = dict(robot='', order_id='', outbound_order_id='', return_order_id='',
                           destination_node_id='', trip_action_id='', order_sent=True, error='',
                           **changes)
        return replace(self, status=status, updated_at=now,
                       history=[*self.history, TicketEvent(now, status, note)], **changes)
