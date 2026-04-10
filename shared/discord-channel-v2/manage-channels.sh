#!/bin/bash
# Manage Discord channel access for SoY v2 bot.
# Usage:
#   manage-channels.sh add <channel_id>        — Add a channel
#   manage-channels.sh remove <channel_id>     — Remove a channel
#   manage-channels.sh list                    — List all channels
#   manage-channels.sh add-all                 — Add all project channels from SoY DB

ACCESS_FILE="$HOME/.claude/channels/discord/access.json"
OWNER_ID="162640938030727169"
DB_PATH="$HOME/.local/share/software-of-you/soy.db"

if [ ! -f "$ACCESS_FILE" ]; then
    echo "Error: $ACCESS_FILE not found"
    exit 1
fi

case "$1" in
    add)
        [ -z "$2" ] && echo "Usage: $0 add <channel_id>" && exit 1
        python3 -c "
import json, sys
with open('$ACCESS_FILE') as f: data = json.load(f)
cid = '$2'
if cid in data['groups']:
    print(f'Channel {cid} already exists')
    sys.exit(0)
data['groups'][cid] = {'requireMention': False, 'allowFrom': ['$OWNER_ID']}
with open('$ACCESS_FILE', 'w') as f: json.dump(data, f, indent=2)
print(f'Added channel {cid}')
"
        ;;
    remove)
        [ -z "$2" ] && echo "Usage: $0 remove <channel_id>" && exit 1
        python3 -c "
import json
with open('$ACCESS_FILE') as f: data = json.load(f)
cid = '$2'
if cid in data['groups']:
    del data['groups'][cid]
    with open('$ACCESS_FILE', 'w') as f: json.dump(data, f, indent=2)
    print(f'Removed channel {cid}')
else:
    print(f'Channel {cid} not found')
"
        ;;
    list)
        python3 -c "
import json
with open('$ACCESS_FILE') as f: data = json.load(f)
print(f'DM Policy: {data[\"dmPolicy\"]}')
print(f'Allowed Users: {len(data[\"allowFrom\"])}')
print(f'Channels: {len(data[\"groups\"])}')
for cid, cfg in data['groups'].items():
    mention = 'mention' if cfg.get('requireMention', True) else 'all messages'
    print(f'  {cid} ({mention})')
"
        ;;
    add-all)
        echo "Syncing project channels from SoY DB..."
        python3 -c "
import json, sqlite3
with open('$ACCESS_FILE') as f: data = json.load(f)
conn = sqlite3.connect('$DB_PATH')
rows = conn.execute('SELECT channel_id, project_name FROM discord_channel_projects').fetchall()
added = 0
for channel_id, project_name in rows:
    if channel_id not in data['groups']:
        data['groups'][channel_id] = {'requireMention': False, 'allowFrom': ['$OWNER_ID']}
        print(f'  Added #{project_name} ({channel_id})')
        added += 1
    else:
        print(f'  Already exists: #{project_name} ({channel_id})')
with open('$ACCESS_FILE', 'w') as f: json.dump(data, f, indent=2)
print(f'Done — {added} channels added, {len(rows)} total projects')
"
        ;;
    *)
        echo "Usage: $0 {add|remove|list|add-all} [channel_id]"
        exit 1
        ;;
esac
