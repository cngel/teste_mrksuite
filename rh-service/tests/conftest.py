import itertools
import os

os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("POSTGRES_USER", "test")
os.environ.setdefault("POSTGRES_PASSWORD", "test")
os.environ.setdefault("POSTGRES_DB", "test")
os.environ.setdefault("MINIO_ROOT_USER", "test")
os.environ.setdefault("MINIO_ROOT_PASSWORD", "test")

import pytest
from fastapi.testclient import TestClient

import app as app_module


class FakeCursor:
    def __init__(self, handler):
        self._handler = handler
        self._rows = []

    def execute(self, sql, params=None):
        self._rows = self._handler(" ".join(sql.split()), tuple(params) if params else ())

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeConnection:
    def __init__(self, handler):
        self._handler = handler

    def cursor(self, cursor_factory=None):
        return FakeCursor(self._handler)

    def commit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


EMPLOYEE_COLUMNS = [
    "full_name", "email", "phone", "department_id", "role", "status", "reports_to", "birth_date", "contract_type",
    "employee_number", "first_name", "last_name", "gender", "nationality", "marital_status", "mobile",
    "address", "province", "city", "bi_number", "bi_expiry", "nif", "niss", "passport_number", "passport_expiry",
    "bank_name", "bank_account", "iban",
]


