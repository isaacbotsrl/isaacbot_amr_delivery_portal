import { useCallback, useEffect, useMemo, useState } from 'react';
import { api } from './api';
import MapView from './MapView';
import TicketDetail from './TicketDetail';
import TicketForm from './TicketForm';
import SettingsPanel, { type PickTarget } from './SettingsPanel';
import { STATUS_LABEL, STATUS_TONE, ago, distance, duration, robotBlocker } from './format';
import type { LatLon, Plan, PortalConfig, Robot, Snapshot, Ticket } from './types';

const POLL_MS = 1500;

function RobotRow({ r, busy, now, onReturn, onPause, onResume }: {
  r: Robot; busy: boolean; now: number;
  onReturn: () => void; onPause: () => void; onResume: () => void;
}) {
  const why = robotBlocker(r, busy);
  const tone = !r.online ? 'muted' : why && why !== 'busy' ? 'bad' : r.driving ? 'accent' : 'ok';
  return (
    <li className="robot-row">
      <div className="row-head">
        <strong>{r.name}</strong>
        <span className={`chip chip-${tone}`}>
          {why || (r.driving ? 'driving' : r.paused ? 'paused' : 'ready')}
        </span>
      </div>
      <div className="muted small">
        {r.battery != null ? `${Math.round(r.battery)}%${r.charging ? ' ⚡' : ''}` : 'battery ?'}
        {' · '}{r.operating_mode || 'mode ?'}
        {' · '}{r.position ? 'lidar-aligned' : r.gps ? `GPS ${r.gps_fix_type || ''}` : 'no position'}
        {' · '}seen {ago(r.received_at, now)}
      </div>
      {r.errors.length > 0 && (
        <ul className="errors">
          {r.errors.map((e, i) => <li key={i} className={e.level === 'FATAL' ? 'error' : 'warn'}>
            {e.type}{e.description ? `: ${e.description}` : ''}</li>)}
        </ul>
      )}
      {r.delivery && r.online && (
        <div className="actions">
          {r.paused
            ? <button type="button" className="btn-small" onClick={onResume}>Resume</button>
            : <button type="button" className="btn-small" onClick={onPause}>Pause</button>}
          {!busy && <button type="button" className="btn-small" onClick={onReturn}>Return to shop</button>}
        </div>
      )}
    </li>
  );
}

