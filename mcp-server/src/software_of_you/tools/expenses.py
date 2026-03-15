"""Expense tracking tool — log, list, edit, and summarize expenses for CRA T2125 tax filing."""

from datetime import datetime

from mcp.server.fastmcp import FastMCP

from software_of_you.db import execute, execute_many, execute_write, rows_to_dicts


VALID_CATEGORIES = (
    "union_dues", "agent_commission", "travel", "home_office",
    "equipment", "professional_development", "marketing",
    "meals_entertainment", "office_supplies", "software_subscriptions",
    "phone_internet", "professional_fees", "insurance", "vehicle", "other",
)

# CRA default deductible percentages
DEFAULT_DEDUCTIBLE_PCT = {cat: 100.0 for cat in VALID_CATEGORIES}
DEFAULT_DEDUCTIBLE_PCT["meals_entertainment"] = 50.0

CATEGORY_LABELS = {
    "union_dues": "Union Dues",
    "agent_commission": "Agent Commission",
    "travel": "Travel",
    "home_office": "Home Office",
    "equipment": "Equipment",
    "professional_development": "Professional Development",
    "marketing": "Marketing",
    "meals_entertainment": "Meals & Entertainment",
    "office_supplies": "Office Supplies",
    "software_subscriptions": "Software & Subscriptions",
    "phone_internet": "Phone & Internet",
    "professional_fees": "Professional Fees",
    "insurance": "Insurance",
    "vehicle": "Vehicle",
    "other": "Other",
}


