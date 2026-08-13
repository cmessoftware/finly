import os
import sys
from dotenv import load_dotenv

# Load .env from the project root and backend directory so local env vars are available
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(BASE_DIR)
load_dotenv(os.path.join(ROOT_DIR, ".env"))
load_dotenv(os.path.join(BASE_DIR, ".env"))

# Fix Windows console encoding for Unicode characters
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8')

from fastapi import FastAPI, BackgroundTasks, Depends, HTTPException, status, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from typing import Optional, List, Literal
from datetime import datetime, timedelta, date
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from database.database import SessionLocal, get_db, Transaction as DBTransaction, Debt as DBDebt, AppSetting, User as DBUser, MonthlyBalance as DBMonthlyBalance
from security.auth_dependencies import get_current_user, require_role, require_permission

# Configuration
app = FastAPI(title="Finly API", version="1.0.0")

# Initialize PostgreSQL Database
try:
    from services.database_service import database_service
    if database_service.is_connected():
        print("✅ PostgreSQL database connected successfully")
    else:
        print("⚠️ PostgreSQL database not connected")
        database_service = None
except Exception as e:
    print(f"⚠️ PostgreSQL database not available: {e}")
    database_service = None

# Initialize Debt Service
try:
    from services.debt_service import debt_service
    if database_service is not None:
        print("✅ Debt service initialized successfully")
    else:
        print("⚠️ Debt service not available (requires database)")
        debt_service = None
except Exception as e:
    print(f"⚠️ Debt service not available: {e}")
    debt_service = None

# Initialize Credit Card Service
try:
    from services.credit_card_service import CreditCardService
    credit_card_service = CreditCardService()
    print("✅ Credit Card service initialized successfully")
except Exception as e:
    print(f"⚠️ Credit Card service not available: {e}")
    credit_card_service = None

# Initialize Google Sheets (optional backup)
try:
    from services.google_sheets import sheets_service
    if sheets_service.sheet_id:
        if sheets_service.connect():
            sheets_service.initialize_sheet()
            print("✅ Google Sheets connected successfully (backup)")
        else:
            print("⚠️ Google Sheets not connected")
    else:
        print("⚠️ GOOGLE_SHEET_ID not configured")
        sheets_service = None
except Exception as e:
    print(f"⚠️ Google Sheets not available: {e}")
    sheets_service = None

# CORS Configuration
# Allow both local development and production frontend
allowed_origins = [
    "http://localhost:5173",  # Local development (Vite)
    "http://localhost:5174",  # Alternative local port
    "http://localhost:3000",  # Docker frontend
    "https://smartfi-frontend.onrender.com",  # Production (Render)
]

# Add production frontend URL if configured (may differ from default above)
frontend_url = os.getenv("FRONTEND_URL")
if frontend_url and frontend_url not in allowed_origins:
    allowed_origins.append(frontend_url)
    print(f"✅ CORS configured for production: {frontend_url}")
