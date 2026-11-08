# isaacbot_amr_delivery_portal

The delivery portal for the Isaacbot fleet. Pizzeria staff use it to:

- create a delivery ticket, from an address or by clicking the customer's door on the map;
- see every robot on a real map (OpenStreetMap);
- send a robot. The portal turns a walking route into a **VDA 5050 2.0 order** with many
  waypoints, sends it over MQTT, and follows the robot until it's back at the shop.

```
staff browser ──HTTP──▶ portal :8040 ──GET /api/robots──▶ RMF site manager :8020
                         │   ▲
                order /  │   │ state / connection
          instantActions ▼   │
                     Mosquitto :1883 (uagv/v2/<manufacturer>/<serial>/…)
                         │   ▲
                         ▼   │
                 robot: isaacbot_amr_vda5050_adapter → mission manager → Nav 2
```

## How the robots navigate

The robots navigate by **lidar only**. Each trip starts a fresh live SLAM map at the shop, and there are
**no pre-built maps**. GPS **never** feeds Nav 2. It is used only to:

- show the robots on the map;
- seed the start position;
- compute the walking route and the ETA;
- line the route up with the robot's live SLAM map.

For that, orders and positions use one shared frame, `ENU_MAP_ID`: metres East (x) and North (y)
of a fixed datum, the pizzeria. On the robot, the VDA 5050 adapter:

1. keeps a GPS-to-SLAM alignment, fitted from (GPS fix, SLAM pose) pairs as the robot drives;
2. converts the order's nodes into its SLAM map;
3. re-projects the remaining nodes when its SLAM map drifts;
4. reports `agvPosition` back in the ENU frame, with the raw GPS fix in an `information` entry
   (`infoType: gpsFix`).

That robot-side work is Phases 1–2 of the delivery plan, in the ardennes workspace. Until then, a
robot's position only shows up once its adapter reports the ENU frame or a `gpsFix`.

## A delivery, step by step

| Status | What happens |
|---|---|
| `NEW` | Ticket created. |
| `ASSIGNED` | With `START_TRIP=true` (default), the instant action `startTrip` goes out first. The robot starts a fresh SLAM map and aligns it to GPS. Once it reports `FINISHED`, the order is sent. It starts at the robot's position and has a node every `WAYPOINT_SPACING_M` along the route (corners kept), plus the `destination` node. |
| `EN_ROUTE` | The robot accepted the order and is driving. The ETA counts down. |
| `ARRIVED` | The robot reached `destination`, within `DESTINATION_TOLERANCE_M`. |
| `DELIVERED` | The customer confirmed (`confirmPickup` finished, `CARGO_ACTIONS=true`), or staff pressed **Mark delivered**. |
| `RETURNING` | A return order back to the `shop` node. If routing fails, the robot retraces the outbound route. |
| `DONE` | Back at the shop. |

From any active status you can **Cancel** or mark the ticket **Failed**. Both send `cancelOrder` to the robot.
**Retry** sends the ticket back to `NEW`. **Pause** and **Resume** send `startPause` / `stopPause`.

A robot takes deliveries only when all of these hold:
- it is a *delivery robot*, either listed in `DELIVERY_ROBOTS` or flagged `delivery` by the site
  manager;
- it is online;
- it is not e-stopped;
- it has no other active ticket.

Keep delivery robots **out of RMF**: two masters must never send orders to one robot.

The ETA is the walking distance divided by (`MAX_SPEED` × 0.75). Car traffic doesn't apply to a
sidewalk robot.

## Routing and addresses

| `ROUTING_PROVIDER` | What it is |
|---|---|
| `osrm` (default) | OSRM foot profile. The default URL is the public FOSSGIS router (`routing.openstreetmap.de`), which is for **light use only**. For production, self-host `osrm-backend` with your region's OSM extract and set `ROUTING_URL`. |
| `valhalla` | Valhalla `pedestrian` costing, at `ROUTING_URL`. |
| `google` | Google Routes API, `travelMode=WALK`. Needs `GOOGLE_API_KEY`, and each request is billed. |
| `straight` | A straight line, for simulation. |

Address search uses Nominatim (OpenStreetMap). Its limit is one request per second, and the portal enforces that.

## Run it

### Try it without robots

```bash
docker compose -f docker-compose.sim.yml up --build
# open http://127.0.0.1:8041 (any user name, password "sim")
```

This starts a private Mosquitto broker and two fake VDA 5050 robots (`backend/tools/fake_robot.py`).
The robots drive 5× real time, finish the cargo actions, and report a noisy GPS fix.

### Server (isaacbot-server-001, VPN 10.8.0.1)

Port 8030 belongs to the RMF container, so the portal uses **8040**.

```bash
ssh isaacbot-server-001@10.8.0.1
git clone https://github.com/isaacbotsrl/isaacbot_amr_delivery_portal.git   # once
cd isaacbot_amr_delivery_portal
cp .env.example .env    # once: datum, passwords, DELIVERY_ROBOTS, MQTT login
git pull --ff-only && docker compose build && docker compose up -d
```

The image build runs the backend tests, the TypeScript type check and the UI tests. A failing
test fails the build.

The MQTT login (`MQTT_USER`) needs `readwrite` on the delivery robots' topics in the RMF broker's ACL.
Add it in the site manager (`isaacbot_amr_rmf/site_manager`).

### Development

```bash
cd backend && python3 -m pytest -q tests     # domain/application tests need only the stdlib
cd frontend && npm install && npm run dev    # proxies /api to PORTAL_API (default :8040)
```

## Layout

The code follows Clean Architecture:

```
backend/delivery_portal/
  domain/          geo (WGS84↔ENU, resampling, polylines), tickets, order_builder (VDA 5050),
                   eta, robots (VDA state → status), ports — stdlib only
  application/     delivery_service: tickets, plan, assign, follow robot state, return, cancel
  infrastructure/  routing (OSRM/Valhalla/Google), geocoding (Nominatim), site_manager,
                   mqtt_bridge (paho), sqlite_tickets
  presentation/    FastAPI API + config + wiring; serves the built UI
backend/tools/fake_robot.py   a fake VDA 5050 robot
frontend/                     React 18 + Vite + TypeScript + Leaflet
```

## API

These are the HTTP endpoints, all behind basic auth except `/api/health`. The OpenAPI docs are at `/api/docs`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/snapshot` | Robots (live state, trail), tickets, link status (the UI polls this every 1.5 s). |
| GET | `/api/config` | Datum, shop, routing provider, order settings. |
| GET | `/api/geocode?q=` | Address search. |
| POST | `/api/tickets` | Create a ticket: `{customer, lat, lon, address?, phone?, notes?}`. |
| POST | `/api/tickets/{id}/plan` | Preview the route, ETA and VDA 5050 order: `{robot}`. |
| GET | `/api/tickets/{id}/order.json?robot=` | Download the VDA 5050 order. |
| POST | `/api/tickets/{id}/assign` | Send it: `{robot}`. |
| POST | `/api/tickets/{id}/delivered` | Staff confirm the hand-off; the robot is sent back if `AUTO_RETURN` is on. |
| POST | `/api/tickets/{id}/return` | Send the robot back now. |
| POST | `/api/tickets/{id}/cancel` | Cancel: `{reason?}`. |
| POST | `/api/tickets/{id}/fail` | Mark failed: `{reason?}`. |
| POST | `/api/tickets/{id}/retry` | Back to `NEW`. |
| POST | `/api/robots/{name}/pause` | `startPause`. |
| POST | `/api/robots/{name}/resume` | `stopPause`. |
| POST | `/api/robots/{name}/return` | Send an idle robot back to the shop. |