def register(server: FastMCP) -> None:
    @server.tool()
    def expenses(
        action: str,
        record_id: int = 0,
        amount: float = 0.0,
        currency: str = "CAD",
        category: str = "",
        description: str = "",
        vendor: str = "",
        reference_number: str = "",
        tax_year: int = 0,
        expense_date: str = "",
        hst_gst_amount: float = 0.0,
        deductible_pct: float = 0.0,
        income_record_id: int = 0,
        contact_id: int = 0,
        contact_name: str = "",
        project_id: int = 0,
        notes: str = "",
    ) -> dict:
        """Track expenses for CRA T2125 self-employment tax filing.

        Actions:
          add     — Log an expense (amount, category required)
          list    — List records (optional: tax_year, category, vendor filters)
          get     — Get full details of a record (record_id required)
          edit    — Update a record (record_id required)
          delete  — Delete a record (record_id required)
          summary — Aggregate by category for a tax year
        """
        if action == "add":
            return _add(
                amount, currency, category, description, vendor,
                reference_number, tax_year, expense_date, hst_gst_amount,
                deductible_pct, income_record_id, contact_id, contact_name,
                project_id, notes,
            )
        elif action == "list":
            return _list(tax_year, category, vendor)
        elif action == "get":
            return _get(record_id)
        elif action == "edit":
            return _edit(
                record_id, amount, currency, category, description, vendor,
                reference_number, tax_year, expense_date, hst_gst_amount,
                deductible_pct, income_record_id, contact_id, contact_name,
                project_id, notes,
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


def _format_amount(val, currency="CAD"):
    if val is None:
        return "—"
    return f"${val:,.2f} {currency}"


def _compute_deductible(amount, deductible_pct):
    """Compute the deductible amount."""
    return round(amount * deductible_pct / 100, 2)


def _add(amount, currency, category, description, vendor, reference_number,
         tax_year, expense_date, hst_gst_amount, deductible_pct,
         income_record_id, contact_id, contact_name, project_id, notes):
    if not amount:
        return {"error": "amount is required."}
    if not category:
        return {"error": "category is required."}
    if category not in VALID_CATEGORIES:
        return {"error": f"Invalid category '{category}'. Use: {', '.join(VALID_CATEGORIES)}"}

    # Resolve tax year from expense_date or current year
    if not tax_year:
        if expense_date:
            try:
                tax_year = int(expense_date[:4])
            except (ValueError, IndexError):
                tax_year = _current_year()
        else:
            tax_year = _current_year()

    # Auto-set deductible_pct from CRA defaults if not explicitly provided
    if not deductible_pct:
        deductible_pct = DEFAULT_DEDUCTIBLE_PCT.get(category, 100.0)

    deductible_amount = _compute_deductible(amount, deductible_pct)

    # Resolve contact
    cid = _resolve_contact(contact_id, contact_name)

    rid = execute_write(
        """INSERT INTO expense_records
           (amount, currency, category, description, vendor, reference_number,
            tax_year, expense_date, hst_gst_amount, deductible_pct, deductible_amount,
            income_record_id, contact_id, project_id, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            amount, currency, category,
            description or None, vendor or None, reference_number or None,
            tax_year, expense_date or None, hst_gst_amount,
            deductible_pct, deductible_amount,
            income_record_id or None, cid, project_id or None,
            notes or None,
        ),
    )
    execute_write(
        """INSERT INTO activity_log (entity_type, entity_id, action, details, created_at)
           VALUES ('expense', ?, 'expense_added', ?, datetime('now'))""",
        (rid, f"{vendor or category} — {_format_amount(amount, currency)}"),
    )

    # Fetch linked income record for display
    income_info = None
    if income_record_id:
        rows = execute("SELECT source, amount, category FROM income_records WHERE id = ?", (income_record_id,))
        if rows:
            r = rows[0]
            income_info = {"id": income_record_id, "source": r["source"], "amount": r["amount"]}

    contact_info = None
    if cid:
        rows = execute("SELECT name, company FROM contacts WHERE id = ?", (cid,))
        if rows:
            contact_info = {"id": cid, "name": rows[0]["name"], "company": rows[0]["company"]}

    result = {
        "record_id": rid,
        "amount": amount,
        "currency": currency,
        "category": category,
        "deductible_pct": deductible_pct,
        "deductible_amount": deductible_amount,
        "tax_year": tax_year,
    }
    if vendor:
        result["vendor"] = vendor
    if hst_gst_amount:
        result["hst_gst_amount"] = hst_gst_amount

    suggestions = ["Use expenses(action='summary') to see year-to-date totals"]
    if not income_record_id:
        suggestions.append(f"Link to income: expenses(action='edit', record_id={rid}, income_record_id=...)")

    return {
        "result": result,
        "income_record": income_info,
        "contact": contact_info,
        "_context": {
            "suggestions": suggestions,
            "presentation": f"Confirm expense recorded: {_format_amount(amount, currency)} — {CATEGORY_LABELS.get(category, category)}."
            + (f" ({deductible_pct}% deductible = {_format_amount(deductible_amount, currency)})" if deductible_pct < 100 else ""),
        },
    }


def _list(tax_year, category, vendor):
    year = tax_year or _current_year()

    conditions = ["e.tax_year = ?"]
    params = [year]
    if category:
        conditions.append("e.category = ?")
        params.append(category)
    if vendor:
        conditions.append("e.vendor LIKE ?")
        params.append(f"%{vendor}%")

    where = " AND ".join(conditions)
    rows = execute(
        f"""SELECT e.*, c.name as contact_name, i.source as income_source
            FROM expense_records e
            LEFT JOIN contacts c ON e.contact_id = c.id
            LEFT JOIN income_records i ON e.income_record_id = i.id
            WHERE {where}
            ORDER BY e.expense_date DESC, e.created_at DESC""",
        tuple(params),
    )

    records = rows_to_dicts(rows)
    total_amount = sum(r["amount"] for r in records)
    total_deductible = sum(r["deductible_amount"] for r in records if r["deductible_amount"] is not None)
    total_hst_gst = sum(r["hst_gst_amount"] for r in records if r["hst_gst_amount"] is not None)

    filters_desc = f"tax year {year}"
    if category:
        filters_desc += f", category={category}"
    if vendor:
        filters_desc += f", vendor contains '{vendor}'"

    return {
        "result": records,
        "count": len(records),
        "totals": {
            "amount": round(total_amount, 2),
            "deductible": round(total_deductible, 2),
            "hst_gst": round(total_hst_gst, 2),
        },
        "_context": {
            "filters": filters_desc,
            "suggestions": [
                "Use expenses(action='summary') for breakdown by category",
                "Use expenses(action='list', category='union_dues') to filter",
            ],
            "presentation": "Show as a table: date, vendor, category, amount, deductible. Show totals at bottom.",
        },
    }


def _get(record_id):
    if not record_id:
        return {"error": "record_id is required."}

    rows = execute(
        """SELECT e.*, c.name as contact_name, c.company as contact_company,
                  p.name as project_name,
                  i.source as income_source, i.amount as income_amount, i.category as income_category
           FROM expense_records e
           LEFT JOIN contacts c ON e.contact_id = c.id
           LEFT JOIN projects p ON e.project_id = p.id
           LEFT JOIN income_records i ON e.income_record_id = i.id
           WHERE e.id = ?""",
        (record_id,),
    )
    if not rows:
        return {"error": f"No expense record with id {record_id}."}

    record = rows_to_dicts(rows)[0]
    return {
        "result": record,
        "_context": {
            "suggestions": [
                f"Edit: expenses(action='edit', record_id={record_id}, ...)",
                f"Delete: expenses(action='delete', record_id={record_id})",
            ],
            "presentation": "Show all fields. Format amounts with $ and 2 decimal places. Show linked income record if present.",
        },
    }


def _edit(record_id, amount, currency, category, description, vendor,
          reference_number, tax_year, expense_date, hst_gst_amount,
          deductible_pct, income_record_id, contact_id, contact_name,
          project_id, notes):
    if not record_id:
        return {"error": "record_id is required."}

    existing = execute("SELECT * FROM expense_records WHERE id = ?", (record_id,))
    if not existing:
        return {"error": f"No expense record with id {record_id}."}

    if category and category not in VALID_CATEGORIES:
        return {"error": f"Invalid category '{category}'. Use: {', '.join(VALID_CATEGORIES)}"}

    updates = []
    params = []

    for field, value in [
        ("amount", amount), ("currency", currency), ("category", category),
        ("description", description), ("vendor", vendor),
        ("reference_number", reference_number), ("tax_year", tax_year),
        ("expense_date", expense_date), ("hst_gst_amount", hst_gst_amount),
        ("project_id", project_id), ("notes", notes),
    ]:
        if isinstance(value, (int, float)) and value == 0:
            continue
        if isinstance(value, str) and not value:
            continue
        updates.append(f"{field} = ?")
        params.append(value)

    # Handle income_record_id (allow setting to link)
    if income_record_id:
        updates.append("income_record_id = ?")
        params.append(income_record_id)

    # Resolve contact
    cid = _resolve_contact(contact_id, contact_name)
    if cid:
        updates.append("contact_id = ?")
        params.append(cid)

    # Recompute deductible_amount if amount or pct changed
    final_amount = amount if amount else existing[0]["amount"]
    final_pct = deductible_pct if deductible_pct else existing[0]["deductible_pct"]

    # If category changed, check if default pct should apply
    if category and not deductible_pct:
        final_pct = DEFAULT_DEDUCTIBLE_PCT.get(category, existing[0]["deductible_pct"])

    if deductible_pct or amount or category:
        updates.append("deductible_pct = ?")
        params.append(final_pct)
        updates.append("deductible_amount = ?")
        params.append(_compute_deductible(final_amount, final_pct))

    if not updates:
        return {"error": "No fields to update."}

    updates.append("updated_at = datetime('now')")
    params.append(record_id)

    changed_fields = [u.split(" =")[0] for u in updates[:-1]]

    execute_many([
        (f"UPDATE expense_records SET {', '.join(updates)} WHERE id = ?", tuple(params)),
        (
            """INSERT INTO activity_log (entity_type, entity_id, action, details, created_at)
               VALUES ('expense', ?, 'expense_updated', ?, datetime('now'))""",
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

    existing = execute("SELECT vendor, category, amount, currency FROM expense_records WHERE id = ?", (record_id,))
    if not existing:
        return {"error": f"No expense record with id {record_id}."}

    rec = existing[0]
    label = rec["vendor"] or CATEGORY_LABELS.get(rec["category"], rec["category"])

    execute_many([
        ("DELETE FROM expense_records WHERE id = ?", (record_id,)),
        (
            """INSERT INTO activity_log (entity_type, entity_id, action, details, created_at)
               VALUES ('expense', ?, 'expense_deleted', ?, datetime('now'))""",
            (record_id, f"Deleted: {label} — {_format_amount(rec['amount'], rec['currency'])}"),
        ),
    ])

    return {
        "result": {"record_id": record_id, "deleted": True},
        "_context": {"presentation": f"Confirmed deletion of {label} expense record."},
    }


def _summary(tax_year):
    year = tax_year or _current_year()

    # By category
    by_category = rows_to_dicts(execute(
        """SELECT category,
                  COUNT(*) as count,
                  ROUND(SUM(amount), 2) as total,
                  ROUND(SUM(COALESCE(deductible_amount, amount)), 2) as deductible,
                  ROUND(SUM(COALESCE(hst_gst_amount, 0)), 2) as hst_gst
           FROM expense_records
           WHERE tax_year = ?
           GROUP BY category
           ORDER BY total DESC""",
        (year,),
    ))

    # Grand totals
    totals_rows = execute(
        """SELECT COUNT(*) as count,
                  ROUND(SUM(amount), 2) as total,
                  ROUND(SUM(COALESCE(deductible_amount, amount)), 2) as deductible,
                  ROUND(SUM(COALESCE(hst_gst_amount, 0)), 2) as hst_gst
           FROM expense_records
           WHERE tax_year = ?""",
        (year,),
    )
    totals = rows_to_dicts(totals_rows)[0] if totals_rows else {}

    # Cross-reference: get income totals for net profit calculation
    income_totals = execute(
        """SELECT ROUND(SUM(amount), 2) as gross,
                  ROUND(SUM(COALESCE(net_amount, amount)), 2) as net
           FROM income_records
           WHERE tax_year = ?""",
        (year,),
    )
    income = rows_to_dicts(income_totals)[0] if income_totals else {}

    net_profit = None
    if income.get("net") is not None and totals.get("deductible") is not None:
        net_profit = round(income["net"] - totals["deductible"], 2)

    return {
        "result": {
            "tax_year": year,
            "by_category": by_category,
            "totals": totals,
            "income_cross_ref": income,
            "net_profit": net_profit,
        },
        "_context": {
            "suggestions": [
                "Use income(action='summary') for income breakdown",
                "Net profit = net income − deductible expenses",
                "Use expenses(action='list') to see individual records",
            ],
            "presentation": "Show category breakdown as table. Highlight grand totals and net profit.",
        },
    }
