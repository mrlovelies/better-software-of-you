---
description: Set up the cc wrapper for auto-handoff on session exit
allowed-tools: ["Bash", "Read"]
---

# Session Setup — Auto-Handoff Wrapper

Install the `cc` shell wrapper so every Claude Code session automatically generates a handoff on exit. Run this on each machine where you use Software of You.

## Step 1: Make the wrapper executable

```bash
chmod +x "${CLAUDE_PLUGIN_ROOT:-$(pwd)}/shared/cc"
```

## Step 2: Create ~/bin and symlink

```bash
mkdir -p "$HOME/bin"
ln -sf "${CLAUDE_PLUGIN_ROOT:-$(pwd)}/shared/cc" "$HOME/bin/cc"
```

## Step 3: Check PATH

```bash
echo "$PATH" | tr ':' '\n' | grep -q "$HOME/bin" && echo "OK" || echo "MISSING"
```

If MISSING, tell the user to add `~/bin` to their PATH:

**For zsh** (macOS default, also WSL2 if using zsh):
```
echo 'export PATH="$HOME/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
```

**For bash** (some Linux/WSL2):
```
echo 'export PATH="$HOME/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc
```

## Step 4: Verify

```bash
which cc
```

Should show `$HOME/bin/cc`.

## Step 5: Report

If everything succeeded:

"Auto-handoff is set up on **$(hostname -s)**. From now on, use `cc` instead of `claude` to start sessions:

```
cc                                → interactive session with auto-handoff
cc --dangerously-skip-permissions → autonomous mode
cc -p "question"                  → pipe mode (no handoff)
```

When you exit an interactive session, `cc` automatically generates a handoff to the database. Telegram and any new Claude Code session will see it.

If there's an active handoff when you start, you'll see a one-line reminder to run `/pickup`."
