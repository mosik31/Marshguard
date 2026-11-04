# MarshGuard

Hydrological digital twin and citizen reporting for the Velachery – Pallikaranai – Okkiyam corridor.
One FastAPI server runs the hydraulic model, stores reports and tickets, receives WhatsApp messages,
and pushes live updates to the command centre dashboard.

```
marshguard/
├── backend/
│   ├── app/
│   │   ├── main.py         API routes, live updates (SSE), WhatsApp webhook
│   │   ├── network.py      Drain graph (NetworkX), Manning's equation, root-cause logic
│   │   ├── pipeline.py     Citizen conversation: photo + location → verify → route → ticket
│   │   ├── whatsapp.py     Meta Cloud API: signature check, media download, replies
│   │   ├── classifier.py   YOLOv8 hook (optional) and fixed scores for simulator photos
│   │   ├── geo.py          Location pin → nearest channel → owning agency
│   │   └── store.py        SQLite storage (reports, tickets, channels, sessions)
│   ├── tests/              WhatsApp flow tests against a fake Meta API
│   ├── scripts/            fake_meta_webhook.py: check your webhook before connecting Meta
│   ├── requirements.txt
│   ├── .env.example
│   └── Dockerfile
└── frontend/
    ├── dashboard.html / dashboard.js   Command centre
    └── whatsapp.html / whatsapp.js     WhatsApp simulator
```

## Run it

```bash
cd backend
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # optional for the simulator
uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000/dashboard and http://localhost:8000/whatsapp side by side.
Send a photo and a location from the simulator: the report, ticket and network change appear on
the dashboard at once. Click **Mark cleared** on the dashboard and the citizen gets a message back.

With Docker, from the `marshguard/` folder:

```bash
docker build -f backend/Dockerfile -t marshguard .
docker run -p 8000:8000 --env-file backend/.env marshguard
```

## How a report flows

1. The citizen sends a photo and a location pin (two WhatsApp messages, either order).
2. The photo is classified. Below 0.70 confidence the bot asks for a clearer photo.
   Without YOLO weights, real photos go to **Citizen reports → Needs review** on the dashboard.
3. The pin is matched to the nearest channel within 2.5 km, which gives the owning agency (GCC, WRD or Forest Dept).
4. A ticket is raised, the channel's blockage is set, and the hydraulic model recalculates.
5. The dashboard updates live. Clearing the channel closes its tickets and notifies each reporter.
6. Citizens can reply `STATUS MG-3100` at any time.

## Connect a real WhatsApp number

The server already speaks the WhatsApp Cloud API. You only need a Meta app and a public HTTPS address.

### 1. Get a public HTTPS address for the server

Meta can only deliver messages to a public `https://` URL. Pick one:

| Option | How | Notes |
|---|---|---|
| ngrok (laptop) | `ngrok http 8000` | URL changes every restart on the free plan; update it in Meta each time |
| GitHub Codespaces | Run the server, then **Ports → 8000 → Port visibility → Public** | Works only while the Codespace is running |
| Render | See the hosting guide | Free service sleeps after 15 min; open it before testing |

Your webhook URL is that address plus `/webhook/whatsapp`.

### 2. Create the Meta app

1. Go to developers.facebook.com → **My Apps → Create App**. Choose the use case
   **Connect with customers through WhatsApp** and select or create a business portfolio.
2. Open **WhatsApp → API Setup**. Meta gives you a free **test phone number**.
3. Under **To**, add the phones that will test the bot (each person confirms with a code).
   The test number can only message numbers on this list.
4. Copy into `backend/.env`:
   - **Temporary access token** → `WHATSAPP_TOKEN`
   - **Phone number ID** (not the phone number itself) → `WHATSAPP_PHONE_NUMBER_ID`
5. **App settings → Basic → App secret → Show** → `WHATSAPP_APP_SECRET`
6. Choose a secret word → `WHATSAPP_VERIFY_TOKEN`
7. Restart the server. The dashboard's **WhatsApp connection** panel should say *Connected*.

### 3. Check your server before involving Meta

```bash
cd backend
python scripts/fake_meta_webhook.py https://<your-public-host> text hi
```

Both lines must say PASS / 200. If not, fix the URL, verify token or app secret first.

### 4. Point Meta at your server

1. **WhatsApp → Configuration → Webhook → Edit**.
2. Callback URL: `https://<your-public-host>/webhook/whatsapp`. Verify token: your `WHATSAPP_VERIFY_TOKEN`.
3. **Verify and save**. Then under **Webhook fields**, **Subscribe** to `messages`.

