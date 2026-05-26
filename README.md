# Intune Device Info Puller

A Python script that queries the **Microsoft Graph API** to retrieve Intune-managed device information for a list of users parsed from a ticketing system export.

---

## What It Does

1. Reads a `Tickets.json` file containing support ticket data.
2. Extracts employee user IDs from each ticket's HTML description using a regex pattern.
3. Resolves each employee ID to an Azure AD object ID via the Graph API (with domain fallback logic).
4. Fetches all Intune-managed devices for each resolved user.
5. Optionally pulls compliance policy states and/or configuration profile states per device.
6. Saves filtered results to `device_results.json` and a human-readable summary to `device_summary.txt`.
7. Logs any unresolvable users or empty device responses to `device_errors.json`.

---

## Prerequisites

- Python 3.10+
- An Azure AD **Bearer token** with the following permissions:
  - `User.Read.All`
  - `DeviceManagementManagedDevices.Read.All`
- A valid `Tickets.json` export (see [Input Format](#input-format) below)

---

## Installation

```bash
git clone <your-repo-url>
cd <repo-folder>
pip install requests python-dotenv
```

---

## Configuration

Create a `.env` file in the project root:

```env
SESSION_TOKEN=your_bearer_token_here
TENANT_URL=https://graph.microsoft.com
```

> ⚠️ **Never commit your `.env` file.** Add it to `.gitignore`.

You can also adjust the following constants directly in the script:

| Constant | Default | Description |
|---|---|---|
| `THROTTLE_MIN` | `3` | Minimum seconds to wait between API requests |
| `THROTTLE_MAX` | `7` | Maximum seconds to wait between API requests |
| `DOMAIN` | `@domain.com` | Email domain appended to extracted employee IDs |
| `JSON_FILE` | `Tickets.json` | Path to your ticket export file |
| `RESULTS_FILE` | `device_results.json` | Output file for device data |
| `ERRORS_FILE` | `device_errors.json` | Output file for unresolved users |
| `KEEP_FIELDS` | *(see script)* | Set of device fields from Intune to retain in output |

---

## Input Format

`Tickets.json` should be a JSON array of ticket objects. Each object must have an `Id` field and a `Description` field containing HTML. The script looks for the following pattern in the description:

```html
<strong>Employee User ID:</strong> EMP12345 (Delete)
```

**Example:**

```json
[
  {
    "Id": "10042",
    "Description": "<p><strong>Employee User ID:</strong> jsmith (Delete)</p>"
  }
]
```

---

## Usage

```bash
python solution.py
```

### Optional Flags (in-script)

When calling `pull_device_info()`, you can enable additional API calls per device:

```python
results = pull_device_info(
    users=target_users,
    include_compliance=True,   # Fetches compliance policy states
    include_configs=True,      # Fetches configuration profile states
)
```

> Enabling these will significantly increase runtime and API call volume due to per-device requests.

---

## Output Files

| File | Description |
|---|---|
| `device_results.json` | Filtered device data keyed by user UPN |
| `device_errors.json` | Users that could not be resolved or had no devices |
| `device_summary.txt` | Human-readable summary of all devices per user |

**Example `device_summary.txt` entry:**
```
============================================================
 User: jsmith@domain.com  |  Devices: 1
============================================================

  Device #1
    Device Name         : LAPTOP-ABC123
    Manufacturer        : Dell
    Model               : Latitude 5540
    Serial              : 1A2B3C4D
    OS                  : Windows
    Owner Type          : company
    Ticket ID           : 10042
```

---

## Resume / Deduplication

The script automatically skips users that already exist in `device_results.json`. This means if a run is interrupted, you can simply re-run the script and it will pick up where it left off without duplicating data or making redundant API calls.

---

## Throttling

All API requests are separated by a randomized delay (`THROTTLE_MIN`–`THROTTLE_MAX` seconds) to avoid hitting Graph API rate limits. Adjust these values in the script if needed.

---

## Troubleshooting

| Error | Likely Cause |
|---|---|
| `401 Unauthorized` | Bearer token is expired — generate a new one |
| `403 Forbidden` | Token lacks `DeviceManagementManagedDevices.Read.All` |
| `No employee ID found in entry ID X` | Ticket description doesn't match the expected HTML pattern |
| `Could not resolve user` | UPN/domain mismatch — check `DOMAIN` and `DOMAIN_FALLBACKS` in `get_user_id()` |

---

## Notes

- The script **does not modify or delete** any data in Azure AD or Intune — it is read-only.
- Results are incrementally written to disk after each user is processed, so partial data is preserved even if the script crashes mid-run.
- The `KEEP_FIELDS` set controls which device properties are saved. Edit it to add or remove fields as needed, and update `print_summary()` accordingly if you want them reflected in the text output.
