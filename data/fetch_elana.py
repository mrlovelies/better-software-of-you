import subprocess, json, urllib.request, sys

token = subprocess.check_output(
    ["python3", "/home/mrlovelies/.software-of-you/shared/google_auth.py", "token"],
    stderr=subprocess.DEVNULL
).decode().strip()

# Search for messages from elana
url = "https://gmail.googleapis.com/gmail/v1/users/me/messages?q=from%3Aelana&maxResults=10"
req = urllib.request.Request(url, headers={"Authorization": "Bearer " + token})
with urllib.request.urlopen(req) as resp:
    search_data = json.loads(resp.read())

messages = search_data.get("messages", [])
print(f"Found {len(messages)} messages")

results = []
for msg in messages:
    msg_id = msg["id"]
    detail_url = "https://gmail.googleapis.com/gmail/v1/users/me/messages/" + msg_id + "?format=metadata&metadataHeaders=Subject&metadataHeaders=From&metadataHeaders=Date"
    req2 = urllib.request.Request(detail_url, headers={"Authorization": "Bearer " + token})
    with urllib.request.urlopen(req2) as resp2:
        detail = json.loads(resp2.read())

    headers = {h["name"]: h["value"] for h in detail.get("payload", {}).get("headers", [])}
    result = {
        "id": msg_id,
        "subject": headers.get("Subject", "(no subject)"),
        "from": headers.get("From", ""),
        "date": headers.get("Date", ""),
        "snippet": detail.get("snippet", "")
    }
    results.append(result)
    print(json.dumps(result, indent=2))
