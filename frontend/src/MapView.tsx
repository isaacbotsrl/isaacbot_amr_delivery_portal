import { useEffect, useRef } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { STATUS_LABEL, STATUS_TONE, robotPoint } from './format';
import type { LatLon, Plan, PortalConfig, Robot, Ticket } from './types';

interface Props {
  config: PortalConfig;
  robots: Robot[];
  tickets: Ticket[];
  selectedTicket: Ticket | null;
  plan: Plan | null;
  draft: LatLon | null;
  picking: boolean;
  onPick: (p: LatLon) => void;
  onSelectTicket: (id: string) => void;
}

/** ENU yaw (rad, 0 = East, counter-clockwise) -> compass bearing (deg, 0 = North, clockwise). */
const bearing = (theta: number) => 90 - (theta * 180) / Math.PI;

const esc = (s: string) =>
  s.replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]!);

function robotIcon(r: Robot): L.DivIcon {
  const heading = r.theta != null ? bearing(r.theta) : null;
  const state = !r.online ? 'off' : r.e_stop && r.e_stop !== 'NONE' ? 'stop' : r.driving ? 'drive' : 'idle';
  const arrow = heading != null
    ? `<span class="robot-arrow" style="transform: rotate(${heading}deg)"></span>` : '';
  return L.divIcon({
    className: '',
    html: `<div class="robot-pin robot-${state}">${arrow}<span class="robot-dot"></span>` +
      `<span class="robot-label">${esc(r.name)}</span></div>`,
    iconSize: [28, 28],
    iconAnchor: [14, 14],
  });
}

export default function MapView(p: Props) {
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const layers = useRef<Record<string, L.LayerGroup>>({});
  const onPick = useRef(p.onPick);
  const onSelect = useRef(p.onSelectTicket);
  const picking = useRef(p.picking);
  onPick.current = p.onPick;
  onSelect.current = p.onSelectTicket;
  picking.current = p.picking;
  const fitted = useRef(false);

  // Create the map once.
  useEffect(() => {
    if (!el.current || map.current) return;
    const m = L.map(el.current, { zoomControl: true }).setView(p.config.shop, 16);
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    }).addTo(m);
    for (const name of ['route', 'plan', 'trails', 'tickets', 'robots', 'shop', 'draft']) {
      layers.current[name] = L.layerGroup().addTo(m);
    }
    L.marker(p.config.shop, {
      icon: L.divIcon({ className: '', html: '<div class="shop-pin">🍕</div>', iconSize: [34, 34], iconAnchor: [17, 17] }),
      title: 'Pizzeria',
      zIndexOffset: 500,
    }).bindTooltip('Pizzeria (shop)').addTo(layers.current.shop);
    m.on('click', (e: L.LeafletMouseEvent) => {
      if (picking.current) onPick.current([e.latlng.lat, e.latlng.lng]);
    });
    map.current = m;
    const resize = new ResizeObserver(() => m.invalidateSize());
    resize.observe(el.current);
    return () => {
      resize.disconnect();
      m.remove();
      map.current = null;
    };
  }, [p.config.shop]);

  useEffect(() => {
    el.current?.classList.toggle('picking', p.picking);
  }, [p.picking]);

  // Robots: marker at the aligned pose (or GPS), faint GPS dot, trail.
  useEffect(() => {
    const robots = layers.current.robots;
    const trails = layers.current.trails;
    if (!robots || !trails) return;
    robots.clearLayers();
    trails.clearLayers();
    const selectedRobot = p.selectedTicket?.robot;
    for (const r of p.robots) {
      const at = robotPoint(r);
      if (r.trail.length > 1) {
        L.polyline(r.trail, {
          className: r.name === selectedRobot ? 'trail trail-selected' : 'trail',
          interactive: false,
        }).addTo(trails);
      }
      if (r.gps && r.position) {
        L.circleMarker(r.gps, { radius: 5, className: 'gps-dot', interactive: false }).addTo(robots);
      }
      if (!at) continue;
      L.marker(at, { icon: robotIcon(r), zIndexOffset: 1000 })
        .bindTooltip(
          `${esc(r.name)} · ${r.battery != null ? Math.round(r.battery) + '%' : 'battery ?'}` +
            (r.position ? '' : ' · GPS only'),
        )
        .addTo(robots);
    }
    if (!fitted.current && map.current) {
      const pts = p.robots.map(robotPoint).filter((x): x is LatLon => x != null);
      if (pts.length) {
        map.current.fitBounds(L.latLngBounds([p.config.shop, ...pts]).pad(0.3), { maxZoom: 17 });
        fitted.current = true;
      }
    }
  }, [p.robots, p.selectedTicket?.robot, p.config.shop]);

  // Ticket destinations, and the selected ticket's planned route.
  useEffect(() => {
    const group = layers.current.tickets;
    const route = layers.current.route;
    if (!group || !route) return;
    group.clearLayers();
    route.clearLayers();
    for (const t of p.tickets) {
      if (!t.active && t.status !== 'NEW' && t.id !== p.selectedTicket?.id) continue;
      const selected = t.id === p.selectedTicket?.id;
      L.circleMarker(t.destination, {
        radius: selected ? 10 : 7,
        className: `dest dest-${STATUS_TONE[t.status]}${selected ? ' dest-selected' : ''}`,
      })
        .bindTooltip(`${esc(t.customer)} · ${STATUS_LABEL[t.status]}`)
        .on('click', (e) => {
          L.DomEvent.stopPropagation(e);
          onSelect.current(t.id);
        })
        .addTo(group);
    }
    if (p.selectedTicket && p.selectedTicket.route.length > 1) {
      L.polyline(p.selectedTicket.route, { className: 'route', interactive: false }).addTo(route);
    }
  }, [p.tickets, p.selectedTicket]);

  // Route preview before sending.
  useEffect(() => {
    const group = layers.current.plan;
    if (!group) return;
    group.clearLayers();
    if (!p.plan) return;
    L.polyline(p.plan.route, { className: 'plan-route', interactive: false }).addTo(group);
    for (const w of p.plan.waypoints) {
      L.circleMarker(w, { radius: 3, className: 'plan-node', interactive: false }).addTo(group);
    }
    map.current?.fitBounds(L.latLngBounds(p.plan.route).pad(0.25), { maxZoom: 18 });
  }, [p.plan]);

  // The point being picked for a new ticket.
  useEffect(() => {
    const group = layers.current.draft;
    if (!group) return;
    group.clearLayers();
    if (p.draft) {
      L.circleMarker(p.draft, { radius: 9, className: 'draft-dot', interactive: false }).addTo(group);
      map.current?.panTo(p.draft);
    }
  }, [p.draft]);

  return <div ref={el} className="map" aria-label="Fleet map" />;
}
