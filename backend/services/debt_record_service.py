"""Service for real-debt tracking: DebtRecord and DebtPayment CRUD."""
from database.database import (
    SessionLocal, DebtRecord, DebtPayment, DebtRecordStatus, DebtType,
    BudgetItem, BudgetType, FlowType, ExpenseType, DebtStatus
)
from datetime import date, datetime
import calendar
import math
import logging

from services.schema_checks import assert_debt_record_schema

logger = logging.getLogger(__name__)


class DebtRecordService:
    def __init__(self):
        self.db = SessionLocal()

    def close(self):
        if self.db:
            self.db.close()

    def _ensure_schema(self):
        assert_debt_record_schema(self.db.bind)

    # ──────────────────────────────────────────────────────────
    # DEBT RECORDS
    # ──────────────────────────────────────────────────────────

    def _projection_base_date(self, record: DebtRecord) -> date:
        # start_date = debt acquisition date (not first installment date).
        # Projection must start on first installment date (due_date).
        # If due_date is not provided, default to next month from start_date.
        if record.due_date is not None:
            return record.due_date
        if record.start_date is not None:
            return self._add_months(record.start_date, 1)
        return date.today()

    def _projection_installment_count(self, record: DebtRecord) -> int:
        total = float(record.total_installments) if record.total_installments is not None else None
        current = float(record.current_installment) if record.current_installment is not None else None
        pending = float(record.pending_installments) if record.pending_installments is not None else None

        if pending is not None and pending > 0:
            # Keep one row for any remaining fractional installment.
            return max(1, int(math.ceil(pending)))

        if total is not None and current is not None:
            remaining = int(round(total - current + 1.0))
            return max(1, remaining)
        if total is not None:
            return max(1, int(round(total)))
        return 1

    def _uses_salary_percent_installments(self, record: DebtRecord) -> bool:
        mode = getattr(record, 'installment_mode', None) or 'FIXED'
        return str(mode).upper() == 'SALARY_PERCENT'

    def _salary_at_installment_index(self, record: DebtRecord, installment_index: int) -> float:
        base_salary = float(record.base_salary or 0)
        increase_percent = float(record.salary_increase_percent or 0)
        interval = int(record.salary_increase_interval_months or 0)
        if base_salary <= 0:
            return 0.0
        if interval <= 0 or increase_percent <= 0:
            return base_salary
        periods = max(0, int(installment_index) // interval)
        return round(base_salary * math.pow(1.0 + increase_percent / 100.0, periods), 2)

    def _installment_amount_at_index(self, record: DebtRecord, installment_index: int) -> float:
        if self._uses_salary_percent_installments(record):
            salary = self._salary_at_installment_index(record, installment_index)
            percent = float(record.installment_salary_percent or 0)
            if salary <= 0 or percent <= 0:
                return 0.0
            return round(salary * percent / 100.0, 2)

        return self._fixed_installment_amount_at_index(record, installment_index)

    def _interest_vat_rate(self, record: DebtRecord) -> float:
        rate = getattr(record, 'interest_vat_rate', None)
        if rate is None:
            return 21.0
        return max(0.0, float(rate))

    def _pending_installments_for_calc(self, record: DebtRecord) -> float:
        pending = float(record.pending_installments) if record.pending_installments is not None else None
        if pending is None or pending <= 0:
            count = self._projection_installment_count(record)
            pending = float(count if count > 0 else 1)
        return pending

    def _annuity_payment(self, outstanding: float, monthly_rate: float, n: int) -> float:
        if n <= 0:
            return outstanding
        if monthly_rate <= 0:
            return outstanding / n
        denominator = 1.0 - math.pow(1.0 + monthly_rate, -n)
        if denominator <= 0:
            return outstanding / n
        return outstanding * monthly_rate / denominator

    def _balance_after_payments(self, outstanding: float, monthly_rate: float, pmt: float, payments_made: int) -> float:
        balance = outstanding
        for _ in range(payments_made):
            interest = balance * monthly_rate
            principal = pmt - interest
            balance = max(0.0, balance - principal)
        return balance

    def _fixed_installment_amount_at_index(self, record: DebtRecord, installment_index: int) -> float:
        outstanding = float(record.outstanding_amount or 0)
        if outstanding <= 0:
            return 0.0

        pending = self._pending_installments_for_calc(record)
        annual_rate = float(record.annual_interest_rate or 0)
        vat_rate = self._interest_vat_rate(record)

        if annual_rate <= 0:
            return round(outstanding / pending, 2)

        monthly_rate = annual_rate / 100.0 / 12.0
        use_annuity = abs(pending - round(pending)) < 1e-9

        if use_annuity:
            n = int(round(pending))
            pmt = self._annuity_payment(outstanding, monthly_rate, n)
            balance = self._balance_after_payments(outstanding, monthly_rate, pmt, installment_index)
            interest = balance * monthly_rate
            vat = interest * vat_rate / 100.0 if vat_rate > 0 else 0.0
            return round(pmt + vat, 2)

        base = outstanding / pending
        interest_estimate = outstanding * monthly_rate
        vat = interest_estimate * vat_rate / 100.0 if vat_rate > 0 else 0.0
        return round(base + vat, 2)

    def _sync_projection_ejecutado(self, record: DebtRecord, quota, per_installment_amount: float):
        """Derive monto_ejecutado and status for one scheduled quota from payment progress."""
        amount = float(per_installment_amount or 0)
        if amount <= 0:
            return 0.0, DebtStatus.PENDIENTE

        if float(record.outstanding_amount or 0) <= 1e-6:
            return round(amount, 2), DebtStatus.PAGADA

        current = float(record.current_installment or 1)
        completed = current - 1.0
        q = float(quota)
        completed_floor = math.floor(completed + 1e-9)
        completed_frac = completed - completed_floor

        if q <= completed_floor + 1e-9:
            return round(amount, 2), DebtStatus.PAGADA
        if completed_frac > 1e-9 and abs(q - (completed_floor + 1)) < 1e-9:
            ejecutado = round(amount * completed_frac, 2)
            status = DebtStatus.PAGO_PARCIAL if ejecutado < amount - 0.01 else DebtStatus.PAGADA
            return ejecutado, status
        return 0.0, DebtStatus.PENDIENTE

    def _validate_salary_percent_fields(self, data: dict, existing: DebtRecord = None):
        mode = str(data.get('installment_mode') or (existing.installment_mode if existing else 'FIXED')).upper()
        if mode != 'SALARY_PERCENT':
            return

        base_salary = data.get('base_salary')
        if base_salary is None and existing is not None:
            base_salary = existing.base_salary
        percent = data.get('installment_salary_percent')
        if percent is None and existing is not None:
            percent = existing.installment_salary_percent
        increase = data.get('salary_increase_percent')
        if increase is None and existing is not None:
            increase = existing.salary_increase_percent
        interval = data.get('salary_increase_interval_months')
        if interval is None and existing is not None:
            interval = existing.salary_increase_interval_months

        if base_salary is None or float(base_salary) <= 0:
            raise ValueError("base_salary must be greater than 0 for SALARY_PERCENT installment mode")
        if percent is None or float(percent) <= 0:
            raise ValueError("installment_salary_percent (z) must be greater than 0 for SALARY_PERCENT mode")
        if increase is None or float(increase) < 0:
            raise ValueError("salary_increase_percent (x) must be greater or equal to 0 for SALARY_PERCENT mode")
        if interval is None or int(interval) < 1:
            raise ValueError("salary_increase_interval_months (n) must be at least 1 for SALARY_PERCENT mode")

        total = data.get('total_installments')
        if total is None and existing is not None:
            total = existing.total_installments
        if total is None or float(total) <= 0:
            raise ValueError("total_installments is required for SALARY_PERCENT installment mode")

    def _installment_amount_for_progress(self, record: DebtRecord) -> float:
        outstanding = float(record.outstanding_amount or 0)
        if outstanding <= 0:
            return 0.0

        if self._uses_salary_percent_installments(record):
            current = float(record.current_installment or 1)
            installment_index = max(0, int(math.floor(current - 1)))
            amount = self._installment_amount_at_index(record, installment_index)
            return amount if amount > 0 else 0.0

        current = float(record.current_installment or 1)
        installment_index = max(0, int(math.floor(current - 1)))
        amount = self._installment_amount_at_index(record, installment_index)
        return amount if amount > 0 else 0.0

    def _apply_payment_reconciliation(self, record: DebtRecord, amount: float, reverse: bool = False):
        """Apply (or reverse) payment effects on outstanding and installments."""
        signed_amount = -amount if reverse else amount
        current_outstanding = float(record.outstanding_amount or 0)

        if not reverse and signed_amount > current_outstanding + 1e-6:
            raise ValueError("Payment amount cannot exceed outstanding amount")

        installment_amount = self._installment_amount_for_progress(record)
        progressed_installments = (signed_amount / installment_amount) if installment_amount > 0 else 0.0

        if reverse:
            record.outstanding_amount = current_outstanding + amount
        else:
            record.outstanding_amount = max(0.0, current_outstanding - amount)

        total = float(record.total_installments) if record.total_installments is not None else None
        current = float(record.current_installment) if record.current_installment is not None else None
        pending = float(record.pending_installments) if record.pending_installments is not None else None

        if current is not None:
            updated_current = current + progressed_installments
            min_current = 0.0
            max_current = (total + 1.0) if total is not None else updated_current
            record.current_installment = min(max(updated_current, min_current), max_current)

        if pending is not None:
            updated_pending = pending - progressed_installments
            max_pending = total if total is not None else pending + abs(progressed_installments)
            record.pending_installments = min(max(updated_pending, 0.0), max_pending)

        if float(record.outstanding_amount or 0) <= 1e-6:
            record.outstanding_amount = 0.0
            record.status = DebtRecordStatus.CANCELADA
            if total is not None:
                record.current_installment = total + 1.0
            if record.pending_installments is not None:
                record.pending_installments = 0.0
        elif record.status == DebtRecordStatus.CANCELADA:
            record.status = DebtRecordStatus.ACTIVA

    def _add_months(self, dt: date, months: int) -> date:
        year = dt.year + (dt.month - 1 + months) // 12
        month = ((dt.month - 1 + months) % 12) + 1
        day = min(dt.day, calendar.monthrange(year, month)[1])
        return date(year, month, day)

    def _projection_schedule(self, record: DebtRecord) -> list:
        base = self._projection_base_date(record)
        count = self._projection_installment_count(record)
        total = float(record.total_installments) if record.total_installments is not None else None

        if total is not None:
            first_quota = int(total - count + 1)
        else:
            first_quota = int(float(record.current_installment or 1))

        schedule = []
        for idx in range(count):
            projection_date = self._add_months(base, idx)
            month_key = projection_date.strftime("%Y-%m")
            quota = first_quota + idx
            schedule.append((month_key, projection_date, quota))
        return schedule

    def _projection_amount(self, record: DebtRecord) -> float:
        """Legacy helper: installment amount for the next scheduled payment."""
        return self._fixed_installment_amount_at_index(record, 0)

    def _build_projection_detail(self, record: DebtRecord, quota=None, installment_amount=None) -> str:
        quota_label = ""
        current = quota
        if current is None and record.current_installment is not None:
            current = record.current_installment
        if current is not None and record.total_installments is not None:
            quota_label = f" - cuota {self._fmt_quota(current)}/{self._fmt_quota(record.total_installments)}"
        amount_label = ""
        if installment_amount is not None and self._uses_salary_percent_installments(record):
            amount_label = f" (${installment_amount:,.2f})"
        return f"DBT {record.debt_name}{quota_label}{amount_label} (debt-record {record.id})"

    def _fmt_quota(self, value: float) -> str:
        n = float(value)
        if n.is_integer():
            return str(int(n))
        return f"{n:.2f}".rstrip("0").rstrip(".")

    def _normalize_installments(self, data: dict, existing: DebtRecord = None) -> dict:
        normalized = dict(data)

        total = normalized.get("total_installments")
        current = normalized.get("current_installment")
        pending = normalized.get("pending_installments")

        if total is None and existing is not None:
            total = existing.total_installments
        if current is None and existing is not None:
            current = existing.current_installment
        if pending is None and existing is not None:
            pending = existing.pending_installments

        total = float(total) if total is not None else None
        current = float(current) if current is not None else None
        pending = float(pending) if pending is not None else None

        if total is not None and total <= 0:
            raise ValueError("total_installments must be greater than 0")
        if current is not None and current < 0:
            raise ValueError("current_installment must be greater or equal to 0")
        if pending is not None and pending < 0:
            raise ValueError("pending_installments must be greater or equal to 0")
        if total is not None and current is not None and current > total:
            raise ValueError("current_installment cannot be greater than total_installments")

        if current is None and total is not None:
            current = 1.0

        if pending is None and total is not None and current is not None:
            pending = max(0.0, total - current + 1.0)

        normalized["total_installments"] = total
        normalized["current_installment"] = current
        normalized["pending_installments"] = pending

        mode = normalized.get("installment_mode")
        if mode is None and existing is not None:
            mode = existing.installment_mode
        normalized["installment_mode"] = str(mode or "FIXED").upper()
        self._validate_salary_percent_fields(normalized, existing=existing)

        interval = normalized.get("salary_increase_interval_months")
        if interval is not None:
            normalized["salary_increase_interval_months"] = int(interval)

        return normalized

    def _resolve_due_date(self, start_date_value, due_date_value):
        start_date = _parse_date(start_date_value)
        due_date = _parse_date(due_date_value)
        if due_date is not None:
            return due_date
        if start_date is not None:
            return self._add_months(start_date, 1)
        return None

    def _reconcile_projection_if_needed(self, record: DebtRecord, projections_for_record: list) -> bool:
        expected_schedule = self._projection_schedule(record)
        expected_months = {month_key for month_key, _, _ in expected_schedule}
        existing_by_month = {
            p.version_source_month: p for p in projections_for_record if p.version_source_month
        }
        existing_months = set(existing_by_month.keys())

        if expected_months != existing_months:
            self._upsert_budget_projection(record)
            return True

        for idx, (month_key, _, quota) in enumerate(expected_schedule):
            existing = existing_by_month.get(month_key)
            if not existing:
                self._upsert_budget_projection(record)
                return True
            expected_amount = self._installment_amount_at_index(record, idx)
            if abs(float(existing.monto_total or 0) - expected_amount) > 0.01:
                self._upsert_budget_projection(record)
                return True
            expected_ej, _ = self._sync_projection_ejecutado(record, quota, expected_amount)
            if abs(float(existing.monto_ejecutado or 0) - expected_ej) > 0.01:
                self._upsert_budget_projection(record)
                return True
            if existing.debt_quota_number is not None and quota is not None:
                if abs(float(existing.debt_quota_number) - float(quota)) > 0.0001:
                    self._upsert_budget_projection(record)
                    return True

        return False

    def _upsert_budget_projection(self, record: DebtRecord):
        """Create/update monthly budget projections linked to a debt record."""
        schedule = self._projection_schedule(record)

        existing = self.db.query(BudgetItem).filter(
            BudgetItem.debt_record_id == record.id,
            BudgetItem.user_id == record.user_id,
        ).all()
        existing_by_month = {item.version_source_month: item for item in existing if item.version_source_month}

        valid_months = {month_key for month_key, _, _ in schedule}

        for idx, (month_key, projection_date, quota) in enumerate(schedule):
            per_installment_amount = self._installment_amount_at_index(record, idx)
            projection_date_iso = projection_date.isoformat()
            budget_item = existing_by_month.get(month_key)
            detail = self._build_projection_detail(record, quota, per_installment_amount)

            if not budget_item:
                budget_item = BudgetItem(
                    user_id=record.user_id,
                    debt_record_id=record.id,
                    version_source_month=month_key,
                    fecha=projection_date_iso,
                    fecha_vencimiento=projection_date_iso,
                    tipo="Deuda no tarjeta",
                    categoria="Deudas",
                    detalle=detail,
                    monto_total=per_installment_amount,
                    monto_pagado=0.0,
                    monto_ejecutado=0.0,
                    estimated_payment=per_installment_amount,
                    status=DebtStatus.PENDIENTE,
                    tipo_presupuesto=BudgetType.OBLIGATION,
                    tipo_flujo=FlowType.GASTO,
                    expense_type=ExpenseType.VARIABLE,
                    debt_source=record.debt_source or record.creditor,
                    debt_quota_number=quota,
                    debt_total_quotas=record.total_installments,
                )
                self.db.add(budget_item)
            else:
                budget_item.fecha = projection_date_iso
                budget_item.fecha_vencimiento = projection_date_iso
                budget_item.detalle = detail
                budget_item.monto_total = per_installment_amount
                budget_item.estimated_payment = per_installment_amount
                budget_item.debt_source = record.debt_source or record.creditor
                budget_item.debt_quota_number = quota
                budget_item.debt_total_quotas = record.total_installments
                budget_item.updated_at = datetime.utcnow()

            ejecutado, quota_status = self._sync_projection_ejecutado(record, quota, per_installment_amount)
            budget_item.monto_ejecutado = ejecutado
            budget_item.monto_pagado = ejecutado
            budget_item.status = quota_status

        # Remove obsolete projections when dates/installments changed.
        for item in existing:
            if item.version_source_month not in valid_months:
                self.db.delete(item)

    def _projection_to_dict(self, projection: BudgetItem) -> dict:
        if not projection:
            return None
        return {
            "id": projection.id,
            "debt_record_id": projection.debt_record_id,
            "fecha": projection.fecha,
            "fecha_vencimiento": projection.fecha_vencimiento,
            "monto_total": projection.monto_total,
            "monto_ejecutado": projection.monto_ejecutado,
            "estimated_payment": projection.estimated_payment,
            "status": projection.status.value if hasattr(projection.status, "value") else projection.status,
            "tipo_flujo": projection.tipo_flujo.value if hasattr(projection.tipo_flujo, "value") else projection.tipo_flujo,
            "version_source_month": projection.version_source_month,
            "debt_quota_number": projection.debt_quota_number,
            "debt_total_quotas": projection.debt_total_quotas,
        }

    def _projection_sort_key(self, projection: BudgetItem):
        return (projection.version_source_month or "", projection.id)

    def get_debt_records(self, user_id: int, status: str = None) -> list:
        """Return all debt records for a user, optionally filtered by status."""
        self._ensure_schema()
        q = self.db.query(DebtRecord).filter(DebtRecord.user_id == user_id)
        if status:
            try:
                status_enum = DebtRecordStatus(status)
                q = q.filter(DebtRecord.status == status_enum)
            except ValueError:
                pass
        records = q.order_by(DebtRecord.created_at.desc()).all()
        return [self._record_to_dict(r) for r in records]

    def get_debt_records_with_projection(self, user_id: int, status: str = None) -> list:
        """Return debt records enriched with linked budget projection summary."""
        self._ensure_schema()
        q = self.db.query(DebtRecord).filter(DebtRecord.user_id == user_id)
        if status:
            try:
                q = q.filter(DebtRecord.status == DebtRecordStatus(status))
            except ValueError:
                pass

        records = q.order_by(DebtRecord.created_at.desc()).all()
        record_ids = [r.id for r in records]
        projections = []
        if record_ids:
            projections = self.db.query(BudgetItem).filter(
                BudgetItem.user_id == user_id,
                BudgetItem.debt_record_id.in_(record_ids)
            ).all()

        by_record = {}
        for p in projections:
            by_record.setdefault(p.debt_record_id, []).append(p)

        reconciled_any = False
        for record in records:
            projections_for_record = by_record.get(record.id, [])
            if self._reconcile_projection_if_needed(record, projections_for_record):
                reconciled_any = True

        if reconciled_any:
            self.db.commit()
            projections = self.db.query(BudgetItem).filter(
                BudgetItem.user_id == user_id,
                BudgetItem.debt_record_id.in_(record_ids)
            ).all() if record_ids else []
            by_record = {}
            for p in projections:
                by_record.setdefault(p.debt_record_id, []).append(p)

        result = []
        for record in records:
            projections_for_record = by_record.get(record.id, [])
            projections_sorted = sorted(projections_for_record, key=self._projection_sort_key)
            projection_current = None
            if projections_sorted and record.current_installment is not None:
                current = float(record.current_installment)
                projection_current = next(
                    (
                        p for p in projections_sorted
                        if p.debt_quota_number is not None and abs(float(p.debt_quota_number) - current) < 0.0001
                    ),
                    None,
                )

            if projection_current is None and projections_sorted:
                projection_current = projections_sorted[0]

            item = self._record_to_dict(record)
            item["projection_count"] = len(projections_sorted)
            item["projection_current"] = self._projection_to_dict(projection_current)
            item["projections"] = [self._projection_to_dict(p) for p in projections_sorted]
            item["projection_months"] = [p.version_source_month for p in projections_sorted if p.version_source_month]
            result.append(item)

        return result

    def get_debt_record(self, record_id: int, user_id: int) -> dict:
        """Return a single debt record (must belong to user)."""
        record = self.db.query(DebtRecord).filter(
            DebtRecord.id == record_id,
            DebtRecord.user_id == user_id
        ).first()
        if not record:
            return None
        return self._record_to_dict(record)

    def create_debt_record(self, data: dict, user_id: int) -> dict:
        """Create a new debt record."""
        self._ensure_schema()
        payload = self._normalize_installments(data)
        start_date = _parse_date(payload.get("start_date"))
        due_date = self._resolve_due_date(start_date, payload.get("due_date"))
        record = DebtRecord(
            user_id=user_id,
            debt_name=payload["debt_name"],
            debt_type=DebtType(payload["debt_type"]),
            debt_source=payload.get("debt_source"),
            creditor=payload.get("creditor"),
            currency=payload.get("currency", "ARS"),
            principal_amount=float(payload["principal_amount"]),
            outstanding_amount=float(payload.get("outstanding_amount", payload["principal_amount"])),
            annual_interest_rate=float(payload["annual_interest_rate"]) if payload.get("annual_interest_rate") is not None else None,
            interest_vat_rate=float(payload.get("interest_vat_rate", 21.0)) if payload.get("interest_vat_rate") is not None else 21.0,
            total_installments=payload.get("total_installments"),
            current_installment=payload.get("current_installment"),
            pending_installments=payload.get("pending_installments"),
            installment_mode=payload.get("installment_mode", "FIXED"),
            base_salary=float(payload["base_salary"]) if payload.get("base_salary") is not None else None,
            installment_salary_percent=float(payload["installment_salary_percent"]) if payload.get("installment_salary_percent") is not None else None,
            salary_increase_percent=float(payload["salary_increase_percent"]) if payload.get("salary_increase_percent") is not None else None,
            salary_increase_interval_months=int(payload["salary_increase_interval_months"]) if payload.get("salary_increase_interval_months") is not None else None,
            start_date=start_date,
            due_date=due_date,
            status=DebtRecordStatus(payload.get("status", DebtRecordStatus.ACTIVA.value)),
            notes=payload.get("notes"),
        )
        if record.due_date is None and record.start_date is not None:
            record.due_date = self._add_months(record.start_date, 1)
        try:
            self.db.add(record)
            self.db.flush()
            self._upsert_budget_projection(record)
            self.db.commit()
            self.db.refresh(record)
        except Exception:
            self.db.rollback()
            raise
        logger.info(f"✅ DebtRecord created (id={record.id}) for user {user_id}")
        return self._record_to_dict(record)

    def update_debt_record(self, record_id: int, data: dict, user_id: int) -> dict:
        """Update fields of an existing debt record."""
        record = self.db.query(DebtRecord).filter(
            DebtRecord.id == record_id,
            DebtRecord.user_id == user_id
        ).first()
        if not record:
            return None

        payload = self._normalize_installments(data, existing=record)

        updatable = [
            "debt_name", "creditor", "currency", "principal_amount",
            "outstanding_amount", "annual_interest_rate", "interest_vat_rate", "notes",
            "debt_source", "total_installments", "current_installment", "pending_installments",
            "installment_mode", "base_salary", "installment_salary_percent",
            "salary_increase_percent", "salary_increase_interval_months",
        ]
        for field in updatable:
            if field in payload:
                setattr(record, field, payload[field])

        if "debt_type" in payload:
            record.debt_type = DebtType(payload["debt_type"])
        if "status" in payload:
            record.status = DebtRecordStatus(payload["status"])
        if "start_date" in payload:
            record.start_date = _parse_date(payload["start_date"])
        if "due_date" in payload:
            record.due_date = self._resolve_due_date(record.start_date, payload["due_date"])
        elif record.due_date is None and record.start_date is not None:
            # Keep date semantics stable for old records edited without an explicit due_date.
            record.due_date = self._add_months(record.start_date, 1)

        record.updated_at = datetime.utcnow()
        try:
            self._upsert_budget_projection(record)
            self.db.commit()
            self.db.refresh(record)
        except Exception:
            self.db.rollback()
            raise
        return self._record_to_dict(record)

    def delete_debt_record(self, record_id: int, user_id: int) -> bool:
        """Delete a debt record and its payments (cascade)."""
        record = self.db.query(DebtRecord).filter(
            DebtRecord.id == record_id,
            DebtRecord.user_id == user_id
        ).first()
        if not record:
            return False

        projections = self.db.query(BudgetItem).filter(
            BudgetItem.debt_record_id == record_id,
            BudgetItem.user_id == user_id
        ).all()
        for projection in projections:
            self.db.delete(projection)

        self.db.delete(record)
        try:
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        logger.info(f"🗑️ DebtRecord {record_id} deleted by user {user_id}")
        return True

    # ──────────────────────────────────────────────────────────
    # DEBT PAYMENTS
    # ──────────────────────────────────────────────────────────

    def get_payments(self, debt_record_id: int, user_id: int) -> list:
        """Return all payments for a debt record (verifying ownership)."""
        record = self.db.query(DebtRecord).filter(
            DebtRecord.id == debt_record_id,
            DebtRecord.user_id == user_id
        ).first()
        if not record:
            return None  # signals not found / unauthorized
        payments = self.db.query(DebtPayment).filter(
            DebtPayment.debt_record_id == debt_record_id
        ).order_by(DebtPayment.payment_date.desc()).all()
        return [self._payment_to_dict(p) for p in payments]

    def add_payment(self, debt_record_id: int, data: dict, user_id: int) -> dict:
        """Register a payment against a debt record, reducing outstanding_amount."""
        record = self.db.query(DebtRecord).filter(
            DebtRecord.id == debt_record_id,
            DebtRecord.user_id == user_id
        ).first()
        if not record:
            return None

        amount = float(data["amount"])
        if amount <= 0:
            raise ValueError("Payment amount must be greater than 0")

        if float(record.outstanding_amount or 0) <= 0:
            raise ValueError("Debt is already fully paid")

        payment = DebtPayment(
            debt_record_id=debt_record_id,
            transaction_id=data.get("transaction_id"),
            payment_date=_parse_date(data.get("payment_date")) or date.today(),
            amount=amount,
            notes=data.get("notes"),
        )
        self.db.add(payment)

        self._apply_payment_reconciliation(record, amount=amount, reverse=False)
        record.updated_at = datetime.utcnow()
        self._upsert_budget_projection(record)

        self.db.commit()
        self.db.refresh(payment)
        self.db.refresh(record)
        logger.info(f"✅ Payment {payment.id} registered for DebtRecord {debt_record_id}")
        return self._payment_to_dict(payment)

    def delete_payment(self, payment_id: int, user_id: int) -> bool:
        """Delete a payment and restore outstanding_amount on the parent record."""
        payment = self.db.query(DebtPayment).filter(
            DebtPayment.id == payment_id
        ).first()
        if not payment:
            return False

        # Verify ownership via parent record
        record = self.db.query(DebtRecord).filter(
            DebtRecord.id == payment.debt_record_id,
            DebtRecord.user_id == user_id
        ).first()
        if not record:
            return False

        self._apply_payment_reconciliation(record, amount=float(payment.amount), reverse=True)
        record.updated_at = datetime.utcnow()
        self._upsert_budget_projection(record)

        self.db.delete(payment)
        self.db.commit()
        logger.info(f"🗑️ Payment {payment_id} deleted, outstanding restored for DebtRecord {payment.debt_record_id}")
        return True

    # ──────────────────────────────────────────────────────────
    # SERIALIZATION HELPERS
    # ──────────────────────────────────────────────────────────

    def _record_to_dict(self, record: DebtRecord) -> dict:
        return {
            "id": record.id,
            "user_id": record.user_id,
            "debt_name": record.debt_name,
            "debt_type": record.debt_type.value if record.debt_type else None,
            "debt_source": record.debt_source,
            "creditor": record.creditor,
            "currency": record.currency,
            "principal_amount": record.principal_amount,
            "outstanding_amount": record.outstanding_amount,
            "annual_interest_rate": record.annual_interest_rate,
            "interest_vat_rate": record.interest_vat_rate if record.interest_vat_rate is not None else 21.0,
            "total_installments": record.total_installments,
            "current_installment": record.current_installment,
            "pending_installments": record.pending_installments,
            "installment_mode": record.installment_mode or "FIXED",
            "base_salary": record.base_salary,
            "installment_salary_percent": record.installment_salary_percent,
            "salary_increase_percent": record.salary_increase_percent,
            "salary_increase_interval_months": record.salary_increase_interval_months,
            "start_date": record.start_date.isoformat() if record.start_date else None,
            "due_date": record.due_date.isoformat() if record.due_date else None,
            "status": record.status.value if record.status else None,
            "notes": record.notes,
            "created_at": record.created_at.isoformat() if record.created_at else None,
            "updated_at": record.updated_at.isoformat() if record.updated_at else None,
        }

    def _payment_to_dict(self, payment: DebtPayment) -> dict:
        return {
            "id": payment.id,
            "debt_record_id": payment.debt_record_id,
            "transaction_id": payment.transaction_id,
            "payment_date": payment.payment_date.isoformat() if payment.payment_date else None,
            "amount": payment.amount,
            "notes": payment.notes,
            "created_at": payment.created_at.isoformat() if payment.created_at else None,
        }


# ── Helpers ───────────────────────────────────────────────────

def _parse_date(value) -> date:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None
