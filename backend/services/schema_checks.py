"""Detect missing DB columns before ORM queries fail with opaque 500s."""
from sqlalchemy import inspect

DEBT_RECORD_REQUIRED_COLUMNS = (
    "installment_mode",
    "base_salary",
    "installment_salary_percent",
    "salary_increase_percent",
    "salary_increase_interval_months",
    "interest_vat_rate",
)

CC_PURCHASE_REQUIRED_COLUMNS = (
    "movement_type",
    "cash_advance_fee",
    "currency",
    "amount_in_pesos",
)


def missing_table_columns(engine, table_name: str, required: tuple) -> list:
    inspector = inspect(engine)
    if table_name not in inspector.get_table_names():
        return list(required)
    existing = {col["name"] for col in inspector.get_columns(table_name)}
    return [col for col in required if col not in existing]


def debt_record_schema_status(engine) -> dict:
    missing = missing_table_columns(engine, "debt_records", DEBT_RECORD_REQUIRED_COLUMNS)
    return {"ok": not missing, "missing_columns": missing}


def credit_card_purchase_schema_status(engine) -> dict:
    missing = missing_table_columns(engine, "credit_card_purchases", CC_PURCHASE_REQUIRED_COLUMNS)
    return {"ok": not missing, "missing_columns": missing}


def assert_debt_record_schema(engine):
    status = debt_record_schema_status(engine)
    if not status["ok"]:
        cols = ", ".join(status["missing_columns"])
        raise RuntimeError(
            f"BD desactualizada: faltan columnas en debt_records ({cols}). "
            "Ejecute: cd backend && alembic upgrade head"
        )


def assert_credit_card_purchase_schema(engine):
    status = credit_card_purchase_schema_status(engine)
    if not status["ok"]:
        cols = ", ".join(status["missing_columns"])
        raise RuntimeError(
            f"BD desactualizada: faltan columnas en credit_card_purchases ({cols}). "
            "Ejecute: cd backend && alembic upgrade head"
        )
