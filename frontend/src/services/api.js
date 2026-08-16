import axios from 'axios';
import { parseArNumber } from '../utils/currencyUtils';

// Runtime (Docker/nginx) > build-time env > dev proxy (same origin) > IPv4 fallback.
// Use 127.0.0.1 instead of localhost: on Windows, localhost:8000 can hit WSL/Docker via IPv6 and hang.
function resolveApiUrl() {
  const runtime = window.ENV?.VITE_API_URL?.trim();
  if (runtime) return runtime;
  if (import.meta.env.DEV) return '';
  const built = import.meta.env.VITE_API_URL?.trim();
  if (built) return built;
  return 'http://127.0.0.1:8000';
}

const API_URL = resolveApiUrl();
// Render cold start + Neon latency can exceed 15s on first heavy requests.
const API_TIMEOUT_MS = Number(import.meta.env.VITE_API_TIMEOUT_MS) || 45000;

const api = axios.create({
  baseURL: API_URL,
  timeout: API_TIMEOUT_MS,
});

// Add token to requests
api.interceptors.request.use(
  (config) => {
    const token = localStorage.getItem('token');
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
  },
  (error) => {
    return Promise.reject(error);
  }
);

// Handle token expiration — try refresh before giving up
// Use a queue to prevent concurrent refresh calls from racing
let isRefreshing = false;
let failedQueue = [];

const processQueue = (error, token = null) => {
  failedQueue.forEach(({ resolve, reject }) => {
    if (error) {
      reject(error);
    } else {
      resolve(token);
    }
  });
  failedQueue = [];
};

api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const originalRequest = error.config;
    if (error.response?.status === 401 && !originalRequest._retry) {
      // If a refresh is already in progress, queue this request
      if (isRefreshing) {
        return new Promise((resolve, reject) => {
          failedQueue.push({ resolve, reject });
        }).then((token) => {
          originalRequest.headers.Authorization = `Bearer ${token}`;
          return api(originalRequest);
        });
      }

      originalRequest._retry = true;
      isRefreshing = true;

      const refreshToken = localStorage.getItem('refresh_token');
      if (refreshToken) {
        try {
          const res = await axios.post(`${API_URL}/api/auth/refresh`, { refresh_token: refreshToken });
          const newToken = res.data.access_token;
          localStorage.setItem('token', newToken);
          processQueue(null, newToken);
          originalRequest.headers.Authorization = `Bearer ${newToken}`;
          return api(originalRequest);
        } catch (refreshError) {
          processQueue(refreshError, null);
          // refresh failed — log out
        } finally {
          isRefreshing = false;
        }
      }
      isRefreshing = false;
      localStorage.removeItem('token');
      localStorage.removeItem('user');
      localStorage.removeItem('refresh_token');
      window.location.href = '/';
    }
    return Promise.reject(error);
  }
);

export const authAPI = {
  login: (username, password) => {
    const formData = new FormData();
    formData.append('username', username);
    formData.append('password', password);
    return api.post('/api/auth/login', formData);
  },
  getCurrentUser: () => api.get('/api/auth/me'),
  logout: () => api.post('/api/auth/logout'),
  changePassword: (currentPassword, newPassword) =>
    api.post('/api/auth/change-password', { current_password: currentPassword, new_password: newPassword }),
};

export const transactionsAPI = {
  getCategories: () => api.get('/api/categories'),
  createCategory: (name) => api.post('/api/categories', { name }),
  deleteCategory: (id) => api.delete(`/api/categories/${id}`),
  getTransactionTypes: () => api.get('/api/transaction-types'),
  getNecessityTypes: () => api.get('/api/necessity-types'),
  saveTransaction: (transaction) => api.post('/api/transactions', transaction),
  getTransactions: () => api.get('/api/transactions'),
  importCSV: (data) => api.post('/api/transactions/import', data),
  migrateFromLocalStorage: (data) => api.post('/api/transactions/migrate', data),
  syncFromSheets: (force = false) => api.post(`/api/transactions/sync-from-sheets?force=${force}`),
  syncToSheets: (force = false) => api.post(`/api/transactions/sync-to-sheets?force=${force}`),
  debugSync: () => api.get('/api/transactions/debug-sync'),
  updateTransaction: (id, transaction) => api.put(`/api/transactions/${id}`, transaction),
  deleteTransaction: (id) => api.delete(`/api/transactions/${id}`),
};