elif frontend_url:
    print(f"✅ CORS configured for production: {frontend_url}")

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    # Also allow any localhost origin (different ports) via regex to avoid CORS issues in dev
    allow_origin_regex=r"^http://(localhost|127\.0\.0\.1)(:\\d+)?$",
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Return JSON 500 so CORS middleware can attach headers (avoids opaque browser CORS errors)."""
    if isinstance(exc, HTTPException):
        raise exc
    print(f"❌ Unhandled error on {request.method} {request.url.path}: {exc}")
    return JSONResponse(status_code=500, content={"detail": str(exc)})

# Health check endpoint
@app.get("/api/health")
async def health_check():
    return {
        "status": "ok",
        "database_connected": database_service is not None and database_service.is_connected(),
        "db_revision": _get_alembic_revision(),
        "db_schema": _get_schema_status(),
        "google_sheets_connected": sheets_service is not None and sheets_service.sheet is not None,
        "sheet_id": sheets_service.sheet_id if sheets_service else None
    }

# Pydantic Models (business domain only — auth models live in security/)

class Debt(BaseModel):
    id: Optional[int] = None
    fecha: str
    tipo: str
    categoria: Optional[str] = None
    monto_total: float
    monto_pagado: Optional[float] = 0.0
    detalle: Optional[str] = None
    fecha_vencimiento: str
    status: Optional[str] = "Pendiente"
    # Nuevos campos - Fase A Refactor
    tipo_presupuesto: Optional[str] = "OBLIGATION"
    tipo_flujo: Optional[str] = "Gasto"
    expense_type: Optional[str] = "VARIABLE"
    monto_ejecutado: Optional[float] = 0.0
    estimated_payment: Optional[float] = None

class Transaction(BaseModel):
    id: Optional[int] = None
    timestamp: Optional[str] = None
    date: str
    type: str
    category: str
    amount: float
    necessity: str
    payment_method: str = "Débito"
    detail: str = ""
    budget_item_id: Optional[int] = None
    debt_id: Optional[int] = None
    assignment_status: Optional[str] = "ASIGNADA_MANUAL"
    origin: Optional[str] = "MANUAL"
    source_month: Optional[str] = None


class OpenMonthRequest(BaseModel):
    year_month: str
    include_carryover: bool = True
    include_budget_clone: bool = True

class CloneMonthRequest(BaseModel):
    source_month: int  # 1-12
    source_year: int
    target_month: int  # 1-12
    target_year: int

class CloneUserDataRequest(BaseModel):
    source_user_id: int
    target_user_id: int
    modules: List[str]  # "transactions", "budget_items", "credit_cards"
    clone_all: bool = True
    start_month: Optional[int] = None  # 1-12
    start_year: Optional[int] = None
    end_month: Optional[int] = None  # 1-12
    end_year: Optional[int] = None

# Credit Card Models
class CreditCardCreate(BaseModel):
    card_name: str
    bank_name: str
    closing_day: int  # 1-31
    due_day: int  # 1-31
    currency: Optional[str] = "USD"
    credit_limit: Optional[float] = None
    is_active: Optional[bool] = True
    notes: Optional[str] = None

class CreditCardUpdate(BaseModel):
    card_name: Optional[str] = None
    bank_name: Optional[str] = None
    closing_day: Optional[int] = None
    due_day: Optional[int] = None
    currency: Optional[str] = None
    credit_limit: Optional[float] = None
    is_active: Optional[bool] = None
    notes: Optional[str] = None

class CreditCardPurchaseCreate(BaseModel):
    card_id: int
    transaction_id: Optional[int] = None
    description: str
    amount: float
    purchase_date: str  # ISO format
    installments: int = 1
    interest_rate: Optional[float] = 0.0
    plan_type: Optional[str] = "MANUAL"  # MANUAL or AUTOMATIC
    currency: Optional[str] = "ARS"  # ARS or USD
    movement_type: Optional[str] = "normal"  # normal | cash_advance
    cash_advance_fee: Optional[float] = 0.0

class InstallmentPayment(BaseModel):
    payment_date: str  # ISO format
    amount_paid: float
    notes: Optional[str] = None

class CreditCardBulkPurchaseItem(BaseModel):
    card_id: int
    description: str
    amount: float
    purchase_date: str  # ISO format YYYY-MM-DD
    installments: int = 1
    interest_rate: Optional[float] = 0.0
    detalle: Optional[str] = None
    movement_type: Optional[str] = "normal"
    cash_advance_fee: Optional[float] = 0.0


# Debt Records Models (DBT module source of truth)
class DebtRecordCreate(BaseModel):
    debt_name: str = Field(..., min_length=1, max_length=120)
    debt_type: Literal["TARJETA", "PRESTAMO", "HIPOTECA", "PERSONAL", "OTRO"]
    debt_source: Optional[str] = Field(default=None, max_length=50)
    creditor: Optional[str] = Field(default=None, max_length=120)
    currency: str = Field(default="ARS", min_length=3, max_length=3)
    principal_amount: float = Field(..., gt=0)
    outstanding_amount: Optional[float] = Field(default=None, ge=0)
    annual_interest_rate: Optional[float] = Field(default=None, ge=0)
    interest_vat_rate: Optional[float] = Field(default=21.0, ge=0, le=100)
    total_installments: Optional[float] = Field(default=None, gt=0)
    current_installment: Optional[float] = Field(default=None, ge=0)
    pending_installments: Optional[float] = Field(default=None, ge=0)
    start_date: Optional[date] = None
    due_date: Optional[date] = None
    status: Optional[Literal["ACTIVA", "CANCELADA", "VENCIDA"]] = "ACTIVA"
    notes: Optional[str] = None
    installment_mode: Optional[Literal["FIXED", "SALARY_PERCENT"]] = "FIXED"
    base_salary: Optional[float] = Field(default=None, gt=0)
    installment_salary_percent: Optional[float] = Field(default=None, gt=0, le=100)
    salary_increase_percent: Optional[float] = Field(default=None, ge=0)
    salary_increase_interval_months: Optional[int] = Field(default=None, ge=1)


class DebtRecordUpdate(BaseModel):
    debt_name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    debt_type: Optional[Literal["TARJETA", "PRESTAMO", "HIPOTECA", "PERSONAL", "OTRO"]] = None
    debt_source: Optional[str] = Field(default=None, max_length=50)
    creditor: Optional[str] = Field(default=None, max_length=120)
    currency: Optional[str] = Field(default=None, min_length=3, max_length=3)
    principal_amount: Optional[float] = Field(default=None, gt=0)
    outstanding_amount: Optional[float] = Field(default=None, ge=0)
    annual_interest_rate: Optional[float] = Field(default=None, ge=0)
    interest_vat_rate: Optional[float] = Field(default=21.0, ge=0, le=100)
    total_installments: Optional[float] = Field(default=None, gt=0)
    current_installment: Optional[float] = Field(default=None, ge=0)
    pending_installments: Optional[float] = Field(default=None, ge=0)
    start_date: Optional[date] = None
    due_date: Optional[date] = None
    status: Optional[Literal["ACTIVA", "CANCELADA", "VENCIDA"]] = None
    notes: Optional[str] = None
    installment_mode: Optional[Literal["FIXED", "SALARY_PERCENT"]] = None
    base_salary: Optional[float] = Field(default=None, gt=0)
    installment_salary_percent: Optional[float] = Field(default=None, gt=0, le=100)
    salary_increase_percent: Optional[float] = Field(default=None, ge=0)
    salary_increase_interval_months: Optional[int] = Field(default=None, ge=1)


class DebtPaymentCreate(BaseModel):
    payment_date: Optional[date] = None
    amount: float = Field(..., gt=0)
    transaction_id: Optional[int] = None
    notes: Optional[str] = None

# ── Security routers ─────────────────────────────────────────
from security.auth_router import router as auth_router
from security.users_router import router as users_router
from security.roles_router import router as roles_router

app.include_router(auth_router)
app.include_router(users_router)
app.include_router(roles_router)

# ── Seed default data on startup ─────────────────────────────
def _get_alembic_revision() -> dict:
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory
        from alembic.runtime.migration import MigrationContext
        from database.database import engine

        alembic_ini = os.path.join(BASE_DIR, "alembic.ini")
        script = ScriptDirectory.from_config(Config(alembic_ini))
        with engine.connect() as conn:
            current = MigrationContext.configure(conn).get_current_revision()
        return {"current": current, "head": script.get_current_head()}
    except Exception as e:
        return {"error": str(e)}


def _get_schema_status() -> dict:
    try:
        from database.database import engine
        from services.schema_checks import debt_record_schema_status, credit_card_purchase_schema_status
        return {
            "debt_records": debt_record_schema_status(engine),
            "credit_card_purchases": credit_card_purchase_schema_status(engine),
        }
    except Exception as e:
        return {"error": str(e)}


def _run_db_migrations():
    """Apply pending Alembic migrations (local dev + Render via start.sh)."""
    from alembic.config import Config
    from alembic import command
    alembic_ini = os.path.join(BASE_DIR, "alembic.ini")
    command.upgrade(Config(alembic_ini), "head")
    print("✅ Database migrations applied")


@app.on_event("startup")
def on_startup():
    _run_db_migrations()
    from security.seed_data import seed
    db = SessionLocal()
    try:
        seed(db)
        print("✅ Security seed data initialized")
    except Exception as e:
        print(f"⚠️ Security seed failed: {e}")
    finally:
        db.close()

# Routes
@app.get("/")
async def root():
    return {"message": "Finly API v1.0.0"}

@app.get("/api/categories")
async def get_categories(current_user: DBUser = Depends(get_current_user)):
    if database_service:
        cats = database_service.get_categories()
        return cats
    return []

@app.post("/api/categories")
async def create_category(data: dict, current_user: DBUser = Depends(require_role(["ADMIN"]))):
    name = (data.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="El nombre de la categoría es requerido")
    try:
        cat = database_service.add_category(name)
        return cat
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))

@app.delete("/api/categories/{category_id}")
async def delete_category(category_id: int, current_user: DBUser = Depends(require_role(["ADMIN"]))):
    try:
        database_service.delete_category(category_id)
        return {"message": "Categoría eliminada"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

@app.get("/api/transaction-types")
async def get_transaction_types(current_user: DBUser = Depends(get_current_user)):
    return ["Gasto", "Ingreso"]

@app.get("/api/necessity-types")
async def get_necessity_types(current_user: DBUser = Depends(get_current_user)):
    return ["Necesario", "Superfluo", "Importante pero no urgente"]

# Background task helpers for Google Sheets sync
def _sync_transaction_to_sheets(transaction_data: dict):
    """Sync a single transaction to Google Sheets in background"""
    try:
        if sheets_service:
            success = sheets_service.add_transaction(transaction_data)
            if success:
                print(f"✅ Transaction also saved to Google Sheets (backup)")
    except Exception as e:
        print(f"⚠️ Could not save to Google Sheets backup: {e}")

def _sync_transactions_batch_to_sheets(transactions: list):
    """Sync a batch of transactions to Google Sheets in background"""
    try:
        if sheets_service:
            success = sheets_service.add_transactions_batch(transactions)
            if success:
                print(f"✅ {len(transactions)} transactions also saved to Google Sheets (backup)")
    except Exception as e:
        print(f"⚠️ Could not save imported transactions to Google Sheets backup: {e}")

# Transaction routes
@app.post("/api/transactions")
async def create_transaction(
    transaction: Transaction,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(require_role(["ADMIN", "WRITER"]))
):
    from services.month_service import validate_period_for_mutation
    is_admin = any((getattr(role, "name", "").upper() == "ADMIN") for role in (current_user.roles or []))
    year_month = transaction.date[:7]
    validate_period_for_mutation(year_month, is_admin, (transaction.origin or "MANUAL"), db, user_id=current_user.id)

    # Save to PostgreSQL (primary storage)
    transaction_id = None
    error_detail = None
    if database_service:
        try:
            transaction_id = database_service.add_transaction(transaction.dict(), user_id=current_user.id)
            if transaction_id:
                print(f"✅ Transaction {transaction_id} saved to PostgreSQL")
            else:
                print(f"❌ Failed to save to PostgreSQL")
        except Exception as e:
            error_detail = str(e)
            print(f"❌ Error saving to PostgreSQL: {e}")
            import traceback
            traceback.print_exc()
    
    # Schedule Google Sheets backup in background (non-blocking)
    if sheets_service and transaction_id:
        background_tasks.add_task(_sync_transaction_to_sheets, transaction.dict())
    
    if transaction_id:
        return {
            "message": "Transaction created successfully",
            "transaction": transaction,
            "id": transaction_id
        }
    else:
        detail_msg = f"Failed to save transaction: {error_detail}" if error_detail else "Failed to save transaction"
        raise HTTPException(status_code=500, detail=detail_msg)

@app.post("/api/transactions/import")
async def import_transactions(
    transactions: List[Transaction],
    background_tasks: BackgroundTasks,
    current_user: DBUser = Depends(require_role(["ADMIN", "WRITER"]))
):
    if not database_service:
        raise HTTPException(status_code=503, detail="Database not configured")

    added_count = 0
    errors = []
    imported_transactions = []

    for index, transaction in enumerate(transactions):
        try:
            transaction_data = transaction.dict()
            transaction_id = database_service.add_transaction(transaction_data, user_id=current_user.id)
            if transaction_id:
                added_count += 1
                imported_transactions.append(transaction_data)
            else:
                errors.append(f"Fila {index + 1}: no se pudo guardar (validar tipo, necesidad y formato de datos)")
        except Exception as e:
            errors.append(f"Fila {index + 1}: {str(e)}")

    if added_count == 0:
        detail = "No se pudo importar ninguna transacción"
        if errors:
            detail = f"{detail}. {errors[0]}"
        raise HTTPException(status_code=400, detail=detail)

    # Schedule Google Sheets backup in background (non-blocking)
    if sheets_service and imported_transactions:
        background_tasks.add_task(_sync_transactions_batch_to_sheets, imported_transactions)

    return {
        "message": f"{added_count} transacciones importadas exitosamente",
        "added": added_count,
        "total": len(transactions),
        "errors": errors if errors else None
    }

@app.get("/api/transactions")
async def get_transactions(current_user: DBUser = Depends(get_current_user)):
    # Get from PostgreSQL (primary storage)
    if database_service:
        try:
            transactions = database_service.get_all_transactions(user_id=current_user.id)
            print(f"✅ Retrieved {len(transactions)} transactions from PostgreSQL")
            return transactions
        except Exception as e:
            print(f"❌ Error getting transactions from PostgreSQL: {e}")
            # Fallback to Google Sheets if database fails
            if sheets_service:
                try:
                    transactions = sheets_service.get_all_transactions()
                    print(f"✅ Retrieved {len(transactions)} transactions from Google Sheets (fallback)")
                    return transactions
                except Exception as e2:
                    print(f"❌ Error getting transactions from Google Sheets: {e2}")
            return []
    else:
        print("⚠️ PostgreSQL not configured")
        return []

@app.post("/api/transactions/migrate")
async def migrate_transactions(
    transactions: List[Transaction],
    current_user: DBUser = Depends(require_role(["ADMIN", "WRITER"]))
):
    """Migrate transactions from localStorage to Google Sheets"""
    if not sheets_service:
        raise HTTPException(status_code=503, detail="Google Sheets not configured")
    
    try:
        # Get existing transactions to avoid duplicates
        existing = sheets_service.get_all_transactions()
        existing_ids = set()
        
        # Try to extract IDs from existing transactions
        for t in existing:
            if 'id' in t:
                existing_ids.add(t['id'])
        
        # Filter out duplicates
        new_transactions = []
        for t in transactions:
            t_dict = t.dict() if hasattr(t, 'dict') else t
            if 'id' in t_dict and t_dict['id'] not in existing_ids:
                new_transactions.append(t_dict)
            elif 'id' not in t_dict:
                new_transactions.append(t_dict)
        
        if len(new_transactions) > 0:
            success = sheets_service.add_transactions_batch(new_transactions)
            if success:
                print(f"✅ Migrated {len(new_transactions)} new transactions to Google Sheets")
                return {
                    "message": f"Successfully migrated {len(new_transactions)} transactions",
                    "migrated_count": len(new_transactions),
                    "skipped_count": len(transactions) - len(new_transactions)
                }
            else:
                raise HTTPException(status_code=500, detail="Failed to save to Google Sheets")
        else:
            return {
                "message": "All transactions already exist in Google Sheets",
                "migrated_count": 0,
                "skipped_count": len(transactions)
            }
    except Exception as e:
        print(f"❌ Error migrating transactions: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/transactions/sync-from-sheets")
async def sync_from_sheets(
    force: bool = False,
    current_user: DBUser = Depends(require_role(["ADMIN", "WRITER"]))
):
    """Sync transactions from Google Sheets to PostgreSQL
    
    Args:
        force: If True, clears PostgreSQL and resyncs all transactions from Sheets
    """
    if not sheets_service:
        raise HTTPException(status_code=503, detail="Google Sheets not configured")
    
    if not database_service:
        raise HTTPException(status_code=503, detail="Database not configured")
    
    try:
        # Get transactions from Google Sheets
        print("📥 Fetching transactions from Google Sheets...")
        sheets_transactions = sheets_service.get_all_transactions()
        print(f"✅ Found {len(sheets_transactions)} transactions in Google Sheets")
        
        if not sheets_transactions:
            return {
                "message": "No transactions found in Google Sheets",
                "synced_count": 0,
                "skipped_count": 0
            }
        
        # If force mode, clear the database first
        if force:
            print("⚠️ FORCE MODE: Clearing PostgreSQL database...")
            # Delete all transactions
            db_transactions = database_service.get_all_transactions()
            for t in db_transactions:
                try:
                    database_service.delete_transaction(t.get('id'))
                except:
                    pass
            print(f"✅ Cleared {len(db_transactions)} transactions from PostgreSQL")
            existing_fingerprints = set()
            new_transactions = sheets_transactions
            skipped = 0
        else:
            # Get existing transactions from PostgreSQL to avoid duplicates
            print("📥 Fetching existing transactions from PostgreSQL...")
            db_transactions = database_service.get_all_transactions()
            print(f"✅ Found {len(db_transactions)} existing transactions in PostgreSQL")
            
            # Build set of existing transaction fingerprints (date + amount + category + detail)
            # Since Google Sheets IDs are row numbers, we can't rely on them
            existing_fingerprints = set()
            for t in db_transactions:
                fingerprint = f"{t.get('date')}_{t.get('amount')}_{t.get('category')}_{t.get('detail', '')}"
                existing_fingerprints.add(fingerprint)
                
            print(f"📊 Sample fingerprints from DB (first 3):")
            for i, fp in enumerate(list(existing_fingerprints)[:3]):
                print(f"  {i+1}. {fp}")
            
            # Filter new transactions
            new_transactions = []
            skipped = 0
            
            for t in sheets_transactions:
                # Create fingerprint
                fingerprint = f"{t.get('date')}_{t.get('amount')}_{t.get('category')}_{t.get('detail', '')}"
                
                if fingerprint in existing_fingerprints:
                    skipped += 1
                    continue
                
                # Ensure payment_method field exists
                if 'payment_method' not in t:
                    t['payment_method'] = 'Débito'  # Default value
                
                new_transactions.append(t)
        
        # Save new transactions to PostgreSQL
        if new_transactions:
            print(f"💾 Saving {len(new_transactions)} new transactions to PostgreSQL...")
            saved_count = 0
            
            for t in new_transactions:
                try:
                    # Remove the row ID from sheets as it shouldn't be used in DB
                    t_copy = t.copy()
                    if 'id' in t_copy:
                        del t_copy['id']
                    
                    transaction_id = database_service.add_transaction(t_copy)
                    if transaction_id:
                        saved_count += 1
                except Exception as e:
                    print(f"⚠️ Error saving transaction: {e}")
                    continue
            
            print(f"✅ Synced {saved_count} new transactions from Google Sheets to PostgreSQL")
            return {
                "message": f"Successfully synced {saved_count} transactions from Google Sheets",
                "synced_count": saved_count,
                "skipped_count": skipped,
                "total_sheets": len(sheets_transactions),
                "total_db": len(db_transactions) + saved_count
            }
        else:
            return {
                "message": "All Google Sheets transactions already exist in PostgreSQL",
                "synced_count": 0,
                "skipped_count": skipped,
                "total_sheets": len(sheets_transactions),
                "total_db": len(db_transactions)
            }
            
    except Exception as e:
        print(f"❌ Error syncing from Google Sheets: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/transactions/sync-to-sheets")
async def sync_to_sheets(
    force: bool = False,
    current_user: DBUser = Depends(require_role(["ADMIN"]))
):
    """Sync transactions from PostgreSQL to Google Sheets
    
    Args:
        force: If True, clears Google Sheets and resyncs all transactions from PostgreSQL
    """
    if not sheets_service:
        raise HTTPException(status_code=503, detail="Google Sheets not configured")
    
    if not database_service:
        raise HTTPException(status_code=503, detail="Database not configured")
    
    try:
        # Get transactions from PostgreSQL
        print("📥 Fetching transactions from PostgreSQL...")
        db_transactions = database_service.get_all_transactions()
        print(f"✅ Found {len(db_transactions)} transactions in PostgreSQL")
        
        if not db_transactions:
            return {
                "message": "No transactions found in PostgreSQL",
                "synced_count": 0,
                "skipped_count": 0
            }
        
        # Get transactions from Google Sheets
        print("📥 Fetching transactions from Google Sheets...")
        sheets_transactions = sheets_service.get_all_transactions()
        print(f"✅ Found {len(sheets_transactions)} transactions in Google Sheets")
        
        # If force mode, clear Google Sheets first
        if force:
            print("⚠️ FORCE MODE: Clearing Google Sheets...")
            # Clear all rows except header in one operation
            try:
                success = sheets_service.clear_all_transactions()
                if success:
                    print(f"✅ Cleared all transactions from Google Sheets")
                    # Reinitialize headers to ensure they're correct
                    sheets_service.initialize_sheet()
                else:
                    print("⚠️ Failed to clear Google Sheets, continuing anyway...")
            except Exception as e:
                print(f"⚠️ Error clearing Google Sheets: {e}, continuing anyway...")
            
            existing_fingerprints = set()
            new_transactions = db_transactions
            skipped = 0
        else:
            # Build set of existing transaction fingerprints in Sheets
            existing_fingerprints = set()
            for t in sheets_transactions:
                fingerprint = f"{t.get('date')}_{t.get('amount')}_{t.get('category')}_{t.get('detail', '')}"
                existing_fingerprints.add(fingerprint)
            
            # Filter new transactions
            new_transactions = []
            skipped = 0
            
            for t in db_transactions:
                fingerprint = f"{t.get('date')}_{t.get('amount')}_{t.get('category')}_{t.get('detail', '')}"
                
                if fingerprint in existing_fingerprints:
                    skipped += 1
                    continue
                
                new_transactions.append(t)
        
        # Save new transactions to Google Sheets
        if new_transactions:
            print(f"💾 Saving {len(new_transactions)} new transactions to Google Sheets...")
            saved_count = 0
            
            for t in new_transactions:
                try:
                    success = sheets_service.add_transaction(t)
                    if success:
                        saved_count += 1
                except Exception as e:
                    print(f"⚠️ Error saving transaction to Sheets: {e}")
                    continue
            
            print(f"✅ Synced {saved_count} new transactions from PostgreSQL to Google Sheets")
            return {
                "message": f"Successfully synced {saved_count} transactions to Google Sheets",
                "synced_count": saved_count,
                "skipped_count": skipped,
                "total_db": len(db_transactions),
                "total_sheets": len(sheets_transactions) + saved_count
            }
        else:
            return {
                "message": "All PostgreSQL transactions already exist in Google Sheets",
                "synced_count": 0,
                "skipped_count": skipped,
                "total_db": len(db_transactions),
                "total_sheets": len(sheets_transactions)
            }
            
    except Exception as e:
        print(f"❌ Error syncing to Google Sheets: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/transactions/debug-sync")
async def debug_sync_status(
    current_user: DBUser = Depends(require_role(["ADMIN"]))
):
    """Debug endpoint to see what's in Sheets vs PostgreSQL"""
    if not sheets_service or not database_service:
        raise HTTPException(status_code=503, detail="Services not configured")
    
    try:
        sheets_txs = sheets_service.get_all_transactions()
        db_txs = database_service.get_all_transactions()
        
        sheets_fingerprints = set()
        for t in sheets_txs:
            fp = f"{t.get('date')}_{t.get('amount')}_{t.get('category')}_{t.get('detail', '')}"
            sheets_fingerprints.add(fp)
        
        db_fingerprints = set()
        for t in db_txs:
            fp = f"{t.get('date')}_{t.get('amount')}_{t.get('category')}_{t.get('detail', '')}"
            db_fingerprints.add(fp)
        
        only_in_sheets = sheets_fingerprints - db_fingerprints
        only_in_db = db_fingerprints - sheets_fingerprints
        in_both = sheets_fingerprints & db_fingerprints
        
        return {
            "total_in_sheets": len(sheets_txs),
            "total_in_db": len(db_txs),
            "only_in_sheets": len(only_in_sheets),
            "only_in_db": len(only_in_db),
            "in_both": len(in_both),
            "sample_only_sheets": list(only_in_sheets)[:5],
            "sample_only_db": list(only_in_db)[:5],
            "sample_sheets_txs": sheets_txs[:5],
            "sample_db_txs": db_txs[:5]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/api/transactions/{transaction_id}")
async def update_transaction(
    transaction_id: int,
    transaction: Transaction,
    current_user: DBUser = Depends(require_role(["ADMIN", "WRITER"]))
):
    """Update an existing transaction"""
    if not database_service:
        raise HTTPException(status_code=503, detail="Database not configured")

    db = SessionLocal()
    try:
        from services.month_service import validate_period_for_mutation
        is_admin = any((getattr(role, "name", "").upper() == "ADMIN") for role in (current_user.roles or []))

        existing_tx = (
            db.query(DBTransaction)
            .filter(DBTransaction.id == transaction_id, DBTransaction.user_id == current_user.id)
            .first()
        )
        if existing_tx and existing_tx.date:
            validate_period_for_mutation(existing_tx.date[:7], is_admin, (existing_tx.origin or "MANUAL"), db, user_id=current_user.id)

        validate_period_for_mutation(transaction.date[:7], is_admin, (transaction.origin or "MANUAL"), db, user_id=current_user.id)
    finally:
        db.close()
    
    try:
        success = database_service.update_transaction(transaction_id, transaction.dict(), user_id=current_user.id)
        if success:
            print(f"✅ Transaction {transaction_id} updated in PostgreSQL")
            
            # Also update in Google Sheets if configured
            if sheets_service:
                try:
                    sheets_service.update_transaction(transaction_id, transaction.dict())
                    print(f"✅ Transaction {transaction_id} also updated in Google Sheets (backup)")
                except Exception as e:
                    print(f"⚠️ Could not update in Google Sheets backup: {e}")
            
            return {"message": "Transaction updated successfully", "transaction": transaction}
        else:
            raise HTTPException(status_code=404, detail="Transacción no encontrada")
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        print(f"❌ Error updating transaction: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/transactions/{transaction_id}")
async def delete_transaction(
    transaction_id: int,
    current_user: DBUser = Depends(require_role(["ADMIN", "WRITER"]))
):
    """Delete a transaction"""
    if not database_service:
        raise HTTPException(status_code=503, detail="Database not configured")

    db = SessionLocal()
    try:
        from services.month_service import validate_period_for_mutation
        is_admin = any((getattr(role, "name", "").upper() == "ADMIN") for role in (current_user.roles or []))
        existing_tx = (
            db.query(DBTransaction)
            .filter(DBTransaction.id == transaction_id, DBTransaction.user_id == current_user.id)
            .first()
        )
        if existing_tx and existing_tx.date:
            validate_period_for_mutation(existing_tx.date[:7], is_admin, (existing_tx.origin or "MANUAL"), db, user_id=current_user.id)
    finally:
        db.close()
    
    try:
        success = database_service.delete_transaction(transaction_id, user_id=current_user.id)
        if success:
            print(f"✅ Transaction {transaction_id} deleted from PostgreSQL")
            
            # Also delete from Google Sheets if configured
            if sheets_service:
                try:
                    sheets_service.delete_transaction(transaction_id)
                    print(f"✅ Transaction {transaction_id} also deleted from Google Sheets (backup)")
                except Exception as e:
                    print(f"⚠️ Could not delete from Google Sheets backup: {e}")
            
            return {"message": "Transaction deleted successfully", "id": transaction_id}
        else:
            raise HTTPException(status_code=404, detail="Transaction not found")
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error deleting transaction: {e}")
        raise HTTPException(status_code=500, detail=str(e))

def _is_admin(user: DBUser) -> bool:
    return any(r.name.upper() == "ADMIN" for r in user.roles)


def _budget_user_scope(user: DBUser):
    """Admin sees all family budget items; others only their own (+ legacy null user_id)."""
    return None if _is_admin(user) else user.id


# Debt routes
@app.get("/api/debts")
async def get_debts(current_user: DBUser = Depends(get_current_user)):
    """Get all debts"""
    if not debt_service:
        raise HTTPException(status_code=503, detail="Debt service not configured")
    
    try:
        debts = debt_service.get_all_debts(user_id=_budget_user_scope(current_user))
        return debts
    except Exception as e:
        print(f"❌ Error getting debts: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/debts/summary")
async def get_debt_summary(current_user: DBUser = Depends(get_current_user)):
    """Get debt summary statistics"""
    if not debt_service:
        raise HTTPException(status_code=503, detail="Debt service not configured")
    
    try:
        summary = debt_service.get_debt_summary(user_id=_budget_user_scope(current_user))
        return summary
    except Exception as e:
        print(f"❌ Error getting debt summary: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/debts/import-csv")
async def import_debts_csv(
    debts: List[Debt],
    current_user: DBUser = Depends(require_role(["ADMIN", "WRITER"]))
):
    """Importación masiva de presupuestos desde CSV"""
    if not debt_service:
        raise HTTPException(status_code=503, detail="Debt service not configured")

    added_count = 0
    errors = []

    for index, debt in enumerate(debts):
        try:
            debt_id = debt_service.add_debt(debt.dict(), user_id=current_user.id)
            if debt_id:
                added_count += 1
            else:
                errors.append(f"Fila {index + 1}: Error al guardar")
        except Exception as e:
            errors.append(f"Fila {index + 1}: {str(e)}")

    return {
        "message": f"{added_count} presupuestos importados exitosamente",
        "added": added_count,
        "total": len(debts),
        "errors": errors if errors else None
    }

@app.post("/api/debts/clone-month")
async def clone_month_debts(
    request: CloneMonthRequest,
    current_user: DBUser = Depends(require_role(["ADMIN", "WRITER"]))
):
    """Clonar presupuestos de un mes a otro"""
    if not debt_service:
        raise HTTPException(status_code=503, detail="Debt service not configured")
    
    try:
        from datetime import date
        import calendar
        
        # Validar meses y años
        if not (1 <= request.source_month <= 12 and 1 <= request.target_month <= 12):
            raise HTTPException(status_code=400, detail="Mes debe estar entre 1 y 12")
        
        if not (2000 <= request.source_year <= 2100 and 2000 <= request.target_year <= 2100):
            raise HTTPException(status_code=400, detail="Año debe estar entre 2000 y 2100")
        
        # Obtener todos los debts del mes origen
        db = SessionLocal()
        query = db.query(DBDebt)
        query = query.filter(DBDebt.user_id == current_user.id)
        debts = query.all()
        
        cloned_count = 0
        cloned_items = []
        
        for debt in debts:
            # Parsear fecha de vencimiento
            try:
                from datetime import datetime
                # Manejar formato DD/MM/YYYY o YYYY-MM-DD
                if '/' in debt.fecha_vencimiento:
                    fecha_parts = debt.fecha_vencimiento.split('/')
                    debt_day = int(fecha_parts[0])
                    debt_month = int(fecha_parts[1])
                    debt_year = int(fecha_parts[2])
                else:
                    fecha_parts = debt.fecha_vencimiento.split('-')
                    debt_year = int(fecha_parts[0])
                    debt_month = int(fecha_parts[1])
                    debt_day = int(fecha_parts[2])
                
                # Verificar si es del mes origen
                if debt_month == request.source_month and debt_year == request.source_year:
                    # Calcular nuevo día (ajustar si el día no existe en el mes destino)
                    max_day_target = calendar.monthrange(request.target_year, request.target_month)[1]
                    new_day = min(debt_day, max_day_target)
                    
                    # Crear nueva fecha en formato YYYY-MM-DD
                    new_fecha_venc = f"{request.target_year}-{request.target_month:02d}-{new_day:02d}"
                    
                    # Crear nuevo debt
                    new_debt_data = {
                        'fecha': new_fecha_venc,
                        'tipo': debt.tipo,
                        'categoria': debt.categoria,
                        'monto_total': debt.monto_total,
                        'monto_pagado': 0.0,
                        'detalle': debt.detalle,
                        'fecha_vencimiento': new_fecha_venc,
                        'status': 'PENDIENTE',
                        'tipo_presupuesto': debt.tipo_presupuesto.value if hasattr(debt.tipo_presupuesto, 'value') else debt.tipo_presupuesto,
                        'tipo_flujo': debt.tipo_flujo.value if hasattr(debt.tipo_flujo, 'value') else debt.tipo_flujo,
                        'expense_type': debt.expense_type.value if hasattr(debt.expense_type, 'value') else debt.expense_type,
                        'monto_ejecutado': 0.0,
                        'estimated_payment': debt.estimated_payment if debt.estimated_payment is not None else debt.monto_total
                    }
                    
                    new_debt_id = debt_service.add_debt(new_debt_data, user_id=current_user.id)
                    if new_debt_id:
                        cloned_count += 1
                        cloned_items.append({
                            'original_id': debt.id,
                            'new_id': new_debt_id,
                            'detalle': debt.detalle,
                            'monto': debt.monto_total,
                            'original_date': debt.fecha_vencimiento,
                            'new_date': new_fecha_venc
                        })
            
            except Exception as e:
                print(f"Error clonando debt {debt.id}: {e}")
                continue
        
        db.close()
        
        return {
            "message": f"{cloned_count} presupuestos clonados exitosamente",
            "cloned_count": cloned_count,
            "source_month": request.source_month,
            "source_year": request.source_year,
            "target_month": request.target_month,
            "target_year": request.target_year,
            "cloned_items": cloned_items
        }
    
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error clonando presupuestos: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/debts/{debt_id}")
async def get_debt(
    debt_id: int,
    current_user: DBUser = Depends(get_current_user)
):
    """Get a specific debt"""
    if not debt_service:
        raise HTTPException(status_code=503, detail="Debt service not configured")
    
    try:
        debt = debt_service.get_debt_by_id(debt_id, user_id=_budget_user_scope(current_user))
        if not debt:
            raise HTTPException(status_code=404, detail="Debt not found")
        return debt
    except Exception as e:
        print(f"❌ Error getting debt: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/debts")
async def create_debt(
    debt: Debt,
    current_user: DBUser = Depends(require_role(["ADMIN", "WRITER"]))
):
    """Create a new debt"""
    if not debt_service:
        raise HTTPException(status_code=503, detail="Debt service not configured")
    
    try:
        debt_id = debt_service.add_debt(debt.dict(), user_id=current_user.id)
        if debt_id:
            print(f"✅ Debt {debt_id} created in PostgreSQL")
            return {
                "message": "Debt created successfully",
                "debt": debt,
                "id": debt_id
            }
        else:
            raise HTTPException(status_code=500, detail="Failed to create debt")
    except Exception as e:
        print(f"❌ Error creating debt: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/api/debts/{debt_id}")
async def update_debt(
    debt_id: int,
    debt: Debt,
    current_user: DBUser = Depends(require_role(["ADMIN", "WRITER"]))
):
    """Update an existing debt"""
    if not debt_service:
        raise HTTPException(status_code=503, detail="Debt service not configured")
    
    try:
        success = debt_service.update_debt(debt_id, debt.dict(), user_id=_budget_user_scope(current_user))
        if success:
            print(f"✅ Debt {debt_id} updated in PostgreSQL")
            return {"message": "Debt updated successfully", "debt": debt}
        else:
            raise HTTPException(status_code=404, detail="Debt not found")
    except Exception as e:
        print(f"❌ Error updating debt: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/debts/{debt_id}")
async def delete_debt(
    debt_id: int,
    current_user: DBUser = Depends(require_role(["ADMIN", "WRITER"]))
):
    """Delete a debt"""
    if not debt_service:
        raise HTTPException(status_code=503, detail="Debt service not configured")
    
    try:
        # Check if there are linked transactions first
        db = SessionLocal()
        linked_q = db.query(DBTransaction).filter(DBTransaction.budget_item_id == debt_id)
        if not _is_admin(current_user):
            linked_q = linked_q.filter(DBTransaction.user_id == current_user.id)
        linked_count = linked_q.count()
        db.close()
        
        if linked_count > 0:
            raise HTTPException(
                status_code=400, 
                detail=f"No se puede eliminar: hay {linked_count} transacción(es) vinculada(s). Elimínelas primero."
            )
        
        success = debt_service.delete_debt(debt_id, user_id=_budget_user_scope(current_user))
        if success:
            print(f"✅ Debt {debt_id} deleted from PostgreSQL")
            return {"message": "Debt deleted successfully", "id": debt_id}
        else:
            raise HTTPException(status_code=404, detail="Item de presupuesto no encontrado")
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error deleting debt: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# FASE B - Budget Items API Aliases (mantiene compatibilidad)
# ============================================================

@app.get("/api/budget-items")
async def get_budget_items(current_user: DBUser = Depends(get_current_user)):
    """Alias for GET /api/debts - Get all budget items"""
    if not debt_service:
        raise HTTPException(status_code=503, detail="Debt service not configured")
    
    try:
        debts = debt_service.get_all_debts(user_id=_budget_user_scope(current_user))
        return debts
    except Exception as e:
        print(f"❌ Error getting budget items: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/budget-items/summary")
async def get_budget_items_summary(
    month: Optional[int] = None,
    year: Optional[int] = None,
    current_user: DBUser = Depends(get_current_user)
):
    """Get budget items summary, optionally filtered by month/year"""
    if not debt_service:
        raise HTTPException(status_code=503, detail="Debt service not configured")
    
    try:
        summary = debt_service.get_debt_summary(month=month, year=year, user_id=_budget_user_scope(current_user))
        return summary
    except Exception as e:
        print(f"❌ Error getting budget items summary: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/budget-items/import-csv")
async def import_budget_items_csv(
    debts: List[Debt],
    current_user: DBUser = Depends(require_role(["ADMIN", "WRITER"]))
):
    """Alias for POST /api/debts/import-csv - Import budget items from CSV"""
    if not debt_service:
        raise HTTPException(status_code=503, detail="Debt service not configured")
    
    try:
        added_count = 0
        errors = []
        
        for debt_data in debts:
            try:
                debt_id = debt_service.add_debt(debt_data.dict(), user_id=current_user.id)
                if debt_id:
                    added_count += 1
                    print(f"✅ Budget item {debt_id} added")
                else:
                    errors.append(f"Failed to add budget item: {debt_data.detalle}")
            except Exception as e:
                errors.append(f"Error adding budget item {debt_data.detalle}: {str(e)}")
        
        return {
            "message": f"{added_count} budget items imported successfully",
            "added": added_count,
            "total": len(debts),
            "errors": errors if errors else None
        }
    except Exception as e:
        print(f"❌ Error importing budget items: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/budget-items/{item_id}")
async def get_budget_item(
    item_id: int,
    current_user: DBUser = Depends(get_current_user)
):
    """Alias for GET /api/debts/{id} - Get a specific budget item"""
    if not debt_service:
        raise HTTPException(status_code=503, detail="Debt service not configured")
    
    try:
        debt = debt_service.get_debt_by_id(item_id, user_id=_budget_user_scope(current_user))
        if not debt:
            raise HTTPException(status_code=404, detail="Budget item not found")
        return debt
    except Exception as e:
        print(f"❌ Error getting budget item: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/budget-items")
async def create_budget_item(
    debt: Debt,
    current_user: DBUser = Depends(require_role(["ADMIN", "WRITER"]))
):
    """Alias for POST /api/debts - Create a new budget item"""
    if not debt_service:
        raise HTTPException(status_code=503, detail="Debt service not configured")
    
    try:
        debt_id = debt_service.add_debt(debt.dict(), user_id=current_user.id)
        if debt_id:
            print(f"✅ Budget item {debt_id} created in PostgreSQL")
            return {
                "message": "Budget item created successfully",
                "debt": debt,
                "id": debt_id
            }
        else:
            raise HTTPException(status_code=500, detail="Failed to create budget item")
    except Exception as e:
        print(f"❌ Error creating budget item: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/api/budget-items/{item_id}")
async def update_budget_item(
    item_id: int,
    debt: Debt,
    current_user: DBUser = Depends(require_role(["ADMIN", "WRITER"]))
):
    """Alias for PUT /api/debts/{id} - Update an existing budget item"""
    if not debt_service:
        raise HTTPException(status_code=503, detail="Debt service not configured")
    
    try:
        success = debt_service.update_debt(item_id, debt.dict(), user_id=_budget_user_scope(current_user))
        if success:
            print(f"✅ Budget item {item_id} updated in PostgreSQL")
            return {"message": "Budget item updated successfully", "debt": debt}
        else:
            raise HTTPException(status_code=404, detail="Budget item not found")
    except Exception as e:
        print(f"❌ Error updating budget item: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/budget-items/{item_id}")
async def delete_budget_item(
    item_id: int,
    current_user: DBUser = Depends(require_role(["ADMIN", "WRITER"]))
):
    """Alias for DELETE /api/debts/{id} - Delete a budget item"""
    if not debt_service:
        raise HTTPException(status_code=503, detail="Debt service not configured")
    
    try:
        # Check if there are linked transactions first
        db = SessionLocal()
        linked_q = db.query(DBTransaction).filter(DBTransaction.budget_item_id == item_id)
        if not _is_admin(current_user):
            linked_q = linked_q.filter(DBTransaction.user_id == current_user.id)
        linked_count = linked_q.count()
        db.close()
        
        if linked_count > 0:
            raise HTTPException(
                status_code=400, 
                detail=f"No se puede eliminar: hay {linked_count} transacción(es) vinculada(s). Elimínelas primero."
            )
        
        success = debt_service.delete_debt(item_id, user_id=_budget_user_scope(current_user))
        if success:
            print(f"✅ Budget item {item_id} deleted from PostgreSQL")
            return {"message": "Budget item deleted successfully", "id": item_id}
        else:
            raise HTTPException(status_code=404, detail="Item de presupuesto no encontrado")
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error deleting budget item: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# CREDIT CARD MANAGEMENT API
# ============================================================

@app.get("/api/credit-cards")
async def get_credit_cards(
    active_only: bool = True,
    current_user: DBUser = Depends(get_current_user)
):
    """Get all credit cards"""
    if not credit_card_service:
        raise HTTPException(status_code=503, detail="Credit Card service not configured")
    
    try:
        cards = credit_card_service.get_credit_cards(active_only=active_only, user_id=current_user.id)
        return cards
    except Exception as e:
        print(f"❌ Error fetching credit cards: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/credit-cards/monthly-purchases-total")
async def get_monthly_purchases_total(
    month: int,
    year: int,
    current_user: DBUser = Depends(get_current_user)
):
    """Get total credit card purchases for a specific month across all cards"""
    if not credit_card_service:
        raise HTTPException(status_code=503, detail="Credit Card service not configured")

    try:
        result = credit_card_service.get_monthly_purchases_total(year, month, user_id=current_user.id)
        return result
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        print(f"❌ Error fetching monthly purchases total: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/credit-cards/{card_id}")
async def get_credit_card(
    card_id: int,
    current_user: DBUser = Depends(get_current_user)
):
    """Get a specific credit card"""
    if not credit_card_service:
        raise HTTPException(status_code=503, detail="Credit Card service not configured")
    
    try:
        card = credit_card_service.get_credit_card(card_id, user_id=current_user.id)
        if not card:
            raise HTTPException(status_code=404, detail="Credit card not found")
        return card
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error fetching credit card: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/credit-cards")
async def create_credit_card(
    card: CreditCardCreate,
    current_user: DBUser = Depends(require_role(["ADMIN", "WRITER"]))
):
    """Create a new credit card"""
    if not credit_card_service:
        raise HTTPException(status_code=503, detail="Credit Card service not configured")
    
    try:
        card_id = credit_card_service.create_credit_card(card.dict(), user_id=current_user.id)
        if card_id:
            print(f"✅ Credit card {card_id} created successfully")
            return {
                "message": "Credit card created successfully",
                "id": card_id,
                "card": card
            }
        else:
            raise HTTPException(status_code=500, detail="Failed to create credit card")
    except ValueError as e:
        print(f"⚠️ Validation error creating credit card: {e}")
        raise HTTPException(status_code=409, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error creating credit card: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/api/credit-cards/{card_id}")
async def update_credit_card(
    card_id: int,
    card: CreditCardUpdate,
    current_user: DBUser = Depends(require_role(["ADMIN", "WRITER"]))
):
    """Update an existing credit card"""
    if not credit_card_service:
        raise HTTPException(status_code=503, detail="Credit Card service not configured")
    
    try:
        updated_card = credit_card_service.update_credit_card(card_id, card.dict(exclude_unset=True), user_id=current_user.id)
        if updated_card:
            print(f"✅ Credit card {card_id} updated successfully")
            return {
                "message": "Credit card updated successfully",
                "card": updated_card
            }
        else:
            raise HTTPException(status_code=404, detail="Credit card not found")
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error updating credit card: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/credit-cards/{card_id}")
async def delete_credit_card(
    card_id: int,
    current_user: DBUser = Depends(require_role(["ADMIN", "WRITER"]))
):
    """Delete a credit card"""
    if not credit_card_service:
        raise HTTPException(status_code=503, detail="Credit Card service not configured")
    
    try:
        success = credit_card_service.delete_credit_card(card_id, user_id=current_user.id)
        if success:
            print(f"✅ Credit card {card_id} deleted successfully")
            return {"message": "Credit card deleted successfully", "id": card_id}
        else:
            raise HTTPException(status_code=404, detail="Credit card not found")
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error deleting credit card: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/credit-cards/{card_id}/summary")
async def get_credit_card_summary(
    card_id: int,
    current_user: DBUser = Depends(get_current_user)
):
    """Get credit card summary with analytics"""
    if not credit_card_service:
        raise HTTPException(status_code=503, detail="Credit Card service not configured")
    
    try:
        summary = credit_card_service.get_card_summary(card_id)
        if not summary:
            raise HTTPException(status_code=404, detail="Credit card not found")
        return summary
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error fetching credit card summary: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/credit-cards/{card_id}/purchases-summary")
async def get_credit_card_purchases_summary(
    card_id: int,
    current_user: DBUser = Depends(get_current_user)
):
    """Get aggregated purchases summary for a credit card (for pie chart)"""
    if not credit_card_service:
        raise HTTPException(status_code=503, detail="Credit Card service not configured")
    
    try:
        summary = credit_card_service.get_purchases_summary(card_id)
        return summary
    except Exception as e:
        print(f"❌ Error fetching purchases summary: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/credit-cards/purchases")
async def create_purchase(
    purchase: CreditCardPurchaseCreate,
    current_user: DBUser = Depends(require_role(["ADMIN", "WRITER"]))
):
    """Create a new credit card purchase with installment plan"""
    if not credit_card_service:
        raise HTTPException(status_code=503, detail="Credit Card service not configured")
    
    try:
        purchase_id = credit_card_service.create_purchase(purchase.dict())
        if purchase_id:
            print(f"✅ Purchase {purchase_id} created successfully")
            return {
                "message": "Purchase created successfully",
                "id": purchase_id,
                "purchase": purchase
            }
        else:
            raise HTTPException(status_code=500, detail="Failed to create purchase")
    except ValueError as e:
        print(f"⚠️ Validation error creating purchase: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        print(f"❌ Error creating purchase: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/credit-cards/{card_id}/purchases")
async def get_card_purchases(
    card_id: int,
    current_user: DBUser = Depends(get_current_user)
):
    """Get all purchases for a specific credit card"""
    if not credit_card_service:
        raise HTTPException(status_code=503, detail="Credit Card service not configured")
    
    try:
        purchases = credit_card_service.get_purchases(card_id=card_id)
        return purchases
    except Exception as e:
        print(f"❌ Error fetching purchases: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/installment-plans/{plan_id}/schedule")
async def get_installment_schedule(
    plan_id: int,
    current_user: DBUser = Depends(get_current_user)
):
    """Get the installment schedule for a plan"""
    if not credit_card_service:
        raise HTTPException(status_code=503, detail="Credit Card service not configured")
    
    try:
        schedule = credit_card_service.get_installment_schedule(plan_id)
        if not schedule:
            raise HTTPException(status_code=404, detail="Installment plan not found")
        return schedule
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error fetching installment schedule: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/api/installments/{installment_id}/pay")
async def pay_installment(
    installment_id: int,
    payment: InstallmentPayment,
    current_user: DBUser = Depends(require_role(["ADMIN", "WRITER"]))
):
    """Mark an installment as paid"""
    if not credit_card_service:
        raise HTTPException(status_code=503, detail="Credit Card service not configured")
    
    try:
        success = credit_card_service.pay_installment(installment_id, payment.dict())
        if success:
            print(f"✅ Installment {installment_id} marked as paid")
            return {
                "message": "Installment marked as paid successfully",
                "installment_id": installment_id
            }
        else:
            raise HTTPException(status_code=404, detail="Installment not found or already paid")
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error paying installment: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/api/installments/{installment_id}/unpay")
async def unpay_installment(
    installment_id: int,
    current_user: DBUser = Depends(require_role(["ADMIN", "WRITER"]))
):
    """Revert a paid installment back to pending"""
    if not credit_card_service:
        raise HTTPException(status_code=503, detail="Credit Card service not configured")
    
    try:
        success = credit_card_service.unpay_installment(installment_id)
        if success:
            print(f"✅ Installment {installment_id} reverted to pending")
            return {
                "message": "Payment reverted successfully",
                "installment_id": installment_id
            }
        else:
            raise HTTPException(status_code=404, detail="Installment not found or not paid")
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error unpaying installment: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/api/credit-cards/purchases/{purchase_id}")
async def update_purchase(
    purchase_id: int,
    purchase_data: dict,
    current_user: DBUser = Depends(require_role(["ADMIN", "WRITER"]))
):
    """Update an existing credit card purchase"""
    if not credit_card_service:
        raise HTTPException(status_code=503, detail="Credit Card service not configured")
    
    try:
        success = credit_card_service.update_purchase(purchase_id, purchase_data)
        if success:
            print(f"✅ Purchase {purchase_id} updated successfully")
            return {
                "message": "Purchase updated successfully",
                "purchase_id": purchase_id
            }
        else:
            raise HTTPException(status_code=404, detail="Purchase not found")
    except HTTPException:
        raise
    except ValueError as e:
        print(f"⚠️ Validation error updating purchase: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        print(f"❌ Error updating purchase: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/credit-cards/purchases/{purchase_id}")
async def delete_purchase(
    purchase_id: int,
    current_user: DBUser = Depends(require_role(["ADMIN", "WRITER"]))
):
    """Delete a credit card purchase"""
    if not credit_card_service:
        raise HTTPException(status_code=503, detail="Credit Card service not configured")
    
    try:
        success = credit_card_service.delete_purchase(purchase_id)
        if success:
            print(f"✅ Purchase {purchase_id} deleted successfully")
            return {"message": "Purchase deleted successfully", "purchase_id": purchase_id}
        else:
            raise HTTPException(status_code=404, detail="Purchase not found")
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error deleting purchase: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/credit-cards/{card_id}/period-installments")
async def get_card_period_installments(
    card_id: int,
    year: int,
    month: int,
    current_user: DBUser = Depends(require_role(["ADMIN", "WRITER", "READER"]))
):
    """Get all installments for a card in a specific month"""
    if not credit_card_service:
        raise HTTPException(status_code=503, detail="Credit Card service not configured")
    
    try:
        result = credit_card_service.get_card_period_installments(card_id, year, month)
        if result is None:
            raise HTTPException(status_code=404, detail="Credit card not found")
        return result
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error fetching period installments: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/credit-cards/{card_id}/register-period-budget")
async def register_card_period_budget(
    card_id: int,
    period_data: dict,
    current_user: DBUser = Depends(require_role(["ADMIN", "WRITER"]))
):
    """Register a budget item for all installments due in a card period"""
    if not credit_card_service:
        raise HTTPException(status_code=503, detail="Credit Card service not configured")
    
    year = period_data.get('year')
    month = period_data.get('month')
    payment_type = period_data.get('payment_type', 'total')
    custom_minimum = period_data.get('minimum_payment', 0)
    if not year or not month:
        raise HTTPException(status_code=400, detail="year and month are required")
    if payment_type not in ('total', 'minimum'):
        raise HTTPException(status_code=400, detail="payment_type must be 'total' or 'minimum'")
    
    try:
        result = credit_card_service.register_card_period_budget(
            card_id, int(year), int(month), payment_type, float(custom_minimum)
        )
        if result is None:
            raise HTTPException(status_code=404, detail="Credit card not found")
        if 'error' in result:
            raise HTTPException(status_code=400, detail=result['error'])
        return result
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error registering period budget: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/api/credit-cards/{card_id}/period-config")
async def update_period_config(
    card_id: int,
    config_data: dict,
    current_user: DBUser = Depends(require_role(["ADMIN", "WRITER"]))
):
    """Update closing_day and due_day for a specific card period"""
    if not credit_card_service:
        raise HTTPException(status_code=503, detail="Credit Card service not configured")
    
    year = config_data.get('year')
    month = config_data.get('month')
    closing_day = config_data.get('closing_day')
    due_day = config_data.get('due_day')
    
    if not all([year, month, closing_day, due_day]):
        raise HTTPException(status_code=400, detail="year, month, closing_day, due_day are required")
    
    closing_day = int(closing_day)
    due_day = int(due_day)
    if not (1 <= closing_day <= 31) or not (1 <= due_day <= 31):
        raise HTTPException(status_code=400, detail="closing_day and due_day must be between 1 and 31")
    
    try:
        result = credit_card_service.save_period_config(
            card_id, int(year), int(month), closing_day, due_day
        )
        return result
    except Exception as e:
        print(f"❌ Error updating period config: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/credit-cards/{card_id}/period-for-date")
async def get_period_for_date(
    card_id: int,
    purchase_date: str,
    current_user: DBUser = Depends(require_role(["ADMIN", "WRITER", "READER"]))
):
    """Given a purchase date, return which billing period it belongs to."""
    if not credit_card_service:
        raise HTTPException(status_code=503, detail="Credit Card service not configured")
    try:
        result = credit_card_service.get_period_for_date(card_id, purchase_date)
        if result is None:
            raise HTTPException(status_code=404, detail="Credit card not found")
        return result
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error getting period for date: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/credit-cards/{card_id}/import-csv")
async def bulk_import_credit_card_purchases(
    card_id: int,
    purchases: List[CreditCardBulkPurchaseItem],
    current_user: DBUser = Depends(require_role(["ADMIN", "WRITER"]))
):
    """Bulk import credit card purchases from CSV data"""
    if not credit_card_service:
        raise HTTPException(status_code=503, detail="Credit Card service not configured")

    # Verify card exists
    card = credit_card_service.get_credit_card(card_id)
    if not card:
        raise HTTPException(status_code=404, detail="Credit card not found")

    added_count = 0
    errors = []

    for index, purchase in enumerate(purchases):
        try:
            purchase_data = {
                'card_id': card_id,
                'description': purchase.description,
                'amount': purchase.amount,
                'purchase_date': purchase.purchase_date,
                'installments': purchase.installments,
                'interest_rate': purchase.interest_rate or 0.0,
                'category': 'General',
                'movement_type': purchase.movement_type or 'normal',
                'cash_advance_fee': purchase.cash_advance_fee or 0.0,
            }
            purchase_id = credit_card_service.create_purchase(purchase_data)
            if purchase_id:
                added_count += 1
            else:
                errors.append(f"Fila {index + 1}: Error al crear compra")
        except Exception as e:
            errors.append(f"Fila {index + 1} ({purchase.description}): {str(e)}")

    return {
        "message": f"{added_count} gastos importados exitosamente",
        "added": added_count,
        "total": len(purchases),
        "errors": errors if errors else None
    }

# ==================== MONTH CLOSING ENDPOINTS ====================

@app.get("/api/month-closings")
async def get_all_closings(current_user: DBUser = Depends(get_current_user)):
    """Get all month closings"""
    return database_service.get_all_closings(user_id=current_user.id)

@app.get("/api/month-closings/{year}/{month}")
async def get_month_closing(year: int, month: int, current_user: DBUser = Depends(get_current_user)):
    """Check if a specific month is closed, with stale detection"""
    closing = database_service.get_month_closing(year, month, user_id=current_user.id)
    if not closing:
        return {"closed": False, "year": year, "month": month}
    # Detect if closing is stale (balance changed since close)
    cc_total = 0.0
    if credit_card_service:
        try:
            cc_data = credit_card_service.get_monthly_purchases_total(year, month, user_id=current_user.id)
            cc_total = cc_data.get('total', 0.0)
        except Exception:
            pass
    current = database_service.calculate_month_balance(year, month, cc_total, user_id=current_user.id)
    is_stale = round(current['balance'], 2) != round(closing['balance'], 2)
    return {"closed": True, "is_stale": is_stale, "current_balance": current['balance'], **closing}

@app.post("/api/month-closings/{year}/{month}")
async def close_month(year: int, month: int, current_user: DBUser = Depends(require_role(["ADMIN"]))):
    """Close (or re-close) a month: calculate balance and create carry-over transaction"""
    if month < 1 or month > 12:
        raise HTTPException(status_code=400, detail="Mes inválido")
    # Get CC purchases total for this month
    cc_total = 0.0
    if credit_card_service:
        try:
            cc_data = credit_card_service.get_monthly_purchases_total(year, month, user_id=current_user.id)
            cc_total = cc_data.get('total', 0.0)
        except Exception:
            pass
    try:
        result = database_service.close_month(year, month, current_user.username, cc_total, user_id=current_user.id)
        return result
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))

@app.delete("/api/month-closings/{year}/{month}")
async def reopen_month(year: int, month: int, current_user: DBUser = Depends(require_role(["ADMIN"]))):
    """Reopen a closed month (delete closing record and carry-over transaction)"""
    try:
        database_service.reopen_month(year, month, user_id=current_user.id)
        return {"message": f"Mes {month}/{year} reabierto"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

# ==================== SETTINGS ENDPOINTS ====================

@app.get("/api/settings/{key}")
async def get_setting(key: str, current_user: DBUser = Depends(get_current_user)):
    """Get a specific setting value"""
    db = SessionLocal()
    try:
        setting = db.query(AppSetting).filter(AppSetting.key == key).first()
        if not setting:
            raise HTTPException(status_code=404, detail=f"Setting '{key}' not found")
        return {"key": setting.key, "value": setting.value, "description": setting.description}
    finally:
        db.close()

@app.put("/api/settings/{key}")
async def update_setting(key: str, body: dict, current_user: DBUser = Depends(require_role(["ADMIN"]))):
    """Update a setting value (admin only)"""
    db = SessionLocal()
    try:
        setting = db.query(AppSetting).filter(AppSetting.key == key).first()
        if not setting:
            setting = AppSetting(key=key, value=str(body.get("value", "")), description=body.get("description", ""))
            db.add(setting)
        else:
            setting.value = str(body.get("value", setting.value))
            if "description" in body:
                setting.description = body["description"]
        db.commit()
        db.refresh(setting)
        return {"key": setting.key, "value": setting.value, "description": setting.description}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

# ── Admin: Clone user data ───────────────────────────────────

@app.post("/api/admin/clone-data")
async def clone_user_data_endpoint(
    request: CloneUserDataRequest,
    current_user: DBUser = Depends(require_role(["ADMIN"]))
):
    """Clone data from one user to another (admin only)."""
    if request.source_user_id == request.target_user_id:
        raise HTTPException(status_code=400, detail="Usuario origen y destino deben ser diferentes")

    valid_modules = {"transactions", "budget_items", "credit_cards"}
    invalid = set(request.modules) - valid_modules
    if invalid:
        raise HTTPException(status_code=400, detail=f"Módulos inválidos: {', '.join(invalid)}")

    if not request.clone_all:
        if not all([request.start_month, request.start_year, request.end_month, request.end_year]):
            raise HTTPException(status_code=400, detail="Se requiere rango de meses completo cuando clone_all es false")
        if not (1 <= request.start_month <= 12 and 1 <= request.end_month <= 12):
            raise HTTPException(status_code=400, detail="Mes debe estar entre 1 y 12")

    # Verify both users exist
    db = SessionLocal()
    try:
        from database.database import User as DBUserModel
        source = db.query(DBUserModel).filter(DBUserModel.id == request.source_user_id).first()
        target = db.query(DBUserModel).filter(DBUserModel.id == request.target_user_id).first()
        if not source:
            raise HTTPException(status_code=404, detail="Usuario origen no encontrado")
        if not target:
            raise HTTPException(status_code=404, detail="Usuario destino no encontrado")
    finally:
        db.close()

    try:
        from services.clone_service import clone_user_data
        result = clone_user_data(
            source_user_id=request.source_user_id,
            target_user_id=request.target_user_id,
            modules=request.modules,
            clone_all=request.clone_all,
            start_month=request.start_month,
            start_year=request.start_year,
            end_month=request.end_month,
            end_year=request.end_year,
        )
        return {
            "message": "Datos clonados exitosamente",
            "source_user_id": request.source_user_id,
            "target_user_id": request.target_user_id,
            **result,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al clonar datos: {str(e)}")

# ============================================================
# BANK ACCOUNTS API
# ============================================================

try:
    from services.bank_account_service import BankAccountService
    bank_account_service = BankAccountService()
    print("✅ Bank Account service initialized successfully")
except Exception as e:
    print(f"⚠️ Bank Account service not available: {e}")
    bank_account_service = None


@app.get("/api/accounts")
async def get_accounts(
    active_only: bool = True,
    current_user: DBUser = Depends(require_permission("accounts.read"))
):
    """List bank accounts. Admins see all; writers see only their own."""
    if not bank_account_service:
        raise HTTPException(status_code=503, detail="Bank Account service not configured")
    return bank_account_service.get_accounts(
        user_id=current_user.id,
        is_admin=_is_admin(current_user),
        active_only=active_only
    )


@app.get("/api/accounts/{account_id}")
async def get_account(
    account_id: int,
    current_user: DBUser = Depends(require_permission("accounts.read"))
):
    """Get a single bank account."""
    if not bank_account_service:
        raise HTTPException(status_code=503, detail="Bank Account service not configured")
    account = bank_account_service.get_account(
        account_id=account_id,
        user_id=current_user.id,
        is_admin=_is_admin(current_user)
    )
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    return account


@app.post("/api/accounts", status_code=201)
async def create_account(
    data: dict,
    current_user: DBUser = Depends(require_permission("accounts.write"))
):
    """Create a new bank account."""
    if not bank_account_service:
        raise HTTPException(status_code=503, detail="Bank Account service not configured")
    try:
        return bank_account_service.create_account(data=data, user_id=current_user.id)
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/accounts/{account_id}")
async def update_account(
    account_id: int,
    data: dict,
    current_user: DBUser = Depends(require_permission("accounts.write"))
):
    """Update a bank account."""
    if not bank_account_service:
        raise HTTPException(status_code=503, detail="Bank Account service not configured")
    result = bank_account_service.update_account(
        account_id=account_id,
        data=data,
        user_id=current_user.id,
        is_admin=_is_admin(current_user)
    )
    if not result:
        raise HTTPException(status_code=404, detail="Account not found")
    return result


@app.delete("/api/accounts/{account_id}")
async def delete_account(
    account_id: int,
    current_user: DBUser = Depends(require_permission("accounts.delete"))
):
    """Deactivate (soft-delete) a bank account."""
    if not bank_account_service:
        raise HTTPException(status_code=503, detail="Bank Account service not configured")
    ok = bank_account_service.delete_account(
        account_id=account_id,
        user_id=current_user.id,
        is_admin=_is_admin(current_user)
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Account not found")
    return {"message": "Account deactivated successfully"}


# ============================================================
# DEBT RECORDS API
# ============================================================

try:
    from services.debt_record_service import DebtRecordService
    debt_record_service = DebtRecordService()
    print("✅ Debt Record service initialized successfully")
except Exception as e:
    print(f"⚠️ Debt Record service not available: {e}")
    debt_record_service = None


@app.get("/api/debt-records")
async def get_debt_records(
    status: Optional[str] = None,
    current_user: DBUser = Depends(require_permission("debt_records.read"))
):
    """List all debt records for the current user."""
    if not debt_record_service:
        raise HTTPException(status_code=503, detail="Debt Record service not configured")
    normalized_status = status.upper() if isinstance(status, str) else None
    if normalized_status not in {"ACTIVA", "CANCELADA", "VENCIDA"}:
        normalized_status = None
    return debt_record_service.get_debt_records(user_id=current_user.id, status=normalized_status)


@app.get("/api/debt-records/projected")
async def get_debt_records_projected(
    status: Optional[str] = None,
    current_user: DBUser = Depends(require_permission("debt_records.read"))
):
    """List debt records enriched with linked budget projection state."""
    if not debt_record_service:
        raise HTTPException(status_code=503, detail="Debt Record service not configured")
    normalized_status = status.upper() if isinstance(status, str) else None
    if normalized_status not in {"ACTIVA", "CANCELADA", "VENCIDA"}:
        normalized_status = None
    try:
        return debt_record_service.get_debt_records_with_projection(
            user_id=current_user.id,
            status=normalized_status
        )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        print(f"❌ Error listing projected debt records: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/debt-records/{record_id}")
async def get_debt_record(
    record_id: int,
    current_user: DBUser = Depends(require_permission("debt_records.read"))
):
    """Get a single debt record."""
    if not debt_record_service:
        raise HTTPException(status_code=503, detail="Debt Record service not configured")
    record = debt_record_service.get_debt_record(record_id=record_id, user_id=current_user.id)
    if not record:
        raise HTTPException(status_code=404, detail="Debt record not found")
    return record


@app.post("/api/debt-records", status_code=201)
async def create_debt_record(
    data: DebtRecordCreate,
    current_user: DBUser = Depends(require_permission("debt_records.write"))
):
    """Create a new debt record."""
    if not debt_record_service:
        raise HTTPException(status_code=503, detail="Debt Record service not configured")
    try:
        return debt_record_service.create_debt_record(data=data.dict(exclude_none=True), user_id=current_user.id)
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=422, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        print(f"❌ Error creating debt record: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/debt-records/{record_id}")
async def update_debt_record(
    record_id: int,
    data: DebtRecordUpdate,
    current_user: DBUser = Depends(require_permission("debt_records.write"))
):
    """Update a debt record."""
    if not debt_record_service:
        raise HTTPException(status_code=503, detail="Debt Record service not configured")
    payload = data.dict(exclude_unset=True)
    if not payload:
        raise HTTPException(status_code=400, detail="No fields provided to update")
    try:
        result = debt_record_service.update_debt_record(
            record_id=record_id, data=payload, user_id=current_user.id
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        print(f"❌ Error updating debt record {record_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    if not result:
        raise HTTPException(status_code=404, detail="Debt record not found")
    return result


@app.delete("/api/debt-records/{record_id}")
async def delete_debt_record(
    record_id: int,
    current_user: DBUser = Depends(require_permission("debt_records.delete"))
):
    """Delete a debt record and all its payments."""
    if not debt_record_service:
        raise HTTPException(status_code=503, detail="Debt Record service not configured")
    try:
        ok = debt_record_service.delete_debt_record(record_id=record_id, user_id=current_user.id)
    except Exception as e:
        print(f"❌ Error deleting debt record {record_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    if not ok:
        raise HTTPException(status_code=404, detail="Debt record not found")
    return {"message": "Debt record deleted successfully"}


@app.get("/api/debt-records/{record_id}/payments")
async def get_debt_payments(
    record_id: int,
    current_user: DBUser = Depends(require_permission("debt_records.read"))
):
    """List all payments for a debt record."""
    if not debt_record_service:
        raise HTTPException(status_code=503, detail="Debt Record service not configured")
    payments = debt_record_service.get_payments(debt_record_id=record_id, user_id=current_user.id)
    if payments is None:
        raise HTTPException(status_code=404, detail="Debt record not found")
    return payments


@app.post("/api/debt-records/{record_id}/payments", status_code=201)
async def add_debt_payment(
    record_id: int,
    data: DebtPaymentCreate,
    current_user: DBUser = Depends(require_permission("debt_records.write"))
):
    """Register a payment against a debt record."""
    if not debt_record_service:
        raise HTTPException(status_code=503, detail="Debt Record service not configured")
    try:
        result = debt_record_service.add_payment(
            debt_record_id=record_id, data=data.dict(exclude_none=True), user_id=current_user.id
        )
        if result is None:
            raise HTTPException(status_code=404, detail="Debt record not found")
        return result
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/debt-records/payments/{payment_id}")
async def delete_debt_payment(
    payment_id: int,
    current_user: DBUser = Depends(require_permission("debt_records.delete"))
):
    """Delete a debt payment (restores outstanding amount)."""
    if not debt_record_service:
        raise HTTPException(status_code=503, detail="Debt Record service not configured")
    ok = debt_record_service.delete_payment(payment_id=payment_id, user_id=current_user.id)
    if not ok:
        raise HTTPException(status_code=404, detail="Payment not found")
    return {"message": "Payment deleted successfully"}


# ============================================================
# MONTHLY PERIOD API (exp-month-close)
# ============================================================

class ReopenMonthRequest(BaseModel):
    reason: str


@app.post("/api/months")
async def open_month_endpoint(
    request: OpenMonthRequest,
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(require_role(["ADMIN", "WRITER"]))
):
    """Open a month with optional carryover creation and budget cloning."""
    from services.month_service import open_month as svc_open_month
    return svc_open_month(
        year_month=request.year_month,
        user_id=current_user.id,
        username=current_user.username,
        db=db,
        skip_carryover=not request.include_carryover,
        skip_clone=not request.include_budget_clone,
    )


@app.post("/api/months/{year_month}/close")
async def close_month_endpoint(
    year_month: str,
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(require_role(["ADMIN"]))
):
    """Close a monthly period and create a snapshot (admin or writer)"""
    from services.month_service import close_month as svc_close_month
    return svc_close_month(year_month, current_user.id, current_user.username, db)


@app.post("/api/months/{year_month}/reopen")
async def reopen_month_endpoint(
    year_month: str,
    request: ReopenMonthRequest,
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user)
):
    """Reopen a closed monthly period with a required reason.

    Admins may reopen any period. Writers may reopen only periods they closed themselves.
    """
    from services.month_service import reopen_month as svc_reopen_month

    # Determine admin status
    is_admin = any((getattr(role, "name", "").upper() == "ADMIN") for role in (current_user.roles or []))

    # If user is not admin, ensure they have WRITER role — otherwise deny
    if not is_admin:
        has_writer = any((getattr(role, "name", "").upper() == "WRITER") for role in (current_user.roles or []))
        if not has_writer:
            raise HTTPException(status_code=403, detail="Not enough permissions")

    # Delegate to service (service will enforce ownership check for non-admins)
    return svc_reopen_month(year_month, current_user.id, current_user.username, request.reason, db, is_admin=is_admin)


@app.get("/api/months/{year_month}/status")
async def get_month_status_endpoint(
    year_month: str,
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user)
):
    """Get the status and latest snapshot of a monthly period"""
    from services.month_service import get_month_status, get_latest_snapshot
    result = get_month_status(year_month, db, current_user.id)
    snapshot = get_latest_snapshot(year_month, db, current_user.id)
    if snapshot:
        result["snapshot"] = snapshot
    return result


@app.get("/api/months")
async def get_months_endpoint(
    include_status: bool = False,
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user)
):
    """List monthly periods with optional status details"""
    from services.month_service import get_months_with_status
    if include_status:
        return get_months_with_status(db, current_user.id)
    return []


@app.get("/api/months/{year_month}/carryover")
async def get_month_carryover_endpoint(
    year_month: str,
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user)
):
    """Get carryover details for a specific month."""
    row = (
        db.query(DBMonthlyBalance)
        .filter(
            DBMonthlyBalance.user_id == current_user.id,
            DBMonthlyBalance.target_month == year_month,
        )
        .order_by(DBMonthlyBalance.carryover_date.desc())
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="No hay carryover registrado para este mes")

    return {
        "id": row.id,
        "source_month": row.source_month,
        "target_month": row.target_month,
        "balance_amount": row.balance_amount,
        "balance_type": row.balance_type,
        "carryover_date": row.carryover_date.isoformat() if row.carryover_date else None,
        "transaction_id": row.transaction_id,
    }


@app.get("/api/months/{year_month}/budget-items")
async def get_month_budget_items_endpoint(
    year_month: str,
    include_clone_info: bool = False,
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user)
):
    """List budget items for a month, optionally including clone metadata."""
    items = (
        db.query(DBDebt)
        .filter(
            DBDebt.user_id == current_user.id,
            DBDebt.fecha.like(f"{year_month}%"),
        )
        .order_by(DBDebt.fecha.asc(), DBDebt.id.asc())
        .all()
    )

    result = []
    for item in items:
        data = {
            "id": item.id,
            "fecha": item.fecha,
            "tipo": item.tipo,
            "categoria": item.categoria,
            "monto_total": item.monto_total,
            "monto_pagado": item.monto_pagado,
            "detalle": item.detalle,
            "fecha_vencimiento": item.fecha_vencimiento,
            "status": item.status.value if hasattr(item.status, "value") else item.status,
        }
        if include_clone_info:
            data["clone_info"] = {
                "cloned_from_item_id": item.cloned_from_item_id,
                "base_cloned": item.base_cloned,
                "version_source_month": item.version_source_month,
            }
        result.append(data)

    return result


@app.get("/api/budget-items/{item_id}/clone-lineage")
async def get_budget_item_clone_lineage(
    item_id: int,
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user)
):
    """Return clone lineage chain for a budget item (from current to original source)."""
    current = (
        db.query(DBDebt)
        .filter(DBDebt.id == item_id, DBDebt.user_id == current_user.id)
        .first()
    )
    if not current:
        raise HTTPException(status_code=404, detail="Budget item not found")

    lineage = []
    visited = set()
    node = current
    while node and node.id not in visited:
        visited.add(node.id)
        lineage.append({
            "id": node.id,
            "fecha": node.fecha,
            "categoria": node.categoria,
            "monto_total": node.monto_total,
            "base_cloned": node.base_cloned,
            "version_source_month": node.version_source_month,
            "cloned_from_item_id": node.cloned_from_item_id,
        })

        if not node.cloned_from_item_id:
            break
        node = (
            db.query(DBDebt)
            .filter(DBDebt.id == node.cloned_from_item_id, DBDebt.user_id == current_user.id)
            .first()
        )

    return {
        "item_id": item_id,
        "lineage": lineage,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

