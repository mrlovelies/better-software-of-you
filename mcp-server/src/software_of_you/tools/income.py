"""Income tracking tool — log, list, edit, and summarize income from multiple sources."""

from datetime import datetime

from mcp.server.fastmcp import FastMCP

from software_of_you.db import execute, execute_many, execute_write, rows_to_dicts


VALID_CATEGORIES = ("vo_commercial", "freelance", "employment", "residual", "other")


def register(server: FastMCP) -> None:
    @server.tool()
    def income(
        action: str,
        record_id: int = 0,
        amount: float = 0.0,
        currency: str = "CAD",
        source: str = "",
        category: str = "",
        description: str = "",
        reference_number: str = "",
        tax_year: int = 0,
        received_date: str = "",
        invoice_date: str = "",
        contact_id: int = 0,
        contact_name: str = "",
        project_id: int = 0,
        agent_fee_pct: float = 0.0,
        agent_fee_amount: float = 0.0,
        tax_withheld: float = 0.0,
        notes: str = "",
    ) -> dict:
        """Track income from multiple sources for tax filing.

        Actions:
          add     — Log an income record (amount, source, category required)
          list    — List records (optional: tax_year, category, source filters)
          get     — Get full details of a record (record_id required)
          edit    — Update a record (record_id required)
          delete  — Delete a record (record_id required)
          summary — Aggregate totals by category and source for a tax year
        """
        if action == "add":
            return _add(
                amount, currency, source, category, description,
                reference_number, tax_year, received_date, invoice_date,
                contact_id, contact_name, project_id,
                agent_fee_pct, agent_fee_amount, tax_withheld, notes,
            )
        elif action == "list":
            return _list(tax_year, category, source)
        elif action == "get":
            return _get(record_id)
        elif action == "edit":
            return _edit(
                record_id, amount, currency, source, category, description,
                reference_number, tax_year, received_date, invoice_date,
                contact_id, contact_name, project_id,
                agent_fee_pct, agent_fee_amount, tax_withheld, notes,
            )
        elif action == "delete":
            return _delete(record_id)
        elif action == "summary":
            return _summary(tax_year)
        else:
            return {"error": f"Unknown action: {action}. Use: add, list, get, edit, delete, summary"}


def _resolve_contact(contact_id, contact_name):
    """Resolve a contact by ID or fuzzy name match."""
    if contact_id:
        return contact_id
    if contact_name:
        rows = execute("SELECT id FROM contacts WHERE name LIKE ?", (f"%{contact_name}%",))
        if len(rows) == 1:
            return rows[0]["id"]
    return None


def _current_year():
    return datetime.now().year


def _compute_fees(amount, agent_fee_pct, agent_fee_amount):
    """Compute agent fee and net amount. Returns (agent_fee_pct, agent_fee_amount, net_amount)."""
    if agent_fee_pct > 0:
        computed_fee = round(amount * agent_fee_pct / 100, 2)
        return agent_fee_pct, computed_fee, round(amount - computed_fee, 2)
    elif agent_fee_amount > 0:
        pct = round(agent_fee_amount / amount * 100, 2) if amount else 0
        return pct, agent_fee_amount, round(amount - agent_fee_amount, 2)
    return None, None, amount


def _format_amount(val, currency="CAD"):
    """Format a numeric amount for display."""
    if val is None:
        return "—"
    return f"${val:,.2f} {currency}"


