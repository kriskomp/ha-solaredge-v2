# SolarEdge Monitoring API v2 for Home Assistant

**English** | [Polski](README.pl.md)

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories)
[![Test](https://github.com/kriskomp/ha-solaredge-v2/actions/workflows/test.yml/badge.svg)](https://github.com/kriskomp/ha-solaredge-v2/actions/workflows/test.yml)
[![Release](https://img.shields.io/github/v/release/kriskomp/ha-solaredge-v2)](https://github.com/kriskomp/ha-solaredge-v2/releases)

A Home Assistant integration that reads a SolarEdge site through the **Monitoring API v2**
(`monitoringapi.solaredge.com/v2`). SolarEdge switches off the old V1 API (`?api_key=`), which
the built-in `solaredge` integration uses, at the beginning of November 2026.

The integration is built for the **free plan (2,000 credits a month)**:

- **One credit per poll.** One call for the inverter telemetry (15-minute steps since midnight)
  returns power, today's energy, the day's peak, and voltage, current and frequency.
- **Polls only in daylight**, from sunrise to sunset, plus one final read after sunset.
- **Its own credit counter**, because the API does not report monthly usage. The billing cycle
  starts on a day of the month you choose, and you can correct the counter with the number shown
  in the developer portal.
- **Restarting Home Assistant costs no credits.** The state is saved in `.storage`. After a
  restart the sensors show their last values right away, and the next poll happens at
  `last poll + interval`.
- **Successor of the built-in `solaredge` integration.** The setup can take over its sensors with
  the same `entity_id` and their full history, so graphs, statistics and the Energy dashboard
  continue without a gap.
- **Energy this month, this year and lifetime** cost one credit a day. The integration builds
  them from an API baseline plus today's energy.
- **SolarEdge consent without copy and paste.** Home Assistant receives the redirect itself after
  you click "Approve".
- **Two polling modes:**
  - *fixed*: one interval all year, sized for the longest day;
  - *adaptive*: the credits left are spread over the daylight left in the cycle.

The integration's interface in Home Assistant (setup, options, sensor names, notifications) is
available in English and Polish and follows the language set in your Home Assistant profile.

## Sensors

| Sensor | Cost | Notes |
|---|---|---|
| Current power | per poll | 15-minute average, the latest interval is updated as it fills; **0 W** when there has been no data for more than 60 min (night, inverter off) |
| Energy today | per poll | sum of the inverter's 15-minute Wh; suitable for the Energy dashboard |
| Peak power today | per poll | attribute `peak_time` |
| AC voltage / current / frequency (per inverter) | per poll | |
| Energy this month / this year / lifetime | 1 credit/day | option (on by default) |
| Open alerts | 1 credit/day | option |
| Consumption / import / export / self-consumption today | +1 credit per poll | option, only for sites with a meter |
| Battery | +1 credit per poll | option, experimental |
| Diagnostics: credits used, remaining and projected, poll interval, next poll, data time, consent expiry | 0 | |

The more "per poll" sensor groups you enable, the less often the integration can poll. Before
saving the options, the integration shows the resulting interval.

## Installation (HACS)

1. HACS → ⋮ → *Custom repositories* → `https://github.com/kriskomp/ha-solaredge-v2`, type
   *Integration*.
2. Install **SolarEdge Monitoring API v2** and restart Home Assistant.

The integration uses the domain `solaredge_v2`, like other integrations based on API v2, so only
one of them can be installed. The built-in `solaredge` integration can run alongside it.

## Configuration

### 1. Redirect URL: what to enter

After you click **Approve** on the SolarEdge page, the browser goes to the *redirect URL* with an
authorization code. The integration supports two variants:

| Variant | Redirect URL | How it works |
|---|---|---|
| **Recommended** | `https://<your-HA-address>/api/solaredge_v2/callback` | Home Assistant receives the code and finishes the setup by itself, no copy and paste. |
| Fallback | `http://localhost/callback` | The browser shows a connection error and you paste the whole address from the address bar into the setup dialog. |

`<your-HA-address>` is the address at which **your browser** opens Home Assistant over HTTPS,
for example:
- your own domain behind a reverse proxy (e.g. `ha.example.com`);
- a Nabu Casa address (`xxxxxxxx.ui.nabu.casa`);
- DuckDNS (`yourname.duckdns.org`).

Requirements:
- **HTTPS with a publicly registered domain only**: the SolarEdge console does not accept an IP
  address, `homeassistant.local` or `http://` (except `localhost`);
- the address must open **in the browser in which you click Approve**. SolarEdge's servers do
  not connect to Home Assistant; the browser follows the redirect;
- if you use a reverse proxy, the path `/api/...` must reach Home Assistant, which is the
  standard setup;
- in Home Assistant, set this address as the **external URL** (*Settings → System → Network →
  Home Assistant URL → Internet*). The setup then suggests it automatically.

### 2. Application in the SolarEdge developer console

At <https://developer.solaredge.com> (sign in with your mySolarEdge account):

1. **Create Application**, service **Monitoring API**, access type **Site Access**.
2. Scopes **SITE_DATA** and **DEVICE_DATA**.
3. **Allowed Redirect URL(s)**: enter the redirect URL from step 1. You can add both variants
   (*Add Redirect URL*); then click *Save Changes*. The address must be **identical** to the one
   in Home Assistant, including `https://` and the `/api/solaredge_v2/callback` ending.
4. Save the **Client ID** and **Client Secret**. The secret is shown only once (*Download as
   .env*).

On the *Dashboard* page, check the **Subscription start date**. Its day of the month is the day
the credits reset (the *billing day* option).

### 3. Adding the integration

*Settings → Devices & services → Add integration → SolarEdge Monitoring API v2*:

1. Enter the Client ID and Client Secret. The **Redirect URL** field is filled with Home
   Assistant's external URL plus `/api/solaredge_v2/callback`. It must match the one in the
   SolarEdge application.
2. **Recommended variant:** a SolarEdge window opens. Sign in with your mySolarEdge account and
   click **Approve**. You see a "Done" page and the setup in Home Assistant continues by itself.
3. **Fallback (localhost):** after **Approve** the browser shows a connection error. Copy the
   **whole address from the address bar** (`http://localhost/callback?code=...&site_id=...`) and
   paste it into the setup dialog. The code is valid only for a short time.

If something goes wrong in the recommended variant (for example the address does not match the
one in the SolarEdge application), the setup shows the reason and lets you paste the address by
hand.

Why does the integration have its own redirect address instead of Home Assistant's built-in
OAuth flow? SolarEdge does not return the `state` parameter that the built-in OAuth flow (and
my.home-assistant.io) requires; verified on 16.10.2026. The integration therefore hands the code
to the setup flow that is waiting for the consent.

### Changing the redirect URL or the application details

On the integration card: **⋮ → Reconfigure**. You can change the Client ID, Client Secret and
Redirect URL there, for example to switch from `localhost` to your Home Assistant address or to
a new domain. You then approve access again. Sensors, history and the credit counter stay.

Change the redirect URL here if you removed from the SolarEdge application the address the
integration used before. SolarEdge would reject the old address at the next authorization.

### Re-authentication

SolarEdge asks for it when the refresh token expires (after 30 days without use) or when the
24-month consent ends. Home Assistant shows a notification. The flow first asks you to confirm
the Redirect URL (suggesting your Home Assistant address) and then works like the initial setup.

### Moving from the built-in `solaredge` integration

If the built-in SolarEdge integration (API v1) is set up for the same site, the setup offers to
**take over its sensors**: current power and energy today, this month, this year and lifetime.

The taken-over entities keep **the same `entity_id`, name, area, unit and their full history**
(graphs, statistics, Energy dashboard). Dashboards and automations keep working unchanged.

The built-in integration is **disabled**, not deleted. Delete it yourself once everything works.
If *Repairs* offers to delete the statistics of the old sensors, do not.

### 4. Options

*Configure* on the integration: polling mode, sensor groups, monthly limit, billing day, reserve
and minimum interval. After you submit the form, the integration shows how often it will poll
with the new settings.

## Polling on demand

`homeassistant.update_entity` on this integration's sensors **does not call the API**, so "refresh
every X minutes" automations kept from the built-in integration do not spend credits. To poll
outside the schedule, use the `solaredge_v2.refresh` action.

## Actions

- `solaredge_v2.set_credits_used`: sets the local counter to the number shown in the portal
  (*Dashboard → Credit cycle*).
- `solaredge_v2.refresh`: polls immediately (spends credits).

## Cache and credit counter (`.storage`)

Each site has its own private file `/config/.storage/solaredge_v2.<entry_id>` holding:

- **token**: the current access/refresh token pair, saved immediately after every rotation;
- **credit counter**: cycle start, credits used and whether the limit is exhausted;
- **readings**: site and inverter data, today's power, energy, peak and V/I/f, the month, year
  and previous-years baselines, alerts and the time of the last poll.

Rules (the same as in [kriskomp/ha_Pstryk](https://github.com/kriskomp/ha_Pstryk)):

- a request is **counted before it is sent**, and the counter reaches the file within a second,
  so neither a timeout nor a Home Assistant crash during a poll loses credits;
- the counter **rather overestimates than underestimates**: failed requests are counted too;
- a damaged file or a damaged part of it **does not block start-up**: each section is read
  separately and an unreadable one is skipped with a warning in the log;
- a failed write is logged and does not stop polling;
- the file is deleted together with the integration.

## Tokens and consent

- The access token is valid for 2 hours and renewed automatically.
- **Every refresh returns a new refresh token and the old one stops working at once.** The
  integration saves the new token immediately. Do not use the same credentials in two places at
  the same time (for example in a test script and in Home Assistant).
- A refresh token expires after 30 days without use. The integration refreshes it in time, also
  while the credits are exhausted.
- Site Access consent lasts at most 24 months. 30 days before it ends, a notification appears in
  *Repairs*.

## Findings from tests against the live API (16–18.10.2026)

- `from`/`to` must be an *Instant* (`2026-10-16T05:53:19Z` or with an offset). Local time without
  a zone returns HTTP 400.
- 15-minute data for a whole day (more than 12 hours) is accepted for `/power` and
  `/inverters/telemetry`.
- `resolution=MONTH` accepts a window of at most 3 years; `YEAR` has no limit.
- Rate-limit headers: only `x-ratelimit-limit-minute` (10 on the Free plan) and
  `x-ratelimit-remaining-minute`. There is no monthly counter.
- `GET /sites` with an OAuth token returns 403 (it works only with a Fleet key).
- API v2 does not provide optimizer data or live power flow (power flow is a paid plan).
- With the inverter off, or at night, the 15-minute intervals are `"value": null`. API v1 kept
  showing the last known power in that case.

## Changelog and releases

See [CHANGELOG.md](CHANGELOG.md) and
[Releases](https://github.com/kriskomp/ha-solaredge-v2/releases). Versions use the format
`YYYY.MM.DD.N`. A release (tag `v<version>` with notes from the changelog) is published
automatically when the version in `manifest.json` changes on `main` and the tests pass.

## Origin

Fork of [trooperthorn/ha_int_solaredge](https://github.com/trooperthorn/ha_int_solaredge) (MIT).
The API client and the response models come from the original. Authorization, scheduling, the
credit counter, the cache and the sensors were rewritten.
