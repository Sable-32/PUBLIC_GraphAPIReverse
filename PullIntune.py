import requests
import json
import random
import time
import re
from dotenv import load_dotenv
import os

load_dotenv()

SESSION_TOKEN = os.getenv("SESSION_TOKEN") #  
TENANT_URL = os.getenv("TENANT_URL")  # e.g. https://graph.microsoft.com

GRAPH_BASE = "https://graph.microsoft.com/v1.0"

HEADERS = {
    "Authorization": f"Bearer {SESSION_TOKEN}",
    "Content-Type": "application/json",
}

THROTTLE_MIN = 3   # seconds – lower bound of random delay
THROTTLE_MAX = 7  # seconds – upper bound of random delay



def _throttle():
    """Sleep for a random interval between THROTTLE_MIN and THROTTLE_MAX seconds."""
    delay = random.uniform(THROTTLE_MIN, THROTTLE_MAX)
    print(f"    [~] Throttling — waiting {delay:.1f}s before next request...")
    time.sleep(delay)



def get_user_id(upn: str) -> str | None:
    """Try UPN first, fall back to alternate domain casings and mail filter."""
    # Common fallback domains to try if UPN lookup fails (adjust as needed for your org)
    DOMAIN_FALLBACKS = [
        "@Domain.com",
        "@domain.com",
    ]

    # Build list of UPNs to try — start with the one passed in, then fallbacks
    username = upn.split("@")[0]
    candidates = [upn] + [username + d for d in DOMAIN_FALLBACKS if (username + d) != upn]

    for candidate in candidates:
        url = f"{GRAPH_BASE}/users/{candidate}"
        resp = requests.get(url, headers=HEADERS)
        _throttle()
        if resp.status_code == 200:
            print(f"    [~] Resolved via UPN: {candidate}")
            return resp.json().get("id")
        print(f"    [~] UPN failed for {candidate}, trying next...")

    # Final fallback: mail filter on original UPN
    print(f"    [~] All UPN attempts failed, trying mail filter...")
    filter_url = f"{GRAPH_BASE}/users?$filter=mail eq '{upn}'"
    resp2 = requests.get(filter_url, headers=HEADERS)
    _throttle()
    if resp2.status_code == 200:
        users = resp2.json().get("value", [])
        if users:
            print(f"    [~] Resolved via mail filter: {users[0].get('userPrincipalName')}")
            return users[0].get("id")

    print(f"[!] Could not resolve user '{upn}' after all fallbacks.")
    return None

def clean_emp_id(raw: str) -> str:
    """Strip whitespace, non-breaking spaces, and other invisible unicode."""
    cleaned = re.sub(r'[\s\u00a0\u200b\u200c\u200d\ufeff]+', '', raw)
    return cleaned.strip()


def get_managed_devices(user: str) -> list[dict]:
    """
    Return all Intune-managed devices for a given user.

    Args:
        user: UPN (email) or AAD object ID of the target user.

    Returns:
        List of device detail dicts, empty list on failure.
    """
    url = f"{GRAPH_BASE}/users/{user}/managedDevices"
    devices = []

    while url:
        resp = requests.get(url, headers=HEADERS)
        _throttle()
        if resp.status_code == 401:
            print("[!] 401 Unauthorized — your session token may be expired.")
            break
        if resp.status_code == 403:
            print("[!] 403 Forbidden — your account lacks DeviceManagementManagedDevices.Read.All.")
            break
        if resp.status_code != 200:
            print(f"[!] Unexpected error {resp.status_code}: {resp.text}")
            break

        data = resp.json()
        devices.extend(data.get("value", []))
        url = data.get("@odata.nextLink")  # follow pagination

    return devices


def get_device_compliance(device_id: str) -> dict:
    """Fetch compliance policy states for a specific managed device."""
    url = f"{GRAPH_BASE}/deviceManagement/managedDevices/{device_id}/deviceCompliancePolicyStates"
    resp = requests.get(url, headers=HEADERS)
    _throttle()
    if resp.status_code == 200:
        return resp.json().get("value", [])
    return {}


def get_device_configurations(device_id: str) -> list[dict]:
    """Fetch configuration profile states for a specific managed device."""
    url = f"{GRAPH_BASE}/deviceManagement/managedDevices/{device_id}/deviceConfigurationStates"
    resp = requests.get(url, headers=HEADERS)
    _throttle()
    if resp.status_code == 200:
        return resp.json().get("value", [])
    return []


