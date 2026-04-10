#!/bin/bash
# Database sync — scheduled backup/export for cross-machine sync.
#
# Syncthing syncs the codebase but NOT the live database.
# This script creates a point-in-time snapshot that Syncthing CAN safely sync.
# Other machines can import from the snapshot when needed.
#
# Usage:
#   db_sync.sh backup     # Create an exportable snapshot
#   db_sync.sh import     # Import from another machine's snapshot (if newer)
#   db_sync.sh status     # Show sync state

DB_DIR="${HOME}/.local/share/software-of-you"
DB_PATH="${DB_DIR}/soy.db"
EXPORT_PATH="${DB_DIR}/soy.db.sync-export"
EXPORT_META="${DB_DIR}/soy.db.sync-meta"
HOSTNAME=$(hostname)

case "$1" in
    backup)
        # Check integrity first
        INTEGRITY=$(sqlite3 "$DB_PATH" "PRAGMA integrity_check;" 2>&1 | head -1)
        if [ "$INTEGRITY" != "ok" ]; then
            echo "[WARN] Database has integrity issues: $INTEGRITY"
            echo "[WARN] Skipping export to avoid propagating corruption"
            exit 1
        fi

        # Create atomic snapshot using SQLite backup API
        sqlite3 "$DB_PATH" ".backup '${EXPORT_PATH}'" 2>&1
        if [ $? -eq 0 ]; then
            # Write metadata
            CONTACTS=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM contacts;" 2>/dev/null || echo "?")
            PROJECTS=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM projects;" 2>/dev/null || echo "?")
            SIGNALS=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM harvest_signals;" 2>/dev/null || echo "0")

            cat > "$EXPORT_META" <<EOF
hostname=${HOSTNAME}
exported_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
contacts=${CONTACTS}
projects=${PROJECTS}
signals=${SIGNALS}
EOF
            echo "[OK] Exported: ${CONTACTS} contacts, ${PROJECTS} projects, ${SIGNALS} signals"
            echo "[OK] Export: ${EXPORT_PATH}"
        else
            echo "[ERROR] Backup failed"
            exit 1
        fi
        ;;

    import)
        # Check if there's a newer export from another machine
        if [ ! -f "$EXPORT_PATH" ] || [ ! -f "$EXPORT_META" ]; then
            echo "[INFO] No sync export found"
            exit 0
        fi

        EXPORT_HOST=$(grep "hostname=" "$EXPORT_META" | cut -d= -f2)
        if [ "$EXPORT_HOST" = "$HOSTNAME" ]; then
            echo "[INFO] Export is from this machine — nothing to import"
            exit 0
        fi

        # Check if export is newer than our DB
        EXPORT_TIME=$(stat -c %Y "$EXPORT_PATH" 2>/dev/null || stat -f %m "$EXPORT_PATH" 2>/dev/null)
        DB_TIME=$(stat -c %Y "$DB_PATH" 2>/dev/null || stat -f %m "$DB_PATH" 2>/dev/null)

        if [ "$EXPORT_TIME" -gt "$DB_TIME" ]; then
            # Backup current before overwriting
            cp "$DB_PATH" "${DB_PATH}.pre-import-$(date +%Y%m%d-%H%M%S)"

            # Check export integrity
            INTEGRITY=$(sqlite3 "$EXPORT_PATH" "PRAGMA integrity_check;" 2>&1 | head -1)
            if [ "$INTEGRITY" != "ok" ]; then
                echo "[WARN] Export has integrity issues — skipping import"
                exit 1
            fi

            cp "$EXPORT_PATH" "$DB_PATH"
            rm -f "${DB_PATH}-wal" "${DB_PATH}-shm"
            echo "[OK] Imported from ${EXPORT_HOST}"
            cat "$EXPORT_META"
        else
            echo "[INFO] Local DB is newer than export — skipping"
        fi
        ;;

    status)
        echo "=== Database Sync Status ==="
        echo "Machine: ${HOSTNAME}"
        echo "DB: ${DB_PATH}"

        if [ -f "$DB_PATH" ]; then
            INTEGRITY=$(sqlite3 "$DB_PATH" "PRAGMA integrity_check;" 2>&1 | head -1)
            echo "Integrity: ${INTEGRITY}"
            echo "Size: $(du -h "$DB_PATH" | cut -f1)"
            echo "Modified: $(stat -c %y "$DB_PATH" 2>/dev/null || stat -f "%Sm" "$DB_PATH" 2>/dev/null)"
        fi

        if [ -f "$EXPORT_META" ]; then
            echo ""
            echo "Last export:"
            cat "$EXPORT_META"
        fi
        ;;

    *)
        echo "Usage: db_sync.sh {backup|import|status}"
        exit 1
        ;;
esac
