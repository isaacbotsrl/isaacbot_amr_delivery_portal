import { useEffect, useState } from 'react';
import { api } from './api';
import type { DeliverySettings, LatLon } from './types';

export type PickTarget = 'datum' | 'shop';

interface Props {
  activeDeliveries: number;
  picked: { target: PickTarget; point: LatLon } | null;
  picking: PickTarget | null;
  setPicking: (t: PickTarget | null) => void;
  onSaved: () => void;
}

const COMPASS = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'];
const compass = (deg: number) => COMPASS[Math.round(((deg % 360) + 360) % 360 / 45) % 8];

function parseLatLon(text: string): LatLon | null {
  const m = text.trim().match(/^(-?\d+(?:\.\d+)?)\s*[,; ]\s*(-?\d+(?:\.\d+)?)$/);
  if (!m) return null;
  const lat = Number(m[1]);
  const lon = Number(m[2]);
  return Math.abs(lat) <= 90 && Math.abs(lon) <= 180 ? [lat, lon] : null;
}

const fmt = (p: LatLon) => `${p[0].toFixed(6)}, ${p[1].toFixed(6)}`;

export default function SettingsPanel({ activeDeliveries, picked, picking, setPicking, onSaved }: Props) {
  const [saved, setSaved] = useState<DeliverySettings | null>(null);
  const [datum, setDatum] = useState('');
  const [shop, setShop] = useState('');
  const [sameSpot, setSameSpot] = useState(true);
  const [heading, setHeading] = useState('');
  const [mapId, setMapId] = useState('');
  const [cargo, setCargo] = useState(false);
  const [autoReturn, setAutoReturn] = useState(true);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  const load = (s: DeliverySettings) => {
    setSaved(s);
    setDatum(fmt(s.datum));
    setShop(fmt(s.shop));
    setSameSpot(s.datum[0] === s.shop[0] && s.datum[1] === s.shop[1]);
    setHeading(s.parked_heading_deg == null ? '' : String(s.parked_heading_deg));
    setMapId(s.enu_map_id);
    setCargo(s.cargo_actions);
    setAutoReturn(s.auto_return);
  };

  useEffect(() => {
    api.settings().then(load).catch((e) => setMessage({ ok: false, text: String(e.message) }));
  }, []);

  useEffect(() => {
    if (!picked) return;
    if (picked.target === 'datum') setDatum(fmt(picked.point));
    else setShop(fmt(picked.point));
  }, [picked]);

  if (!saved) return <p className="muted">{message?.text ?? 'Loading settings…'}</p>;

  const datumPoint = parseLatLon(datum);
  const shopPoint = sameSpot ? datumPoint : parseLatLon(shop);
  const headingValue = heading.trim() === '' ? null : Number(heading);
  const headingOk = headingValue === null || (Number.isFinite(headingValue) && headingValue >= 0 && headingValue < 360);
  const valid = !!datumPoint && !!shopPoint && headingOk && /^[A-Za-z0-9_.:-]{1,64}$/.test(mapId);
  const locked = activeDeliveries > 0;

  const save = async () => {
    if (!valid || !datumPoint || !shopPoint) return;
    setBusy(true);
    setMessage(null);
    try {
      const s = await api.saveSettings({
        datum: datumPoint, shop: shopPoint, enu_map_id: mapId, parked_heading_deg: headingValue,
        cargo_actions: cargo, auto_return: autoReturn,
      });
      load(s);
      setMessage({ ok: true, text: 'Saved. Each robot takes the new location with its next trip.' });
      onSaved();
    } catch (e) {
      setMessage({ ok: false, text: String((e as Error).message) });
    } finally {
      setBusy(false);
    }
  };

  const pickButton = (target: PickTarget) => (
    <button type="button" className={`btn-small ${picking === target ? 'on' : ''}`}
            onClick={() => setPicking(picking === target ? null : target)}>
      {picking === target ? 'Click the map…' : 'Pick on map'}
    </button>
  );

  return (
    <section className="card form">
      <div className="card-head"><h2>Pizzeria settings</h2></div>
      {locked && (
        <p className="warn">
          {activeDeliveries} deliver{activeDeliveries === 1 ? 'y is' : 'ies are'} running: the location can change once
          they are done (their orders use the current one).
        </p>
      )}
      <label>
        Pizzeria location (lat, lon)
        <div className="row">
          <input value={datum} onChange={(e) => setDatum(e.target.value)} placeholder="44.426800, 26.102500" />
          {pickButton('datum')}
        </div>
      </label>
      <p className="hint">
        The shop door. Every route and every robot position is measured from here; robots get it with each
        trip. In Google Maps: right-click the door, click the coordinates to copy them.
      </p>
      <label className="check">
        <input type="checkbox" checked={sameSpot} onChange={(e) => setSameSpot(e.target.checked)} />
        Robots park at the pizzeria location
      </label>
      {!sameSpot && (
        <label>
          Parking spot (lat, lon)
          <div className="row">
            <input value={shop} onChange={(e) => setShop(e.target.value)} />
            {pickButton('shop')}
          </div>
        </label>
      )}
      <label>
        Parked heading (degrees, 0 = North, 90 = East)
        <input value={heading} onChange={(e) => setHeading(e.target.value)} inputMode="decimal"
               placeholder="unknown" />
      </label>
      <p className="hint">
        {headingValue !== null && headingOk
          ? `Parked robots face ${compass(headingValue)} (arrow on the map). `
          : 'Unknown: a trip can only start after the robot drove ~10 m straight. '}
        Lets a trip start aligned to GPS right at the shop.
      </p>
      <label className="check">
        <input type="checkbox" checked={cargo} onChange={(e) => setCargo(e.target.checked)} />
        Cargo box (unlock at the door, wait for the pickup, lock)
      </label>
      <label className="check">
        <input type="checkbox" checked={autoReturn} onChange={(e) => setAutoReturn(e.target.checked)} />
        Send the robot back as soon as the order is delivered
      </label>
      <details>
        <summary className="muted small">Advanced</summary>
        <label>
          Frame id (VDA 5050 mapId)
          <input value={mapId} onChange={(e) => setMapId(e.target.value)} />
        </label>
      </details>
      {message && <p className={message.ok ? 'ok' : 'error'} role="status">{message.text}</p>}
      <div className="actions">
        <button type="button" className="btn-primary" disabled={!valid || busy || locked} onClick={() => void save()}>
          Save settings
        </button>
        <button type="button" className="btn-small" disabled={busy} onClick={() => { load(saved); setMessage(null); }}>
          Undo changes
        </button>
      </div>
    </section>
  );
}
