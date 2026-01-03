# TimeTree Calendar for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![Maintainer](https://img.shields.io/badge/maintainer-acdcnow-blue)](https://github.com/acdcnow)
[![Version](https://img.shields.io/badge/version-1.1.3-green)]()

This is a custom component for **Home Assistant** that integrates with **TimeTree**. It creates a **Calendar entity** in Home Assistant that syncs with your chosen TimeTree calendar, allowing you to view **and create** events directly from your dashboard.

The integration fetches events based on a configurable polling interval (default: 60 minutes) and provides a "Last Updated" sensor to monitor sync status.

---

## 🏆 Credits & Huge Kudos

**Massive thanks to [eoleedi**](https://github.com/eoleedi) for creating the [TimeTree-Exporter](https://github.com/eoleedi/TimeTree-Exporter).

This Home Assistant integration relies heavily on the reverse-engineered API logic and data structures provided by their excellent work. Without `timetree-exporter`, this integration would not be possible!

---

## ✨ Features

* **Calendar Entity (Read & Write)**:
* View upcoming events in Home Assistant.
* **Create new events** in TimeTree directly from Home Assistant (via the Dashboard or Automations).


* **Configurable Auto-Sync**:
* Default polling is every 60 minutes.
* Adjustable via UI slider (5 minutes to 120 minutes).


* **Sync Monitoring**: Includes a diagnostic sensor (`sensor.timetree_last_updated`) showing exactly when the last successful sync occurred.
* **Multi-Calendar Support**: Select which specific TimeTree calendar to sync during setup.
* **Authentication**: Supports standard Email/Password login.

---

## 📦 Installation

### Option 1: HACS (Recommended)

1. Open **HACS** in Home Assistant.
2. Go to **Integrations** > Top right menu (**⋮**) > **Custom repositories**.
3. Add the URL of this repository.
4. Category: **Integration**.
5. Click **Add**, then find **TimeTree Calendar** in the list and click **Download**.
6. Restart Home Assistant.

### Option 2: Manual Installation

1. Download the latest release.
2. Copy the `custom_components/timetree` folder.
3. Paste it into your Home Assistant's `/config/custom_components/` directory.
4. Restart Home Assistant.

---

## ⚙️ Configuration

### Initial Setup

1. Navigate to **Settings** > **Devices & Services**.
2. Click **+ Add Integration**.
3. Search for **TimeTree Calendar**.
4. Enter your **TimeTree Email** and **Password**.
5. Select the **Calendar** you wish to sync.
6. Set your desired **Update Interval** (default: 60 min).

### Changing Settings (Update Interval)

You can change how often Home Assistant fetches data without reinstalling:

1. Go to **Settings** > **Devices & Services** > **TimeTree Calendar**.
2. Click **Configure**.
3. Adjust the **Scan Interval** slider (5 to 120 minutes).
4. Click **Submit** (The change takes effect immediately).

---

## 🐞 Troubleshooting & Debugging

If you encounter issues (e.g., "Unknown Error" or events not syncing), you can enable **verbose debug logging** to see exactly what is happening with the TimeTree API.

1. Open your `configuration.yaml` file.
2. Add the following logger configuration:

```yaml
logger:
  default: info
  logs:
    custom_components.timetree: debug

```

3. Restart Home Assistant.
4. Check your **Home Assistant Logs** (Settings > System > Logs). You will now see detailed messages, including:
* API Login attempts and Session ID retrieval.
* Full JSON payloads being sent to TimeTree (useful for checking Create Event issues).
* Raw responses and error codes from TimeTree.

---

## 🛠 Usage

### Creating Events

You can create events using the standard `calendar.create_event` service in scripts or automations:

```yaml
service: calendar.create_event
target:
  entity_id: calendar.timetree_family
data:
  summary: "Family Dinner"
  description: "Pizza night!"
  start_date_time: "2025-12-31 18:00:00"
  end_date_time: "2025-12-31 20:00:00"
  location: "Home"

```

### Monitoring

Check the **Last Updated** sensor (e.g., `sensor.timetree_calendar_last_updated`) to see the timestamp of the last successful API connection. This is useful for debugging connection issues.

---

## ⚠️ Limitations & Notes

* **Cloud Polling**: This integration requires an active internet connection to communicate with `timetreeapp.com`.
* **Rate Limits**: While the interval is configurable down to 5 minutes, be mindful of TimeTree's API limits. If you experience errors, increase the interval.
* **Internal API**: This integration uses TimeTree's internal API (simulating the web client). Changes to their backend could impact functionality.

---
