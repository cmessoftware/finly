"""
Unit tests for DebtRecord monthly projection behavior (DBT-FEAT-003).
Run from backend/ with: conda run -n finly pytest tests/test_debt_record_projection_service.py -v
"""
import os
import sys
from pathlib import Path
from uuid import uuid4

# Use a local SQLite database for deterministic service-level tests.
TEST_DB_PATH = Path(__file__).resolve().parent / "test_dbt_projection.db"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB_PATH.as_posix()}"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
import math

from database.database import Base, engine, SessionLocal, User, Role, BudgetItem, DebtRecord
from services.debt_record_service import DebtRecordService


@pytest.fixture(scope="module", autouse=True)
def setup_db():
    if TEST_DB_PATH.exists():
        TEST_DB_PATH.unlink()

    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)
    engine.dispose()

    if TEST_DB_PATH.exists():
        TEST_DB_PATH.unlink()


@pytest.fixture(scope="function")
def seeded_user_id():
    db = SessionLocal()
    role = Role(name=f"WRITER_PROJ_{uuid4().hex[:8]}", description="Writer for projection tests")
    db.add(role)
    db.flush()

    user = User(
        username=f"projection_user_{role.id}",
        email=f"projection_user_{role.id}@test.local",
        hashed_password="hashed",
        is_active=True,
        is_locked=False,
    )
    user.roles = [role]
    db.add(user)
    db.commit()

    user_id = user.id
    db.close()
    return user_id


@pytest.fixture(scope="function")
def service():
    svc = DebtRecordService()
    try:
        yield svc
    finally:
        svc.close()


def _count_projection_months(record_id):
    db = SessionLocal()
    months = [
        row.version_source_month
        for row in db.query(BudgetItem)
        .filter(BudgetItem.debt_record_id == record_id)
        .order_by(BudgetItem.version_source_month.asc())
        .all()
    ]
    db.close()
    return months


TEST_DELETE_PREFIX = "[TEST-DELETE] "


def test_create_without_due_date_defaults_next_month_and_generates_12(service, seeded_user_id):
    rec = service.create_debt_record(
        {
            "debt_name": f"{TEST_DELETE_PREFIX}TEST_12",
            "debt_type": "PERSONAL",
            "principal_amount": 1200000,
            "outstanding_amount": 1200000,
            "total_installments": 12,
            "current_installment": 1,
            "start_date": "2026-06-02",
        },
        user_id=seeded_user_id,
    )

    db = SessionLocal()
    row = db.query(DebtRecord).filter(DebtRecord.id == rec["id"]).first()
    db.close()

    assert str(row.due_date) == "2026-07-02"

    months = _count_projection_months(rec["id"])
    assert len(months) == 12
    assert months[0] == "2026-07"
    assert months[-1] == "2027-06"


def test_projection_count_matches_total_installments_when_mid_loan(service, seeded_user_id):
    rec = service.create_debt_record(
        {
            "debt_name": f"{TEST_DELETE_PREFIX}TEST_REMAINING",
            "debt_type": "PERSONAL",
            "principal_amount": 600000,
            "outstanding_amount": 600000,
            "total_installments": 6,
            "current_installment": 3,
            "start_date": "2026-01-10",
        },
        user_id=seeded_user_id,
    )

    months = _count_projection_months(rec["id"])
    assert len(months) == 6

    records = service.get_debt_records_with_projection(user_id=seeded_user_id)
    target = next(r for r in records if r["id"] == rec["id"])
    cuota1 = next(p for p in target["projections"] if p["debt_quota_number"] == 1)
    cuota3 = next(p for p in target["projections"] if p["debt_quota_number"] == 3)
    assert cuota1["status"] in ("PAGADA", "Pagada")
    assert cuota3["status"] in ("PENDIENTE", "Pendiente")


