import { useState, useEffect, useMemo } from 'react';
import { Chart as ChartJS, ArcElement, CategoryScale, LinearScale, BarElement, Title, Tooltip, Legend } from 'chart.js';
import { Pie, Bar } from 'react-chartjs-2';
import { debtsAPI, debtRecordsAPI, transactionsAPI } from '../services/api';
import { useToast } from './ToastContainer';
import ConfirmDialog from './ConfirmDialog';
import { formatDate, toISODate } from '../utils/dateUtils';
import { formatArCurrency, formatArNumber, parseArNumber, formatAmountInputOnBlur } from '../utils/currencyUtils';
import { exportToCsv } from '../utils/csvExport';
import BudgetCSVImport from './BudgetCSVImport';
import EditDebtModal from './EditDebtModal';
import NewDebtModal from './NewDebtModal';
import EditBudgetItemModal from './EditBudgetItemModal';
import NewBudgetItemModal from './NewBudgetItemModal';

ChartJS.register(ArcElement, CategoryScale, LinearScale, BarElement, Title, Tooltip, Legend);

function LoadingOverlay({ label }) {
  return (
    <div
      className="absolute inset-0 bg-white/75 flex flex-col items-center justify-center z-10 min-h-[12rem]"
      role="status"
      aria-live="polite"
      aria-label={label}
    >
      <div className="inline-block animate-spin rounded-full h-12 w-12 border-b-2 border-finly-primary" aria-hidden="true" />
      <p className="text-gray-500 mt-4 text-sm">{label}</p>
    </div>
  );
}

const MONTH_NAMES = [
  'Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio',
  'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre'
];

/** Cuota del mes vs saldo total del préstamo (DBT-BUG-016). */
const getDebtPaymentContext = (debt) => {
  const installmentDue = Number(debt.estimated_payment ?? debt.monto_total ?? 0);
  const installmentPaid = Number(debt.monto_ejecutado ?? debt.monto_pagado ?? 0);
  const installmentRemaining = Math.max(0, installmentDue - installmentPaid);
  const totalOutstanding = Number(debt.outstanding_amount ?? 0);
  const defaultAmount = installmentRemaining > 0
    ? installmentRemaining
    : Math.min(installmentDue, totalOutstanding);
  return {
    installmentDue,
    installmentRemaining,
    totalOutstanding,
    defaultAmount: defaultAmount > 0 ? defaultAmount : 0,
  };
};

const getYearMonthKey = (value) => {
  if (!value) return '';
  const raw = String(value).trim();

  // Prefer YYYY-MM from ISO-like values to avoid timezone shifts.
  const isoLike = raw.match(/^(\d{4})-(\d{2})/);
  if (isoLike) return `${isoLike[1]}-${isoLike[2]}`;

  // DD/MM/YYYY (with optional single-digit day/month)
  const dmy = raw.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})$/);
  if (dmy) {
    return `${dmy[3]}-${dmy[2].padStart(2, '0')}`;
  }

  const iso = toISODate(raw);
  return iso ? iso.slice(0, 7) : '';
};

const collectBudgetMonths = (debtList) =>
  [...new Set(
    debtList
      .map((debt) => getYearMonthKey(debt.fecha_vencimiento || debt.fecha))
      .filter(Boolean)
  )].sort();

const debtHasProjectionMonth = (debt, monthKey) =>
  Boolean(
    debt.projection_by_month?.[monthKey] ||
    (debt.projections || []).some((p) => p?.version_source_month === monthKey)
  );

const collectProjectionMonths = (debtList) => {
  const keys = new Set();
  debtList.forEach((debt) => {
    Object.keys(debt.projection_by_month || {}).forEach((key) => keys.add(key));
    (debt.projections || []).forEach((p) => {
      if (p?.version_source_month) keys.add(p.version_source_month);
    });
    (debt.projection_months || []).forEach((month) => {
      if (month) keys.add(month);
    });
  });
  return [...keys].sort();
};

const pickBestMonthKey = (monthKeys, referenceDate = new Date()) => {
  if (!monthKeys.length) return null;
  const todayKey = `${referenceDate.getFullYear()}-${String(referenceDate.getMonth() + 1).padStart(2, '0')}`;
  return (
    monthKeys.find((key) => key >= todayKey) ||
    monthKeys.filter((key) => key <= todayKey).pop() ||
    monthKeys[0]
  );
};

