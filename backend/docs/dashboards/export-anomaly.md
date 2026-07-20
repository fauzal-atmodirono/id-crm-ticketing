# Metrics Export and Anomaly Detection

This document covers the two endpoints added in the `metrics export + anomaly` item and the configuration keys that control them.

---

## Endpoints

### `GET /metrics/export`

Downloads the full bot-metrics dashboard as a file attachment.

| Query param | Values | Default | Description |
|---|---|---|---|
| `format` | `xlsx` or `pdf` | `xlsx` | File format of the report |

**`format=xlsx`** returns an Excel workbook (`application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`) with one sheet per dashboard view (volume, resolution, CSAT, NPS, speed, fallback, bounce, quality). The response body begins with the `PK` ZIP magic bytes.

**`format=pdf`** returns a PDF (`application/pdf`) with the same data in tabular layout. The response body begins with `%PDF-`.

Any other value returns `400 Bad Request`.

The `Content-Disposition` header is always `attachment; filename=bot-metrics.xlsx` (or `.pdf`), so browsers trigger a download automatically.

---

### `GET /metrics/anomalies`

Returns the currently-flagged channel-volume anomalies as JSON.

**Response shape:**

```json
{
  "anomalies": [
    {
      "channel": "whatsapp",
      "current_volume": 260,
      "baseline_mean": 90.0,
      "z_score": 11.3
    }
  ]
}
```

Each item in `anomalies` has four fields:

| Field | Type | Description |
|---|---|---|
| `channel` | string | Channel name (e.g. `web`, `whatsapp`, `phone`) |
| `current_volume` | int | Ticket/conversation count in the current window |
| `baseline_mean` | float | Mean volume over the baseline window |
| `z_score` | float | Standard deviations above the baseline mean |

An empty `anomalies` list means no channel is currently breaching the threshold.

#### How anomalies are detected

The BigQuery view `v_channel_anomaly` provides per-channel statistics: the current window's volume, the baseline mean, and the baseline standard deviation. The app then applies:

```
z = (current_volume - baseline_mean) / baseline_stddev
flag if z > anomaly_zscore_k  AND  baseline_mean >= anomaly_min_baseline
```

Channels with `baseline_stddev <= 0`, a null mean, or a baseline mean below `anomaly_min_baseline` are skipped (not enough history to flag).

---

## Configuration Keys

### SMTP / Report delivery

All keys are read from environment variables (or `.env` at the backend root).

| Key | Default | Description |
|---|---|---|
| `REPORT_ENABLED` | `false` | Master switch. **Must be `true`** for any report emails to be sent or the report scheduler to start. |
| `SMTP_HOST` | `""` | SMTP server hostname. If empty when `REPORT_ENABLED=true`, the mock (no-op) email adapter is used. |
| `SMTP_PORT` | `587` | SMTP server port. `STARTTLS` is always attempted. |
| `SMTP_USER` | `""` | SMTP username. Login is skipped when empty. |
| `SMTP_PASSWORD` | `""` | SMTP password. |
| `SMTP_FROM` | `""` | Envelope sender address (the `From:` header). |
| `REPORT_RECIPIENTS` | `""` | Comma-separated list of recipient email addresses. No email is sent when empty. |
| `REPORT_INTERVAL_HOURS` | `24` | How often the scheduled report job fires (hours). |

#### `report_enabled` default-off behaviour

`REPORT_ENABLED` defaults to `false`. When false:

- The report scheduler does **not** start (no background thread is created).
- The `build_email_report_port` factory returns `MockEmailReport`, which records calls in-memory but never opens an SMTP connection.

Set `REPORT_ENABLED=true` and supply `SMTP_HOST` to activate live email delivery.

---

### Anomaly detection thresholds

| Key | Default | Description |
|---|---|---|
| `ANOMALY_ZSCORE_K` | `3.0` | Z-score multiplier `k`. A channel is flagged when its current volume is more than `k` standard deviations above the baseline mean. |
| `ANOMALY_MIN_BASELINE` | `20` | Minimum baseline mean. Channels whose historical average is below this value are skipped to avoid false positives from low-traffic channels. |

Lowering `ANOMALY_ZSCORE_K` makes detection more sensitive (more alerts). Raising `ANOMALY_MIN_BASELINE` requires more historical volume before a channel can be flagged.

---

## Scheduled alert emails

When `REPORT_ENABLED=true` and `REPORT_RECIPIENTS` is non-empty, the report scheduler:

1. Renders the dashboard as both `.xlsx` and `.pdf` and emails them to all recipients every `REPORT_INTERVAL_HOURS` hours.
2. After each render, queries `v_channel_anomaly`, runs the z-score check, and sends a separate "Channel anomaly detected" alert email listing each flagged channel if any are found.

The scheduler runs in a background thread and never propagates exceptions to the web process — failures are logged at `ERROR` level via structlog but do not affect HTTP serving.