def test_reconcile_restores_missing_projection_rows(service, seeded_user_id):
    rec = service.create_debt_record(
        {
            "debt_name": f"{TEST_DELETE_PREFIX}TEST_RECONCILE",
            "debt_type": "PERSONAL",
            "principal_amount": 300000,
            "outstanding_amount": 300000,
            "total_installments": 6,
            "current_installment": 1,
            "start_date": "2026-03-01",
        },
        user_id=seeded_user_id,
    )

    db = SessionLocal()
    rows = (
        db.query(BudgetItem)
        .filter(BudgetItem.debt_record_id == rec["id"])
        .order_by(BudgetItem.version_source_month.asc())
        .all()
    )

    # Corrupt one projection row to simulate inconsistent historic data.
    db.delete(rows[-1])
    db.commit()
    db.close()

    result = service.get_debt_records_with_projection(user_id=seeded_user_id)
    target = next(r for r in result if r["id"] == rec["id"])

    assert target["projection_count"] == 6
    assert len(target.get("projections", [])) == 6


def test_projection_amount_applies_annual_interest_annuity(service, seeded_user_id):
    rec = service.create_debt_record(
        {
            "debt_name": f"{TEST_DELETE_PREFIX}TEST_INTEREST_ANNUITY",
            "debt_type": "PERSONAL",
            "principal_amount": 5000000,
            "outstanding_amount": 5000000,
            "annual_interest_rate": 88,
            "interest_vat_rate": 0,
            "total_installments": 12,
            "current_installment": 1,
            "start_date": "2026-06-01",
        },
        user_id=seeded_user_id,
    )

    records = service.get_debt_records_with_projection(user_id=seeded_user_id)
    target = next(r for r in records if r["id"] == rec["id"])
    first_projection = target["projection_current"]

    monthly_rate = 0.88 / 12.0
    n = 12
    expected_quota = 5000000 * monthly_rate / (1 - math.pow(1 + monthly_rate, -n))

    assert first_projection is not None
    assert first_projection["monto_total"] == pytest.approx(expected_quota, rel=1e-4)


def test_add_partial_payment_reconciles_outstanding_and_installments(service, seeded_user_id):
    rec = service.create_debt_record(
        {
            "debt_name": f"{TEST_DELETE_PREFIX}TEST_PAYMENT_PARTIAL",
            "debt_type": "PERSONAL",
            "principal_amount": 1200000,
            "outstanding_amount": 1200000,
            "total_installments": 12,
            "current_installment": 1,
            "pending_installments": 12,
            "start_date": "2026-01-01",
        },
        user_id=seeded_user_id,
    )

    service.add_payment(
        debt_record_id=rec["id"],
        data={"amount": 250000, "payment_date": "2026-02-01"},
        user_id=seeded_user_id,
    )

    updated = service.get_debt_record(record_id=rec["id"], user_id=seeded_user_id)
    assert updated["outstanding_amount"] == pytest.approx(950000, rel=1e-9)
    assert updated["current_installment"] == pytest.approx(3.5, rel=1e-9)
    assert updated["pending_installments"] == pytest.approx(9.5, rel=1e-9)
    assert updated["status"] == "ACTIVA"


def test_add_full_payment_cancels_debt(service, seeded_user_id):
    rec = service.create_debt_record(
        {
            "debt_name": f"{TEST_DELETE_PREFIX}TEST_PAYMENT_FULL",
            "debt_type": "PERSONAL",
            "principal_amount": 500000,
            "outstanding_amount": 500000,
            "total_installments": 5,
            "current_installment": 1,
            "pending_installments": 5,
            "start_date": "2026-03-01",
        },
        user_id=seeded_user_id,
    )

    service.add_payment(
        debt_record_id=rec["id"],
        data={"amount": 500000, "payment_date": "2026-03-10"},
        user_id=seeded_user_id,
    )

    updated = service.get_debt_record(record_id=rec["id"], user_id=seeded_user_id)
    assert updated["outstanding_amount"] == pytest.approx(0.0, rel=1e-9)
    assert updated["pending_installments"] == pytest.approx(0.0, rel=1e-9)
    assert updated["current_installment"] == pytest.approx(6.0, rel=1e-9)
    assert updated["status"] == "CANCELADA"