def _add(amount, currency, source, category, description, reference_number,
         tax_year, received_date, invoice_date, contact_id, contact_name,
         project_id, agent_fee_pct, agent_fee_amount, tax_withheld, notes):
    if not amount:
        return {"error": "amount is required."}
    if not source:
        return {"error": "source is required."}
    if not category:
        return {"error": "category is required."}
    if category not in VALID_CATEGORIES:
        return {"error": f"Invalid category '{category}'. Use: {', '.join(VALID_CATEGORIES)}"}

    # Resolve tax year
    if not tax_year:
        if received_date:
            try:
                tax_year = int(received_date[:4])
            except (ValueError, IndexError):
                tax_year = _current_year()
        else:
            tax_year = _current_year()

    # Resolve contact
    cid = _resolve_contact(contact_id, contact_name)

    # Compute fees
    fee_pct, fee_amt, net = _compute_fees(amount, agent_fee_pct, agent_fee_amount)

    rid = execute_many([
        (
            """INSERT INTO income_records
               (amount, currency, source, category, description, reference_number,
                tax_year, received_date, invoice_date, contact_id, project_id,
                agent_fee_pct, agent_fee_amount, net_amount, tax_withheld, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                amount, currency, source, category,
                description or None, reference_number or None,
                tax_year, received_date or None, invoice_date or None,
                cid, project_id or None,
                fee_pct, fee_amt, net,
                tax_withheld, notes or None,
            ),
        ),
        (
            """INSERT INTO activity_log (entity_type, entity_id, action, details, created_at)
               VALUES ('income', last_insert_rowid(), 'income_added', ?, datetime('now'))""",
            (f"{source} — {_format_amount(amount, currency)} ({category})",),
        ),
    ])

    # Fetch contact name for display
    contact_info = None
    if cid:
        rows = execute("SELECT name, company FROM contacts WHERE id = ?", (cid,))
        if rows:
            contact_info = {"id": cid, "name": rows[0]["name"], "company": rows[0]["company"]}

    result = {
        "record_id": rid,
        "amount": amount,
        "currency": currency,
        "source": source,
        "category": category,
        "tax_year": tax_year,
    }
    if fee_amt is not None:
        result["agent_fee_pct"] = fee_pct
        result["agent_fee_amount"] = fee_amt
        result["net_amount"] = net

    suggestions = ["Use income(action='summary') to see year-to-date totals"]
    if not cid and source:
        suggestions.append(f"Link to a contact: income(action='edit', record_id={rid}, contact_name='...')")

    return {
        "result": result,
        "contact": contact_info,
        "_context": {
            "suggestions": suggestions,
            "presentation": f"Confirm income recorded: {_format_amount(amount, currency)} from {source}.",
        },
    }


def _list(tax_year, category, source):
    year = tax_year or _current_year()

    conditions = ["i.tax_year = ?"]
    params = [year]
    if category:
        conditions.append("i.category = ?")
        params.append(category)
    if source:
        conditions.append("i.source LIKE ?")
        params.append(f"%{source}%")

    where = " AND ".join(conditions)
    rows = execute(
        f"""SELECT i.*, c.name as contact_name
            FROM income_records i
            LEFT JOIN contacts c ON i.contact_id = c.id
            WHERE {where}
            ORDER BY i.received_date DESC, i.created_at DESC""",
        tuple(params),
    )

    records = rows_to_dicts(rows)
    total_gross = sum(r["amount"] for r in records)
    total_net = sum(r["net_amount"] for r in records if r["net_amount"] is not None)
    total_fees = sum(r["agent_fee_amount"] for r in records if r["agent_fee_amount"] is not None)

    filters_desc = f"tax year {year}"
    if category:
        filters_desc += f", category={category}"
    if source:
        filters_desc += f", source contains '{source}'"

    return {
        "result": records,
        "count": len(records),
        "totals": {
            "gross": round(total_gross, 2),
            "agent_fees": round(total_fees, 2),
            "net": round(total_net, 2),
        },
        "_context": {
            "filters": filters_desc,
            "suggestions": [
                "Use income(action='summary') for breakdown by category and source",
                "Use income(action='list', category='vo_commercial') to filter",
            ],
            "presentation": "Show as a table: date, source, category, gross, net. Show totals at bottom.",
        },
    }


def _get(record_id):
    if not record_id:
        return {"error": "record_id is required."}

    rows = execute(
        """SELECT i.*, c.name as contact_name, c.company as contact_company,
                  p.name as project_name
           FROM income_records i
           LEFT JOIN contacts c ON i.contact_id = c.id
           LEFT JOIN projects p ON i.project_id = p.id
           WHERE i.id = ?""",
        (record_id,),
    )
    if not rows:
        return {"error": f"No income record with id {record_id}."}

    record = rows_to_dicts(rows)[0]
    return {
        "result": record,
        "_context": {
            "suggestions": [
                f"Edit: income(action='edit', record_id={record_id}, ...)",
                f"Delete: income(action='delete', record_id={record_id})",
            ],
            "presentation": "Show all fields. Format amounts with $ and 2 decimal places.",
        },
    }


def _edit(record_id, amount, currency, source, category, description,
          reference_number, tax_year, received_date, invoice_date,
          contact_id, contact_name, project_id,
          agent_fee_pct, agent_fee_amount, tax_withheld, notes):
    if not record_id:
        return {"error": "record_id is required."}

    # Check record exists
    existing = execute("SELECT * FROM income_records WHERE id = ?", (record_id,))
    if not existing:
        return {"error": f"No income record with id {record_id}."}

    if category and category not in VALID_CATEGORIES:
        return {"error": f"Invalid category '{category}'. Use: {', '.join(VALID_CATEGORIES)}"}

    updates = []
    params = []

    for field, value in [
        ("amount", amount), ("currency", currency), ("source", source),
        ("category", category), ("description", description),
        ("reference_number", reference_number), ("tax_year", tax_year),
        ("received_date", received_date), ("invoice_date", invoice_date),
        ("project_id", project_id), ("tax_withheld", tax_withheld),
        ("notes", notes),
    ]:
        # Skip zero/empty values (they mean "no change")
        if isinstance(value, (int, float)) and value == 0:
            continue
        if isinstance(value, str) and not value:
            continue
        updates.append(f"{field} = ?")
        params.append(value)

    # Resolve contact
    cid = _resolve_contact(contact_id, contact_name)
    if cid:
        updates.append("contact_id = ?")
        params.append(cid)

    # Recompute fees if amount or fee changed
    final_amount = amount if amount else existing[0]["amount"]
    final_fee_pct = agent_fee_pct if agent_fee_pct else (existing[0]["agent_fee_pct"] or 0)
    final_fee_amt = agent_fee_amount if agent_fee_amount else 0

    if agent_fee_pct or agent_fee_amount or amount:
        fee_pct, fee_amt, net = _compute_fees(final_amount, final_fee_pct, final_fee_amt)
        if fee_pct is not None:
            updates.append("agent_fee_pct = ?")
            params.append(fee_pct)
        if fee_amt is not None:
            updates.append("agent_fee_amount = ?")
            params.append(fee_amt)
        updates.append("net_amount = ?")
        params.append(net)

    if not updates:
        return {"error": "No fields to update."}

    updates.append("updated_at = datetime('now')")
    params.append(record_id)

    changed_fields = [u.split(" =")[0] for u in updates[:-1]]

    execute_many([
        (f"UPDATE income_records SET {', '.join(updates)} WHERE id = ?", tuple(params)),
        (
            """INSERT INTO activity_log (entity_type, entity_id, action, details, created_at)
               VALUES ('income', ?, 'income_updated', ?, datetime('now'))""",
            (record_id, f"Updated: {', '.join(changed_fields)}"),
        ),
    ])

    return {
        "result": {"record_id": record_id, "updated_fields": changed_fields},
        "_context": {"presentation": "Confirm what was changed."},
    }


def _delete(record_id):
    if not record_id:
        return {"error": "record_id is required."}

    existing = execute("SELECT source, amount, currency FROM income_records WHERE id = ?", (record_id,))
    if not existing:
        return {"error": f"No income record with id {record_id}."}

    rec = existing[0]
    execute_many([
        ("DELETE FROM income_records WHERE id = ?", (record_id,)),
        (
            """INSERT INTO activity_log (entity_type, entity_id, action, details, created_at)
               VALUES ('income', ?, 'income_deleted', ?, datetime('now'))""",
            (record_id, f"Deleted: {rec['source']} — {_format_amount(rec['amount'], rec['currency'])}"),
        ),
    ])

    return {
        "result": {"record_id": record_id, "deleted": True},
        "_context": {"presentation": f"Confirmed deletion of {rec['source']} record."},
    }


def _summary(tax_year):
    year = tax_year or _current_year()

    # By category
    by_category = rows_to_dicts(execute(
        """SELECT category,
                  COUNT(*) as count,
                  ROUND(SUM(amount), 2) as gross,
                  ROUND(SUM(COALESCE(agent_fee_amount, 0)), 2) as agent_fees,
                  ROUND(SUM(COALESCE(net_amount, amount)), 2) as net,
                  ROUND(SUM(COALESCE(tax_withheld, 0)), 2) as tax_withheld
           FROM income_records
           WHERE tax_year = ?
           GROUP BY category
           ORDER BY gross DESC""",
        (year,),
    ))

    # By source
    by_source = rows_to_dicts(execute(
        """SELECT source,
                  COUNT(*) as count,
                  ROUND(SUM(amount), 2) as gross,
                  ROUND(SUM(COALESCE(agent_fee_amount, 0)), 2) as agent_fees,
                  ROUND(SUM(COALESCE(net_amount, amount)), 2) as net
           FROM income_records
           WHERE tax_year = ?
           GROUP BY source
           ORDER BY gross DESC""",
        (year,),
    ))

    # Grand totals
    totals_rows = execute(
        """SELECT COUNT(*) as count,
                  ROUND(SUM(amount), 2) as gross,
                  ROUND(SUM(COALESCE(agent_fee_amount, 0)), 2) as agent_fees,
                  ROUND(SUM(COALESCE(net_amount, amount)), 2) as net,
                  ROUND(SUM(COALESCE(tax_withheld, 0)), 2) as tax_withheld
           FROM income_records
           WHERE tax_year = ?""",
        (year,),
    )
    totals = rows_to_dicts(totals_rows)[0] if totals_rows else {}

    return {
        "result": {
            "tax_year": year,
            "by_category": by_category,
            "by_source": by_source,
            "totals": totals,
        },
        "_context": {
            "suggestions": [
                "Export to CSV for your accountant",
                "Use income(action='list') to see individual records",
            ],
            "presentation": "Show category and source breakdowns as tables. Highlight grand totals.",
        },
    }
