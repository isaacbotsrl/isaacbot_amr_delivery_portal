import { describe, expect, it } from 'vitest';
import { distance, duration, robotBlocker, robotPoint } from './format';
import type { Robot } from './types';

const robot = (over: Partial<Robot> = {}): Robot => ({
  name: 'r', serial_number: 'S', manufacturer: 'IsaacBot', delivery: true,
  connection: 'ONLINE', online: true, received_at: 1, position: null, theta: null,
  localization_score: null, gps: null, gps_fix_type: '', gps_hdop: null, map_id: '',
  battery: 80, charging: false, operating_mode: 'AUTOMATIC', e_stop: 'NONE',
  field_violation: false, driving: false, paused: false, order_id: '', last_node_id: '',
  remaining_nodes: 0, actions: [], errors: [], trail: [], ...over,
});

describe('format', () => {
  it('formats durations', () => {
    expect(duration(null)).toBe('—');
    expect(duration(42)).toBe('42 s');
    expect(duration(600)).toBe('10 min');
    expect(duration(3 * 3600 + 5 * 60)).toBe('3 h 05 min');
  });

  it('formats distances', () => {
    expect(distance(950.4)).toBe('950 m');
    expect(distance(1234)).toBe('1.23 km');
  });

  it('explains why a robot cannot deliver', () => {
    expect(robotBlocker(robot(), false)).toBe('');
    expect(robotBlocker(robot({ delivery: false }), false)).toBe('RMF robot');
    expect(robotBlocker(robot({ online: false, connection: 'OFFLINE' }), false)).toBe('offline');
    expect(robotBlocker(robot({ e_stop: 'MANUAL' }), false)).toBe('e-stop (manual)');
    expect(robotBlocker(robot(), true)).toBe('busy');
  });

  it('prefers the aligned pose over GPS', () => {
    expect(robotPoint(robot({ gps: [1, 2] }))).toEqual([1, 2]);
    expect(robotPoint(robot({ gps: [1, 2], position: [3, 4] }))).toEqual([3, 4]);
  });
});