export const adminAPI = {
  // Users
  getUsers: () => api.get('/api/admin/users'),
  getUser: (id) => api.get(`/api/admin/users/${id}`),
  createUser: (user) => api.post('/api/admin/users', user),
  updateUser: (id, user) => api.put(`/api/admin/users/${id}`, user),
  activateUser: (id) => api.patch(`/api/admin/users/${id}/activate`),
  deactivateUser: (id) => api.patch(`/api/admin/users/${id}/deactivate`),
  unlockUser: (id) => api.patch(`/api/admin/users/${id}/unlock`),
  resetPassword: (id, newPassword) => api.post(`/api/admin/users/${id}/reset-password`, { new_password: newPassword }),
  // Roles
  getRoles: () => api.get('/api/admin/roles'),
  createRole: (role) => api.post('/api/admin/roles', role),
  updateRole: (id, role) => api.put(`/api/admin/roles/${id}`, role),
  deleteRole: (id) => api.delete(`/api/admin/roles/${id}`),
  getPermissions: () => api.get('/api/admin/roles/permissions/all'),
  // Settings
  getSetting: (key) => api.get(`/api/settings/${key}`),
  updateSetting: (key, value) => api.put(`/api/settings/${key}`, { value }),
  // Clone data
  cloneUserData: (data) => api.post('/api/admin/clone-data', data),
};

export const monthClosingAPI = {
  getAll: () => api.get('/api/month-closings'),
  getStatus: (year, month) => api.get(`/api/month-closings/${year}/${month}`),
  closeMonth: (year, month) => api.post(`/api/month-closings/${year}/${month}`),
  reopenMonth: (year, month) => api.delete(`/api/month-closings/${year}/${month}`),
};

export const monthsAPI = {
  getStatus: (yearMonth) => api.get(`/api/months/${yearMonth}/status`),
  openMonth: (payload) => api.post('/api/months', payload),
  closeMonth: (yearMonth) => api.post(`/api/months/${yearMonth}/close`),
  reopenMonth: (yearMonth, reason) => api.post(`/api/months/${yearMonth}/reopen`, { reason }),
  getCarryover: (yearMonth) => api.get(`/api/months/${yearMonth}/carryover`),
  getBudgetItems: (yearMonth, includeCloneInfo = false) =>
    api.get(`/api/months/${yearMonth}/budget-items?include_clone_info=${includeCloneInfo}`),
  getMonths: () => api.get('/api/months?include_status=true'),
};

const toNumber = (value, fallback = 0) => {
  if (typeof value === 'number') {
    return Number.isFinite(value) ? value : fallback;
  }
  const n = parseArNumber(value);
  return Number.isFinite(n) ? n : fallback;
};

const roundCurrency = (value) => Math.round((toNumber(value, 0) + Number.EPSILON) * 100) / 100;

const debtTypeFromUiType = (tipo = '') => {
  const value = String(tipo || '').toLowerCase();
  if (value.includes('tarjeta')) return 'TARJETA';
  if (value.includes('pr') && value.includes('stamo')) return 'PRESTAMO';
  if (value.includes('hipoteca')) return 'HIPOTECA';
  if (value.includes('personal')) return 'PERSONAL';
  return 'OTRO';
};

const uiTypeFromDebtType = (debtType = '') => {
  const map = {
    TARJETA: 'Tarjeta',
    PRESTAMO: 'Préstamo',
    HIPOTECA: 'Hipoteca',
    PERSONAL: 'Personal',
    OTRO: 'Otro',
  };
  return map[debtType] || 'Otro';
};

const budgetStatusFromDebtRecord = (status, outstandingAmount) => {
  if (status === 'VENCIDA') return 'VENCIDA';
  if (status === 'CANCELADA' || toNumber(outstandingAmount, 0) <= 0) return 'PAGADA';
  return 'PENDIENTE';
};

