import { useEffect, useState } from 'react';
import { api } from './api';
import { STATUS_LABEL, STATUS_TONE, clockTime, distance, duration, robotBlocker } from './format';
import type { Plan, PortalConfig, Robot, Ticket } from './types';

interface Props {
  ticket: Ticket;
  robots: Robot[];
  busyRobots: Set<string>;
  config: PortalConfig;
  plan: Plan | null;
  setPlan: (p: Plan | null) => void;
  onClose: () => void;
}

const SOURCE: Record<Plan['start_source'], string> = {
  slam: 'its lidar position',
  gps: 'its GPS fix (not aligned yet)',
  shop: 'the shop (position unknown)',
};

export default function TicketDetail({ ticket: t, robots, busyRobots, config, plan, setPlan, onClose }: Props) {
  const free = robots.filter((r) => !robotBlocker(r, busyRobots.has(r.name)));
  const [robot, setRobot] = useState(free[0]?.name ?? '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [showJson, setShowJson] = useState(false);

  useEffect(() => {
    setPlan(null);
    setError('');
    setShowJson(false);
  }, [t.id, setPlan]);

  useEffect(() => {
    if (!robot && free.length) setRobot(free[0].name);
  }, [free, robot]);

  const run = async (f: () => Promise<unknown>) => {
    setBusy(true);
    setError('');
    try {
      await f();
    } catch (e) {
      setError(String((e as Error).message));
    } finally {
      setBusy(false);
    }
  };

  const assigned = robots.find((r) => r.name === t.robot);
  const ask = (label: string) => window.prompt(label) ?? null;

  return (
    <section className="card detail">
      <div className="card-head">
        <h2>{t.customer}</h2>
        <button type="button" className="link" onClick={onClose}>Close</button>
      </div>
      <p>
        <span className={`chip chip-${STATUS_TONE[t.status]}`}>{STATUS_LABEL[t.status]}</span>
        {t.robot && <span className="chip">{t.robot}</span>}
        {t.remaining_s != null && <span className="chip">ETA {duration(t.remaining_s)}</span>}
      </p>
      {t.address && <p>{t.address}</p>}
      {t.phone && <p><a href={`tel:${t.phone}`}>{t.phone}</a></p>}
      {t.notes && <p className="notes">{t.notes}</p>}
      {t.error && <p className="error" role="alert">{t.error}</p>}
      {t.distance_m > 0 && (
        <p className="muted">Route {distance(t.distance_m)} · planned {duration(t.eta_s)}</p>
      )}

      {t.status === 'NEW' && (
        <div className="dispatch">
          <label>
            Robot
            <select value={robot} onChange={(e) => { setRobot(e.target.value); setPlan(null); }}>
              <option value="" disabled>Choose a robot</option>
              {robots.map((r) => {
                const why = robotBlocker(r, busyRobots.has(r.name));
                return (
                  <option key={r.name} value={r.name} disabled={!!why}>
                    {r.name}{why ? ` (${why})` : r.battery != null ? ` · ${Math.round(r.battery)}%` : ''}
                  </option>
                );
              })}
            </select>
          </label>
          <div className="actions">
            <button type="button" className="btn-small" disabled={!robot || busy}
                    onClick={() => void run(async () => setPlan(await api.plan(t.id, robot)))}>
              Preview route
            </button>
            <button type="button" className="btn-primary" disabled={!robot || busy}
                    onClick={() => void run(async () => { await api.assign(t.id, robot); setPlan(null); })}>
              Send {robot || 'robot'}
            </button>
          </div>
          {plan && plan.robot === robot && (
            <div className="plan">
              <p>
                <strong>{distance(plan.distance_m)}</strong> in {plan.nodes} waypoints · about{' '}
                <strong>{duration(plan.eta_s)}</strong> at {config.max_speed} m/s
              </p>
              <p className="muted">
                Starts from {SOURCE[plan.start_source]} · routed by {plan.provider}
              </p>
              <div className="actions">
                <button type="button" className="link" onClick={() => setShowJson((v) => !v)}>
                  {showJson ? 'Hide' : 'Show'} VDA 5050 order
                </button>
                <a className="link" href={api.orderUrl(t.id, robot)} download>Download JSON</a>
              </div>
              {showJson && <pre className="json">{JSON.stringify(plan.order, null, 2)}</pre>}
            </div>
          )}
        </div>
      )}

      {assigned && t.active && (
        <p className="muted">
          {assigned.name}: {assigned.driving ? 'driving' : assigned.paused ? 'paused' : 'stopped'}
          {assigned.last_node_id && ` · passed ${assigned.last_node_id}`}
          {` · ${assigned.remaining_nodes} waypoints left`}
        </p>
      )}

      <div className="actions">
        {t.status === 'ARRIVED' && (
          <button type="button" className="btn-primary" disabled={busy}
                  onClick={() => void run(() => api.delivered(t.id))}>
            Mark delivered{config.auto_return ? ' & return' : ''}
          </button>
        )}
        {t.status === 'DELIVERED' && !config.auto_return && (
          <button type="button" className="btn-primary" disabled={busy}
                  onClick={() => void run(() => api.sendBack(t.id))}>Send back to shop</button>
        )}
        {assigned && t.active && (
          assigned.paused
            ? <button type="button" className="btn-small" disabled={busy}
                      onClick={() => void run(() => api.resume(assigned.name))}>Resume</button>
            : <button type="button" className="btn-small" disabled={busy}
                      onClick={() => void run(() => api.pause(assigned.name))}>Pause</button>
        )}
        {['NEW', 'ASSIGNED', 'EN_ROUTE', 'ARRIVED'].includes(t.status) && (
          <button type="button" className="btn-small btn-danger" disabled={busy}
                  onClick={() => {
                    const reason = ask('Cancel this delivery? Reason (optional):');
                    if (reason !== null) void run(() => api.cancel(t.id, reason));
                  }}>Cancel</button>
        )}
        {t.active && (
          <button type="button" className="btn-small btn-danger" disabled={busy}
                  onClick={() => {
                    const reason = ask('Mark as failed (the robot stops where it is). Reason:');
                    if (reason !== null) void run(() => api.fail(t.id, reason));
                  }}>Failed</button>
        )}
        {(t.status === 'FAILED' || t.status === 'CANCELLED') && (
          <button type="button" className="btn-small" disabled={busy}
                  onClick={() => void run(() => api.retry(t.id))}>Retry</button>
        )}
      </div>
      {error && <p className="error" role="alert">{error}</p>}

      <ol className="history">
        {t.history.map((e, i) => (
          <li key={i}>
            <span className="muted">{clockTime(e.at)}</span> {STATUS_LABEL[e.status]}
            {e.note && <span className="muted"> · {e.note}</span>}
          </li>
        ))}
      </ol>
    </section>
  );
}
