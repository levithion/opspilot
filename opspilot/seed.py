"""Demo directory data so the platform is usable out of the box (dev only)."""

from __future__ import annotations

from opspilot.db import Database, utcnow

USERS = [
    # user_id, email, name, team, role, vpn, mfa
    ("u1001", "alice@opspilot.example", "Alice Archer", "Engineering", "employee", 1, 1),
    ("u1002", "bob@opspilot.example", "Bob Banerjee", "Engineering", "team_lead", 1, 1),
    ("u1003", "carol@opspilot.example", "Carol Costa", "IT", "it_admin", 1, 1),
    ("u1004", "dave@opspilot.example", "Dave Dunn", "Finance", "employee", 0, 1),
    ("u1005", "erin@opspilot.example", "Erin Evans", "HR", "employee", 1, 0),
    ("svc-automation", "automation@opspilot.example", "Automation Service", "IT", "automation", 0, 1),
]

GRANTS = [
    ("u1001", "SharePoint: Engineering Wiki", "member"),
    ("u1001", "GitHub: opspilot-org", "write"),
    ("u1001", "VPN: corp-gateway", "user"),
    ("u1002", "SharePoint: Engineering Wiki", "owner"),
    ("u1002", "GitHub: opspilot-org", "admin"),
    ("u1003", "Entra ID: Tenant", "global-reader"),
    ("u1004", "SharePoint: Finance Reports", "member"),
    ("u1005", "SharePoint: HR Confidential", "owner"),
]

LICENSES = [
    ("Microsoft 365 E3", "u1001", "assigned", "2027-03-31", "ENG-100"),
    ("Microsoft 365 E3", "u1002", "assigned", "2027-03-31", "ENG-100"),
    ("JetBrains All Products", "u1001", "assigned", "2026-12-15", "ENG-100"),
    ("Figma Professional", "u1002", "assigned", "2026-11-30", "ENG-100"),
    ("Adobe Acrobat Pro", "u1004", "assigned", "2027-01-20", "FIN-200"),
    ("Tableau Creator", None, "available", "2027-02-28", "FIN-200"),
]

BUDGETS = {"Engineering": 80.0, "IT": 40.0, "Finance": 20.0, "HR": 15.0}


def seed_demo_data(db: Database) -> None:
    """Idempotent and safe when several processes start at once (the check runs inside the write lock)."""
    with db.transaction() as conn:
        if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] > 0:
            return
        _seed(conn)


def _seed(db) -> None:
    for u in USERS:
        db.execute("INSERT INTO users(user_id,email,name,team,role,vpn_enabled,mfa_enrolled) VALUES (?,?,?,?,?,?,?)", u)
    now = utcnow()
    for user_id, resource, level in GRANTS:
        db.execute(
            "INSERT INTO access_grants(user_id,resource,level,granted_by,granted_at) VALUES (?,?,?,?,?)",
            (user_id, resource, level, "u1003", now),
        )
    for product, owner, status, renewal, cc in LICENSES:
        db.execute(
            "INSERT INTO licenses(product,owner_user_id,seat_status,renewal_date,cost_center) VALUES (?,?,?,?,?)",
            (product, owner, status, renewal, cc),
        )
    for team, usd in BUDGETS.items():
        db.execute("INSERT OR IGNORE INTO budgets(team, monthly_usd) VALUES (?,?)", (team, usd))