class FakeRhDB:
    """Emula as tabelas do rh-service, todas escopadas por company_id — o
    isolamento entre empresas é reforçado em cada query (ver test_isolation.py)."""

    def __init__(self):
        self.departments = {}
        self.employees = {}
        self.salary_profiles = {}
        self.deduction_rules = {}
        self.payrolls = {}
        self.payroll_items = {}
        self.evaluations = {}
        self.trainings = {}
        self.blocklist = {}
        self._seq = {"departments": itertools.count(1), "employees": itertools.count(1),
                     "salary_profiles": itertools.count(1), "deduction_rules": itertools.count(1),
                     "payrolls": itertools.count(1), "payroll_items": itertools.count(1),
                     "evaluations": itertools.count(1), "trainings": itertools.count(1)}

    def connection_factory(self):
        return FakeConnection(self.handle)

    def handle(self, sql, p):
        if sql.startswith("SELECT jti FROM jwt_blocklist"):
            jti, = p
            return [{"jti": jti}] if jti in self.blocklist else []

        # --- departments ---
        if sql.startswith("SELECT * FROM departments WHERE company_id = %s ORDER BY name"):
            company_id, = p
            rows = [d for d in self.departments.values() if d["company_id"] == company_id]
            return sorted(rows, key=lambda d: d["name"])

        if sql.startswith("INSERT INTO departments"):
            name, color, company_id = p
            did = next(self._seq["departments"])
            row = {"id": did, "name": name, "color": color, "company_id": company_id}
            self.departments[did] = row
            return [row]

        if sql.startswith("UPDATE departments SET name = %s"):
            name, color, did, company_id = p
            row = self.departments.get(did)
            if not row or row["company_id"] != company_id:
                return []
            row.update({"name": name, "color": color})
            return [row]

        if sql.startswith("SELECT 1 FROM employees WHERE department_id = %s AND company_id = %s"):
            did, company_id = p
            return [{"1": 1}] if any(e["department_id"] == did and e["company_id"] == company_id
                                      for e in self.employees.values()) else []

        if sql.startswith("DELETE FROM departments WHERE id = %s AND company_id = %s"):
            did, company_id = p
            row = self.departments.get(did)
            if row and row["company_id"] == company_id:
                self.departments.pop(did)
            return []

        # --- employees ---
        if sql.startswith("SELECT * FROM employees WHERE department_id = %s AND company_id = %s"):
            did, company_id = p
            rows = [e for e in self.employees.values() if e["department_id"] == did and e["company_id"] == company_id]
            return sorted(rows, key=lambda e: -e["id"])

        if sql.startswith("SELECT * FROM employees WHERE company_id = %s ORDER BY created_at DESC"):
            company_id, = p
            rows = [e for e in self.employees.values() if e["company_id"] == company_id]
            return sorted(rows, key=lambda e: -e["id"])

        if sql.startswith("INSERT INTO employees"):
            *values, company_id = p
            values_dict = dict(zip(EMPLOYEE_COLUMNS, values))
            eid = next(self._seq["employees"])
            row = {"id": eid, "photo_object_name": None, "created_at": eid, "company_id": company_id, **values_dict}
            self.employees[eid] = row
            return [row]

        if sql.startswith("SELECT id FROM employees WHERE id = %s AND company_id = %s"):
            eid, company_id = p
            row = self.employees.get(eid)
            return [{"id": eid}] if row and row["company_id"] == company_id else []

        if sql.startswith("SELECT 1 FROM employees WHERE id = %s AND company_id = %s"):
            eid, company_id = p
            row = self.employees.get(eid)
            return [{"1": 1}] if row and row["company_id"] == company_id else []

        if sql.startswith("SELECT photo_object_name FROM employees WHERE id=%s AND company_id=%s"):
            eid, company_id = p
            row = self.employees.get(eid)
            return [{"photo_object_name": row["photo_object_name"]}] if row and row["company_id"] == company_id else []

        if sql.startswith("SELECT * FROM employees WHERE id = %s AND company_id = %s"):
            eid, company_id = p
            row = self.employees.get(eid)
            return [row] if row and row["company_id"] == company_id else []

        if sql.startswith("UPDATE employees SET full_name=%s"):
            *values, eid, company_id = p
            row = self.employees.get(eid)
            if not row or row["company_id"] != company_id:
                return []
            row.update(dict(zip(EMPLOYEE_COLUMNS, values)))
            return [row]

        if sql.startswith("UPDATE employees SET status=%s WHERE id=%s"):
            status, eid, company_id = p
            row = self.employees.get(eid)
            if not row or row["company_id"] != company_id:
                return []
            row["status"] = status
            return [row]

        if sql.startswith("UPDATE employees SET photo_object_name=%s"):
            object_name, eid, company_id = p
            row = self.employees.get(eid)
            if not row or row["company_id"] != company_id:
                return []
            row["photo_object_name"] = object_name
            return [row]

        if sql.startswith("DELETE FROM employees WHERE id = %s AND company_id = %s"):
            eid, company_id = p
            row = self.employees.get(eid)
            if row and row["company_id"] == company_id:
                self.employees.pop(eid)
            return []

        # --- salary_profiles ---
        if sql.startswith("SELECT id FROM salary_profiles WHERE employee_id = %s"):
            eid, = p
            row = self.salary_profiles.get(eid)
            return [{"id": row["id"]}] if row else []

        if sql.startswith("INSERT INTO salary_profiles"):
            employee_id, base_salary, food_allowance, transport_allowance, other_allowance, company_id = p
            pid = next(self._seq["salary_profiles"])
            row = {"id": pid, "employee_id": employee_id, "base_salary": base_salary,
                   "food_allowance": food_allowance, "transport_allowance": transport_allowance,
                   "other_allowance": other_allowance, "created_at": pid, "company_id": company_id}
            self.salary_profiles[employee_id] = row
            return [row]

        if sql.startswith("SELECT id, employee_id, base_salary"):
            eid, = p
            row = self.salary_profiles.get(eid)
            return [row] if row else []

        if sql.startswith("UPDATE salary_profiles"):
            base_salary, food_allowance, transport_allowance, other_allowance, eid, company_id = p
            row = self.salary_profiles.get(eid)
            if not row or row["company_id"] != company_id:
                return []
            row.update({"base_salary": base_salary, "food_allowance": food_allowance,
                        "transport_allowance": transport_allowance, "other_allowance": other_allowance})
            return [row]

        if sql.startswith("SELECT * FROM salary_profiles WHERE employee_id = %s"):
            eid, = p
            row = self.salary_profiles.get(eid)
            return [row] if row else []

        # --- deduction_rules ---
        if sql.startswith("SELECT id FROM deduction_rules WHERE company_id = %s AND country_code = %s AND LOWER(name) = LOWER(%s) AND id <>"):
            company_id, country_code, name, exclude_id = p
            return [{"id": r["id"]} for r in self.deduction_rules.values()
                    if r["company_id"] == company_id and r["country_code"] == country_code
                    and r["name"].lower() == name.lower() and r["id"] != exclude_id]

        if sql.startswith("SELECT id FROM deduction_rules WHERE company_id = %s AND country_code = %s"):
            company_id, country_code, name = p
            return [{"id": r["id"]} for r in self.deduction_rules.values()
                    if r["company_id"] == company_id and r["country_code"] == country_code
                    and r["name"].lower() == name.lower()]

        if sql.startswith("INSERT INTO deduction_rules"):
            name, description, calculation_type, calculation_base, value, country_code, company_id = p
            rid = next(self._seq["deduction_rules"])
            row = {"id": rid, "name": name, "description": description, "calculation_type": calculation_type,
                   "calculation_base": calculation_base, "value": value, "country_code": country_code,
                   "active": True, "company_id": company_id}
            self.deduction_rules[rid] = row
            return [row]

        if sql.startswith("SELECT * FROM deduction_rules WHERE company_id = %s ORDER BY id DESC"):
            company_id, = p
            rows = [r for r in self.deduction_rules.values() if r["company_id"] == company_id]
            return sorted(rows, key=lambda r: -r["id"])

        if sql.startswith("SELECT * FROM deduction_rules WHERE id=%s AND company_id=%s") \
                or sql.startswith("SELECT * FROM deduction_rules WHERE id = %s AND company_id = %s"):
            rid, company_id = p
            row = self.deduction_rules.get(rid)
            return [row] if row and row["company_id"] == company_id else []

        if sql.startswith("UPDATE deduction_rules"):
            name, description, calculation_type, calculation_base, value, active, rid, company_id = p
            row = self.deduction_rules.get(rid)
            if not row or row["company_id"] != company_id:
                return []
            row.update({"name": name, "description": description, "calculation_type": calculation_type,
                        "calculation_base": calculation_base, "value": value, "active": active})
            return [row]

        if sql.startswith("SELECT * FROM deduction_rules WHERE active = true"):
            country_code, company_id = p
            rows = [r for r in self.deduction_rules.values()
                    if r["active"] and r["country_code"] == country_code and r["company_id"] == company_id]
            return rows

        # --- trainings ---
        if sql.startswith("INSERT INTO trainings"):
            employee_id, title, provider, status, start_date, end_date, hours, cert_url, company_id = p
            tid = next(self._seq["trainings"])
            row = {"id": tid, "employee_id": employee_id, "title": title, "provider": provider, "status": status,
                   "start_date": start_date, "end_date": end_date, "hours": hours, "cert_url": cert_url,
                   "company_id": company_id, "created_at": tid}
            self.trainings[tid] = row
            return [row]

        if sql.startswith("UPDATE trainings"):
            employee_id, title, provider, status, start_date, end_date, hours, cert_url, training_id, company_id = p
            row = self.trainings.get(training_id)
            if not row or row["company_id"] != company_id:
                return []
            row.update({"employee_id": employee_id, "title": title, "provider": provider, "status": status,
                        "start_date": start_date, "end_date": end_date, "hours": hours, "cert_url": cert_url})
            return [row]

        # --- evaluations ---
        if sql.startswith("INSERT INTO evaluations"):
            employee_id, cycle, period, eval_date, rating, comments, company_id = p
            eid = next(self._seq["evaluations"])
            row = {"id": eid, "employee_id": employee_id, "cycle": cycle, "period": period, "eval_date": eval_date,
                   "rating": rating, "comments": comments, "company_id": company_id}
            self.evaluations[eid] = row
            return [row]

        if sql.startswith("UPDATE evaluations"):
            employee_id, cycle, period, eval_date, rating, comments, evaluation_id, company_id = p
            row = self.evaluations.get(evaluation_id)
            if not row or row["company_id"] != company_id:
                return []
            row.update({"employee_id": employee_id, "cycle": cycle, "period": period, "eval_date": eval_date,
                        "rating": rating, "comments": comments})
            return [row]

        # --- payrolls ---
        if sql.startswith("SELECT id FROM payrolls WHERE employee_id = %s"):
            eid, month, year = p
            rows = [r for r in self.payrolls.values()
                    if r["employee_id"] == eid and r["month"] == month and r["year"] == year]
            return [{"id": rows[0]["id"]}] if rows else []

        if sql.startswith("INSERT INTO payrolls"):
            (employee_id, month, year, total_earnings, total_deductions,
             net_salary, gross_salary, status, company_id) = p
            pid = next(self._seq["payrolls"])
            row = {"id": pid, "employee_id": employee_id, "month": month, "year": year,
                   "total_earnings": total_earnings, "total_deductions": total_deductions,
                   "net_salary": net_salary, "gross_salary": gross_salary, "status": status,
                   "company_id": company_id}
            self.payrolls[pid] = row
            return [row]

        if sql.startswith("INSERT INTO payroll_items"):
            payroll_id, item_type, description, amount, created_value, company_id = p
            iid = next(self._seq["payroll_items"])
            self.payroll_items[iid] = {"id": iid, "payroll_id": payroll_id, "item_type": item_type,
                                        "description": description, "amount": amount, "company_id": company_id}
            return []

        if sql.startswith("SELECT * FROM payrolls WHERE employee_id = %s"):
            eid, = p
            rows = [r for r in self.payrolls.values() if r["employee_id"] == eid]
            return sorted(rows, key=lambda r: (-r["year"], -r["month"]))

        if sql.startswith("SELECT * FROM payrolls WHERE id = %s AND company_id = %s"):
            pid, company_id = p
            row = self.payrolls.get(pid)
            return [row] if row and row["company_id"] == company_id else []

        if sql.startswith("SELECT id, item_type, description, amount FROM payroll_items WHERE payroll_id = %s"):
            pid, = p
            rows = [r for r in self.payroll_items.values() if r["payroll_id"] == pid]
            return sorted(rows, key=lambda r: r["id"])

        if sql.startswith("UPDATE payrolls SET status = 'approved'"):
            pid, company_id = p
            row = self.payrolls.get(pid)
            if not row or row["company_id"] != company_id:
                return []
            row["status"] = "approved"
            return [row]

        raise AssertionError(f"SQL não reconhecido pelo FakeRhDB: {sql!r} params={p!r}")


@pytest.fixture
def fake_db(monkeypatch):
    db = FakeRhDB()
    monkeypatch.setattr(app_module, "get_connection", db.connection_factory)
    monkeypatch.setattr(app_module, "upload_bytes", lambda data, object_name, content_type: None)
    monkeypatch.setattr(app_module, "delete_object", lambda object_name: None)
    monkeypatch.setattr(app_module, "get_presigned_url", lambda object_name: f"https://minio.local/{object_name}")
    return db


@pytest.fixture
def client(fake_db):
    return TestClient(app_module.app)
