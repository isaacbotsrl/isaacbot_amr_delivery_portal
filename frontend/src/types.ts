export type LatLon = [number, number];

export type TicketStatus =
  | 'NEW' | 'ASSIGNED' | 'EN_ROUTE' | 'ARRIVED' | 'DELIVERED'
  | 'RETURNING' | 'DONE' | 'FAILED' | 'CANCELLED';

export interface TicketEvent { at: number; status: TicketStatus; note: string }

export interface Ticket {
  id: string;
  customer: string;
  address: string;
  phone: string;
  notes: string;
  destination: LatLon;
  status: TicketStatus;
  robot: string;
  order_id: string;
  outbound_order_id: string;
  return_order_id: string;
  destination_node_id: string;
  trip_action_id: string;
  order_sent: boolean;
  route: LatLon[];
  distance_m: number;
  eta_s: number;
  created_at: number;
  updated_at: number;
  history: TicketEvent[];
  error: string;
  remaining_s: number | null;
  active: boolean;
}

export interface RobotAction { id: string; type: string; status: string }
export interface RobotError { type: string; level: string; description: string }

export interface Robot {
  name: string;
  serial_number: string;
  manufacturer: string;
  delivery: boolean;
  connection: string;
  online: boolean;
  received_at: number | null;
  position: LatLon | null;
  theta: number | null;
  localization_score: number | null;
  gps: LatLon | null;
  gps_fix_type: string;
  gps_hdop: number | null;
  map_id: string;
  battery: number | null;
  charging: boolean;
  operating_mode: string;
  e_stop: string;
  field_violation: boolean;
  driving: boolean;
  paused: boolean;
  order_id: string;
  last_node_id: string;
  remaining_nodes: number;
  actions: RobotAction[];
  errors: RobotError[];
  trail: LatLon[];
}

export interface Snapshot {
  robots: Robot[];
  tickets: Ticket[];
  mqtt: { connected: boolean; error: string; broker?: string };
  fleet_error: string;
}

export interface PortalConfig {
  datum: LatLon;
  shop: LatLon;
  map_id: string;
  routing: string;
  cargo_actions: boolean;
  auto_return: boolean;
  start_trip: boolean;
  enu_map_id: string;
  parked_heading_deg: number | null;
  max_speed: number;
  waypoint_spacing_m: number;
}

export interface Plan {
  robot: string;
  start: LatLon;
  start_source: 'slam' | 'gps' | 'shop';
  provider: string;
  route: LatLon[];
  router_distance_m: number;
  waypoints: LatLon[];
  distance_m: number;
  eta_s: number;
  nodes: number;
  order: Record<string, unknown>;
}

export interface DeliverySettings {
  datum: LatLon;
  shop: LatLon;
  enu_map_id: string;
  parked_heading_deg: number | null;
  cargo_actions: boolean;
  auto_return: boolean;
}

export interface GeocodeHit { label: string; point: LatLon }

export interface NewTicket {
  customer: string;
  lat: number;
  lon: number;
  address: string;
  phone: string;
  notes: string;
}
