"""Tickets in SQLite (one row per ticket, the ticket as JSON). Stdlib only."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from typing import List, Optional

from ..domain.geo import GeoPoint
from ..domain.ports import TicketRepository
from ..domain.tickets import CLOSED, Ticket, TicketEvent, TicketStatus


def ticket_to_dict(t: Ticket) -> dict:
    return {
        'id': t.id, 'customer': t.customer, 'address': t.address, 'phone': t.phone,
        'notes': t.notes, 'destination': [t.destination.lat, t.destination.lon],
        'status': t.status.value, 'robot': t.robot, 'order_id': t.order_id,
        'outbound_order_id': t.outbound_order_id, 'return_order_id': t.return_order_id,
        'destination_node_id': t.destination_node_id,
        'route': [[p.lat, p.lon] for p in t.route], 'distance_m': t.distance_m,
        'eta_s': t.eta_s, 'created_at': t.created_at, 'updated_at': t.updated_at,
        'history': [{'at': e.at, 'status': e.status.value, 'note': e.note} for e in t.history],
        'error': t.error,
    }


def ticket_from_dict(d: dict) -> Ticket:
    return Ticket(
        id=d['id'], customer=d['customer'], address=d.get('address', ''),
        phone=d.get('phone', ''), notes=d.get('notes', ''),
        destination=GeoPoint(*d['destination']), status=TicketStatus(d['status']),
        robot=d.get('robot', ''), order_id=d.get('order_id', ''),
        outbound_order_id=d.get('outbound_order_id', ''),
        return_order_id=d.get('return_order_id', ''),
        destination_node_id=d.get('destination_node_id', ''),
        route=[GeoPoint(*p) for p in d.get('route', [])], distance_m=d.get('distance_m', 0.0),
        eta_s=d.get('eta_s', 0.0), created_at=d.get('created_at', 0.0),
        updated_at=d.get('updated_at', 0.0),
        history=[TicketEvent(e['at'], TicketStatus(e['status']), e.get('note', ''))
                 for e in d.get('history', [])],
        error=d.get('error', ''))


class SqliteTicketRepository(TicketRepository):
    def __init__(self, path: str):
        if path != ':memory:':
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock, self._db:
            self._db.execute('PRAGMA journal_mode=WAL')
            self._db.execute(
                'CREATE TABLE IF NOT EXISTS tickets (id TEXT PRIMARY KEY, status TEXT NOT NULL, '
                'updated_at REAL NOT NULL, body TEXT NOT NULL)')
            self._db.execute('CREATE INDEX IF NOT EXISTS tickets_status ON tickets(status)')

    def save(self, ticket: Ticket) -> None:
        with self._lock, self._db:
            self._db.execute(
                'INSERT INTO tickets (id, status, updated_at, body) VALUES (?, ?, ?, ?) '
                'ON CONFLICT(id) DO UPDATE SET status=excluded.status, '
                'updated_at=excluded.updated_at, body=excluded.body',
                (ticket.id, ticket.status.value, ticket.updated_at,
                 json.dumps(ticket_to_dict(ticket))))

    def get(self, ticket_id: str) -> Optional[Ticket]:
        with self._lock:
            row = self._db.execute('SELECT body FROM tickets WHERE id = ?',
                                   (ticket_id,)).fetchone()
        return ticket_from_dict(json.loads(row[0])) if row else None

    def list(self, include_closed_since: Optional[float] = None) -> List[Ticket]:
        closed = [s.value for s in CLOSED]
        with self._lock:
            if include_closed_since is None:
                rows = self._db.execute('SELECT body FROM tickets').fetchall()
            else:
                marks = ','.join('?' * len(closed))
                rows = self._db.execute(
                    f'SELECT body FROM tickets WHERE status NOT IN ({marks}) OR updated_at >= ?',
                    (*closed, include_closed_since)).fetchall()
        return [ticket_from_dict(json.loads(r[0])) for r in rows]
