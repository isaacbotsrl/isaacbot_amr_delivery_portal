import pytest

from delivery_portal.domain.geo import GeoPoint
from delivery_portal.domain.tickets import Ticket, TicketError, TicketStatus

P = GeoPoint(44.43, 26.11)


def test_create_validates():
    t = Ticket.create('  Ana  ', P, phone='+40 712 345 678', now=5.0)
    assert t.customer == 'Ana' and t.status == TicketStatus.NEW and t.history[0].at == 5.0
    with pytest.raises(TicketError):
        Ticket.create(' ', P)
    with pytest.raises(TicketError):
        Ticket.create('Ana', P, phone='call me')


def test_happy_path_and_history():
    t = Ticket.create('Ana', P, now=0)
    for s in (TicketStatus.ASSIGNED, TicketStatus.EN_ROUTE, TicketStatus.ARRIVED,
              TicketStatus.DELIVERED, TicketStatus.RETURNING, TicketStatus.DONE):
        t = t.transition(s, now=1)
    assert [e.status for e in t.history][-1] == TicketStatus.DONE and not t.active


def test_illegal_transition():
    t = Ticket.create('Ana', P)
    with pytest.raises(TicketError):
        t.transition(TicketStatus.DELIVERED)
    done = t.transition(TicketStatus.CANCELLED)
    with pytest.raises(TicketError):
        done.transition(TicketStatus.ASSIGNED)


def test_retry_clears_robot():
    t = Ticket.create('Ana', P).transition(TicketStatus.ASSIGNED, robot='r', order_id='o')
    t = t.transition(TicketStatus.FAILED).transition(TicketStatus.NEW)
    assert t.robot == '' and t.order_id == '' and t.status == TicketStatus.NEW