const toDebtRecordPayloadFromBudgetForm = (debt) => {
  const montoTotal = toNumber(debt.monto_total, 0);
  const montoEjecutado = toNumber(debt.monto_ejecutado ?? debt.monto_pagado, 0);
  const outstanding = Math.max(0, montoTotal - montoEjecutado);

  const debtName = String(
    debt.debt_name || debt.detalle || `${debt.tipo || 'Deuda'} ${debt.categoria || ''}`
  ).trim();

  const parseNullableNumber = (value) => {
    if (value === null || value === undefined || value === '') return null;
    const n = parseArNumber(value);
    return Number.isFinite(n) ? n : null;
  };

  const debtType = debt.debt_type || debtTypeFromUiType(debt.tipo);
  const annualInterestRate = parseNullableNumber(debt.annual_interest_rate);
  const totalInstallments = parseNullableNumber(debt.total_installments);
  const currentInstallment = parseNullableNumber(debt.current_installment);
  const pendingInstallments = parseNullableNumber(debt.pending_installments);

  return {
    debt_name: debtName,
    debt_type: debtType,
    debt_source: debt.debt_source || null,
    creditor: debt.creditor || debt.categoria || null,
    currency: debt.currency || 'ARS',
    principal_amount: montoTotal,
    outstanding_amount: outstanding,
    annual_interest_rate: annualInterestRate,
    interest_vat_rate: parseNullableNumber(debt.interest_vat_rate) ?? 21,
    total_installments: totalInstallments,
    current_installment: currentInstallment,
    pending_installments: pendingInstallments,
    start_date: debt.fecha || null,
    due_date: debt.fecha_vencimiento || null,
    status: outstanding <= 0 ? 'CANCELADA' : 'ACTIVA',
    notes: debt.notes || debt.detalle || null,
    installment_mode: debt.installment_mode || 'FIXED',
    base_salary: parseNullableNumber(debt.base_salary),
    installment_salary_percent: parseNullableNumber(debt.installment_salary_percent),
    salary_increase_percent: parseNullableNumber(debt.salary_increase_percent),
    salary_increase_interval_months: debt.salary_increase_interval_months != null && debt.salary_increase_interval_months !== ''
      ? parseInt(debt.salary_increase_interval_months, 10)
      : null,
  };
};

const mapProjectedDebtRecordToUi = (record) => {
  const projection = record.projection_current || {};
  const projectionList = Array.isArray(record.projections) ? record.projections : [];
  const projectionByMonth = projectionList.reduce((acc, item) => {
    if (item?.version_source_month) {
      acc[item.version_source_month] = item;
    }
    return acc;
  }, {});
  const principalAmount = toNumber(record.principal_amount, toNumber(record.outstanding_amount, 0));
  const outstandingAmount = toNumber(record.outstanding_amount, principalAmount);
  const totalInstallments = toNumber(record.total_installments, 0);
  const currentInstallment = toNumber(record.current_installment, 0);
  const annualInterestRate = toNumber(record.annual_interest_rate, 0);
  const projectionInstallment = toNumber(projection.monto_total, 0);
  const estimatedPayment = projectionInstallment > 0 ? projectionInstallment : principalAmount;
  const paidFromOutstanding = Math.max(0, principalAmount - outstandingAmount);
  const projectionEjecutado = toNumber(projection.monto_ejecutado, 0);
  const montoEjecutado = roundCurrency(projectionEjecutado > 0 ? projectionEjecutado : paidFromOutstanding);
  const fecha = projection.fecha || record.start_date || record.due_date || null;
  const fechaVencimiento = projection.fecha_vencimiento || record.due_date || fecha;

  return {
    id: record.id,
    debt_record_id: record.id,
    debt_name: record.debt_name || '',
    debt_type: record.debt_type || 'OTRO',
    debt_source: record.debt_source || null,
    creditor: record.creditor || null,
    currency: record.currency || 'ARS',
    outstanding_amount: outstandingAmount,
    annual_interest_rate: record.annual_interest_rate,
    interest_vat_rate: record.interest_vat_rate != null ? record.interest_vat_rate : 21,
    total_installments: record.total_installments,
    current_installment: record.current_installment,
    pending_installments: record.pending_installments,
    notes: record.notes || '',
    installment_mode: record.installment_mode || 'FIXED',
    base_salary: record.base_salary,
    installment_salary_percent: record.installment_salary_percent,
    salary_increase_percent: record.salary_increase_percent,
    salary_increase_interval_months: record.salary_increase_interval_months,
    fecha,
    fecha_vencimiento: fechaVencimiento,
    tipo: uiTypeFromDebtType(record.debt_type),
    categoria: record.creditor || 'Deudas',
    detalle: record.debt_name || '',
    monto_total: principalAmount,
    monto_pagado: montoEjecutado,
    monto_ejecutado: montoEjecutado,
    estimated_payment: roundCurrency(estimatedPayment),
    status: budgetStatusFromDebtRecord(record.status, record.outstanding_amount),
    tipo_presupuesto: 'OBLIGATION',
    tipo_flujo: 'Ingreso',
    expense_type: 'VARIABLE',
    debt_source: record.debt_source || record.creditor || null,
    projection_count: toNumber(record.projection_count, 0),
    projection_months: Array.isArray(record.projection_months) ? record.projection_months : Object.keys(projectionByMonth),
    projection_by_month: projectionByMonth,
    projections: projectionList,
  };
};

