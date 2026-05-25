# frontend (operator UI)

Firewall-only React app — no AIGuardX agent/device/behavioral routes.

## Extract from

`../frontend/src/pages/firewall/`, `../frontend/src/components/firewall/`

## Env

- `VITE_CONTROL_API_URL` → control plane (8100)
- `VITE_GATEWAY_URL` → gateway (8300)

## Run

```bash
npm install && npm run dev
```