export default function DebtManager({ canEdit, isAdmin = false, mode = 'debts' }) {
  const isDebtMode = mode === 'debts';
  const viewLabel = isDebtMode ? 'deudas' : 'presupuesto';
  const now = new Date();
  const chartColors = ['#6366F1', '#8B5CF6', '#EC4899', '#F59E0B', '#22C55E', '#3B82F6', '#EF4444'];
  const [debts, setDebts] = useState([]);
  const [categories, setCategories] = useState([]);
  const [newModalOpen, setNewModalOpen] = useState(false);
  const [showCSVImport, setShowCSVImport] = useState(false);
  const [showCloneMonth, setShowCloneMonth] = useState(false);
  const [cloneSourceMonth, setCloneSourceMonth] = useState(new Date().getMonth() + 1);
  const [cloneSourceYear, setCloneSourceYear] = useState(new Date().getFullYear());
  const [isCloning, setIsCloning] = useState(false);
  const [editModalOpen, setEditModalOpen] = useState(false);
  const [debtToEdit, setDebtToEdit] = useState(null);
  const [paymentModal, setPaymentModal] = useState({
    open: false,
    debt: null,
    payments: [],
    paymentsLoading: false,
  });
  const [paymentForm, setPaymentForm] = useState({ payment_date: '', amount: '', notes: '' });
  const [paymentSubmitting, setPaymentSubmitting] = useState(false);
  const [deleteDialog, setDeleteDialog] = useState({ isOpen: false, debtId: null, debtName: '' });
  const [bulkDeleteDialogOpen, setBulkDeleteDialogOpen] = useState(false);
  const [selectedDebtIds, setSelectedDebtIds] = useState([]);
  const [isBulkDeleting, setIsBulkDeleting] = useState(false);
  const [showSelectedOnly, setShowSelectedOnly] = useState(false);
  const [filterFechaDesde, setFilterFechaDesde] = useState('');
  const [filterFechaHasta, setFilterFechaHasta] = useState('');
  const [filterTipo, setFilterTipo] = useState('all');
  const [filterCategoria, setFilterCategoria] = useState('all');
  const [filterTipoPresupuesto, setFilterTipoPresupuesto] = useState('all');
  const [filterDetalle, setFilterDetalle] = useState('');
  const [filterMontoMin, setFilterMontoMin] = useState('');
  const [filterMontoMax, setFilterMontoMax] = useState('');
  const [filterMonth, setFilterMonth] = useState(now.getMonth() + 1);
  const [filterYear, setFilterYear] = useState(now.getFullYear());
  const [monthFilterReady, setMonthFilterReady] = useState(false);
  const [debtsLoading, setDebtsLoading] = useState(true);
  const [sortField, setSortField] = useState('fecha');
  const [sortDirection, setSortDirection] = useState('desc');
  const [currentPage, setCurrentPage] = useState(1);
  const [lineageModal, setLineageModal] = useState({ open: false, loading: false, data: null, error: null });
  const PAGE_SIZE = 10;
  const toast = useToast();

  // Cargar deudas y resumen
  useEffect(() => {
    loadDebts();
    loadCategories();
  }, []);

  useEffect(() => {
    const existingIds = new Set(debts.map((debt) => debt.id));
    setSelectedDebtIds((prev) => prev.filter((id) => existingIds.has(id)));
  }, [debts]);

  const loadDebts = async () => {
    setDebtsLoading(true);
    try {
      const response = isDebtMode
        ? await debtRecordsAPI.getProjectedDebts()
        : await debtsAPI.getDebts();
      const loaded = response.data || [];
      setDebts(loaded);
      console.log(`✅ Loaded ${loaded.length} ${viewLabel} items from API`);
    } catch (error) {
      const detail = error?.response?.data?.detail;
      toast.error(
        detail
          ? `Error al cargar ${viewLabel}: ${typeof detail === 'string' ? detail : JSON.stringify(detail)}`
          : `Error al cargar ${viewLabel}`
      );
      console.error('Error loading debts:', error);
    } finally {
      setDebtsLoading(false);
    }
  };

  // Default to a month that actually has data when the current selection is empty.
  useEffect(() => {
    if (monthFilterReady) return;

    if (debts.length === 0) {
      setMonthFilterReady(true);
      return;
    }

    const selectedKey = `${filterYear}-${String(filterMonth).padStart(2, '0')}`;

    if (isDebtMode) {
      const monthKeys = collectProjectionMonths(debts);

      if (!monthKeys.some((key) => debts.some((debt) => debtHasProjectionMonth(debt, key)))) {
        setMonthFilterReady(true);
        return;
      }

      if (!debts.some((debt) => debtHasProjectionMonth(debt, selectedKey))) {
        const bestMonth = pickBestMonthKey(monthKeys, now);
        if (bestMonth) {
          const [yearPart, monthPart] = bestMonth.split('-');
          setFilterYear(parseInt(yearPart, 10));
          setFilterMonth(parseInt(monthPart, 10));
        }
      }
    } else {
      const monthKeys = collectBudgetMonths(debts);
      if (monthKeys.length > 0 && !monthKeys.includes(selectedKey)) {
        const bestMonth = pickBestMonthKey(monthKeys, now);
        if (bestMonth) {
          const [yearPart, monthPart] = bestMonth.split('-');
          setFilterYear(parseInt(yearPart, 10));
          setFilterMonth(parseInt(monthPart, 10));
        }
      }
    }

    setMonthFilterReady(true);
  }, [debts, filterMonth, filterYear, isDebtMode, monthFilterReady, now]);

  const loadCategories = async () => {
    try {
      const response = await transactionsAPI.getCategories();
      setCategories(response.data || []);
    } catch (error) {
      console.error('Error loading categories:', error);
      // Fallback defensivo para no bloquear el formulario
      setCategories(['Personal', 'Vivienda', 'Transporte', 'Educación', 'Salud', 'Otro']);
    }
  };

  const handleSort = (field) => {
    if (sortField === field) {
      setSortDirection(sortDirection === 'asc' ? 'desc' : 'asc');
    } else {
      setSortField(field);
      setSortDirection('desc');
    }
  };

  const handleEdit = (debt) => {
    if (!canEdit) return;
    
    setDebtToEdit(debt);
    setEditModalOpen(true);
  };

  const handleSaveEdit = async (updatedData) => {
    try {
      if (isDebtMode) {
        await debtRecordsAPI.updateDebt(debtToEdit.id, updatedData);
      } else {
        await debtsAPI.updateDebt(debtToEdit.id, updatedData);
      }
      toast.success(isDebtMode ? 'Deuda actualizada correctamente' : 'Item de presupuesto actualizado correctamente');
      setEditModalOpen(false);
      setDebtToEdit(null);
      loadDebts();
    } catch (error) {
      toast.error(isDebtMode ? 'Error al actualizar deuda' : 'Error al actualizar item de presupuesto');
      console.error('Error updating debt:', error);
    }
  };

  const handleCloseEditModal = () => {
    setEditModalOpen(false);
    setDebtToEdit(null);
  };

  const refreshPaymentModalDebt = async (debtId) => {
    const response = await debtRecordsAPI.getProjectedDebts();
    const updated = (response.data || []).find((item) => item.id === debtId);
    if (!updated) return null;
    const ctx = getDebtPaymentContext(updated);
    setPaymentModal((prev) => ({ ...prev, debt: updated }));
    setPaymentForm((prev) => ({
      ...prev,
      amount: ctx.defaultAmount > 0 ? formatArNumber(ctx.defaultAmount) : '',
    }));
    return updated;
  };

  const loadPaymentHistory = async (debtId) => {
    setPaymentModal((prev) => ({ ...prev, paymentsLoading: true }));
    try {
      const response = await debtRecordsAPI.getPayments(debtId);
      setPaymentModal((prev) => ({
        ...prev,
        paymentsLoading: false,
        payments: response.data || [],
      }));
    } catch (error) {
      setPaymentModal((prev) => ({ ...prev, paymentsLoading: false, payments: [] }));
      console.error('Error loading debt payments:', error);
    }
  };

  const openPaymentModal = (debt) => {
    const today = new Date().toISOString().slice(0, 10);
    const ctx = getDebtPaymentContext(debt);
    setPaymentModal({ open: true, debt, payments: [], paymentsLoading: true });
    setPaymentForm({
      payment_date: today,
      amount: ctx.defaultAmount > 0 ? formatArNumber(ctx.defaultAmount) : '',
      notes: '',
    });
    loadPaymentHistory(debt.id);
  };

  const closePaymentModal = () => {
    if (paymentSubmitting) return;
    setPaymentModal({ open: false, debt: null, payments: [], paymentsLoading: false });
    setPaymentForm({ payment_date: '', amount: '', notes: '' });
  };

  const handleDeleteDebtPayment = async (paymentId) => {
    if (!paymentModal.debt || paymentSubmitting) return;
    if (!window.confirm('¿Eliminar este pago y restaurar saldo/cuotas?')) return;

    setPaymentSubmitting(true);
    try {
      await debtRecordsAPI.deletePayment(paymentId);
      toast.success('Pago eliminado; deuda recalculada');
      await loadDebts();
      await loadPaymentHistory(paymentModal.debt.id);
      await refreshPaymentModalDebt(paymentModal.debt.id);
    } catch (error) {
      const detail = error?.response?.data?.detail || 'Error al eliminar pago';
      toast.error(detail);
    } finally {
      setPaymentSubmitting(false);
    }
  };

  const submitDebtPayment = async (e) => {
    e.preventDefault();
    if (!paymentModal.debt) return;

    const amount = parseArNumber(paymentForm.amount);
    if (!Number.isFinite(amount) || amount <= 0) {
      toast.warning('Ingrese un monto de pago válido');
      return;
    }

    const debt = paymentModal.debt;
    const { totalOutstanding } = getDebtPaymentContext(debt);
    if (totalOutstanding > 0 && amount - totalOutstanding > 0.000001) {
      toast.warning('El pago no puede superar el saldo total de la deuda');
      return;
    }

    setPaymentSubmitting(true);
    try {
      await debtRecordsAPI.addPayment(debt.id, {
        payment_date: paymentForm.payment_date || undefined,
        amount,
        notes: paymentForm.notes?.trim() || undefined,
      });
      toast.success('Pago registrado correctamente');
      await loadDebts();
      await loadPaymentHistory(debt.id);
      await refreshPaymentModalDebt(debt.id);
    } catch (error) {
      const detail = error?.response?.data?.detail || 'Error al registrar pago';
      toast.error(detail);
    } finally {
      setPaymentSubmitting(false);
    }
  };

  const handleDeleteClick = (debt) => {
    if (!canEdit) return;
    
    setDeleteDialog({
      isOpen: true,
      debtId: debt.id,
      debtName: debt.detalle || `Presupuesto de ${debt.monto_total}`
    });
  };

  const handleDeleteConfirm = async () => {
    try {
      if (isDebtMode) {
        await debtRecordsAPI.deleteDebt(deleteDialog.debtId);
      } else {
        await debtsAPI.deleteDebt(deleteDialog.debtId);
      }
      toast.success(isDebtMode ? 'Deuda eliminada correctamente' : 'Item de presupuesto eliminado correctamente');
      loadDebts();
    } catch (error) {
      if (error.response?.status === 400) {
        // Mostrar el mensaje específico del backend
        const message = error.response?.data?.detail || 'No se puede eliminar un item con transacciones asociadas';
        toast.error(message);
      } else {
        toast.error(isDebtMode ? 'Error al eliminar deuda' : 'Error al eliminar item');
      }
      console.error('Error deleting debt:', error);
    }
    setDeleteDialog({ isOpen: false, debtId: null, debtName: '' });
  };

  const toggleDebtSelection = (debtId) => {
    setSelectedDebtIds((prev) => {
      if (prev.includes(debtId)) {
        return prev.filter((id) => id !== debtId);
      }
      return [...prev, debtId];
    });
  };

  const toggleSelectAllDebts = () => {
    const visibleIds = paginatedDebts.map((debt) => debt.id);
    const allSelected = visibleIds.length > 0 && visibleIds.every((id) => selectedDebtIds.includes(id));

    if (allSelected) {
      setSelectedDebtIds((prev) => prev.filter((id) => !visibleIds.includes(id)));
      return;
    }

    setSelectedDebtIds((prev) => {
      const merged = new Set([...prev, ...visibleIds]);
      return Array.from(merged);
    });
  };

  const handleBulkDeleteConfirm = async () => {
    if (selectedDebtIds.length === 0) {
      setBulkDeleteDialogOpen(false);
      return;
    }

    setIsBulkDeleting(true);
    let deletedCount = 0;
    let blockedCount = 0;

    try {
      for (const debtId of selectedDebtIds) {
        try {
          if (isDebtMode) {
            await debtRecordsAPI.deleteDebt(debtId);
          } else {
            await debtsAPI.deleteDebt(debtId);
          }
          deletedCount += 1;
        } catch (error) {
          blockedCount += 1;
          console.error(`Error deleting debt ${debtId}:`, error);
        }
      }

      await loadDebts();
      setSelectedDebtIds([]);

      if (deletedCount > 0) {
        toast.success(
          isDebtMode
            ? `${deletedCount} deuda(s) eliminada(s)`
            : `${deletedCount} item(s) de presupuesto eliminado(s)`
        );
      }
      if (blockedCount > 0) {
        toast.warning(`${blockedCount} item(s) no se pudieron eliminar (pueden tener transacciones vinculadas)`);
      }
    } finally {
      setIsBulkDeleting(false);
      setBulkDeleteDialogOpen(false);
    }
  };

  const getStatusBadge = (status) => {
    const badges = {
      'PENDIENTE': 'bg-yellow-100 text-yellow-800',
      'Pago parcial': 'bg-blue-100 text-blue-800',
      'PAGADA': 'bg-green-100 text-green-800',
      'VENCIDA': 'bg-red-100 text-red-800'
    };
    return badges[status] || 'bg-gray-100 text-gray-800';
  };

  const getStatusText = (status) => {
    const statusTexts = {
      'PENDIENTE': 'Pendiente',
      'Pago parcial': 'Pago parcial',
      'PAGADA': 'Pagada',
      'VENCIDA': 'Vencida'
    };
    return statusTexts[status] || status;
  };

  const getProgressColor = (percentage) => {
    if (percentage >= 100) return 'bg-green-500';
    if (percentage >= 50) return 'bg-blue-500';
    return 'bg-yellow-500';
  };

  const formatCurrency = (amount) => formatArCurrency(amount);

  const handleExportDebtsCsv = () => {
    const rows = displayedDebts.map((debt) => {
      const montoEjecutado = Number(debt.monto_ejecutado ?? debt.monto_pagado ?? 0);
      const percentage = debt.monto_total > 0
        ? (montoEjecutado / Number(debt.monto_total || 0)) * 100
        : 0;

      return {
        id: debt.id,
        fecha: formatDate(debt.fecha),
        tipo: debt.tipo,
        categoria: debt.categoria,
        detalle: debt.detalle || '',
        monto_total: Number(debt.monto_total || 0),
        monto_ejecutado: montoEjecutado,
        monto_pendiente: Number(debt.monto_total || 0) - montoEjecutado,
        progreso_pct: Number(percentage.toFixed(2)),
        status: debt.status,
        fecha_vencimiento: debt.fecha_vencimiento ? formatDate(debt.fecha_vencimiento) : ''
      };
    });

    const exported = exportToCsv({
      filename: `presupuesto_${new Date().toISOString().split('T')[0]}.csv`,
      headers: ['id', 'fecha', 'tipo', 'categoria', 'detalle', 'monto_total', 'monto_pagado', 'monto_pendiente', 'progreso_pct', 'status', 'fecha_vencimiento'],
      rows
    });

    if (!exported) {
      toast.warning('No hay items de presupuesto para exportar');
    }
  };

  const handleCloneMonth = async () => {
    // Target is always the next month from source
    let targetMonth = cloneSourceMonth + 1;
    let targetYear = cloneSourceYear;
    if (targetMonth > 12) {
      targetMonth = 1;
      targetYear += 1;
    }

    setIsCloning(true);
    try {
      const response = await debtsAPI.cloneMonth(cloneSourceMonth, cloneSourceYear, targetMonth, targetYear);
      const data = response.data;
      toast.success(`${data.cloned_count} items clonados al mes ${targetMonth}/${targetYear}`);
      setShowCloneMonth(false);
      loadDebts();
    } catch (error) {
      const detail = error?.response?.data?.detail || error.message;
      toast.error(`Error al clonar: ${detail}`);
    } finally {
      setIsCloning(false);
    }
  };

  const openLineageModal = async (itemId) => {
    setLineageModal({ open: true, loading: true, data: null, error: null });
    try {
      const res = await debtsAPI.getCloneLineage(itemId);
      setLineageModal({ open: true, loading: false, data: res.data, error: null });
    } catch (error) {
      const detail = error?.response?.data?.detail || 'No se pudo cargar el linaje';
      setLineageModal({ open: true, loading: false, data: null, error: detail });
    }
  };

  const monthScopedDebts = useMemo(() => {
    const selectedKey = `${filterYear}-${String(filterMonth).padStart(2, '0')}`;
    if (!isDebtMode) {
      return debts.filter((debt) => {
        const key = getYearMonthKey(debt.fecha_vencimiento || debt.fecha);
        return key === selectedKey;
      });
    }

    return debts
      .map((debt) => {
        let projectionForMonth = debt.projection_by_month?.[selectedKey];
        if (!projectionForMonth && Array.isArray(debt.projections)) {
          projectionForMonth = debt.projections.find(
            (p) => p?.version_source_month === selectedKey
          );
        }

        if (!projectionForMonth) {
          return null;
        }

        return {
          ...debt,
          projection_id: projectionForMonth.id,
          projection_month_key: selectedKey,
          fecha: projectionForMonth.fecha || debt.fecha,
          fecha_vencimiento: projectionForMonth.fecha_vencimiento || debt.fecha_vencimiento || debt.fecha,
          monto_total: Number(projectionForMonth.monto_total ?? debt.estimated_payment ?? 0),
          monto_ejecutado: Number(projectionForMonth.monto_ejecutado ?? 0),
          monto_pagado: Number(projectionForMonth.monto_ejecutado ?? 0),
          estimated_payment: Number(projectionForMonth.monto_total ?? debt.estimated_payment ?? 0),
          status: projectionForMonth.status || debt.status,
          debt_quota_number: projectionForMonth.debt_quota_number,
          debt_total_quotas: projectionForMonth.debt_total_quotas ?? debt.total_installments,
        };
      })
      .filter(Boolean);
  }, [debts, filterMonth, filterYear, isDebtMode]);

  const availableYears = useMemo(() => {
    const years = new Set([
      now.getFullYear() - 2,
      now.getFullYear() - 1,
      now.getFullYear(),
      now.getFullYear() + 1,
      now.getFullYear() + 2,
    ]);

    if (isDebtMode) {
      collectProjectionMonths(debts).forEach((monthKey) => {
        const year = parseInt(String(monthKey).slice(0, 4), 10);
        if (!Number.isNaN(year)) years.add(year);
      });
    } else {
      collectBudgetMonths(debts).forEach((monthKey) => {
        const year = parseInt(String(monthKey).slice(0, 4), 10);
        if (!Number.isNaN(year)) years.add(year);
      });
    }

    return [...years].sort((a, b) => a - b);
  }, [debts, isDebtMode, now]);

  const projectionMonthKeys = useMemo(
    () => (isDebtMode ? collectProjectionMonths(debts) : []),
    [debts, isDebtMode]
  );

  const summary = useMemo(() => {
    return monthScopedDebts.reduce(
      (acc, debt) => {
        const montoTotal = Number(debt.monto_total || 0);
        const montoEjecutado = Number(debt.monto_ejecutado ?? debt.monto_pagado ?? 0);
        const remaining = Math.max(0, montoTotal - montoEjecutado);
        const isGasto = (debt.tipo_flujo || 'Gasto') === 'Gasto';

        acc.total_debts += 1;

        if (isGasto) {
          acc.total_estimated_payment += Number(debt.estimated_payment ?? debt.monto_total ?? 0);

          if (debt.status === 'VENCIDA') {
            acc.overdue_count += 1;
            acc.overdue_amount += remaining;
          } else if (debt.status !== 'PAGADA') {
            acc.pending_amount += remaining;
          }
        }

        return acc;
      },
      {
        total_debts: 0,
        total_estimated_payment: 0,
        pending_amount: 0,
        overdue_count: 0,
        overdue_amount: 0,
      }
    );
  }, [monthScopedDebts]);

  const monthIncomeTotal = useMemo(() => {
    return monthScopedDebts
      .filter((debt) => debt.tipo_flujo === 'Ingreso')
      .reduce((sum, debt) => sum + Number(debt.monto_total || 0), 0);
  }, [monthScopedDebts]);

  const displayedDebts = useMemo(() => {
    let filtered = [...monthScopedDebts];

    if (filterFechaDesde) {
      filtered = filtered.filter((debt) => toISODate(debt.fecha) >= filterFechaDesde);
    }

    if (filterFechaHasta) {
      filtered = filtered.filter((debt) => toISODate(debt.fecha) <= filterFechaHasta);
    }

    if (filterTipo !== 'all') {
      filtered = filtered.filter((debt) => debt.tipo === filterTipo);
    }

    if (filterCategoria !== 'all') {
      filtered = filtered.filter((debt) => debt.categoria === filterCategoria);
    }

    if (filterTipoPresupuesto !== 'all') {
      filtered = filtered.filter((debt) => debt.tipo_presupuesto === filterTipoPresupuesto);
    }

    if (filterDetalle.trim()) {
      const q = filterDetalle.trim().toLowerCase();
      filtered = filtered.filter((debt) => (debt.detalle || '').toLowerCase().includes(q));
    }

    if (filterMontoMin !== '') {
      const min = Number(filterMontoMin);
      if (!Number.isNaN(min)) {
        filtered = filtered.filter((debt) => Number(debt.monto_total || 0) >= min);
      }
    }

    if (filterMontoMax !== '') {
      const max = Number(filterMontoMax);
      if (!Number.isNaN(max)) {
        filtered = filtered.filter((debt) => Number(debt.monto_total || 0) <= max);
      }
    }

    if (showSelectedOnly) {
      filtered = filtered.filter((debt) => selectedDebtIds.includes(debt.id));
    }

    // Ordenar
    if (sortField === 'fecha') {
      filtered.sort((a, b) => {
        const dateA = new Date(toISODate(a.fecha));
        const dateB = new Date(toISODate(b.fecha));
        return sortDirection === 'asc' ? dateA - dateB : dateB - dateA;
      });
    }

    return filtered;
  }, [
    monthScopedDebts,
    selectedDebtIds,
    showSelectedOnly,
    filterFechaDesde,
    filterFechaHasta,
    filterTipo,
    filterCategoria,
    filterTipoPresupuesto,
    filterDetalle,
    filterMontoMin,
    filterMontoMax,
    sortField,
    sortDirection
  ]);

  const availableTipos = useMemo(() => {
    return [...new Set(debts.map((d) => d.tipo).filter(Boolean))].sort((a, b) => a.localeCompare(b));
  }, [debts]);

  const availableCategorias = useMemo(() => {
    return [...new Set(debts.map((d) => d.categoria).filter(Boolean))].sort((a, b) => a.localeCompare(b));
  }, [debts]);

  const budgetCategoryGastosData = useMemo(() => {
    const gastos = displayedDebts.filter(debt => debt.tipo_flujo === 'Gasto');
    const grouped = gastos.reduce((acc, debt) => {
      const category = debt.categoria || 'Sin categoría';
      acc[category] = (acc[category] || 0) + Number(debt.monto_total || 0);
      return acc;
    }, {});

    return {
      labels: Object.keys(grouped),
      datasets: [
        {
          label: 'Gastos por Categoría',
          data: Object.values(grouped),
          backgroundColor: chartColors,
          borderWidth: 2,
          borderColor: '#fff'
        }
      ]
    };
  }, [displayedDebts]);

  const budgetCategoryIngresosData = useMemo(() => {
    const ingresos = displayedDebts.filter(debt => debt.tipo_flujo === 'Ingreso');
    const grouped = ingresos.reduce((acc, debt) => {
      const tipoIngreso = debt.tipo || 'Sin clasificar';
      acc[tipoIngreso] = (acc[tipoIngreso] || 0) + Number(debt.monto_total || 0);
      return acc;
    }, {});

    return {
      labels: Object.keys(grouped),
      datasets: [
        {
          label: 'Ingresos por Tipo',
          data: Object.values(grouped),
          backgroundColor: ['#10b981', '#34d399', '#6ee7b7', '#a7f3d0', '#d1fae5', '#ecfdf5'],
          borderWidth: 2,
          borderColor: '#fff'
        }
      ]
    };
  }, [displayedDebts]);

  const asignacionPorFechaData = useMemo(() => {
    const fechas = {};
    
    displayedDebts.forEach(debt => {
      const dateKey = toISODate(debt.fecha_vencimiento || debt.fecha);
      if (!fechas[dateKey]) {
        fechas[dateKey] = { gastos: 0, ingresos: 0 };
      }
      const amount = Number(debt.monto_total || 0);
      if (debt.tipo_flujo === 'Gasto') {
        fechas[dateKey].gastos += amount;
      } else if (debt.tipo_flujo === 'Ingreso') {
        fechas[dateKey].ingresos += amount;
      }
    });

    const sortedDates = Object.keys(fechas).sort();

    return {
      labels: sortedDates.map((date) => formatDate(date)),
      datasets: [
        {
          label: 'Gastos',
          data: sortedDates.map((date) => fechas[date].gastos),
          backgroundColor: '#ef4444',
          borderColor: '#dc2626',
          borderWidth: 1
        },
        {
          label: 'Ingresos',
          data: sortedDates.map((date) => fechas[date].ingresos),
          backgroundColor: '#10b981',
          borderColor: '#059669',
          borderWidth: 1
        }
      ]
    };
  }, [displayedDebts]);

  const vencimientosData = useMemo(() => {
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    
    const vencidos = [];
    const proximos = [];
    
    // Filtrar solo Gastos (los Ingresos no tienen estado "Vencida")
    const gastosDebts = displayedDebts.filter(debt => debt.tipo_flujo === 'Gasto');
    
    gastosDebts.forEach(debt => {
      if (debt.status === 'PAGADA') return;
      
      const fechaVenc = new Date(debt.fecha_vencimiento || debt.fecha);
      fechaVenc.setHours(0, 0, 0, 0);
      
      const diffDays = Math.floor((fechaVenc - today) / (1000 * 60 * 60 * 24));
      
      if (diffDays < 0) {
        vencidos.push({ ...debt, diasVencido: Math.abs(diffDays) });
      } else if (diffDays <= 7) {
        proximos.push({ ...debt, diasRestantes: diffDays });
      }
    });
    
    return {
      vencidos: vencidos.sort((a, b) => b.diasVencido - a.diasVencido),
      proximos: proximos.sort((a, b) => a.diasRestantes - b.diasRestantes)
    };
  }, [displayedDebts]);

  const totalPorPagar = summary
    ? Number(summary.total_estimated_payment || 0)
    : 0;

  const totalPages = Math.max(1, Math.ceil(displayedDebts.length / PAGE_SIZE));
  const paginatedDebts = useMemo(() => {
    const start = (currentPage - 1) * PAGE_SIZE;
    return displayedDebts.slice(start, start + PAGE_SIZE);
  }, [displayedDebts, currentPage]);

  // Reset page when filters change
  useEffect(() => {
    setCurrentPage(1);
  }, [filterMonth, filterYear, filterFechaDesde, filterFechaHasta, filterTipo, filterCategoria, filterTipoPresupuesto, filterDetalle, filterMontoMin, filterMontoMax, showSelectedOnly, sortField, sortDirection]);

  const allDebtsSelected = paginatedDebts.length > 0 && paginatedDebts.every((debt) => selectedDebtIds.includes(debt.id));

  return (
    <div className="space-y-6">
      {/* Selector de Mes/Año */}
      <div className="bg-white rounded-xl shadow-md p-4 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <button
            onClick={() => {
              if (filterMonth === 1) {
                setFilterMonth(12);
                setFilterYear(prev => prev - 1);
              } else {
                setFilterMonth(prev => prev - 1);
              }
            }}
            className="p-2 rounded-lg hover:bg-gray-100 transition text-lg"
          >
            ◀
          </button>
          <div className="flex items-center gap-2">
            <select
              value={filterMonth}
              onChange={(e) => setFilterMonth(parseInt(e.target.value))}
              className="px-3 py-2 border border-gray-200 rounded-lg text-finly-text font-semibold focus:ring-2 focus:ring-finly-primary focus:outline-none"
            >
              {MONTH_NAMES.map((name, idx) => (
                <option key={idx + 1} value={idx + 1}>{name}</option>
              ))}
            </select>
            <select
              value={filterYear}
              onChange={(e) => setFilterYear(parseInt(e.target.value))}
              className="px-3 py-2 border border-gray-200 rounded-lg text-finly-text font-semibold focus:ring-2 focus:ring-finly-primary focus:outline-none"
            >
              {availableYears.map(y => (
                <option key={y} value={y}>{y}</option>
              ))}
            </select>
          </div>
          <button
            onClick={() => {
              if (filterMonth === 12) {
                setFilterMonth(1);
                setFilterYear(prev => prev + 1);
              } else {
                setFilterMonth(prev => prev + 1);
              }
            }}
            className="p-2 rounded-lg hover:bg-gray-100 transition text-lg"
          >
            ▶
          </button>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-lg font-bold text-finly-text">
            📅 {MONTH_NAMES[filterMonth - 1]} {filterYear}
          </span>
          {(filterMonth !== now.getMonth() + 1 || filterYear !== now.getFullYear()) && (
            <button
              onClick={() => { setFilterMonth(now.getMonth() + 1); setFilterYear(now.getFullYear()); }}
              className="text-xs px-3 py-1 bg-finly-primary text-white rounded-full hover:bg-finly-primaryHover transition"
            >
              Hoy
            </button>
          )}
        </div>
      </div>

      {isDebtMode && !debtsLoading && debts.length > 0 && monthScopedDebts.length === 0 && (
        <div className="bg-amber-50 border border-amber-200 text-amber-900 rounded-xl px-4 py-3 text-sm">
          Tienes {debts.length} deuda(s) registrada(s), pero ninguna con cuota en{' '}
          <strong>{MONTH_NAMES[filterMonth - 1]} {filterYear}</strong>.
          {projectionMonthKeys.length > 0 && (
            <>
              {' '}Prueba otro mes (por ejemplo{' '}
              <button
                type="button"
                className="underline font-semibold"
                onClick={() => {
                  const bestMonth = pickBestMonthKey(projectionMonthKeys, now);
                  if (!bestMonth) return;
                  const [yearPart, monthPart] = bestMonth.split('-');
                  setFilterYear(parseInt(yearPart, 10));
                  setFilterMonth(parseInt(monthPart, 10));
                }}
              >
                {(() => {
                  const bestMonth = pickBestMonthKey(projectionMonthKeys, now);
                  if (!bestMonth) return 'un mes con cuotas';
                  const [yearPart, monthPart] = bestMonth.split('-');
                  return `${MONTH_NAMES[parseInt(monthPart, 10) - 1]} ${yearPart}`;
                })()}
              </button>
              ).
            </>
          )}
        </div>
      )}

      {!isDebtMode && !debtsLoading && debts.length > 0 && monthScopedDebts.length === 0 && (
        <div className="bg-amber-50 border border-amber-200 text-amber-900 rounded-xl px-4 py-3 text-sm">
          Hay {debts.length} ítem(s) de presupuesto cargados, pero ninguno corresponde a{' '}
          <strong>{MONTH_NAMES[filterMonth - 1]} {filterYear}</strong>.
          {collectBudgetMonths(debts).length > 0 && (
            <>
              {' '}Prueba otro mes (por ejemplo{' '}
              <button
                type="button"
                className="underline font-semibold"
                onClick={() => {
                  const bestMonth = pickBestMonthKey(collectBudgetMonths(debts), now);
                  if (!bestMonth) return;
                  const [yearPart, monthPart] = bestMonth.split('-');
                  setFilterYear(parseInt(yearPart, 10));
                  setFilterMonth(parseInt(monthPart, 10));
                }}
              >
                {(() => {
                  const bestMonth = pickBestMonthKey(collectBudgetMonths(debts), now);
                  if (!bestMonth) return 'un mes con datos';
                  const [yearPart, monthPart] = bestMonth.split('-');
                  return `${MONTH_NAMES[parseInt(monthPart, 10) - 1]} ${yearPart}`;
                })()}
              </button>
              ).
            </>
          )}
        </div>
      )}

      {/* Resumen de deudas / presupuesto */}
      {!debtsLoading && (
        <div className="grid grid-cols-1 md:grid-cols-5 gap-4">
          <div className="bg-white p-4 rounded-lg shadow-sm border">
            <p className="text-sm text-gray-600">{isDebtMode ? 'Deudas del Mes' : 'Presupuesto del Mes'}</p>
            <p className="text-2xl font-bold text-gray-900">{summary.total_debts}</p>
          </div>
          <div className="bg-white p-4 rounded-lg shadow-sm border">
            <p className="text-sm text-gray-600">Ingresos Presupuestados</p>
            <p className="text-2xl font-bold text-green-600">{formatCurrency(monthIncomeTotal)}</p>
          </div>
          <div className="bg-white p-4 rounded-lg shadow-sm border">
            <p className="text-sm text-gray-600">Total Estimado a Pagar</p>
            <p className="text-2xl font-bold text-blue-600">{formatCurrency(totalPorPagar)}</p>
          </div>
          <div className="bg-white p-4 rounded-lg shadow-sm border">
            <p className="text-sm text-gray-600">Pendiente no vencido</p>
            <p className="text-2xl font-bold text-yellow-600">{formatCurrency(summary.pending_amount)}</p>
          </div>
          <div className="bg-white p-4 rounded-lg shadow-sm border">
            <p className="text-sm text-gray-600">Vencidas</p>
            <p className="text-2xl font-bold text-red-600">{summary.overdue_count}</p>
            <p className="text-sm text-red-500 mt-1">{formatCurrency(summary.overdue_amount)}</p>
          </div>
        </div>
      )}

      {/* Gráficos reutilizados de Reportes para Presupuestos */}
      {!debtsLoading && displayedDebts.length > 0 && !showCSVImport && (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          {/* Gráfica de Gastos por Categoría */}
          <div className="bg-white rounded-xl shadow-md p-6">
              <h3 className="text-lg font-bold text-finly-text mb-4">
                💸 Gastos por Categoría
              </h3>
              <div className="h-80 flex items-center justify-center">
                <Pie
                  data={budgetCategoryGastosData}
                  options={{
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                      legend: {
                        position: 'bottom'
                      },
                      tooltip: {
                        callbacks: {
                          label: function(context) {
                            const label = context.label || '';
                            const value = context.parsed || 0;
                            return label + ': $' + value.toLocaleString('es-AR');
                          }
                        }
                      }
                    }
                  }}
                />
              </div>
            </div>

          {/* Gráfica de Ingresos por Tipo */}
          <div className="bg-white rounded-xl shadow-md p-6">
              <h3 className="text-lg font-bold text-finly-text mb-4">
                💰 Ingresos por Tipo
              </h3>
              <div className="h-80 flex items-center justify-center">
                <Pie
                  data={budgetCategoryIngresosData}
                  options={{
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                      legend: {
                        position: 'bottom'
                      },
                      tooltip: {
                        callbacks: {
                          label: function(context) {
                            const label = context.label || '';
                            const value = context.parsed || 0;
                            return label + ': $' + value.toLocaleString('es-AR');
                          }
                        }
                      }
                    }
                  }}
                />
              </div>
            </div>

          {/* Gráfica de Asignación por Fecha - OCULTO */}
          {false && (
          <div className="bg-white rounded-xl shadow-md p-6">
            <h3 className="text-lg font-bold text-finly-text mb-4">
              📊 Asignación por Fecha de Vencimiento
            </h3>
            <div className="h-80">
              <Bar
                data={asignacionPorFechaData}
                options={{
                  responsive: true,
                  maintainAspectRatio: false,
                  plugins: {
                    legend: {
                      position: 'top'
                    },
                    tooltip: {
                      callbacks: {
                        label: function(context) {
                          return context.dataset.label + ': $' + context.parsed.y.toLocaleString('es-AR');
                        }
                      }
                    }
                  },
                  scales: {
                    y: {
                      beginAtZero: true,
                      stacked: false,
                      ticks: {
                        callback: function(value) {
                          return '$' + value.toLocaleString('es-AR');
                        }
                      }
                    },
                    x: {
                      stacked: false
                    }
                  }
                }}
              />
            </div>
          </div>
          )}

          {/* Panel de Vencimientos */}
          <div className="bg-white rounded-xl shadow-md p-6">
            <h3 className="text-lg font-bold text-finly-text mb-4">
              ⏰ Vencimientos
            </h3>
            <div className="h-80 overflow-y-auto space-y-4">
              {/* Items Vencidos */}
              {vencimientosData.vencidos.length > 0 && (
                <div>
                  <h4 className="text-sm font-semibold text-red-600 mb-2 flex items-center gap-2">
                    🔴 Vencidos ({vencimientosData.vencidos.length})
                  </h4>
                  <div className="space-y-2">
                    {vencimientosData.vencidos.slice(0, 5).map(debt => (
                      <div 
                        key={debt.id} 
                        onClick={() => handleEdit(debt)}
                        className="bg-red-50 p-2 rounded border-l-4 border-red-500 cursor-pointer hover:bg-red-100 transition-colors"
                      >
                        <div className="text-xs font-semibold text-gray-900">{debt.detalle || debt.tipo}</div>
                        <div className="text-xs text-red-600">Hace {debt.diasVencido} días</div>
                        <div className="text-xs font-bold text-gray-700">{formatCurrency(debt.monto_total)}</div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Items Por Vencer (próximos 7 días) */}
              {vencimientosData.proximos.length > 0 && (
                <div>
                  <h4 className="text-sm font-semibold text-yellow-600 mb-2 flex items-center gap-2">
                    🟡 Próximos 7 días ({vencimientosData.proximos.length})
                  </h4>
                  <div className="space-y-2">
                    {vencimientosData.proximos.slice(0, 5).map(debt => (
                      <div 
                        key={debt.id} 
                        onClick={() => handleEdit(debt)}
                        className="bg-yellow-50 p-2 rounded border-l-4 border-yellow-500 cursor-pointer hover:bg-yellow-100 transition-colors"
                      >
                        <div className="text-xs font-semibold text-gray-900">{debt.detalle || debt.tipo}</div>
                        <div className="text-xs text-yellow-600">
                          {debt.diasRestantes === 0 ? 'Hoy' : `En ${debt.diasRestantes} días`}
                        </div>
                        <div className="text-xs font-bold text-gray-700">{formatCurrency(debt.monto_total)}</div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Mensaje si no hay vencimientos */}
              {vencimientosData.vencidos.length === 0 && vencimientosData.proximos.length === 0 && (
                <div className="text-center text-gray-500 py-8">
                  <p className="text-sm">✅ No hay items vencidos ni por vencer</p>
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Acciones */}
      {!showCSVImport && (
        <div className="flex flex-wrap gap-3">
          {canEdit && (
            <>
              <button
                onClick={() => setNewModalOpen(true)}
                className="bg-blue-600 text-white px-4 py-2 rounded-lg hover:bg-blue-700 transition-colors"
              >
                {isDebtMode ? '+ Nueva Deuda' : '+ Nuevo Item'}
              </button>
              {!isDebtMode && (
                <button
                  onClick={() => setShowCSVImport(true)}
                  className="bg-green-600 text-white px-4 py-2 rounded-lg hover:bg-green-700 transition-colors flex items-center space-x-2"
                >
                  <span>📥</span>
                  <span>Importar CSV</span>
                </button>
              )}
              {!isDebtMode && (
                <button
                  onClick={() => setShowCloneMonth(true)}
                  className="bg-purple-600 text-white px-4 py-2 rounded-lg hover:bg-purple-700 transition-colors flex items-center space-x-2"
                >
                  <span>📋</span>
                  <span>Clonar Mes</span>
                </button>
              )}
            </>
          )}
          <button
            onClick={handleExportDebtsCsv}
            disabled={debts.length === 0}
            className="bg-emerald-600 text-white px-4 py-2 rounded-lg hover:bg-emerald-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors flex items-center space-x-2"
          >
            <span>📤</span>
            <span>Exportar CSV</span>
          </button>
          {canEdit && (
            <label className="text-sm text-gray-600 inline-flex items-center gap-2 px-2">
              <input
                type="checkbox"
                checked={showSelectedOnly}
                onChange={(e) => setShowSelectedOnly(e.target.checked)}
              />
              Mostrar solo seleccionados
            </label>
          )}
          {canEdit && (
            <button
              onClick={() => setBulkDeleteDialogOpen(true)}
              disabled={selectedDebtIds.length === 0 || isBulkDeleting}
              className="bg-red-600 text-white px-4 py-2 rounded-lg hover:bg-red-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              Eliminar seleccionados ({selectedDebtIds.length})
            </button>
          )}
        </div>
      )}

      {/* Importación CSV */}
      {!isDebtMode && showCSVImport && (
        <div className="bg-gray-50 rounded-lg p-4">
          <div className="flex justify-between items-center mb-4">
            <h3 className="text-lg font-semibold">Importar Presupuestos desde CSV</h3>
            <button
              onClick={() => setShowCSVImport(false)}
              className="text-gray-600 hover:text-gray-800"
            >
              ✕ Cerrar
            </button>
          </div>
          <BudgetCSVImport
            onImportSuccess={() => {
              setShowCSVImport(false);
              loadDebts();
            }}
          />
        </div>
      )}

      {/* Modal Clonar Mes */}
      {!isDebtMode && showCloneMonth && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-xl shadow-2xl max-w-md w-full p-6">
            <div className="flex justify-between items-center mb-4">
              <h3 className="text-lg font-bold text-gray-900">📋 Clonar Presupuesto</h3>
              <button
                onClick={() => setShowCloneMonth(false)}
                className="text-gray-400 hover:text-gray-600"
              >
                ✕
              </button>
            </div>
            <p className="text-sm text-gray-600 mb-4">
              Clona todos los items del mes seleccionado al mes siguiente, con montos ejecutados en 0.
            </p>
            <div className="grid grid-cols-2 gap-4 mb-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Mes origen</label>
                <select
                  value={cloneSourceMonth}
                  onChange={(e) => setCloneSourceMonth(parseInt(e.target.value))}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm"
                >
                  {[1,2,3,4,5,6,7,8,9,10,11,12].map(m => (
                    <option key={m} value={m}>
                      {['Enero','Febrero','Marzo','Abril','Mayo','Junio','Julio','Agosto','Septiembre','Octubre','Noviembre','Diciembre'][m-1]}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Año origen</label>
                <input
                  type="number"
                  min="2020"
                  max="2100"
                  value={cloneSourceYear}
                  onChange={(e) => setCloneSourceYear(parseInt(e.target.value))}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm"
                />
              </div>
            </div>
            <div className="bg-blue-50 border border-blue-200 rounded-lg p-3 mb-4">
              <p className="text-sm text-blue-800">
                Se clonarán al mes: <strong>
                  {['Enero','Febrero','Marzo','Abril','Mayo','Junio','Julio','Agosto','Septiembre','Octubre','Noviembre','Diciembre'][(cloneSourceMonth % 12)]}
                  {' '}{cloneSourceMonth === 12 ? cloneSourceYear + 1 : cloneSourceYear}
                </strong>
              </p>
            </div>
            <div className="flex gap-3">
              <button
                onClick={() => setShowCloneMonth(false)}
                className="flex-1 px-4 py-2 border border-gray-300 rounded-lg text-gray-700 hover:bg-gray-50 transition"
              >
                Cancelar
              </button>
              <button
                onClick={handleCloneMonth}
                disabled={isCloning}
                className="flex-1 px-4 py-2 bg-purple-600 text-white rounded-lg hover:bg-purple-700 disabled:opacity-50 transition"
              >
                {isCloning ? 'Clonando...' : 'Clonar'}
              </button>
            </div>
          </div>
        </div>
      )}

      {(!showCSVImport || isDebtMode) && (
        <div className="bg-white rounded-lg shadow-sm border p-4">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-semibold text-gray-700">{isDebtMode ? 'Filtros de Deudas' : 'Filtros de Presupuesto'}</h3>
            <button
              onClick={() => {
                setFilterFechaDesde('');
                setFilterFechaHasta('');
                setFilterTipo('all');
                setFilterCategoria('all');
                setFilterTipoPresupuesto('all');
                setFilterDetalle('');
                setFilterMontoMin('');
                setFilterMontoMax('');
              }}
              className="text-sm text-blue-600 hover:text-blue-800"
            >
              Limpiar filtros
            </button>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-8 gap-3">
            <div>
              <label className="block text-xs text-gray-500 mb-1">Fecha desde</label>
              <input
                type="date"
                value={filterFechaDesde}
                onChange={(e) => setFilterFechaDesde(e.target.value)}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm"
              />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">Fecha hasta</label>
              <input
                type="date"
                value={filterFechaHasta}
                onChange={(e) => setFilterFechaHasta(e.target.value)}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm"
              />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">Tipo</label>
              <select
                value={filterTipo}
                onChange={(e) => setFilterTipo(e.target.value)}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm"
              >
                <option value="all">Todos</option>
                {availableTipos.map((tipo) => (
                  <option key={tipo} value={tipo}>{tipo}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">Categoría</label>
              <select
                value={filterCategoria}
                onChange={(e) => setFilterCategoria(e.target.value)}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm"
              >
                <option value="all">Todas</option>
                {availableCategorias.map((categoria) => (
                  <option key={categoria} value={categoria}>{categoria}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">Presupuesto</label>
              <select
                value={filterTipoPresupuesto}
                onChange={(e) => setFilterTipoPresupuesto(e.target.value)}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm"
              >
                <option value="all">Todos</option>
                <option value="OBLIGATION">🔴 Obligación</option>
                <option value="VARIABLE">🔵 Variable</option>
              </select>
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">Detalle</label>
              <input
                type="text"
                value={filterDetalle}
                onChange={(e) => setFilterDetalle(e.target.value)}
                placeholder="Buscar texto"
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm"
              />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">Monto mín.</label>
              <input
                type="number"
                min="0"
                step="0.01"
                value={filterMontoMin}
                onChange={(e) => setFilterMontoMin(e.target.value)}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm"
              />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">Monto máx.</label>
              <input
                type="number"
                min="0"
                step="0.01"
                value={filterMontoMax}
                onChange={(e) => setFilterMontoMax(e.target.value)}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm"
              />
            </div>
          </div>
        </div>
      )}

      {/* Lista de deudas / presupuesto */}
      <div className="bg-white rounded-lg shadow-md border overflow-hidden relative">
        {debtsLoading && <LoadingOverlay label={`Cargando ${viewLabel}...`} />}
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                {canEdit && (
                  <th className="px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase">
                    <input
                      type="checkbox"
                      checked={allDebtsSelected}
                      onChange={toggleSelectAllDebts}
                      aria-label={`Seleccionar todos los items de ${viewLabel}`}
                    />
                  </th>
                )}
                <th 
                  className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase cursor-pointer hover:bg-gray-100 select-none"
                  onClick={() => handleSort('fecha')}
                  title="Ordenar por fecha"
                >
                  <div className="flex items-center gap-1">
                    <span>Fecha</span>
                    {sortField === 'fecha' && (
                      <span className="text-blue-600">
                        {sortDirection === 'asc' ? '↑' : '↓'}
                      </span>
                    )}
                  </div>
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Tipo</th>
                <th className="px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase">Presupuesto</th>
                <th className="px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase">Flujo</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Categoría</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Detalle</th>
                <th className="px-4 py-3 text-right text-xs font-medium text-gray-500 uppercase">Monto Total</th>
                <th className="px-4 py-3 text-right text-xs font-medium text-gray-500 uppercase">Monto a Pagar</th>
                <th className="px-4 py-3 text-right text-xs font-medium text-gray-500 uppercase">Ejecutado</th>
                <th className="px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase">Progreso</th>
                <th className="px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase">Estado</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Vencimiento</th>
                {canEdit && <th className="px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase">Acciones</th>}
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200">
              {paginatedDebts.length === 0 ? (
                <tr>
                  <td colSpan={canEdit ? "14" : "13"} className="px-4 py-8 text-center text-gray-500">
                    {!debtsLoading && (
                      debts.length === 0
                        ? (isDebtMode ? 'No hay deudas registradas' : 'No hay items de presupuesto registrados')
                        : isDebtMode
                          ? `No hay cuotas en ${MONTH_NAMES[filterMonth - 1]} ${filterYear}. Cambia el mes arriba.`
                          : 'No hay resultados para los filtros aplicados'
                    )}
                  </td>
                </tr>
              ) : (
                paginatedDebts.map((debt) => {
                  const montoEjecutado = debt.monto_ejecutado ?? debt.monto_pagado ?? 0;
                  const cuotaActual = Number(debt.current_installment || 0);
                  const totalCuotas = Number(debt.total_installments || 0);
                  const percentage = debt.monto_total > 0
                    ? Math.min(100, (montoEjecutado / debt.monto_total) * 100)
                    : 0;
                  const remaining = Math.max(0, debt.monto_total - montoEjecutado);
                  const rowKey = isDebtMode && debt.projection_id
                    ? `proj-${debt.projection_id}`
                    : debt.id;
                  const tipoPresupuesto = debt.tipo_presupuesto || 'OBLIGATION';
                  const tipoFlujo = debt.tipo_flujo || 'Gasto';

                  return (
                    <tr key={rowKey} className="hover:bg-gray-50">
                      {canEdit && (
                        <td className="px-4 py-3 text-center">
                          <input
                            type="checkbox"
                            checked={selectedDebtIds.includes(debt.id)}
                            onChange={() => toggleDebtSelection(debt.id)}
                            aria-label={`Seleccionar ${viewLabel} ${debt.id}`}
                          />
                        </td>
                      )}
                      <td className="px-4 py-3 text-sm text-gray-900">{formatDate(debt.fecha)}</td>
                      <td className="px-4 py-3 text-sm text-gray-600">{debt.tipo}</td>
                      <td className="px-4 py-3 text-center">
                        <span className={`px-2 py-1 text-xs font-semibold rounded-full ${
                          tipoPresupuesto === 'OBLIGATION' 
                            ? 'bg-purple-100 text-purple-800' 
                            : 'bg-cyan-100 text-cyan-800'
                        }`}>
                          {tipoPresupuesto === 'OBLIGATION' ? 'Obligación' : 'Variable'}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-center">
                        <span className={`px-2 py-1 text-xs font-semibold rounded-full ${
                          tipoFlujo === 'Gasto' 
                            ? 'bg-red-100 text-red-800' 
                            : 'bg-green-100 text-green-800'
                        }`}>
                          {tipoFlujo === 'Gasto' ? '💸 Gasto' : '💰 Ingreso'}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-sm text-gray-600">{debt.categoria}</td>
                      <td className="px-4 py-3 text-sm text-gray-600">
                        <div className="flex items-center gap-2">
                          <span>{debt.detalle || '-'}</span>
                          {debt.cloned_from_item_id && (
                            <button
                              onClick={() => openLineageModal(debt.id)}
                              className="inline-flex items-center gap-1 px-2 py-0.5 text-[11px] rounded-full bg-indigo-100 text-indigo-700 hover:bg-indigo-200"
                              title={`Clonado desde #${debt.cloned_from_item_id}`}
                            >
                              🧬 Clonado
                            </button>
                          )}
                        </div>
                      </td>
                      <td className="px-4 py-3 text-sm text-right font-medium text-gray-900">
                        {formatCurrency(debt.monto_total)}
                      </td>
                      <td className="px-4 py-3 text-sm text-right text-purple-600">
                        {formatCurrency(debt.estimated_payment != null ? debt.estimated_payment : debt.monto_total)}
                      </td>
                      <td className="px-4 py-3 text-sm text-right text-blue-600">
                        {formatCurrency(montoEjecutado)}
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex flex-col items-center gap-1">
                          <div className="w-full bg-gray-200 rounded-full h-2">
                            <div
                              className={`h-2 rounded-full transition-all ${getProgressColor(percentage)}`}
                              style={{ width: `${Math.min(percentage, 100)}%` }}
                            />
                          </div>
                          <span className="text-xs text-gray-600">
                            {percentage.toFixed(0)}%
                          </span>
                          <span className="text-xs text-gray-500">
                            Resta: {formatCurrency(remaining)}
                          </span>
                        </div>
                      </td>
                      <td className="px-4 py-3 text-center">
                        <span className={`px-2 py-1 text-xs font-semibold rounded-full ${getStatusBadge(debt.status)}`}>
                          {getStatusText(debt.status)}
                          {(debt.debt_quota_number ?? debt.total_installments) > 0 && (
                            ` ${debt.debt_quota_number ?? cuotaActual}/${debt.debt_total_quotas ?? totalCuotas}`
                          )}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-sm text-gray-600">
                        {debt.fecha_vencimiento ? formatDate(debt.fecha_vencimiento) : '-'}
                      </td>
                      {canEdit && (
                        <td className="px-4 py-3 text-center">
                          <div className="flex gap-2 justify-center">
                            {isDebtMode && debt.status !== 'PAGADA' && (
                              <button
                                onClick={() => openPaymentModal(debt)}
                                className="text-emerald-600 hover:text-emerald-800 text-sm font-medium"
                              >
                                Pagar
                              </button>
                            )}
                            <button
                              onClick={() => handleEdit(debt)}
                              className="text-blue-600 hover:text-blue-800 text-sm font-medium"
                            >
                              Editar
                            </button>
                            <button
                              onClick={() => handleDeleteClick(debt)}
                              className="text-red-600 hover:text-red-800 text-sm font-medium"
                            >
                              Eliminar
                            </button>
                          </div>
                        </td>
                      )}
                    </tr>
                  );
                 })
                )}
                {paymentModal.open && paymentModal.debt && (
                  <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
                    <div className="bg-white rounded-xl shadow-2xl max-w-md w-full p-6">
                      <div className="flex justify-between items-center mb-4">
                        <h3 className="text-lg font-bold text-gray-900">Registrar Pago</h3>
                        <button
                          onClick={closePaymentModal}
                          className="text-gray-400 hover:text-gray-600"
                          disabled={paymentSubmitting}
                        >
                          ✕
                        </button>
                      </div>

                      <p className="text-sm text-gray-600 mb-4">
                        {paymentModal.debt.detalle || paymentModal.debt.debt_name}
                      </p>
                      {(() => {
                        const ctx = getDebtPaymentContext(paymentModal.debt);
                        return (
                          <div className="text-sm text-gray-700 mb-4 space-y-1">
                            <p>
                              Cuota actual: <strong>{formatCurrency(ctx.installmentDue)}</strong>
                              {ctx.installmentRemaining < ctx.installmentDue && (
                                <span className="text-gray-500"> — resta {formatCurrency(ctx.installmentRemaining)}</span>
                              )}
                            </p>
                            <p>
                              Saldo total deuda: <strong>{formatCurrency(ctx.totalOutstanding)}</strong>
                            </p>
                          </div>
                        );
                      })()}

                      {paymentModal.payments.length > 0 && (
                        <div className="mb-4 rounded-lg border border-gray-200 bg-gray-50 p-3">
                          <p className="text-xs font-semibold text-gray-600 mb-2">Pagos registrados</p>
                          <ul className="space-y-2 max-h-32 overflow-y-auto">
                            {paymentModal.payments.map((payment) => (
                              <li key={payment.id} className="flex items-center justify-between text-sm gap-2">
                                <span className="text-gray-700">
                                  {formatDate(payment.payment_date)} — {formatCurrency(payment.amount)}
                                </span>
                                <button
                                  type="button"
                                  onClick={() => handleDeleteDebtPayment(payment.id)}
                                  disabled={paymentSubmitting}
                                  className="text-red-600 hover:text-red-800 text-xs font-medium shrink-0"
                                >
                                  Eliminar
                                </button>
                              </li>
                            ))}
                          </ul>
                        </div>
                      )}
                      {paymentModal.paymentsLoading && (
                        <p className="text-xs text-gray-500 mb-4">Cargando pagos...</p>
                      )}

                      <form onSubmit={submitDebtPayment} className="space-y-4">
                        <div>
                          <label className="block text-sm font-medium text-gray-700 mb-1">Fecha de pago</label>
                          <input
                            type="date"
                            value={paymentForm.payment_date}
                            onChange={(e) => setPaymentForm((prev) => ({ ...prev, payment_date: e.target.value }))}
                            className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm"
                          />
                        </div>
                        <div>
                          <label className="block text-sm font-medium text-gray-700 mb-1">Monto *</label>
                          <input
                            type="text"
                            inputMode="decimal"
                            value={paymentForm.amount}
                            onChange={(e) => setPaymentForm((prev) => ({ ...prev, amount: e.target.value }))}
                            onBlur={(e) => setPaymentForm((prev) => ({
                              ...prev,
                              amount: formatAmountInputOnBlur(e.target.value),
                            }))}
                            placeholder="0,00"
                            className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm"
                            required
                          />
                        </div>
                        <div>
                          <label className="block text-sm font-medium text-gray-700 mb-1">Notas</label>
                          <textarea
                            rows={3}
                            value={paymentForm.notes}
                            onChange={(e) => setPaymentForm((prev) => ({ ...prev, notes: e.target.value }))}
                            className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm"
                          />
                        </div>

                        <div className="flex gap-3 pt-2">
                          <button
                            type="button"
                            onClick={closePaymentModal}
                            disabled={paymentSubmitting}
                            className="flex-1 px-4 py-2 border border-gray-300 rounded-lg text-gray-700 hover:bg-gray-50"
                          >
                            Cancelar
                          </button>
                          <button
                            type="submit"
                            disabled={paymentSubmitting}
                            className="flex-1 px-4 py-2 bg-emerald-600 text-white rounded-lg hover:bg-emerald-700 disabled:opacity-50"
                          >
                            {paymentSubmitting ? 'Guardando...' : 'Registrar'}
                          </button>
                        </div>
                      </form>
                    </div>
                  </div>
                )}
            </tbody>
          </table>
        </div>

        {/* Paginación */}
        {totalPages > 1 && (
          <div className="flex items-center justify-between px-4 py-3 border-t border-gray-200">
            <p className="text-sm text-gray-600">
              Mostrando {(currentPage - 1) * PAGE_SIZE + 1}-{Math.min(currentPage * PAGE_SIZE, displayedDebts.length)} de {displayedDebts.length}
            </p>
            <div className="flex items-center gap-2">
              <button
                onClick={() => setCurrentPage(p => Math.max(1, p - 1))}
                disabled={currentPage === 1}
                className="px-3 py-1 text-sm border border-gray-300 rounded-lg hover:bg-gray-100 disabled:opacity-50 disabled:cursor-not-allowed transition"
              >
                ← Anterior
              </button>
              {Array.from({ length: totalPages }, (_, i) => i + 1)
                .filter(page => page === 1 || page === totalPages || Math.abs(page - currentPage) <= 2)
                .reduce((acc, page, idx, arr) => {
                  if (idx > 0 && page - arr[idx - 1] > 1) {
                    acc.push(<span key={`dots-${page}`} className="text-gray-400 text-sm">...</span>);
                  }
                  acc.push(
                    <button
                      key={page}
                      onClick={() => setCurrentPage(page)}
                      className={`px-3 py-1 text-sm rounded-lg transition ${
                        page === currentPage
                          ? 'bg-blue-600 text-white'
                          : 'border border-gray-300 hover:bg-gray-100'
                      }`}
                    >
                      {page}
                    </button>
                  );
                  return acc;
                }, [])}
              <button
                onClick={() => setCurrentPage(p => Math.min(totalPages, p + 1))}
                disabled={currentPage === totalPages}
                className="px-3 py-1 text-sm border border-gray-300 rounded-lg hover:bg-gray-100 disabled:opacity-50 disabled:cursor-not-allowed transition"
              >
                Siguiente →
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Confirm Dialog */}
      <ConfirmDialog
        isOpen={deleteDialog.isOpen}
        onClose={() => setDeleteDialog({ isOpen: false, debtId: null, debtName: '' })}
        onConfirm={handleDeleteConfirm}
        title={isDebtMode ? 'Eliminar Deuda' : 'Eliminar Item de Presupuesto'}
        message={`¿Está seguro de eliminar "${deleteDialog.debtName}"?`}
        type="danger"
      />

      <ConfirmDialog
        isOpen={bulkDeleteDialogOpen}
        onClose={() => {
          if (!isBulkDeleting) {
            setBulkDeleteDialogOpen(false);
          }
        }}
        onConfirm={handleBulkDeleteConfirm}
        title={isDebtMode ? 'Eliminar Deudas Seleccionadas' : 'Eliminar Items Seleccionados'}
        message={isDebtMode
          ? `¿Está seguro de eliminar ${selectedDebtIds.length} deuda(s) seleccionada(s)?`
          : `¿Está seguro de eliminar ${selectedDebtIds.length} item(s) seleccionados del presupuesto?`}
        type="danger"
        confirmText={isBulkDeleting ? 'Eliminando...' : (isDebtMode ? 'Eliminar deudas seleccionadas' : 'Eliminar seleccionados')}
      />

      {/* Edit Debt Modal */}
      {editModalOpen && (
        isDebtMode ? (
          <EditDebtModal
            debt={debtToEdit}
            onSave={handleSaveEdit}
            onClose={handleCloseEditModal}
            categories={categories}
          />
        ) : (
          <EditBudgetItemModal
            debt={debtToEdit}
            onSave={handleSaveEdit}
            onClose={handleCloseEditModal}
            categories={categories}
          />
        )
      )}

      {/* New Debt Modal */}
      {isDebtMode ? (
        <NewDebtModal
          isOpen={newModalOpen}
          onClose={() => setNewModalOpen(false)}
          onCreateDebt={debtRecordsAPI.createDebt}
          yearMonth={`${filterYear}-${String(filterMonth).padStart(2, '0')}`}
          onSuccess={(created) => {
            loadDebts();
            const projectionMonth = created?.due_date || created?.start_date;
            if (projectionMonth) {
              const [yearPart, monthPart] = projectionMonth.slice(0, 7).split('-');
              const year = Number(yearPart);
              const month = Number(monthPart);
              if (year && month) {
                setFilterYear(year);
                setFilterMonth(month);
              }
            }
          }}
        />
      ) : (
        <NewBudgetItemModal
          isOpen={newModalOpen}
          onClose={() => setNewModalOpen(false)}
          onSuccess={() => {
            loadDebts();
          }}
        />
      )}

      {lineageModal.open && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-xl shadow-2xl max-w-2xl w-full p-6">
            <div className="flex justify-between items-center mb-4">
              <h3 className="text-lg font-bold text-gray-900">Linaje de clonación</h3>
              <button
                onClick={() => setLineageModal({ open: false, loading: false, data: null, error: null })}
                className="text-gray-400 hover:text-gray-600"
              >
                ✕
              </button>
            </div>

            {lineageModal.loading && <p className="text-sm text-gray-500">Cargando linaje...</p>}

            {!lineageModal.loading && lineageModal.error && (
              <div className="p-3 bg-red-50 border border-red-200 rounded text-sm text-red-700">
                {lineageModal.error}
              </div>
            )}

            {!lineageModal.loading && lineageModal.data && (
              <div className="space-y-2 max-h-96 overflow-y-auto">
                {lineageModal.data.lineage.map((node, idx) => (
                  <div key={node.id} className="p-3 border border-gray-200 rounded-lg">
                    <div className="flex items-center justify-between">
                      <p className="text-sm font-semibold text-gray-900">Item #{node.id}</p>
                      <span className="text-xs text-gray-500">Paso {idx + 1}</span>
                    </div>
                    <p className="text-sm text-gray-700">Categoría: {node.categoria || '-'}</p>
                    <p className="text-sm text-gray-700">
                      Monto: {formatCurrency(Number(node.monto_total || 0))}
                    </p>
                    <p className="text-xs text-gray-500 mt-1">
                      Mes origen: {node.version_source_month || '-'}
                    </p>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
