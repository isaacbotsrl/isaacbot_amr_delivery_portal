import type {
  DeliverySettings, GeocodeHit, NewTicket, Plan, PortalConfig, Snapshot, Ticket,
} from './types';

export class ApiError extends Error {}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
  });
  const text = await res.text();
  let body: unknown = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = null;
  }
  if (!res.ok) {
    const b = body as { error?: string; detail?: unknown } | null;
    const detail = b?.error ?? (b?.detail ? JSON.stringify(b.detail) : text || res.statusText);
    throw new ApiError(`${res.status}: ${detail}`);
  }
  return body as T;
}

const post = <T>(path: string, body: unknown = {}) =>
  call<T>(path, { method: 'POST', body: JSON.stringify(body) });

export const api = {
  config: () => call<PortalConfig>('/api/config'),
  snapshot: () => call<Snapshot>('/api/snapshot'),
  geocode: (q: string) => call<GeocodeHit[]>(`/api/geocode?q=${encodeURIComponent(q)}`),
  createTicket: (t: NewTicket) => post<Ticket>('/api/tickets', t),
  plan: (id: string, robot: string) => post<Plan>(`/api/tickets/${id}/plan`, { robot }),
  orderUrl: (id: string, robot: string) =>
    `/api/tickets/${id}/order.json?robot=${encodeURIComponent(robot)}`,
  assign: (id: string, robot: string) => post<Ticket>(`/api/tickets/${id}/assign`, { robot }),
  delivered: (id: string) => post<Ticket>(`/api/tickets/${id}/delivered`),
  sendBack: (id: string) => post<Ticket>(`/api/tickets/${id}/return`),
  cancel: (id: string, reason: string) => post<Ticket>(`/api/tickets/${id}/cancel`, { reason }),
  fail: (id: string, reason: string) => post<Ticket>(`/api/tickets/${id}/fail`, { reason }),
  retry: (id: string) => post<Ticket>(`/api/tickets/${id}/retry`),
  pause: (robot: string) => post(`/api/robots/${encodeURIComponent(robot)}/pause`),
  resume: (robot: string) => post(`/api/robots/${encodeURIComponent(robot)}/resume`),
  returnRobot: (robot: string) => post(`/api/robots/${encodeURIComponent(robot)}/return`),
  refreshFleet: () => post<{ error: string }>('/api/fleet/refresh'),
  settings: () => call<DeliverySettings>('/api/settings'),
  saveSettings: (s: DeliverySettings) =>
    call<DeliverySettings>('/api/settings', { method: 'PUT', body: JSON.stringify(s) }),
};