def test_card_extraction_single_installment_projects_in_due_month(service, seeded_user_id):
    """Extraccion TC 1 cuota: debe proyectarse en el mes de la primera cuota (mayo)."""
    rec = service.create_debt_record(
        {
            "debt_name": f"{TEST_DELETE_PREFIX}Extraccion TC mayo sergio",
            "debt_type": "TARJETA",
            "debt_source": "BANCO",
            "creditor": "Banco Galicia",
            "principal_amount": 3500000,
            "outstanding_amount": 3500000,
            "annual_interest_rate": 6.9,
            "total_installments": 1,
            "current_installment": 1,
            "pending_installments": 1,
            "start_date": "2026-05-01",
            "due_date": "2026-05-15",
        },
        user_id=seeded_user_id,
    )

    months = _count_projection_months(rec["id"])
    assert months == ["2026-05"], (
        f"Expected projection in 2026-05 for debt-record {rec['id']} "
        f"({rec['debt_name']}); got {months}. Delete with debt-record id={rec['id']}."
    )

    records = service.get_debt_records_with_projection(user_id=seeded_user_id)
    target = next(r for r in records if r["id"] == rec["id"])
    assert "2026-05" in target.get("projection_months", [])


def test_salary_percent_installments_increase_every_n_months(service, seeded_user_id):
    """Cuota = z% sueldo; sueldo sube x% cada n meses."""
    rec = service.create_debt_record(
        {
            "debt_name": f"{TEST_DELETE_PREFIX}Prestamo sueldo variable",
            "debt_type": "PERSONAL",
            "principal_amount": 5000000,
            "outstanding_amount": 5000000,
            "total_installments": 12,
            "current_installment": 1,
            "start_date": "2026-01-01",
            "due_date": "2026-01-15",
            "installment_mode": "SALARY_PERCENT",
            "base_salary": 1000000,
            "installment_salary_percent": 30,
            "salary_increase_percent": 10,
            "salary_increase_interval_months": 6,
        },
        user_id=seeded_user_id,
    )

    db = SessionLocal()
    projections = (
        db.query(BudgetItem)
        .filter(BudgetItem.debt_record_id == rec["id"])
        .order_by(BudgetItem.version_source_month.asc())
        .all()
    )
    db.close()

    assert len(projections) == 12
    # Cuota 1-6: 30% de 1.000.000 = 300.000
    assert projections[0].monto_total == pytest.approx(300000, rel=1e-9)
    assert projections[5].monto_total == pytest.approx(300000, rel=1e-9)
    # Cuota 7+: 30% de 1.100.000 = 330.000
    assert projections[6].monto_total == pytest.approx(330000, rel=1e-9)
    assert projections[11].monto_total == pytest.approx(330000, rel=1e-9)

    assert f"debt-record {rec['id']}" in projections[0].detalle


def test_projection_amount_includes_iva_on_interest(service, seeded_user_id):
    rec = service.create_debt_record(
        {
            "debt_name": f"{TEST_DELETE_PREFIX}TEST_IVA",
            "debt_type": "PERSONAL",
            "principal_amount": 1000000,
            "outstanding_amount": 1000000,
            "annual_interest_rate": 12,
            "interest_vat_rate": 21,
            "total_installments": 12,
            "current_installment": 1,
            "start_date": "2026-06-01",
        },
        user_id=seeded_user_id,
    )

    records = service.get_debt_records_with_projection(user_id=seeded_user_id)
    target = next(r for r in records if r["id"] == rec["id"])
    first_projection = target["projection_current"]

    monthly_rate = 0.12 / 12.0
    n = 12
    pmt = 1000000 * monthly_rate / (1 - math.pow(1 + monthly_rate, -n))
    interest = 1000000 * monthly_rate
    vat = interest * 0.21
    expected = pmt + vat

    assert first_projection is not None
    assert first_projection["monto_total"] == pytest.approx(expected, rel=1e-4)


