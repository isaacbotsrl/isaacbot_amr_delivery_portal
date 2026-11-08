import { useState } from 'react';
import { api } from './api';
import type { GeocodeHit, LatLon, Ticket } from './types';

interface Props {
  draft: LatLon | null;
  setDraft: (p: LatLon | null) => void;
  onCreated: (t: Ticket) => void;
  onClose: () => void;
}

export default function TicketForm({ draft, setDraft, onCreated, onClose }: Props) {
  const [customer, setCustomer] = useState('');
  const [phone, setPhone] = useState('');
  const [address, setAddress] = useState('');
  const [notes, setNotes] = useState('');
  const [hits, setHits] = useState<GeocodeHit[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const search = async () => {
    setBusy(true);
    setError('');
    try {
      const found = await api.geocode(address);
      setHits(found);
      if (!found.length) setError('No match. Click the customer’s door on the map instead.');
      else if (found.length === 1) setDraft(found[0].point);
    } catch (e) {
      setError(String((e as Error).message));
    } finally {
      setBusy(false);
    }
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!draft) {
      setError('Pick the delivery point: search the address or click the map.');
      return;
    }
    setBusy(true);
    setError('');
    try {
      const t = await api.createTicket({
        customer, phone, address, notes, lat: draft[0], lon: draft[1],
      });
      setDraft(null);
      onCreated(t);
    } catch (err) {
      setError(String((err as Error).message));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="card form" onSubmit={submit}>
      <div className="card-head">
        <h2>New delivery</h2>
        <button type="button" className="link" onClick={onClose}>Close</button>
      </div>
      <label>
        Customer
        <input value={customer} onChange={(e) => setCustomer(e.target.value)} required maxLength={120}
               autoFocus placeholder="Name" />
      </label>
      <label>
        Phone
        <input value={phone} onChange={(e) => setPhone(e.target.value)} maxLength={32}
               inputMode="tel" placeholder="+40 …" />
      </label>
      <label>
        Address
        <div className="row">
          <input value={address} onChange={(e) => setAddress(e.target.value)} maxLength={300}
                 placeholder="Street, number, city"
                 onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); void search(); } }} />
          <button type="button" className="btn-small" disabled={busy || address.trim().length < 3}
                  onClick={() => void search()}>Find</button>
        </div>
      </label>
      {hits.length > 1 && (
        <ul className="hits">
          {hits.map((h) => (
            <li key={h.label}>
              <button type="button" className="link" onClick={() => { setDraft(h.point); setHits([]); }}>
                {h.label}
              </button>
            </li>
          ))}
        </ul>
      )}
      <p className="hint">
        {draft
          ? <>Delivery point: <code>{draft[0].toFixed(6)}, {draft[1].toFixed(6)}</code>. Click the map to adjust it to the door.</>
          : 'Click the map to place the delivery point (the customer’s door).'}
      </p>
      <label>
        Notes
        <textarea value={notes} onChange={(e) => setNotes(e.target.value)} maxLength={1000} rows={2}
                  placeholder="2× Margherita · ring at the gate" />
      </label>
      {error && <p className="error" role="alert">{error}</p>}
      <div className="actions">
        <button type="submit" className="btn-primary" disabled={busy || !customer.trim() || !draft}>
          Create ticket
        </button>
      </div>
    </form>
  );
}