### 5. Try it from a phone

From a phone on the test list, message the test number: send `hi`, then a photo, then tap
**Send location**. The report appears on the dashboard. Without YOLO weights it waits in
**Citizen reports → Needs review**; click *View photo*, choose the blockage and **Verify**.
The citizen gets the ticket number on WhatsApp. **Mark cleared** sends them the closing message.

### What the server handles for you

- **Signature check** on every incoming webhook (`X-Hub-Signature-256`) when the app secret is set.
- **Duplicates**: Meta sometimes delivers the same message twice; each message id is processed once.
- **Read receipts**: citizens see blue ticks when the bot has read their message.
- **Send location button** instead of asking people to find the 📎 menu.
- **24-hour rule**: WhatsApp allows free-form replies only within 24 hours of the citizen's last
  message. Later updates (for example, a channel cleared two days later) need an approved template.
  Create one in WhatsApp Manager → Message templates: category **Utility**, language English,
  body `Update on your MarshGuard report {{1}}: {{2}}`. When approved, set its name as
  `WHATSAPP_TEMPLATE_NAME`. Without it, late updates are skipped and the dashboard shows why.
- **Privacy**: the dashboard shows only the last four digits of a reporter's number.
  Photos are served only through `/api/reports/{id}/photo`.

### Common problems

| Symptom | Cause and fix |
|---|---|
| "The callback URL or verify token couldn't be validated" | Server not reachable at that URL, or verify token differs from `.env`. Run the fake webhook script. |
| Messages arrive in the log but no reply | Token expired (temporary tokens last about a day) or the phone is not on the test list. The **Last error** line on the dashboard shows Meta's message. |
| `401 Bad signature` in the server log | `WHATSAPP_APP_SECRET` is wrong; copy it again from App settings → Basic. |
| Nothing reaches the server | You did not subscribe to the `messages` field, or ngrok / Codespace restarted with a new URL. |
| Error 131047 in Last error | More than 24 h since the citizen's last message. Set up the template above. |

### Beyond testing

For a token that does not expire, create a **System user** in Meta Business settings, assign it the
app and WhatsApp account with full control, and generate a token with `whatsapp_business_messaging`
and `whatsapp_business_management` permissions. To message the public (not just the test list), add
a real phone number to the WhatsApp account and complete Meta's business verification.

### Run the automated tests

```bash
cd backend
pytest -q
```

These run the full WhatsApp flow against a fake Meta API: handshake, bad signatures, photo download,
location button, manual review, duplicates, and the 24-hour template fallback.

## Plug in YOLOv8

Train a detection or classification model with classes `plastic`, `hyacinth`, `silt`, `rubble`, then:

```bash
pip install ultralytics
# in .env
MARSHGUARD_YOLO_WEIGHTS=/path/to/best.pt
```

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/state` | Full model state: channels, actions, alert, reports |
| GET | `/api/stream` | Server-Sent Events: state updates and bot messages |
| POST | `/api/scenario` | `{rain, tideHigh, marshHealthy}` |
| POST | `/api/rain/live` | Pull this hour's rainfall for Velachery from Open-Meteo |
| POST | `/api/channels/{id}/blockage` | `{blockage}` set by an operator |
| POST | `/api/channels/{id}/clear` | Mark cleared, close tickets, notify reporters |
| POST | `/api/reports/{id}/review` | `{cls}` to verify, `{cls: null}` to reject |
| POST | `/api/sim/message` | Simulator: `{phone: "sim-…", type, text \| sample \| channel}` |
| GET/POST | `/webhook/whatsapp` | Meta verification and incoming messages |
| GET | `/api/reports/{id}/photo` | Citizen's photo, for manual review |
| GET | `/api/whatsapp/status` | Connection status and last error |
| POST | `/api/reset` | Reset demo data |

## What is still a placeholder

- Channel sections, slopes, roughness, catchment areas and coordinates are illustrative. Replace
  them in `network.py` with GCC / WRD drain survey data.
- SQLite stands in for PostgreSQL + PostGIS. `geo.py` uses straight lines between nodes;
  production should use surveyed drain geometry with `ST_DWithin`.
- Simulator photos use fixed classifier scores.
- CAP alerts are drafts for TNSDMA approval; nothing is broadcast.
- The dashboard has no login. Add authentication before exposing it beyond a demo.
