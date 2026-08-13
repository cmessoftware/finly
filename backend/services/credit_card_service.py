from database.database import (
    SessionLocal, 
    CreditCard, CreditCardPurchase, InstallmentPlan, InstallmentScheduleItem,
    CreditCardStatement, CreditCardPayment,
    BudgetItem, DebtStatus, BudgetType, FlowType, ExpenseType,
    Transaction, TransactionType, Category, NecessityType, AssignmentStatus,
    InstallmentStatus, StatementStatus, InstallmentPlanType,
    AppSetting, CreditCardPeriodConfig
)
from datetime import datetime, timedelta, date
from dateutil.relativedelta import relativedelta
import logging

from services.schema_checks import assert_credit_card_purchase_schema

logger = logging.getLogger(__name__)

class CreditCardService:
    def __init__(self):
        self.db = SessionLocal()

    def _calculate_billing_date(self, purchase_date: date, currency: str) -> date:
        if currency == 'USD':
            return purchase_date + relativedelta(months=1)
        return purchase_date

    def _calculate_cash_advance_due_date(self, purchase_date: date, due_day: int) -> date:
        """Cash advance derived debt is due in the next card period."""
        from calendar import monthrange

        due_base = purchase_date + relativedelta(months=1)
        last_day = monthrange(due_base.year, due_base.month)[1]
        safe_day = min(max(int(due_day or due_base.day), 1), last_day)
        return date(due_base.year, due_base.month, safe_day)

    def _calculate_cash_advance_fee_amount(self, purchase: CreditCardPurchase) -> float:
        """Calculate fee amount in ARS from stored percentage in cash_advance_fee."""
        base_amount_ars = self._get_purchase_amount_in_ars(purchase)
        fee_percent = float(purchase.cash_advance_fee or 0.0)
        return round(float(base_amount_ars) * fee_percent / 100.0, 2)

    def _get_purchase_effective_total(self, purchase: CreditCardPurchase, plan: InstallmentPlan = None) -> float:
        """Total cost of a purchase in ARS, including cash-advance commission when applicable."""
        if plan is None:
            plan = self.db.query(InstallmentPlan).filter(
                InstallmentPlan.purchase_id == purchase.id
            ).first()
        base = float(plan.total_amount if plan else self._get_purchase_amount_in_ars(purchase))
        if (purchase.movement_type or 'normal') == 'cash_advance':
            return round(base + self._calculate_cash_advance_fee_amount(purchase), 2)
        return round(base, 2)

    def _summarize_period_cash_advances(self, purchases: list) -> dict:
        """Aggregate extraction amounts and commission fees for cash advances in a billing period."""
        extraction_total = 0.0
        commission_total = 0.0
        count = 0
        for purchase in purchases:
            if (purchase.movement_type or 'normal') != 'cash_advance':
                continue
            count += 1
            extraction_total += self._get_purchase_amount_in_ars(purchase)
            commission_total += self._calculate_cash_advance_fee_amount(purchase)
        return {
            'count': count,
            'extraction_total': round(extraction_total, 2),
            'commission_total': round(commission_total, 2),
        }

    def _resolve_cash_advance_category_id(self) -> int:
        """Get or create a category suitable for credit-card cash advances."""
        category = self.db.query(Category).filter(
            Category.name.in_(['Tarjeta de Credito', 'Tarjeta de Crédito'])
        ).first()
        if category:
            return category.id

        category = Category(name='Tarjeta de Credito')
        self.db.add(category)
        self.db.flush()
        return category.id

    def _upsert_cash_advance_transaction(self, purchase: CreditCardPurchase, card: CreditCard):
        """Create or update the transaction mirror so cash advance is visible in Gastos."""
        amount_ars = self._get_purchase_amount_in_ars(purchase)
        fee_amount = self._calculate_cash_advance_fee_amount(purchase)
        total_amount = round(float(amount_ars) + float(fee_amount), 2)
        category_id = self._resolve_cash_advance_category_id()
        detail = (
            f"Extraccion en efectivo - {purchase.description} "
            f"(Comision {float(purchase.cash_advance_fee or 0):.2f}% = ${fee_amount:,.2f})"
        )

        tx = None
        if purchase.transaction_id:
            tx = self.db.query(Transaction).filter(Transaction.id == purchase.transaction_id).first()

        if tx and tx.origin != 'CC_CASH_ADVANCE':
            tx = None

        if not tx:
            tx = Transaction(
                user_id=card.user_id,
                date=purchase.purchase_date.isoformat(),
                type=TransactionType.GASTO,
                category_id=category_id,
                amount=total_amount,
                necessity=NecessityType.NECESARIO,
                payment_method='Tarjeta de Credito',
                detail=detail,
                budget_item_id=None,
                assignment_status=AssignmentStatus.NO_PLANIFICADA,
                origin='CC_CASH_ADVANCE',
            )
            self.db.add(tx)
            self.db.flush()
            purchase.transaction_id = tx.id
        else:
            tx.user_id = card.user_id
            tx.date = purchase.purchase_date.isoformat()
            tx.type = TransactionType.GASTO
            tx.category_id = category_id
            tx.amount = total_amount
            tx.necessity = NecessityType.NECESARIO
            tx.payment_method = 'Tarjeta de Credito'
            tx.detail = detail
            tx.budget_item_id = None
            tx.assignment_status = AssignmentStatus.NO_PLANIFICADA
            tx.origin = 'CC_CASH_ADVANCE'
            tx.timestamp = datetime.utcnow()

    def _delete_cash_advance_transaction(self, purchase: CreditCardPurchase):
        """Delete mirrored transaction when cash advance is removed/deleted."""
        if not purchase.transaction_id:
            return

        tx = self.db.query(Transaction).filter(Transaction.id == purchase.transaction_id).first()
        if tx and tx.origin == 'CC_CASH_ADVANCE':
            self.db.delete(tx)

        purchase.transaction_id = None
    
    def close(self):
        """Close database session"""
        if self.db:
            self.db.close()
    
    # ========================================================================
    # CREDIT CARDS CRUD
    # ========================================================================
    
    def create_credit_card(self, card_data: dict, user_id: int = None) -> int:
        """Create a new credit card"""
        from sqlalchemy.exc import IntegrityError
        try:
            card = CreditCard(
                card_name=card_data['card_name'],
                bank_name=card_data['bank_name'],
                closing_day=card_data['closing_day'],
                due_day=card_data['due_day'],
                currency=card_data.get('currency', 'USD'),
                credit_limit=card_data.get('credit_limit'),
                is_active=card_data.get('is_active', True),
                notes=card_data.get('notes'),
                user_id=user_id
            )
            
            self.db.add(card)
            self.db.commit()
            self.db.refresh(card)
            
            logger.info(f"✅ Credit card '{card.card_name}' created successfully (ID: {card.id})")
            return card.id
            
        except IntegrityError:
            self.db.rollback()
            raise ValueError(f"Ya existe una tarjeta con el nombre '{card_data['card_name']}'")
        except Exception as e:
            self.db.rollback()
            logger.error(f"❌ Error creating credit card: {e}")
            raise
    
    def get_credit_cards(self, active_only=True, user_id: int = None) -> list:
        """Get all credit cards"""
        try:
            query = self.db.query(CreditCard)
            if active_only:
                query = query.filter(CreditCard.is_active == True)
            if user_id is not None:
                query = query.filter(CreditCard.user_id == user_id)
            
            cards = query.order_by(CreditCard.card_name).all()
            
            return [{
                'id': card.id,
                'card_name': card.card_name,
                'bank_name': card.bank_name,
                'closing_day': card.closing_day,
                'due_day': card.due_day,
                'currency': card.currency,
                'credit_limit': card.credit_limit,
                'is_active': card.is_active,
                'notes': card.notes,
                'created_at': card.created_at.isoformat() if card.created_at else None
            } for card in cards]
            
        except Exception as e:
            self.db.rollback()
            logger.error(f"❌ Error fetching credit cards: {e}")
            raise
    
    def get_credit_card(self, card_id: int, user_id: int = None) -> dict:
        """Get a single credit card"""
        try:
            query = self.db.query(CreditCard).filter(CreditCard.id == card_id)
            if user_id is not None:
                query = query.filter(CreditCard.user_id == user_id)
            card = query.first()
            
            if not card:
                return None
            
            return {
                'id': card.id,
                'card_name': card.card_name,
                'bank_name': card.bank_name,
                'closing_day': card.closing_day,
                'due_day': card.due_day,
                'currency': card.currency,
                'credit_limit': card.credit_limit,
                'is_active': card.is_active,
                'notes': card.notes,
                'created_at': card.created_at.isoformat() if card.created_at else None
            }
            
        except Exception as e:
            self.db.rollback()
            logger.error(f"❌ Error fetching credit card {card_id}: {e}")
            raise
    
    def update_credit_card(self, card_id: int, card_data: dict, user_id: int = None) -> dict:
        """Update an existing credit card"""
        try:
            query = self.db.query(CreditCard).filter(CreditCard.id == card_id)
            if user_id is not None:
                query = query.filter(CreditCard.user_id == user_id)
            card = query.first()
            
            if not card:
                return None
            
            if 'card_name' in card_data:
                card.card_name = card_data['card_name']
            if 'bank_name' in card_data:
                card.bank_name = card_data['bank_name']
            if 'closing_day' in card_data:
                card.closing_day = card_data['closing_day']
            if 'due_day' in card_data:
                card.due_day = card_data['due_day']
            if 'currency' in card_data:
                card.currency = card_data['currency']
            if 'credit_limit' in card_data:
                card.credit_limit = card_data['credit_limit']
            if 'is_active' in card_data:
                card.is_active = card_data['is_active']
            if 'notes' in card_data:
                card.notes = card_data['notes']
            
            card.updated_at = datetime.utcnow()
            self.db.commit()
            self.db.refresh(card)
            
            logger.info(f"✅ Credit card {card_id} updated successfully")
            return self.get_credit_card(card_id)
            
        except Exception as e:
            self.db.rollback()
            logger.error(f"❌ Error updating credit card {card_id}: {e}")
            raise
    
    def delete_credit_card(self, card_id: int, user_id: int = None) -> bool:
        """Soft-delete a credit card (mark as inactive)."""
        try:
            query = self.db.query(CreditCard).filter(
                CreditCard.id == card_id,
                CreditCard.is_active == True,
            )
            if user_id is not None:
                query = query.filter(CreditCard.user_id == user_id)
            card = query.first()
            
            if not card:
                return False
            
            card.is_active = False
            card.updated_at = datetime.utcnow()
            self.db.commit()
            logger.info(f"✅ Credit card {card_id} deactivated")
            return True
            
        except Exception as e:
            self.db.rollback()
            logger.error(f"❌ Error deleting credit card {card_id}: {e}")
            raise
    
    # ========================================================================
    # CREDIT CARD PURCHASES
    # ========================================================================
    
    def create_purchase(self, purchase_data: dict) -> int:
        """
        Create a credit card purchase
        
        Args:
            purchase_data: {
                'card_id': int,
                'transaction_id': int (optional),
                'purchase_date': str (YYYY-MM-DD),
                'amount': float,
                'description': str,
                'installments': int (default 1),
                'interest_rate': float (default 0.0),
                'plan_type': str (default 'MANUAL')
            }
        
        Returns:
            purchase_id: int
        """
        try:
            installments = int(purchase_data.get('installments', 1) or 1)
            if installments < 1:
                raise ValueError("installments must be at least 1")
            has_financing = installments > 1
            movement_type = str(purchase_data.get('movement_type', 'normal')).strip().lower()
            if movement_type not in ('normal', 'cash_advance'):
                raise ValueError("movement_type must be 'normal' or 'cash_advance'")

            cash_advance_fee = float(purchase_data.get('cash_advance_fee') or 0.0)
            if cash_advance_fee < 0:
                raise ValueError("cash_advance_fee cannot be negative")
            if cash_advance_fee > 100:
                raise ValueError("cash_advance_fee cannot exceed 100")

            card = self.db.query(CreditCard).filter(CreditCard.id == purchase_data['card_id']).first()
            if not card:
                raise ValueError(f"Credit card {purchase_data['card_id']} not found")

            if movement_type == 'cash_advance':
                if cash_advance_fee <= 0:
                    raise ValueError("cash_advance_fee is required for cash_advance purchases")
            if movement_type == 'cash_advance' and installments != 1:
                raise ValueError("cash_advance purchases must be single installment")

            if movement_type != 'cash_advance':
                cash_advance_fee = 0.0
            
            # Get amount (support both 'amount' and 'total_amount' for backward compatibility)
            amount = purchase_data.get('amount') or purchase_data.get('total_amount')
            if not amount:
                raise ValueError("Amount is required")
            
            # Get category (optional, defaults to 'General')
            category = purchase_data.get('category', 'General')
            
            # Handle currency conversion
            currency = purchase_data.get('currency', 'ARS')
            exchange_rate = None
            amount_in_pesos = None
            
            if currency == 'USD':
                # Fetch exchange rate from app_settings
                setting = self.db.query(AppSetting).filter(AppSetting.key == 'dollar_exchange_rate').first()
                exchange_rate = float(setting.value) if setting else 1.0
                amount_in_pesos = amount * exchange_rate

            purchase_date = datetime.strptime(purchase_data['purchase_date'], '%Y-%m-%d').date()
            billing_date = self._calculate_billing_date(purchase_date, currency)
            
            # 1. Create purchase record
            purchase = CreditCardPurchase(
                card_id=purchase_data['card_id'],
                transaction_id=purchase_data.get('transaction_id'),
                purchase_date=purchase_date,
                billing_date=billing_date,
                total_amount=amount,
                category=category,
                description=purchase_data.get('description', ''),
                installments=installments,
                has_financing=has_financing,
                movement_type=movement_type,
                cash_advance_fee=cash_advance_fee,
                currency=currency,
                exchange_rate=exchange_rate,
                amount_in_pesos=amount_in_pesos,
            )
            
            self.db.add(purchase)
            self.db.flush()

            # 2. Only create installment plan for multi-cuota purchases
            if installments > 1:
                self._create_installment_plan(purchase, purchase_data)

            # 3. Cash advance also creates derived debt in next card period.
            if movement_type == 'cash_advance':
                derived_debt = self._upsert_cash_advance_debt(purchase)
                purchase.derived_debt_id = derived_debt.id
                self._upsert_cash_advance_transaction(purchase, card)
            
            self.db.commit()
            self.db.refresh(purchase)
            
            logger.info(f"✅ Purchase created (ID: {purchase.id}, Amount: {purchase.total_amount}, Installments: {installments})")
            return purchase.id
            
        except Exception as e:
            self.db.rollback()
            logger.error(f"❌ Error creating purchase: {e}")
            raise

    def _calculate_period_due_date(self, card_id: int, year: int, month: int) -> date:
        """Return due date for a period using per-period config fallback."""
        from calendar import monthrange

        _, due_day = self._get_period_config(card_id, year, month)
        if month == 12:
            due_year, due_month = year + 1, 1
        else:
            due_year, due_month = year, month + 1
        due_last = monthrange(due_year, due_month)[1]
        return date(due_year, due_month, min(due_day, due_last))

    def _get_cash_advance_target_period(self, purchase: CreditCardPurchase) -> tuple[int, int]:
        """Derived debt is registered on the period immediately after the purchase period."""
        current_period = self.get_period_for_date(purchase.card_id, purchase.purchase_date.isoformat())
        if not current_period:
            # Fallback when period resolution fails.
            next_period = purchase.purchase_date + relativedelta(months=1)
            return next_period.year, next_period.month

        period_anchor = date(current_period['year'], current_period['month'], 1) + relativedelta(months=1)
        return period_anchor.year, period_anchor.month

    def _upsert_cash_advance_debt(self, purchase: CreditCardPurchase):
        """Create or update a BudgetItem debt linked to a cash advance purchase."""
        card = self.db.query(CreditCard).filter(CreditCard.id == purchase.card_id).first()
        if not card:
            raise ValueError("Credit card not found for cash advance debt generation")

        amount_ars = self._get_purchase_amount_in_ars(purchase)
        fee_amount = self._calculate_cash_advance_fee_amount(purchase)
        debt_total = round(float(amount_ars) + fee_amount, 2)
        target_year, target_month = self._get_cash_advance_target_period(purchase)
        due_date = self._calculate_period_due_date(purchase.card_id, target_year, target_month)

        detail = (
            f"Extraccion TC {card.card_name}: monto ${amount_ars:,.2f} + comision ${fee_amount:,.2f} "
            f"({float(purchase.cash_advance_fee or 0.0):.2f}%) "
            f"[cash_advance:{purchase.id}:{target_year}-{target_month:02d}]"
        )

        debt_item = None
        if purchase.derived_debt_id:
            debt_item = self.db.query(BudgetItem).filter(BudgetItem.id == purchase.derived_debt_id).first()

        if not debt_item:
            tag = f"%[cash_advance:{purchase.id}:{target_year}-{target_month:02d}]%"
            debt_item = self.db.query(BudgetItem).filter(BudgetItem.detalle.like(tag)).first()

        if debt_item:
            debt_item.fecha = purchase.purchase_date.isoformat()
            debt_item.monto_total = debt_total
            debt_item.monto_pagado = 0.0
            debt_item.monto_ejecutado = 0.0
            debt_item.status = DebtStatus.PENDIENTE
            debt_item.detalle = detail
            debt_item.fecha_vencimiento = due_date.isoformat()
            debt_item.updated_at = datetime.utcnow()
        else:
            debt_item = BudgetItem(
                user_id=card.user_id,
                fecha=purchase.purchase_date.isoformat(),
                tipo='Tarjeta',
                categoria='Extraccion Tarjeta',
                monto_total=debt_total,
                monto_pagado=0.0,
                detalle=detail,
                fecha_vencimiento=due_date.isoformat(),
                status=DebtStatus.PENDIENTE,
                tipo_presupuesto=BudgetType.OBLIGATION,
                tipo_flujo=FlowType.GASTO,
                monto_ejecutado=0.0,
            )
            self.db.add(debt_item)
            self.db.flush()

        return debt_item

    def _delete_cash_advance_debt(self, purchase: CreditCardPurchase):
        """Delete derived debt for a purchase if it exists."""
        if purchase.derived_debt_id:
            debt_item = self.db.query(BudgetItem).filter(BudgetItem.id == purchase.derived_debt_id).first()
            if debt_item:
                self.db.delete(debt_item)
        purchase.derived_debt_id = None
    
    def _create_installment_plan(self, purchase: CreditCardPurchase, purchase_data: dict):
        """Create installment plan and schedule for a purchase"""
        try:
            installments = purchase_data.get('installments', 1)
            interest_rate = float(purchase_data.get('interest_rate', 0.0) or 0.0)
            total_amount = purchase.total_amount
            
            # Calculate installment amounts
            if interest_rate > 0:
                # With interest: use amortization formula (interest_rate is annual %)
                monthly_rate = interest_rate / 12 / 100
                installment_amount = (total_amount * monthly_rate * (1 + monthly_rate)**installments) / ((1 + monthly_rate)**installments - 1)
                total_with_interest = installment_amount * installments
                total_interest = total_with_interest - total_amount
            else:
                # Without interest
                installment_amount = total_amount / installments
                total_with_interest = total_amount
                total_interest = 0
            
            # Create installment plan (budget item created per card period via register_card_period_budget)
            plan = InstallmentPlan(
                purchase_id=purchase.id,
                budget_item_id=None,
                total_amount=total_with_interest,
                number_of_installments=installments,
                interest_rate=interest_rate,
                start_date=purchase.purchase_date + relativedelta(months=1),
                plan_type=InstallmentPlanType.ZERO_INTEREST if interest_rate == 0 else InstallmentPlanType.REGULAR,
                notes=purchase_data.get('notes')
            )
            
            self.db.add(plan)
            self.db.flush()
            
            # Create installment schedule
            start_date = purchase.purchase_date + relativedelta(months=1)
            
            for i in range(1, installments + 1):
                due_date = start_date + relativedelta(months=i-1)
                
                # Last installment may have rounding difference
                if i == installments:
                    remaining = total_with_interest - (installment_amount * (installments - 1))
                    current_installment = remaining
                else:
                    current_installment = installment_amount
                
                principal = (total_amount / installments) if interest_rate == 0 else (current_installment - (total_interest / installments))
                interest = current_installment - principal if interest_rate > 0 else 0
                
                schedule_item = InstallmentScheduleItem(
                    plan_id=plan.id,
                    installment_number=i,
                    due_date=due_date,
                    principal_amount=round(principal, 2),
                    interest_amount=round(interest, 2),
                    total_installment_amount=round(current_installment, 2),
                    status=InstallmentStatus.PENDING
                )
                
                self.db.add(schedule_item)
            
            logger.info(f"✅ Installment plan created: {installments} installments of ${installment_amount:.2f}")
            
        except Exception as e:
            logger.error(f"❌ Error creating installment plan: {e}")
            raise
    
    def get_purchases(self, card_id: int = None, filters: dict = None) -> list:
        """Get credit card purchases"""
        try:
            query = self.db.query(CreditCardPurchase)
            
            if card_id:
                query = query.filter(CreditCardPurchase.card_id == card_id)
            
            if filters:
                if 'start_date' in filters:
                    query = query.filter(CreditCardPurchase.purchase_date >= filters['start_date'])
                if 'end_date' in filters:
                    query = query.filter(CreditCardPurchase.purchase_date <= filters['end_date'])
                if 'category' in filters:
                    query = query.filter(CreditCardPurchase.category == filters['category'])
            
            purchases = query.order_by(CreditCardPurchase.purchase_date.desc()).all()
            
            result = []
            for purchase in purchases:
                # Get card info
                card = self.db.query(CreditCard).filter(CreditCard.id == purchase.card_id).first()
                
                # Get installment plan (always query, plans are created for all purchases)
                plan = None
                plan_data = None
                plan = self.db.query(InstallmentPlan).filter(InstallmentPlan.purchase_id == purchase.id).first()
                if plan:
                        # Count paid installments
                        paid_count = self.db.query(InstallmentScheduleItem)\
                            .filter(
                                InstallmentScheduleItem.plan_id == plan.id,
                                InstallmentScheduleItem.status == InstallmentStatus.PAID
                            ).count()
                        
                        plan_data = {
                            'id': plan.id,
                            'total_amount': plan.total_amount,
                            'total_installments': plan.number_of_installments,
                            'interest_rate': plan.interest_rate,
                            'paid_installments': paid_count,
                            'plan_type': plan.plan_type.value if plan.plan_type else None
                        }
                
                is_cash_advance = (purchase.movement_type or 'normal') == 'cash_advance'
                fee_amount = self._calculate_cash_advance_fee_amount(purchase) if is_cash_advance else 0.0
                result.append({
                    'id': purchase.id,
                    'card_id': purchase.card_id,
                    'card_name': card.card_name if card else None,
                    'transaction_id': purchase.transaction_id,
                    'purchase_date': purchase.purchase_date.strftime('%Y-%m-%d'),
                    'total_amount': purchase.total_amount,
                    'total_with_fees': self._get_purchase_effective_total(purchase, plan),
                    'cash_advance_fee_amount': fee_amount,
                    'category': purchase.category,
                    'description': purchase.description,
                    'installments': purchase.installments,
                    'has_financing': purchase.has_financing,
                    'movement_type': purchase.movement_type or 'normal',
                    'cash_advance_fee': float(purchase.cash_advance_fee or 0.0),
                    'derived_debt_id': purchase.derived_debt_id,
                    'installment_plan': plan_data,
                    'plan_id': plan.id if plan else None,
                    'budget_item_id': plan.budget_item_id if plan else None,
                    'debt_id': plan.budget_item_id if plan else None,
                    'interest_rate': plan.interest_rate if plan else 0.0,
                    'currency': purchase.currency or 'ARS',
                    'exchange_rate': purchase.exchange_rate,
                    'amount_in_pesos': purchase.amount_in_pesos,
                    'created_at': purchase.created_at.isoformat() if purchase.created_at else None
                })
            
            return result
            
        except Exception as e:
            self.db.rollback()
            logger.error(f"❌ Error fetching purchases: {e}")
            raise
    
    def get_installment_schedule(self, plan_id: int) -> list:
        """Get installment schedule for a plan"""
        try:
            schedule = self.db.query(InstallmentScheduleItem)\
                .filter(InstallmentScheduleItem.plan_id == plan_id)\
                .order_by(InstallmentScheduleItem.installment_number).all()
            
            return [{
                'id': item.id,
                'installment_number': item.installment_number,
                'due_date': item.due_date.strftime('%Y-%m-%d'),
                'principal_amount': item.principal_amount,
                'interest_amount': item.interest_amount,
                'total_installment_amount': item.total_installment_amount,
                'status': item.status.value if hasattr(item.status, 'value') else item.status,
                'paid_date': item.paid_date.strftime('%Y-%m-%d') if item.paid_date else None,
                'payment_transaction_id': item.payment_transaction_id,
                'notes': item.notes
            } for item in schedule]
            
        except Exception as e:
            self.db.rollback()
            logger.error(f"❌ Error fetching installment schedule: {e}")
            raise
    
    def pay_installment(self, installment_id: int, payment_data: dict) -> bool:
        """
        Mark an installment as paid
        
        Args:
            installment_id: ID of installment schedule item
            payment_data: {
                'payment_date': str (YYYY-MM-DD),
                'transaction_id': int (optional),
                'notes': str (optional)
            }
        """
        try:
            installment = self.db.query(InstallmentScheduleItem)\
                .filter(InstallmentScheduleItem.id == installment_id).first()
            
            if not installment:
                return False
            
            # Update installment status
            installment.status = InstallmentStatus.PAID
            installment.paid_date = datetime.strptime(payment_data['payment_date'], '%Y-%m-%d').date()
            installment.payment_transaction_id = payment_data.get('transaction_id')
            if 'notes' in payment_data:
                installment.notes = payment_data['notes']
            installment.updated_at = datetime.utcnow()
            
            # Legacy: update per-purchase budget item if exists
            plan = self.db.query(InstallmentPlan).filter(InstallmentPlan.id == installment.plan_id).first()
            if plan and plan.budget_item_id:
                budget_item = self.db.query(BudgetItem).filter(BudgetItem.id == plan.budget_item_id).first()
                if budget_item:
                    budget_item.monto_pagado += installment.total_installment_amount
                    if budget_item.monto_pagado >= budget_item.monto_total:
                        budget_item.status = DebtStatus.PAGADA
                    elif budget_item.monto_pagado > 0:
                        budget_item.status = DebtStatus.PAGO_PARCIAL
                    budget_item.updated_at = datetime.utcnow()
            
            # Note: period budget monto_pagado is driven by Gastos module transactions,
            # not by individual installment pay/unpay actions.
            
            self.db.commit()
            self.db.expire_all()
            
            logger.info(f"✅ Installment {installment_id} marked as paid")
            return True
            
        except Exception as e:
            self.db.rollback()
            logger.error(f"❌ Error paying installment: {e}")
            raise
    
    def unpay_installment(self, installment_id: int) -> bool:
        """
        Revert a paid installment back to pending
        
        Args:
            installment_id: ID of installment schedule item to unpay
        """
        try:
            installment = self.db.query(InstallmentScheduleItem)\
                .filter(InstallmentScheduleItem.id == installment_id).first()
            
            if not installment:
                logger.warning(f"⚠️ Installment {installment_id} not found")
                return False
            
            if installment.status != InstallmentStatus.PAID:
                logger.warning(f"⚠️ Installment {installment_id} is not paid, cannot unpay")
                return False
            
            # Store the payment amount before reverting
            payment_amount = installment.total_installment_amount
            
            # Revert installment to pending
            installment.status = InstallmentStatus.PENDING
            installment.paid_date = None
            installment.payment_transaction_id = None
            installment.updated_at = datetime.utcnow()
            
            # Legacy: update per-purchase budget item if exists (subtract the payment)
            plan = self.db.query(InstallmentPlan).filter(InstallmentPlan.id == installment.plan_id).first()
            if plan and plan.budget_item_id:
                budget_item = self.db.query(BudgetItem).filter(BudgetItem.id == plan.budget_item_id).first()
                if budget_item:
                    budget_item.monto_pagado -= payment_amount
                    if budget_item.monto_pagado <= 0:
                        budget_item.status = DebtStatus.PENDIENTE
                        budget_item.monto_pagado = 0
                    elif budget_item.monto_pagado < budget_item.monto_total:
                        budget_item.status = DebtStatus.PAGO_PARCIAL
                    budget_item.updated_at = datetime.utcnow()
            
            # Note: period budget monto_pagado is driven by Gastos module transactions,
            # not by individual installment pay/unpay actions.
            
            self.db.commit()
            self.db.expire_all()
            
            logger.info(f"✅ Installment {installment_id} reverted to pending")
            return True
            
        except Exception as e:
            self.db.rollback()
            logger.error(f"❌ Error unpaying installment: {e}")
            raise
    
    def update_purchase(self, purchase_id: int, purchase_data: dict) -> bool:
        """
        Update an existing purchase with proper interest recalculation and currency handling
        """
        try:
            purchase = self.db.query(CreditCardPurchase)\
                .filter(CreditCardPurchase.id == purchase_id).first()
            
            if not purchase:
                logger.warning(f"⚠️ Purchase {purchase_id} not found")
                return False

            previous_movement_type = (purchase.movement_type or 'normal').lower()
            
            # Update basic fields
            if 'description' in purchase_data:
                purchase.description = purchase_data['description']
            if 'category' in purchase_data:
                purchase.category = purchase_data['category']
            if 'notes' in purchase_data:
                purchase.notes = purchase_data['notes']
            if 'purchase_date' in purchase_data:
                from datetime import date as date_type
                pd = purchase_data['purchase_date']
                purchase.purchase_date = date_type.fromisoformat(pd) if isinstance(pd, str) else pd
            if 'amount' in purchase_data:
                purchase.total_amount = float(purchase_data['amount'])
            if 'installments' in purchase_data:
                purchase.installments = int(purchase_data['installments'])

            if 'movement_type' in purchase_data:
                movement_type = str(purchase_data['movement_type']).strip().lower()
                if movement_type not in ('normal', 'cash_advance'):
                    raise ValueError("movement_type must be 'normal' or 'cash_advance'")
                purchase.movement_type = movement_type

            if 'cash_advance_fee' in purchase_data:
                purchase.cash_advance_fee = float(purchase_data.get('cash_advance_fee') or 0.0)

            if float(purchase.cash_advance_fee or 0.0) < 0:
                raise ValueError("cash_advance_fee cannot be negative")

            current_movement_type = (purchase.movement_type or 'normal').lower()
            if current_movement_type == 'cash_advance' and float(purchase.cash_advance_fee or 0.0) <= 0:
                raise ValueError("cash_advance_fee is required for cash_advance purchases")
            if current_movement_type == 'cash_advance' and int(purchase.installments or 1) > 1:
                raise ValueError("cash_advance purchases must be single installment")
            if current_movement_type != 'cash_advance':
                purchase.cash_advance_fee = 0.0
            
            # Handle currency (Bug 1 fix)
            if 'currency' in purchase_data:
                purchase.currency = purchase_data['currency']
                if purchase.currency == 'USD':
                    setting = self.db.query(AppSetting).filter(AppSetting.key == 'dollar_exchange_rate').first()
                    purchase.exchange_rate = float(setting.value) if setting else 1.0
                    purchase.amount_in_pesos = purchase.total_amount * purchase.exchange_rate
                else:
                    purchase.exchange_rate = None
                    purchase.amount_in_pesos = None

            if 'purchase_date' in purchase_data or 'currency' in purchase_data:
                purchase.billing_date = self._calculate_billing_date(
                    purchase.purchase_date,
                    purchase.currency,
                )
            
            purchase.updated_at = datetime.utcnow()
            
            # Rebuild installment plan if amount or installments changed
            plan = self.db.query(InstallmentPlan)\
                .filter(InstallmentPlan.purchase_id == purchase_id).first()
            
            if plan and ('amount' in purchase_data or 'installments' in purchase_data or 'purchase_date' in purchase_data or 'interest_rate' in purchase_data):
                new_amount = purchase.total_amount
                new_installments = purchase.installments
                
                # Update interest rate if provided
                if 'interest_rate' in purchase_data:
                    plan.interest_rate = float(purchase_data['interest_rate'] or 0.0)
                interest_rate = plan.interest_rate
                
                # Recalculate with interest (interest_rate is annual %)
                if interest_rate > 0:
                    monthly_rate = interest_rate / 12 / 100
                    installment_amount = (new_amount * monthly_rate * (1 + monthly_rate)**new_installments) / ((1 + monthly_rate)**new_installments - 1)
                    total_with_interest = installment_amount * new_installments
                    total_interest = total_with_interest - new_amount
                else:
                    installment_amount = new_amount / new_installments if new_installments > 0 else new_amount
                    total_with_interest = new_amount
                    total_interest = 0
                
                plan.total_amount = total_with_interest
                plan.number_of_installments = new_installments
                plan.updated_at = datetime.utcnow()
                
                # Collect affected periods before deleting
                old_items = self.db.query(InstallmentScheduleItem)\
                    .filter(InstallmentScheduleItem.plan_id == plan.id).all()
                affected_periods = set()
                for item in old_items:
                    affected_periods.add((item.due_date.year, item.due_date.month))
                    self.db.delete(item)
                self.db.flush()
                
                # Recreate schedule items
                start_date = purchase.purchase_date + relativedelta(months=1)
                for i in range(1, new_installments + 1):
                    due_date = start_date + relativedelta(months=i-1)
                    
                    if i == new_installments:
                        remaining = total_with_interest - (installment_amount * (new_installments - 1))
                        current_installment = remaining
                    else:
                        current_installment = installment_amount
                    
                    principal = (new_amount / new_installments) if interest_rate == 0 else (current_installment - (total_interest / new_installments))
                    interest = current_installment - principal if interest_rate > 0 else 0
                    
                    schedule_item = InstallmentScheduleItem(
                        plan_id=plan.id,
                        installment_number=i,
                        due_date=due_date,
                        principal_amount=round(principal, 2),
                        interest_amount=round(interest, 2),
                        total_installment_amount=round(current_installment, 2),
                        status=InstallmentStatus.PENDING
                    )
                    self.db.add(schedule_item)
                    affected_periods.add((due_date.year, due_date.month))
                
                # Legacy: update per-purchase budget item if exists
                if plan.budget_item_id:
                    budget_item = self.db.query(BudgetItem).filter(BudgetItem.id == plan.budget_item_id).first()
                    if budget_item:
                        budget_item.monto_total = total_with_interest
                        budget_item.monto_pagado = 0
                        budget_item.status = DebtStatus.PENDIENTE
                        budget_item.detalle = f"{purchase.description} ({new_installments} cuotas)"
                        budget_item.updated_at = datetime.utcnow()
                
                # Update affected period budget items
                for (year, month) in affected_periods:
                    self._update_period_budget(purchase.card_id, year, month)
            elif plan and 'description' in purchase_data:
                # Update budget detalle even if only description changed
                if plan.budget_item_id:
                    budget_item = self.db.query(BudgetItem).filter(BudgetItem.id == plan.budget_item_id).first()
                    if budget_item:
                        budget_item.detalle = f"{purchase.description} ({purchase.installments} cuotas)"
                        budget_item.updated_at = datetime.utcnow()

            if current_movement_type == 'cash_advance':
                derived_debt = self._upsert_cash_advance_debt(purchase)
                purchase.derived_debt_id = derived_debt.id
                card = self.db.query(CreditCard).filter(CreditCard.id == purchase.card_id).first()
                if not card:
                    raise ValueError(f"Credit card {purchase.card_id} not found")
                self._upsert_cash_advance_transaction(purchase, card)
            elif previous_movement_type == 'cash_advance':
                self._delete_cash_advance_debt(purchase)
                self._delete_cash_advance_transaction(purchase)
            
            self.db.commit()
            logger.info(f"✅ Purchase {purchase_id} updated successfully")
            return True
            
        except Exception as e:
            self.db.rollback()
            logger.error(f"❌ Error updating purchase: {e}")
            raise
    
    def delete_purchase(self, purchase_id: int) -> bool:
        """
        Delete a credit card purchase and its associated plan, schedule, and budget item.
        """
        try:
            purchase = self.db.query(CreditCardPurchase)\
                .filter(CreditCardPurchase.id == purchase_id).first()
            
            if not purchase:
                logger.warning(f"⚠️ Purchase {purchase_id} not found")
                return False
            
            card_id = purchase.card_id

            if (purchase.movement_type or 'normal') == 'cash_advance':
                self._delete_cash_advance_debt(purchase)
            
            # Delete associated installment plan, schedule, and budget item
            plan = self.db.query(InstallmentPlan)\
                .filter(InstallmentPlan.purchase_id == purchase_id).first()
            
            affected_periods = set()
            if plan:
                # Collect affected periods before deleting
                schedule_items = self.db.query(InstallmentScheduleItem)\
                    .filter(InstallmentScheduleItem.plan_id == plan.id).all()
                for item in schedule_items:
                    affected_periods.add((item.due_date.year, item.due_date.month))
                
                # Delete schedule items
                self.db.query(InstallmentScheduleItem)\
                    .filter(InstallmentScheduleItem.plan_id == plan.id).delete()
                
                # Delete legacy budget item if linked
                if plan.budget_item_id:
                    self.db.query(BudgetItem)\
                        .filter(BudgetItem.id == plan.budget_item_id).delete()
                
                # Delete plan
                self.db.delete(plan)

            # Delete derived cash advance debt if linked
            if purchase.derived_debt_id:
                self.db.query(BudgetItem).filter(BudgetItem.id == purchase.derived_debt_id).delete()
                purchase.derived_debt_id = None

            # Delete mirrored cash-advance transaction if linked
            self._delete_cash_advance_transaction(purchase)
            
            # Delete purchase
            self.db.delete(purchase)
            self.db.commit()
            
            # Update affected period budget items (after commit so deleted items are gone)
            for (year, month) in affected_periods:
                self._update_period_budget(card_id, year, month)
            
            logger.info(f"✅ Purchase {purchase_id} deleted successfully")
            return True
            
        except Exception as e:
            self.db.rollback()
            logger.error(f"❌ Error deleting purchase: {e}")
            raise
    
    # ========================================================================
    # PERIOD BUDGET MANAGEMENT
    # ========================================================================
    
    def _get_period_tag(self, card_id: int, year: int, month: int) -> str:
        """Get the machine-readable tag for a card period budget item"""
        return f"[tc:{card_id}:{year}-{month:02d}]"
    
    def _find_period_budget_item(self, card_id: int, year: int, month: int):
        """Find the BudgetItem for a specific card period"""
        tag = self._get_period_tag(card_id, year, month)
        return self.db.query(BudgetItem).filter(
            BudgetItem.detalle.like(f"%{tag}")
        ).first()
    
    def _get_gastos_paid_for_period(self, card_id: int, year: int, month: int) -> float:
        """Sum expense transactions linked to this card's period BudgetItem.
        Only counts transactions whose budget_item_id matches the period budget item for this specific card."""
        from sqlalchemy import func
        period_budget = self._find_period_budget_item(card_id, year, month)
        if not period_budget:
            return 0.0
        total = self.db.query(func.coalesce(func.sum(Transaction.amount), 0)).filter(
            Transaction.budget_item_id == period_budget.id,
            Transaction.type == TransactionType.GASTO,
        ).scalar()
        return float(total or 0)

    def _get_gastos_paid_for_card(self, card_id: int) -> float:
        """Sum all expense transactions linked to budget items belonging to this card."""
        from sqlalchemy import func
        tag_prefix = f"%[tc:{card_id}:%"
        total = self.db.query(func.coalesce(func.sum(Transaction.amount), 0)).join(
            BudgetItem,
            Transaction.budget_item_id == BudgetItem.id
        ).filter(
            Transaction.type == TransactionType.GASTO,
            BudgetItem.detalle.like(tag_prefix)
        ).scalar()
        return float(total or 0)

    def _get_payment_transactions_for_period(self, card_id: int, year: int, month: int) -> list:
        """Return individual payment transactions linked to this card's period BudgetItem."""
        period_budget = self._find_period_budget_item(card_id, year, month)
        if not period_budget:
            return []
        transactions = self.db.query(Transaction).filter(
            Transaction.budget_item_id == period_budget.id,
            Transaction.type == TransactionType.GASTO,
        ).order_by(Transaction.date.desc()).all()
        return [
            {
                'id': t.id,
                'date': t.date,
                'amount': t.amount,
                'payment_method': t.payment_method,
                'detail': t.detail,
            }
            for t in transactions
        ]

    def _get_period_config(self, card_id: int, year: int, month: int):
        """Get period-specific closing_day/due_day, falling back to card defaults."""
        override = self.db.query(CreditCardPeriodConfig).filter(
            CreditCardPeriodConfig.card_id == card_id,
            CreditCardPeriodConfig.year == year,
            CreditCardPeriodConfig.month == month,
        ).first()
        if override:
            return override.closing_day, override.due_day
        card = self.db.query(CreditCard).filter(CreditCard.id == card_id).first()
        return card.closing_day, card.due_day

    def save_period_config(self, card_id: int, year: int, month: int, closing_day: int, due_day: int):
        """Create or update period-specific closing/due day config."""
        config = self.db.query(CreditCardPeriodConfig).filter(
            CreditCardPeriodConfig.card_id == card_id,
            CreditCardPeriodConfig.year == year,
            CreditCardPeriodConfig.month == month,
        ).first()
        if config:
            config.closing_day = closing_day
            config.due_day = due_day
            config.updated_at = datetime.utcnow()
        else:
            config = CreditCardPeriodConfig(
                card_id=card_id, year=year, month=month,
                closing_day=closing_day, due_day=due_day,
            )
            self.db.add(config)
        self.db.commit()
        self.db.refresh(config)
        return {'closing_day': config.closing_day, 'due_day': config.due_day}

    def get_period_for_date(self, card_id: int, purchase_date_str: str) -> dict:
        """Given a purchase date, determine which billing period it belongs to.
        Algorithm: if day >= closing_day → period is next month, else current month."""
        card = self.db.query(CreditCard).filter(CreditCard.id == card_id).first()
        if not card:
            return None
        purchase_date = date.fromisoformat(purchase_date_str)
        closing_day = card.closing_day
        
        if purchase_date.day >= closing_day:
            # Belongs to next month's period
            next_m = purchase_date + relativedelta(months=1)
            period_year, period_month = next_m.year, next_m.month
        else:
            period_year, period_month = purchase_date.year, purchase_date.month
        
        # Get period-specific config (may have override)
        p_closing, p_due = self._get_period_config(card_id, period_year, period_month)
        prev_closing = self._get_prev_period_closing_day(card_id, period_year, period_month)
        period_start, period_end = self._get_period_date_range(p_closing, period_year, period_month, prev_closing)
        
        MONTH_NAMES = ['', 'Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio',
                       'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre']
        start_month_name = MONTH_NAMES[period_start.month]
        end_month_name = MONTH_NAMES[period_end.month]
        period_label = f"{start_month_name} \u2013 {end_month_name} {period_end.year}"
        
        return {
            'year': period_year,
            'month': period_month,
            'period_start': period_start.isoformat(),
            'period_end': period_end.isoformat(),
            'period_label': period_label,
        }

    def _get_period_date_range(self, closing_day: int, year: int, month: int, prev_period_closing_day: int = None):
        """Calculate the date range for a billing period based on closing_day.
        Period M = day after closing of previous period to closing day of month M.
        Uses prev_period_closing_day (the previous period's closing day) to compute
        the start date, avoiding overlap when closing days differ between periods.
        Example: closing_day=20, April 2026 → March 21 to April 20.
        Example: closing_day=29, February 2026 → Jan 30 to Feb 28."""
        from calendar import monthrange
        from datetime import date

        # End of period: closing_day of the target month (clamped to last day)
        last_day = monthrange(year, month)[1]
        end_day = min(closing_day, last_day)
        end_date = date(year, month, end_day)

        # Start of period: day after closing_day of previous month
        # Use previous period's closing_day if provided, otherwise fall back to current
        prev = date(year, month, 1) - timedelta(days=1)  # last day of prev month
        prev_last_day = monthrange(prev.year, prev.month)[1]
        effective_prev_closing = prev_period_closing_day if prev_period_closing_day is not None else closing_day
        prev_closing_clamped = min(effective_prev_closing, prev_last_day)
        start_date = date(prev.year, prev.month, prev_closing_clamped) + timedelta(days=1)

        return start_date, end_date

    def _get_prev_period_closing_day(self, card_id: int, year: int, month: int) -> int:
        """Get the closing_day of the previous period (for computing start of current period)."""
        prev_date = date(year, month, 1) - timedelta(days=1)
        prev_closing, _ = self._get_period_config(card_id, prev_date.year, prev_date.month)
        return prev_closing

    def _get_standalone_purchases_for_period(self, card_id: int, year: int, month: int):
        """Get 1-cuota purchases for a given billing period.
        Uses period-specific closing_day (with card default fallback) for the date range."""
        from sqlalchemy import func

        closing_day, _ = self._get_period_config(card_id, year, month)
        prev_closing = self._get_prev_period_closing_day(card_id, year, month)

        start_date, end_date = self._get_period_date_range(closing_day, year, month, prev_closing)

        return self.db.query(CreditCardPurchase).filter(
            CreditCardPurchase.card_id == card_id,
            CreditCardPurchase.installments == 1,
            func.coalesce(CreditCardPurchase.billing_date, CreditCardPurchase.purchase_date) >= start_date,
            func.coalesce(CreditCardPurchase.billing_date, CreditCardPurchase.purchase_date) <= end_date,
        ).all()

    def _get_purchase_amount_in_ars(self, purchase: CreditCardPurchase) -> float:
        """Get the amount of a purchase in ARS."""
        if purchase.currency == 'USD' and purchase.amount_in_pesos:
            return float(purchase.amount_in_pesos)
        return float(purchase.total_amount)
    
    def _calculate_period_total(self, card_id: int, year: int, month: int) -> float:
        """Calculate total due for a card period: multi-cuota installments + standalone 1-cuota purchases."""
        from sqlalchemy import extract
        # 1. Only multi-cuota installments (>1 cuota)
        installments = self.db.query(InstallmentScheduleItem)\
            .join(InstallmentPlan)\
            .join(CreditCardPurchase)\
            .filter(
                CreditCardPurchase.card_id == card_id,
                InstallmentPlan.number_of_installments > 1,
                extract('year', InstallmentScheduleItem.due_date) == year,
                extract('month', InstallmentScheduleItem.due_date) == month
            ).all()
        total = sum(i.total_installment_amount for i in installments)
        
        # 2. Standalone 1-cuota purchases without plan
        standalone = self._get_standalone_purchases_for_period(card_id, year, month)
        total += sum(self._get_purchase_amount_in_ars(p) for p in standalone)
        
        return total

    def _update_period_budget(self, card_id: int, year: int, month: int):
        """Recalculate and update the period budget item if it exists.
        monto_total comes from installments + standalone purchases; monto_pagado from Gastos module."""
        budget_item = self._find_period_budget_item(card_id, year, month)
        if not budget_item:
            return
        
        total_due = self._calculate_period_total(card_id, year, month)
        total_paid = self._get_gastos_paid_for_period(card_id, year, month)
        
        budget_item.monto_total = round(total_due, 2)
        budget_item.monto_pagado = round(total_paid, 2)
        
        if total_due == 0 or total_paid >= total_due:
            budget_item.status = DebtStatus.PAGADA
        elif total_paid > 0:
            budget_item.status = DebtStatus.PAGO_PARCIAL
        else:
            budget_item.status = DebtStatus.PENDIENTE
        
        budget_item.updated_at = datetime.utcnow()
        self.db.commit()
    
    def get_card_period_installments(self, card_id: int, year: int, month: int) -> dict:
        """Get all installments + standalone purchases for a card in a specific month with summary"""
        try:
            from sqlalchemy import extract
            
            card = self.db.query(CreditCard).filter(CreditCard.id == card_id).first()
            if not card:
                return None
            
            # 1. Only multi-cuota installments (>1 cuota)
            installments = self.db.query(InstallmentScheduleItem)\
                .join(InstallmentPlan)\
                .join(CreditCardPurchase)\
                .filter(
                    CreditCardPurchase.card_id == card_id,
                    InstallmentPlan.number_of_installments > 1,
                    extract('year', InstallmentScheduleItem.due_date) == year,
                    extract('month', InstallmentScheduleItem.due_date) == month
                )\
                .order_by(InstallmentScheduleItem.due_date).all()
            
            items = []
            total_due = 0
            for inst in installments:
                plan = self.db.query(InstallmentPlan).filter(InstallmentPlan.id == inst.plan_id).first()
                purchase = self.db.query(CreditCardPurchase).filter(CreditCardPurchase.id == plan.purchase_id).first() if plan else None
                
                items.append({
                    'id': inst.id,
                    'purchase_id': purchase.id if purchase else None,
                    'installment_number': inst.installment_number,
                    'total_installments': plan.number_of_installments if plan else 1,
                    'due_date': inst.due_date.strftime('%Y-%m-%d'),
                    'amount': inst.total_installment_amount,
                    'status': inst.status.value if hasattr(inst.status, 'value') else inst.status,
                    'purchase_description': purchase.description if purchase else '',
                    'purchase_category': purchase.category if purchase else '',
                    'purchase_currency': purchase.currency if purchase else 'ARS',
                    'item_type': 'installment',
                })
                total_due += inst.total_installment_amount
            
            # 2. Standalone 1-cuota purchases (same month as purchase date)
            standalone = self._get_standalone_purchases_for_period(card_id, year, month)
            cash_advance_summary = self._summarize_period_cash_advances(standalone)
            for p in standalone:
                amount_ars = self._get_purchase_amount_in_ars(p)
                is_cash_advance = (p.movement_type or 'normal') == 'cash_advance'
                fee_amount = self._calculate_cash_advance_fee_amount(p) if is_cash_advance else 0.0
                items.append({
                    'id': p.id,
                    'purchase_id': p.id,
                    'installment_number': 1,
                    'total_installments': 1,
                    'due_date': f"{year}-{month:02d}-01",
                    'amount': amount_ars,
                    'status': 'PENDING',
                    'purchase_description': p.description or '',
                    'purchase_category': p.category or '',
                    'purchase_currency': p.currency or 'ARS',
                    'movement_type': p.movement_type or 'normal',
                    'cash_advance_fee': float(p.cash_advance_fee or 0.0),
                    'cash_advance_fee_amount': fee_amount,
                    'derived_debt_id': p.derived_debt_id,
                    'item_type': 'single',
                })
                total_due += amount_ars
            
            # Paid amount from Gastos module
            total_paid = self._get_gastos_paid_for_period(card_id, year, month)
            
            # Individual payment transactions
            payment_transactions = self._get_payment_transactions_for_period(card_id, year, month)
            
            # Check if period is registered in budget
            period_budget = self._find_period_budget_item(card_id, year, month)
            
            # Minimum payment: use stored custom value from budget item if available, else 5% default
            default_minimum = max(round(total_due * 0.05, 2), min(total_due, 1000.0))
            minimum_payment = default_minimum
            if period_budget and period_budget.detalle:
                import re
                # Match "Pago Mínimo: $150,000" or "(Mín: $150,000)" formats
                min_match = re.search(r'Pago Mínimo:\s*\$([0-9,.]+)', period_budget.detalle) or \
                            re.search(r'Mín:\s*\$([0-9,.]+)', period_budget.detalle)
                if min_match:
                    try:
                        minimum_payment = float(min_match.group(1).replace(',', ''))
                    except ValueError:
                        pass
            
            # Period-specific config (override or card defaults)
            period_closing_day, period_due_day = self._get_period_config(card_id, year, month)
            
            # Calculate full period dates for display
            prev_closing = self._get_prev_period_closing_day(card_id, year, month)
            period_start, period_end = self._get_period_date_range(period_closing_day, year, month, prev_closing)
            from calendar import monthrange as mr
            closing_last = mr(year, month)[1]
            closing_d = min(period_closing_day, closing_last)
            closing_date = date(year, month, closing_d)
            # Due date: due_day of month+1
            if month == 12:
                due_y, due_m = year + 1, 1
            else:
                due_y, due_m = year, month + 1
            due_last = mr(due_y, due_m)[1]
            due_d = min(period_due_day, due_last)
            due_date_val = date(due_y, due_m, due_d)
            
            return {
                'card_id': card_id,
                'card_name': card.card_name,
                'bank_name': card.bank_name,
                'year': year,
                'month': month,
                'closing_day': period_closing_day,
                'due_day': period_due_day,
                'period_start': period_start.isoformat(),
                'period_end': period_end.isoformat(),
                'closing_date': closing_date.isoformat(),
                'due_date': due_date_val.isoformat(),
                'total_due': round(total_due, 2),
                'minimum_payment': minimum_payment,
                'total_paid': round(total_paid, 2),
                'installment_count': len(items),
                'installments': items,
                'payment_transactions': payment_transactions,
                'budget_registered': period_budget is not None,
                'budget_item_id': period_budget.id if period_budget else None,
                'cash_advance_count': cash_advance_summary['count'],
                'cash_advance_extraction_total': cash_advance_summary['extraction_total'],
                'cash_advance_commission_total': cash_advance_summary['commission_total'],
            }
        except Exception as e:
            self.db.rollback()
            logger.error(f"❌ Error fetching period installments: {e}")
            raise
    
    def register_card_period_budget(self, card_id: int, year: int, month: int, payment_type: str = 'total', custom_minimum: float = 0) -> dict:
        """Create or update a BudgetItem for all items due in a card period.
        payment_type: 'total' for full statement, 'minimum' for minimum payment.
        custom_minimum: user-specified minimum payment amount."""
        try:
            from sqlalchemy import extract
            
            card = self.db.query(CreditCard).filter(CreditCard.id == card_id).first()
            if not card:
                return None
            
            # Calculate total from installments + standalone purchases
            total_due = self._calculate_period_total(card_id, year, month)
            
            if total_due == 0:
                return {'error': 'No hay gastos en este período'}
            
            # Determine minimum payment amount
            minimum_amount = custom_minimum if custom_minimum > 0 else max(round(total_due * 0.05, 2), min(total_due, 1000.0))
            
            # Budget amount depends on payment type
            if payment_type == 'minimum':
                budget_amount = minimum_amount
            else:
                budget_amount = total_due
            
            total_paid = self._get_gastos_paid_for_period(card_id, year, month)
            
            # Determine status
            if total_paid >= budget_amount:
                status = DebtStatus.PAGADA
            elif total_paid > 0:
                status = DebtStatus.PAGO_PARCIAL
            else:
                status = DebtStatus.PENDIENTE
            
            # Calculate due date using card's due_day
            from calendar import monthrange
            due_day = min(card.due_day or 1, monthrange(year, month)[1])
            fecha_vencimiento = f"{year}-{month:02d}-{due_day:02d}"
            
            tag = self._get_period_tag(card_id, year, month)
            
            MONTH_NAMES = ['', 'ene', 'feb', 'mar', 'abr', 'may', 'jun',
                           'jul', 'ago', 'sep', 'oct', 'nov', 'dic']
            period_display = f"{MONTH_NAMES[month]} {year}"
            # Format minimum payment for description
            min_fmt = f"${minimum_amount:,.0f}"
            if payment_type == 'minimum':
                pago_tipo = f' - Pago Mínimo: {min_fmt}'
            else:
                pago_tipo = f' (Mín: {min_fmt})'
            detalle = f"Pago Tarjeta {card.card_name} - {card.bank_name} ({period_display}){pago_tipo} {tag}"
            
            # Check if already exists
            budget_item = self._find_period_budget_item(card_id, year, month)
            
            if budget_item:
                budget_item.monto_total = round(budget_amount, 2)
                budget_item.monto_pagado = round(total_paid, 2)
                budget_item.status = status
                budget_item.detalle = detalle
                budget_item.fecha_vencimiento = fecha_vencimiento
                budget_item.updated_at = datetime.utcnow()
                action = 'updated'
            else:
                budget_item = BudgetItem(
                    fecha=f"{year}-{month:02d}-01",
                    tipo="Tarjeta de Crédito",
                    categoria="Tarjeta de Crédito",
                    monto_total=round(budget_amount, 2),
                    monto_pagado=round(total_paid, 2),
                    detalle=detalle,
                    fecha_vencimiento=fecha_vencimiento,
                    status=status,
                    tipo_presupuesto=BudgetType.OBLIGATION,
                    tipo_flujo=FlowType.GASTO,
                    monto_ejecutado=0.0
                )
                self.db.add(budget_item)
                action = 'created'
            
            self.db.commit()
            self.db.refresh(budget_item)
            
            logger.info(f"✅ Period budget item {action} for card {card_id}, period {year}-{month:02d}")
            return {
                'action': action,
                'budget_item_id': budget_item.id,
                'monto_total': budget_item.monto_total,
                'monto_pagado': budget_item.monto_pagado,
                'status': budget_item.status.value if hasattr(budget_item.status, 'value') else budget_item.status,
                'detalle': budget_item.detalle,
            }
        except Exception as e:
            self.db.rollback()
            logger.error(f"❌ Error registering period budget: {e}")
            raise
    
    # ========================================================================
    # SUMMARY & ANALYTICS
    # ========================================================================
    
    def get_card_summary(self, card_id: int) -> dict:
        """Get summary for a credit card"""
        try:
            card = self.db.query(CreditCard).filter(CreditCard.id == card_id).first()
            if not card:
                return None
            
            # Total purchases
            purchases = self.db.query(CreditCardPurchase)\
                .filter(CreditCardPurchase.card_id == card_id).all()
            total_purchases = sum(p.total_amount for p in purchases)
            
            # Pending installments from multi-cuota plans only
            from sqlalchemy import extract
            from datetime import datetime
            pending_installments = self.db.query(InstallmentScheduleItem)\
                .join(InstallmentPlan)\
                .join(CreditCardPurchase)\
                .filter(
                    CreditCardPurchase.card_id == card_id,
                    InstallmentPlan.number_of_installments > 1,
                    InstallmentScheduleItem.status == InstallmentStatus.PENDING
                ).all()
            
            total_pending = sum(i.total_installment_amount for i in pending_installments)
            
            # Add only CURRENT MONTH standalone 1-cuota purchases (bug 9: don't propagate to other months)
            now = datetime.now()
            current_year = now.year
            current_month = now.month
            current_standalone = self._get_standalone_purchases_for_period(card_id, current_year, current_month)
            standalone_total = sum(self._get_purchase_amount_in_ars(p) for p in current_standalone)
            
            total_pending += standalone_total
            total_items = len(pending_installments) + len(current_standalone)

            # Subtract all payments linked to this card across periods.
            # Payments are recorded in Gastos and linked via budget_item_id.
            total_paid_card = self._get_gastos_paid_for_card(card_id)
            total_pending = max(0.0, total_pending - total_paid_card)

            # Next due amount = Total(prev month) - Paid(prev month) + Total(current month)
            # Formula: unpaid balance from previous period + current period charges
            prev_date = now - relativedelta(months=1)
            prev_total = self._calculate_period_total(card_id, prev_date.year, prev_date.month)
            prev_paid = self._get_gastos_paid_for_period(card_id, prev_date.year, prev_date.month)
            current_period_total = self._calculate_period_total(card_id, current_year, current_month)
            
            unpaid_prev = max(0, prev_total - prev_paid)
            next_due_amount = round(unpaid_prev + current_period_total, 2)
            if next_due_amount <= 0:
                next_due_amount = None
            
            return {
                'id': card.id,
                'card_id': card.id,
                'card_name': card.card_name,
                'bank_name': card.bank_name,
                'credit_limit': card.credit_limit,
                'currency': card.currency,
                'total_purchases': total_purchases,
                'current_debt': total_pending,
                'pending_installments': total_items,
                'available_credit': (card.credit_limit - total_pending) if card.credit_limit else None,
                'next_due_amount': next_due_amount
            }
            
        except Exception as e:
            self.db.rollback()
            logger.error(f"❌ Error generating card summary: {e}")
            raise
    
    def get_monthly_purchases_total(self, year: int, month: int, user_id: int = None) -> dict:
        """Get total credit card purchases for a specific month across all cards.
        Only counts purchases NOT already linked to a transaction (to avoid double-counting)."""
        try:
            assert_credit_card_purchase_schema(self.db.bind)
            from sqlalchemy import extract, func

            base_filters = [
                extract('year', CreditCardPurchase.purchase_date) == year,
                extract('month', CreditCardPurchase.purchase_date) == month,
                CreditCardPurchase.transaction_id.is_(None)
            ]
            if user_id is not None:
                base_filters.append(CreditCardPurchase.card_id.in_(
                    self.db.query(CreditCard.id).filter(CreditCard.user_id == user_id)
                ))

            # Only ARS purchases (ignore USD for now, they have separate exchange_rate logic)
            total_ars = self.db.query(
                func.coalesce(func.sum(CreditCardPurchase.total_amount), 0)
            ).filter(
                *base_filters,
                CreditCardPurchase.currency == 'ARS'
            ).scalar()

            # USD purchases converted to ARS via amount_in_pesos
            total_usd_in_pesos = self.db.query(
                func.coalesce(func.sum(CreditCardPurchase.amount_in_pesos), 0)
            ).filter(
                *base_filters,
                CreditCardPurchase.currency != 'ARS'
            ).scalar()

            purchase_count = self.db.query(func.count(CreditCardPurchase.id)).filter(
                *base_filters
            ).scalar()

            total = float(total_ars) + float(total_usd_in_pesos)

            return {
                'year': year,
                'month': month,
                'total_ars': float(total_ars),
                'total_usd_in_pesos': float(total_usd_in_pesos),
                'total': round(total, 2),
                'purchase_count': purchase_count
            }
        except Exception as e:
            self.db.rollback()
            logger.error(f"❌ Error fetching monthly purchases total: {e}")
            raise

    def get_purchases_summary(self, card_id: int) -> dict:
        """Get aggregated summary of purchases by description for pie chart"""
        try:
            purchases = self.db.query(CreditCardPurchase)\
                .filter(CreditCardPurchase.card_id == card_id).all()
            
            by_description = {}
            total_amount = 0
            total_with_interest = 0
            
            for p in purchases:
                desc = p.description or 'Sin descripción'
                plan = self.db.query(InstallmentPlan)\
                    .filter(InstallmentPlan.purchase_id == p.id).first()
                plan_total = self._get_purchase_effective_total(p, plan)
                base_amount = self._get_purchase_amount_in_ars(p)
                
                if desc not in by_description:
                    by_description[desc] = {
                        'description': desc,
                        'count': 0,
                        'total_original': 0,
                        'total_with_interest': 0
                    }
                by_description[desc]['count'] += 1
                by_description[desc]['total_original'] += base_amount
                by_description[desc]['total_with_interest'] += plan_total
                total_amount += base_amount
                total_with_interest += plan_total
            
            return {
                'total_original': total_amount,
                'total_with_interest': total_with_interest,
                'by_description': list(by_description.values()),
                'purchase_count': len(purchases)
            }
        except Exception as e:
            self.db.rollback()
            logger.error(f"❌ Error generating purchases summary: {e}")
            raise