def test_interest_vat_rate_defaults_to_21(service, seeded_user_id):
    rec = service.create_debt_record(
        {
            "debt_name": f"{TEST_DELETE_PREFIX}TEST_IVA_DEFAULT",
            "debt_type": "PERSONAL",
            "principal_amount": 500000,
            "outstanding_amount": 500000,
            "annual_interest_rate": 24,
            "total_installments": 6,
            "current_installment": 1,
            "start_date": "2026-06-01",
        },
        user_id=seeded_user_id,
    )

    row = service.get_debt_record(record_id=rec["id"], user_id=seeded_user_id)
    assert row["interest_vat_rate"] == pytest.approx(21.0, rel=1e-9)


def test_projection_ejecutado_is_per_installment_not_loan_total(service, seeded_user_id):
    rec = service.create_debt_record(
        {
            "debt_name": f"{TEST_DELETE_PREFIX}TEST_PROJ_EJEC",
            "debt_type": "PERSONAL",
            "principal_amount": 1200000,
            "outstanding_amount": 1200000,
            "annual_interest_rate": 0,
            "interest_vat_rate": 0,
            "total_installments": 12,
            "current_installment": 1,
            "start_date": "2026-01-01",
        },
        user_id=seeded_user_id,
    )

    records = service.get_debt_records_with_projection(user_id=seeded_user_id)
    target = next(r for r in records if r["id"] == rec["id"])
    first = target["projections"][0]
    assert first["monto_ejecutado"] == pytest.approx(0, abs=0.01)

    installment = first["monto_total"]
    service.add_payment(
        debt_record_id=rec["id"],
        data={"amount": installment / 2, "payment_date": "2026-02-01"},
        user_id=seeded_user_id,
    )

    records = service.get_debt_records_with_projection(user_id=seeded_user_id)
    target = next(r for r in records if r["id"] == rec["id"])
    cuota1 = target["projections"][0]
    assert cuota1["debt_quota_number"] == 1
    assert cuota1["monto_ejecutado"] == pytest.approx(installment / 2, rel=1e-4)
    assert cuota1["status"] in ("PAGO_PARCIAL", "Pago parcial")
    assert cuota1["monto_ejecutado"] < rec["principal_amount"]


def test_full_installment_payment_marks_quota_paid_and_keeps_schedule(service, seeded_user_id):
    rec = service.create_debt_record(
        {
            "debt_name": f"{TEST_DELETE_PREFIX}TEST_PROJ_FULL_PAY",
            "debt_type": "PERSONAL",
            "principal_amount": 5000000,
            "outstanding_amount": 5000000,
            "annual_interest_rate": 88,
            "interest_vat_rate": 21,
            "total_installments": 12,
            "current_installment": 1,
            "pending_installments": 12,
            "start_date": "2026-03-01",
            "due_date": "2026-03-01",
        },
        user_id=seeded_user_id,
    )

    records = service.get_debt_records_with_projection(user_id=seeded_user_id)
    target = next(r for r in records if r["id"] == rec["id"])
    cuota1_before = target["projections"][0]
    installment = cuota1_before["monto_total"]

    service.add_payment(
        debt_record_id=rec["id"],
        data={"amount": installment, "payment_date": "2026-03-16"},
        user_id=seeded_user_id,
    )

    records = service.get_debt_records_with_projection(user_id=seeded_user_id)
    target = next(r for r in records if r["id"] == rec["id"])
    assert target["current_installment"] == pytest.approx(2.0, rel=1e-9)

    cuota1 = next(p for p in target["projections"] if p["debt_quota_number"] == 1)
    cuota2 = next(p for p in target["projections"] if p["debt_quota_number"] == 2)

    assert cuota1["monto_ejecutado"] == pytest.approx(installment, rel=1e-4)
    assert cuota1["status"] in ("PAGADA", "Pagada")
    assert cuota2["monto_ejecutado"] == pytest.approx(0, abs=0.01)
    assert cuota2["status"] in ("PENDIENTE", "Pendiente")
    assert cuota1["monto_total"] == pytest.approx(installment, rel=1e-4)
    assert cuota2["monto_total"] != cuota1["monto_total"]