export const debtRecordsAPI = {
  getProjectedDebts: (status) => {
    const request = status
      ? api.get('/api/debt-records/projected', { params: { status } })
      : api.get('/api/debt-records/projected');

    return request.then((response) => ({
      ...response,
      data: (response.data || []).map(mapProjectedDebtRecordToUi),
    }));
  },
  createDebt: (debt) => api.post('/api/debt-records', toDebtRecordPayloadFromBudgetForm(debt)),
  updateDebt: (id, debt) => api.put(`/api/debt-records/${id}`, toDebtRecordPayloadFromBudgetForm(debt)),
  deleteDebt: (id) => api.delete(`/api/debt-records/${id}`),
  getPayments: (id) => api.get(`/api/debt-records/${id}/payments`),
  addPayment: (id, payload) => api.post(`/api/debt-records/${id}/payments`, payload),
  deletePayment: (paymentId) => api.delete(`/api/debt-records/payments/${paymentId}`),
};

export const debtsAPI = {
  getDebts: () => api.get('/api/budget-items'),
  getDebtSummary: (month, year) => {
    const params = new URLSearchParams();
    if (month) params.append('month', month);
    if (year) params.append('year', year);
    const qs = params.toString();
    return api.get(`/api/budget-items/summary${qs ? `?${qs}` : ''}`);
  },
  getDebt: (id) => api.get(`/api/budget-items/${id}`),
  createDebt: (debt) => api.post('/api/budget-items', debt),
  updateDebt: (id, debt) => api.put(`/api/budget-items/${id}`, debt),
  deleteDebt: (id) => api.delete(`/api/budget-items/${id}`),
  cloneMonth: (sourceMonth, sourceYear, targetMonth, targetYear) =>
    api.post('/api/debts/clone-month', {
      source_month: sourceMonth,
      source_year: sourceYear,
      target_month: targetMonth,
      target_year: targetYear,
    }),
  getCloneLineage: (itemId) => api.get(`/api/budget-items/${itemId}/clone-lineage`),
  importBudgetCsv: (items) => api.post('/api/budget-items/import-csv', items),
};

export const creditCardAPI = {
  getCreditCards: (activeOnly = true) => api.get(`/api/credit-cards?active_only=${activeOnly}`),
  getCreditCard: (id) => api.get(`/api/credit-cards/${id}`),
  createCreditCard: (card) => api.post('/api/credit-cards', card),
  updateCreditCard: (id, card) => api.put(`/api/credit-cards/${id}`, card),
  deleteCreditCard: (id) => api.delete(`/api/credit-cards/${id}`),
  getCardSummary: (id) => api.get(`/api/credit-cards/${id}/summary`),
  getMonthlyPurchasesTotal: (month, year) => api.get(`/api/credit-cards/monthly-purchases-total?month=${month}&year=${year}`),
  createPurchase: (purchase) => api.post('/api/credit-cards/purchases', purchase),
  updatePurchase: (id, purchase) => api.put(`/api/credit-cards/purchases/${id}`, purchase),
  deletePurchase: (id) => api.delete(`/api/credit-cards/purchases/${id}`),
  getCardPurchases: (cardId) => api.get(`/api/credit-cards/${cardId}/purchases`),
  getPurchasesSummary: (cardId) => api.get(`/api/credit-cards/${cardId}/purchases-summary`),
  getInstallmentSchedule: (planId) => api.get(`/api/installment-plans/${planId}/schedule`),
  payInstallment: (installmentId, payment) => api.put(`/api/installments/${installmentId}/pay`, payment),
  unpayInstallment: (installmentId) => api.put(`/api/installments/${installmentId}/unpay`),
  bulkImportPurchases: (cardId, purchases) => api.post(`/api/credit-cards/${cardId}/import-csv`, purchases),
  getCardPeriodInstallments: (cardId, year, month) => api.get(`/api/credit-cards/${cardId}/period-installments?year=${year}&month=${month}`),
  registerCardPeriodBudget: (cardId, year, month, paymentType = 'total', minimumPayment = 0) => api.post(`/api/credit-cards/${cardId}/register-period-budget`, { year, month, payment_type: paymentType, minimum_payment: minimumPayment }),
  updatePeriodConfig: (cardId, year, month, closingDay, dueDay) => api.put(`/api/credit-cards/${cardId}/period-config`, { year, month, closing_day: closingDay, due_day: dueDay }),
  getPeriodForDate: (cardId, purchaseDate) => api.get(`/api/credit-cards/${cardId}/period-for-date?purchase_date=${purchaseDate}`),
};

export default api;
