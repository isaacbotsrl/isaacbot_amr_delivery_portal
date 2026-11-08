import type { Robot, TicketStatus } from './types';

export function duration(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return '—';
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s} s`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m} min`;
  return `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, '0')} min`;
}

export function distance(metres: number | null | undefined): string {
  if (metres == null || !Number.isFinite(metres)) return '—';
  return metres < 1000 ? `${Math.round(metres)} m` : `${(metres / 1000).toFixed(2)} km`;
}

export function clockTime(epochS: number): string {
  return new Date(epochS * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

export function ago(epochS: number | null, nowS: number): string {
  if (!epochS) return 'never';
  const d = Math.max(0, nowS - epochS);
  return d < 5 ? 'now' : `${duration(d)} ago`;
}

export const STATUS_LABEL: Record<TicketStatus, string> = {
  NEW: 'New',
  ASSIGNED: 'Assigned',
  EN_ROUTE: 'On the way',
  ARRIVED: 'At the door',
  DELIVERED: 'Delivered',
  RETURNING: 'Returning',
  DONE: 'Done',
  FAILED: 'Failed',
  CANCELLED: 'Cancelled',
};

export type Tone = 'ok' | 'warn' | 'bad' | 'muted' | 'accent';

export const STATUS_TONE: Record<TicketStatus, Tone> = {
  NEW: 'accent',
  ASSIGNED: 'accent',
  EN_ROUTE: 'accent',
  ARRIVED: 'warn',
  DELIVERED: 'ok',
  RETURNING: 'ok',
  DONE: 'muted',
  FAILED: 'bad',
  CANCELLED: 'muted',
};

/** One line on why a robot can't take a delivery right now ('' = it can). */
export function robotBlocker(r: Robot, busy: boolean): string {
  if (!r.delivery) return 'RMF robot';
  if (!r.online) return r.connection === 'UNKNOWN' ? 'never seen' : r.connection.toLowerCase();
  if (r.e_stop && r.e_stop !== 'NONE') return `e-stop (${r.e_stop.toLowerCase()})`;
  if (busy) return 'busy';
  return '';
}

/** Where to draw a robot, best first: its aligned SLAM pose, else its raw GPS fix. */
export function robotPoint(r: Robot): [number, number] | null {
  return r.position ?? r.gps;
}