def pull_device_info(users: list[tuple], include_compliance: bool = False, include_configs: bool = False) -> dict:
    results = {}

    # Load existing results to check for duplicates
    existing_results = {}
    if os.path.exists(RESULTS_FILE):
        with open(RESULTS_FILE, "r") as f:
            try:
                existing_results = json.load(f)
            except json.JSONDecodeError:
                existing_results = {}

    for user, ticket_id in users:
        print(f"\n[*] Fetching devices for: {user} (Ticket ID: {ticket_id})")

        # Skip if already in results file
        if user in existing_results:
            print(f"    [~] Skipping {user} — already exists in {RESULTS_FILE}")
            results[user] = existing_results[user]
            continue

        # Resolve UPN to AAD object ID first
        user_id = get_user_id(user)
        if not user_id:
            print(f"    [!] Could not resolve user ID for {user}, skipping.")
            error_entry = {
                "user": user,
                "ticket_id": ticket_id,
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "reason": "Could not resolve AAD object ID from UPN"
            }
            append_to_json(ERRORS_FILE, user, error_entry)
            continue

        print(f"    [+] Resolved {user} -> {user_id}")
        devices = get_managed_devices(user_id)

        if not devices:
            print(f"    No devices found (or access denied).")
            error_entry = {
                "user": user,
                "ticket_id": ticket_id,
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "reason": "No devices found or access denied"
            }
            append_to_json(ERRORS_FILE, user, error_entry)
            print(f"    [!] Error logged to {ERRORS_FILE}")
            results[user] = []
            continue

        print(f"    Found {len(devices)} device(s).")

        for device in devices:
            device_id = device.get("id")
            device["Ticket-ID"] = ticket_id  # attach ticket ID before filtering
            print(f"    -> {device.get('deviceName')} ({device.get('operatingSystem')} {device.get('osVersion')}) | Compliance: {device.get('complianceState', 'unknown')}")

            if include_compliance and device_id:
                device["_compliancePolicyStates"] = get_device_compliance(device_id)

            if include_configs and device_id:
                device["_configurationStates"] = get_device_configurations(device_id)

        # Strip unwanted fields before saving
        filtered_devices = [
            {k: v for k, v in d.items() if k in KEEP_FIELDS}
            for d in devices
        ]

        append_to_json(RESULTS_FILE, user, filtered_devices)
        print(f"    [+] Results for {user} saved to {RESULTS_FILE}")
        results[user] = filtered_devices

    return results

def print_summary(results: dict, output_file: str = "device_summary.txt"):
    FIELDS = [
        ("deviceName",        "Device Name"),
        ("manufacturer",      "Manufacturer"),
        ("model",             "Model"),
        ("serialNumber",      "Serial"),
        ("userPrincipalName", "UPN"),
        ("emailAddress",      "Email"),
        ("operatingSystem",   "OS"),
        ("ownerType",        "Owner Type"),
        ("Ticket-ID",         "Ticket ID"),
    ]

    with open(output_file, "w", encoding="utf-8") as f:
        for user, devices in results.items():
            f.write(f"\n{'='*60}\n")
            f.write(f" User: {user}  |  Devices: {len(devices)}\n")
            f.write(f"{'='*60}\n")
            for i, d in enumerate(devices, 1):
                f.write(f"\n  Device #{i}\n")
                for key, label in FIELDS:
                    val = d.get(key, "N/A")
                    if val not in (None, "", "N/A"):
                        f.write(f"    {label:<20}: {val}\n")

    print(f"[+] Summary written to {output_file}")


RESULTS_FILE = "device_results.json"
ERRORS_FILE = "device_errors.json"
# Modify fields to keep only those relevant to your needs and to reduce noise. You can always add more later by including them in the KEEP_FIELDS set and adjusting the print_summary function accordingly.
KEEP_FIELDS = {
    "id",
    "deviceName",
    "operatingSystem",
    "complianceState",
    "enrolledDateTime",
    "lastSyncDateTime",
    "manufacturer",
    "model",
    "serialNumber",
    "deviceownerType",
    "Ticket-ID",
}

def append_to_json(filepath: str, key: str, value):
    """Load existing JSON file (if any), append the new entry, and save."""
    if os.path.exists(filepath):
        with open(filepath, "r") as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError:
                data = {}
    else:
        data = {}

    data[key] = value

    with open(filepath, "w") as f:
        json.dump(data, f, indent=2, default=str)


if __name__ == "__main__":
    DOMAIN = "@domain.com" # adjust if your org uses a different email domain or if you want to try multiple domains in the get_user_id fallback logic
    JSON_FILE = "Tickets.json"  # expects list of dicts with "Id" and "Description" keys
    EMP_ID_PATTERN = r"Employee User ID:</strong>\s*(.*?)\s*(?:\(Delete\)|</p>)"

    with open(JSON_FILE, "r") as f:
        data = json.load(f)

    if isinstance(data, dict):
        data = [data]

    target_users = []  # list of tuples (upn, ticket_id)

    for entry in data:
        description = entry.get("Description", "")
        ticket_id = entry.get("Id")
        match = re.search(EMP_ID_PATTERN, description)
        if match:
            emp_id = clean_emp_id(match.group(1))
            target_users.append((emp_id + DOMAIN, str(ticket_id)))
        else:
            print(f"[!] No employee ID found in entry ID {entry.get('Id', '?')}: description did not match expected pattern")

    print(f"[+] Loaded {len(target_users)} user(s) from {JSON_FILE}")

    results = pull_device_info(
        users=target_users,
        include_compliance=False,
        include_configs=False,
    )

    print_summary(results, output_file="device_summary.txt")
    print(f"\n[+] All results saved to {RESULTS_FILE}")
    print(f"[+] Any errors saved to {ERRORS_FILE}")