export default function App() {
  const [config, setConfig] = useState<PortalConfig | null>(null);
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [connError, setConnError] = useState('');
  const [tab, setTab] = useState<'tickets' | 'robots' | 'settings'>('tickets');
  const [pickTarget, setPickTarget] = useState<PickTarget | null>(null);
  const [picked, setPicked] = useState<{ target: PickTarget; point: LatLon } | null>(null);
  const [showClosed, setShowClosed] = useState(false);
  const [creating, setCreating] = useState(false);
  const [draft, setDraft] = useState<LatLon | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [notice, setNotice] = useState('');
  const [now, setNow] = useState(Date.now() / 1000);

  const loadConfig = useCallback(() => {
    api.config().then(setConfig).catch((e) => setConnError(String(e.message)));
  }, []);
  useEffect(loadConfig, [loadConfig]);

  const onPick = useCallback((p: LatLon) => {
    if (pickTarget) {
      setPicked({ target: pickTarget, point: p });
      setPickTarget(null);
    } else {
      setDraft(p);
    }
  }, [pickTarget]);

  useEffect(() => {
    let stop = false;
    let timer: number | undefined;
    const tick = async () => {
      try {
        const s = await api.snapshot();
        if (!stop) { setSnap(s); setConnError(''); setNow(Date.now() / 1000); }
      } catch (e) {
        if (!stop) setConnError(String((e as Error).message));
      }
      if (!stop) timer = window.setTimeout(tick, POLL_MS);
    };
    void tick();
    return () => { stop = true; window.clearTimeout(timer); };
  }, []);

  const tickets = snap?.tickets ?? [];
  const robots = snap?.robots ?? [];
  const busyRobots = useMemo(
    () => new Set(tickets.filter((t) => t.active && t.robot).map((t) => t.robot)), [tickets]);
  const selected = tickets.find((t) => t.id === selectedId) ?? null;
  const visible = tickets.filter((t) => showClosed || t.active || t.status === 'NEW');

  const select = useCallback((id: string) => {
    setSelectedId(id);
    setCreating(false);
    setTab('tickets');
  }, []);

  const robotCmd = async (f: () => Promise<unknown>, done: string) => {
    try {
      await f();
      setNotice(done);
    } catch (e) {
      setNotice(String((e as Error).message));
    }
  };

  if (!config) {
    return <div className="boot">{connError ? `Cannot reach the portal: ${connError}` : 'Loading…'}</div>;
  }

  return (
    <div className="app">
      <header className="top">
        <img className="logo logo-light" src="/isaacbot-lockup-black-bold.svg" alt="Isaacbot" />
        <img className="logo logo-dark" src="/isaacbot-lockup-white.svg" alt="Isaacbot" />
        <span className="title">Delivery</span>
        <span className="spacer" />
        <span className={`chip chip-${snap?.mqtt.connected ? 'ok' : 'bad'}`}
              title={snap?.mqtt.error || snap?.mqtt.broker || ''}>
          {snap?.mqtt.connected ? 'Fleet link up' : 'Fleet link down'}
        </span>
        {snap?.fleet_error && <span className="chip chip-warn" title={snap.fleet_error}>Site manager unreachable</span>}
        {connError && <span className="chip chip-bad" title={connError}>Portal unreachable</span>}
      </header>

      <main className="layout">
        <aside className="side">
          <nav className="tabs" role="tablist">
            <button role="tab" aria-selected={tab === 'tickets'} className={tab === 'tickets' ? 'on' : ''}
                    onClick={() => setTab('tickets')}>Deliveries ({tickets.filter((t) => t.active || t.status === 'NEW').length})</button>
            <button role="tab" aria-selected={tab === 'robots'} className={tab === 'robots' ? 'on' : ''}
                    onClick={() => setTab('robots')}>Robots ({robots.filter((r) => r.online).length}/{robots.length})</button>
            <button role="tab" aria-selected={tab === 'settings'} className={tab === 'settings' ? 'on' : ''}
                    onClick={() => setTab('settings')}>Settings</button>
          </nav>

          {notice && <p className="notice" onClick={() => setNotice('')}>{notice}</p>}

          {tab === 'tickets' && (
            <>
              {creating ? (
                <TicketForm draft={draft} setDraft={setDraft}
                            onCreated={(t) => { setCreating(false); select(t.id); }}
                            onClose={() => { setCreating(false); setDraft(null); }} />
              ) : selected ? (
                <TicketDetail key={selected.id} ticket={selected} robots={robots} busyRobots={busyRobots}
                              config={config} plan={plan} setPlan={setPlan}
                              onClose={() => { setSelectedId(null); setPlan(null); }} />
              ) : (
                <div className="list-head">
                  <button type="button" className="btn-primary" onClick={() => { setCreating(true); setSelectedId(null); }}>
                    + New delivery
                  </button>
                  <label className="small">
                    <input type="checkbox" checked={showClosed} onChange={(e) => setShowClosed(e.target.checked)} />
                    {' '}show finished (24 h)
                  </label>
                  {showClosed && tickets.some((t) => ['DONE', 'CANCELLED', 'FAILED'].includes(t.status)) && (
                    <button type="button" className="btn-small btn-danger" onClick={() => {
                      const n = tickets.filter((t) => ['DONE', 'CANCELLED', 'FAILED'].includes(t.status)).length;
                      if (window.confirm(`Delete all ${n} finished tickets? This cannot be undone.`)) {
                        void robotCmd(async () => {
                          const r = await api.clearFinished();
                          if (selected && !selected.active) setSelectedId(null);
                          return r;
                        }, 'Finished tickets deleted.');
                      }
                    }}>Clear finished</button>
                  )}
                </div>
              )}
              <ul className="tickets">
                {visible.length === 0 && <li className="muted empty">No deliveries yet.</li>}
                {visible.map((t: Ticket) => (
                  <li key={t.id}>
                    <button type="button" className={`ticket ${t.id === selectedId ? 'on' : ''}`} onClick={() => select(t.id)}>
                      <span className="row-head">
                        <strong>{t.customer}</strong>
                        <span className={`chip chip-${STATUS_TONE[t.status]}`}>{STATUS_LABEL[t.status]}</span>
                      </span>
                      <span className="muted small">
                        {t.address || `${t.destination[0].toFixed(5)}, ${t.destination[1].toFixed(5)}`}
                        {t.robot && ` · ${t.robot}`}
                        {t.remaining_s != null ? ` · ETA ${duration(t.remaining_s)}`
                          : t.distance_m ? ` · ${distance(t.distance_m)}` : ''}
                      </span>
                      {t.error && <span className="error small">{t.error}</span>}
                    </button>
                  </li>
                ))}
              </ul>
            </>
          )}

          {tab === 'settings' && (
            <SettingsPanel activeDeliveries={tickets.filter((t) => t.active).length}
                           picked={picked} picking={pickTarget} setPicking={setPickTarget}
                           onSaved={loadConfig} />
          )}

          {tab === 'robots' && (
            <ul className="robots">
              {robots.length === 0 && <li className="muted empty">No robots from the site manager.</li>}
              {robots.map((r) => (
                <RobotRow key={r.name} r={r} busy={busyRobots.has(r.name)} now={now}
                          onPause={() => void robotCmd(() => api.pause(r.name), `${r.name}: pause sent`)}
                          onResume={() => void robotCmd(() => api.resume(r.name), `${r.name}: resume sent`)}
                          onReturn={() => {
                            if (window.confirm(`Send ${r.name} back to the shop?`)) {
                              void robotCmd(() => api.returnRobot(r.name), `${r.name}: returning to the shop`);
                            }
                          }} />
              ))}
            </ul>
          )}
        </aside>

        <MapView config={config} robots={robots} tickets={tickets} selectedTicket={selected}
                 plan={plan} draft={draft} picking={(creating && tab === 'tickets') || pickTarget !== null}
                 onPick={onPick} onSelectTicket={select} />
      </main>
    </div>
  );
}
