# Drain-routing backend handoff

This folder contains the tested selector source and the files needed to call the **Web app** project's hosted Momen backend from your existing app. It is not an installer, a Momen project export, or a standalone server. No real drain records or API credentials are included.

## Try the endpoint from your PC

1. Open PowerShell in this folder.
2. With Node.js 20 or later installed, set the webhook URL (ask the team for it; it is not committed because this repo is public): `$env:DRAIN_WEBHOOK_URL = '<url>'`, then run `node .\find-drain.mjs 0 20 500`.
3. `NO_MATCH` is expected while the drain register is empty. Add your real records in the Momen editor under **Data → drain**; then rerun with a latitude and longitude near those records. For each record, `name`, `location`, `urgency_score`, and `is_active` are required (`is_active` defaults to true). `external_id`, `address`, and `urgency_reason` are optional.

## Connect your existing app

Send a JSON **POST** from your app's server to:

```text
$DRAIN_WEBHOOK_URL   # https://villa.momen.app/zero/<project>/callback/<webhook-id>
```

Send `Content-Type: application/json`, with a body like:

```json
{"latitude":0,"longitude":20,"max_walking_distance_meters":500}
```

Use decimal coordinates (latitude −90…90; longitude −180…180), and a nonnegative distance in meters. Check HTTP status before using the JSON body. HTTP 200 responses carry a business status:

- `FOUND`: includes `drain_id`, `drain_name`, `urgency_score`, `straight_line_distance_meters`, `directions_url`, and, where present, address or urgency details. Give the user `directions_url` to open walking directions in Google Maps.
- `NO_MATCH`: no active drain was within the straight-line limit.
- `INVALID_INPUT`: the numerical inputs were outside the allowed range.

These are outcomes, not equivalent to HTTP 500 or a network failure. Use the request ID (`x-request-id`) to look up a failed call in the Momen runtime logs. This webhook is callable without credentials if someone has its URL. Keep the URL on your server, not in public browser or mobile code, and review access before going live. The project currently permits anonymous writes to `drain`; restrict them before trusting a production drain register.

## Selection and throughput limits

- The selector reads at most **1,000** rows, excludes inactive drains and candidates beyond the **straight-line** limit, then picks the greatest urgency score, with shorter distance breaking a tie. `max_walking_distance_meters` is the request field name, not a routed-distance guarantee. A real Google Maps walking route may be longer. No Google Maps Routes API is called.
- The live project's logs showed a limit of **one mutation request per second**, including this webhook. Rapid requests can return HTTP 500 (`fail to trigger callback`) before the selector runs. The functional checks were spaced by 1.5 seconds, without retries. If multiple callers share this project, limit traffic across them rather than relying on a per-user delay.
- Ten distinct live checks passed on 2026-11-21 UTC. The six temporary fixture records were removed, and the empty register returned `NO_MATCH` as expected. See `qa-results.json` for expected and actual outcomes.

## Files and editor locations

- `find-drain.mjs`: small Node.js command-line example for the live endpoint. It makes one request and does not retry.
- `openapi.json`: the webhook's JSON request and response contract.
- `select-drain.momen.js`: exact source of the selector's Momen Run Code node; it uses the Actionflow's `context`, not a standalone Node.js server.
- `qa-results.json`: completed live verification report with request IDs. No real drain data is included.
- In the Web app's editor, open **Action → Actionflow → Find highest-priority nearby drain**. **Load drain register** reads `drain`; **Choose most urgent eligible drain** receives selected fields and ranks them. The **Find nearby urgent drain** webhook is in the Actionflow's **Triggers** tab.
