import { useState, useEffect } from 'react';
import { toISODate } from '../utils/dateUtils';
import { formatArNumber, parseArNumber, formatAmountInputOnBlur, AMOUNT_FIELD_NAMES } from '../utils/currencyUtils';

const DEBT_TYPE_OPTIONS = [
  { value: 'PERSONAL', label: 'Personal' },
  { value: 'PRESTAMO', label: 'Prestamo' },
  { value: 'HIPOTECA', label: 'Hipoteca' },
  { value: 'TARJETA', label: 'Tarjeta' },
  { value: 'OTRO', label: 'Otro' },
];

const DEBT_SOURCE_OPTIONS = [
  { value: 'BANCO', label: 'Banco' },
  { value: 'FINTECH', label: 'Fintech' },
  { value: 'INDIVIDUO', label: 'Individuo' },
  { value: 'EMPRESA', label: 'Empresa' },
  { value: 'OTRO', label: 'Otro' },
];

function EditDebtModal({ debt, onSave, onClose }) {
  const [formData, setFormData] = useState({
    debt_name: '',
    debt_type: 'PERSONAL',
    debt_source: 'BANCO',
    creditor: '',
    fecha: '',
    fecha_vencimiento: '',
    monto_total: '',
    annual_interest_rate: '',
    interest_vat_rate: '21',
    total_installments: '',
    current_installment: '',
    pending_installments: '',
    notes: '',
    tipo_presupuesto: 'OBLIGATION',
    tipo_flujo: 'Ingreso',
    expense_type: 'VARIABLE',
    estimated_payment: '',
    monto_ejecutado: '0',
    installment_mode: 'FIXED',
    base_salary: '',
    installment_salary_percent: '',
    salary_increase_percent: '',
    salary_increase_interval_months: '',
  });

  useEffect(() => {
    if (!debt) return;

    setFormData({
      debt_name: debt.debt_name || debt.detalle || '',
      debt_type: debt.debt_type || debt.tipo || 'PERSONAL',
      debt_source: debt.debt_source || 'BANCO',
      creditor: debt.creditor || debt.categoria || '',
      fecha: toISODate(debt.fecha),
      fecha_vencimiento: toISODate(debt.fecha_vencimiento) || '',
      monto_total: debt.monto_total != null ? formatArNumber(debt.monto_total) : '',
      annual_interest_rate: debt.annual_interest_rate != null ? String(debt.annual_interest_rate) : '',
      interest_vat_rate: debt.interest_vat_rate != null ? String(debt.interest_vat_rate) : '21',
      total_installments: debt.total_installments != null ? String(debt.total_installments) : '',
      current_installment: debt.current_installment != null ? String(debt.current_installment) : '',
      pending_installments: debt.pending_installments != null ? String(debt.pending_installments) : '',
      notes: debt.notes || '',
      tipo_presupuesto: debt.tipo_presupuesto || 'OBLIGATION',
      tipo_flujo: debt.tipo_flujo || 'Ingreso',
      expense_type: debt.expense_type || 'VARIABLE',
      estimated_payment: formatArNumber(
        debt.estimated_payment != null ? debt.estimated_payment : debt.monto_total || 0
      ),
      monto_ejecutado: formatArNumber(debt.monto_ejecutado || debt.monto_pagado || 0),
      installment_mode: debt.installment_mode || 'FIXED',
      base_salary: debt.base_salary != null ? formatArNumber(debt.base_salary) : '',
      installment_salary_percent: debt.installment_salary_percent != null ? String(debt.installment_salary_percent) : '',
      salary_increase_percent: debt.salary_increase_percent != null ? String(debt.salary_increase_percent) : '',
      salary_increase_interval_months: debt.salary_increase_interval_months != null ? String(debt.salary_increase_interval_months) : '',
    });
  }, [debt]);

  const handleChange = (e) => {
    const { name, value, type, checked } = e.target;

    if (name === 'installment_mode_toggle') {
      setFormData((prev) => ({
        ...prev,
        installment_mode: checked ? 'SALARY_PERCENT' : 'FIXED',
      }));
      return;
    }

    if (name === 'monto_total') {
      setFormData((prev) => ({
        ...prev,
        monto_total: value,
        estimated_payment: (!prev.estimated_payment || prev.estimated_payment === prev.monto_total)
          ? value
          : prev.estimated_payment,
      }));
      return;
    }

    setFormData((prev) => ({
      ...prev,
      [name]: value,
    }));
  };

  const handleBlur = (e) => {
    const { name, value } = e.target;
    if (!AMOUNT_FIELD_NAMES.has(name)) return;
    setFormData((prev) => ({
      ...prev,
      [name]: formatAmountInputOnBlur(value),
    }));
  };

  const toNullableNumber = (value) => {
    if (value === '' || value === null || value === undefined) return null;
    const parsed = parseArNumber(value);
    return Number.isFinite(parsed) ? parsed : null;
  };

  const handleSubmit = (e) => {
    e.preventDefault();

    if (!formData.debt_name || !formData.debt_name.trim()) {
      return;
    }

    const montoTotal = parseArNumber(formData.monto_total);
    if (!Number.isFinite(montoTotal) || montoTotal <= 0) {
      return;
    }

    const totalInstallments = toNullableNumber(formData.total_installments);
    const currentInstallment = toNullableNumber(formData.current_installment);
    if (totalInstallments != null && currentInstallment != null && currentInstallment > totalInstallments) {
      return;
    }

    onSave({
      ...formData,
      monto_total: montoTotal,
      annual_interest_rate: toNullableNumber(formData.annual_interest_rate),
      interest_vat_rate: toNullableNumber(formData.interest_vat_rate) ?? 21,
      total_installments: totalInstallments,
      current_installment: currentInstallment,
      pending_installments: toNullableNumber(formData.pending_installments),
      monto_ejecutado: parseArNumber(formData.monto_ejecutado),
      estimated_payment: formData.estimated_payment
        ? parseArNumber(formData.estimated_payment)
        : montoTotal,
      installment_mode: formData.installment_mode || 'FIXED',
      base_salary: toNullableNumber(formData.base_salary),
      installment_salary_percent: toNullableNumber(formData.installment_salary_percent),
      salary_increase_percent: toNullableNumber(formData.salary_increase_percent),
      salary_increase_interval_months: toNullableNumber(formData.salary_increase_interval_months),
    });
  };

  if (!debt) return null;

  const isSalaryPercent = formData.installment_mode === 'SALARY_PERCENT';

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl shadow-2xl max-w-3xl w-full max-h-[90vh] overflow-y-auto">
        <div className="sticky top-0 bg-white border-b border-gray-200 px-6 py-4 flex justify-between items-center">
          <h2 className="text-2xl font-bold text-finly-text">Editar Deuda</h2>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600 transition-colors"
          >
            <svg xmlns="http://www.w3.org/2000/svg" className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        <form onSubmit={handleSubmit} className="p-6 space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-finly-text mb-2">Nombre de la deuda *</label>
              <input
                type="text"
                name="debt_name"
                value={formData.debt_name}
                onChange={handleChange}
                required
                className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-finly-primary focus:border-transparent transition-all"
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-finly-text mb-2">Tipo de deuda *</label>
              <select
                name="debt_type"
                value={formData.debt_type}
                onChange={handleChange}
                required
                className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-finly-primary focus:border-transparent transition-all"
              >
                {DEBT_TYPE_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>{option.label}</option>
                ))}
              </select>
            </div>

            <div>
              <label className="block text-sm font-medium text-finly-text mb-2">Fuente de la deuda *</label>
              <select
                name="debt_source"
                value={formData.debt_source}
                onChange={handleChange}
                required
                className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-finly-primary focus:border-transparent transition-all"
              >
                {DEBT_SOURCE_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>{option.label}</option>
                ))}
              </select>
            </div>

            <div>
              <label className="block text-sm font-medium text-finly-text mb-2">Acreedor / Entidad</label>
              <input
                type="text"
                name="creditor"
                value={formData.creditor}
                onChange={handleChange}
                className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-finly-primary focus:border-transparent transition-all"
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-finly-text mb-2">Fecha de toma de deuda *</label>
              <input
                type="date"
                name="fecha"
                value={formData.fecha}
                onChange={handleChange}
                required
                className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-finly-primary focus:border-transparent transition-all"
              />
              <p className="text-xs text-gray-500 mt-1">Corresponde a la fecha en que se tomo la deuda.</p>
            </div>

            <div>
              <label className="block text-sm font-medium text-finly-text mb-2">Fecha de primera cuota</label>
              <input
                type="date"
                name="fecha_vencimiento"
                value={formData.fecha_vencimiento}
                onChange={handleChange}
                className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-finly-primary focus:border-transparent transition-all"
              />
              <p className="text-xs text-gray-500 mt-1">Si se omite, se proyecta desde el mes siguiente a la toma.</p>
            </div>

            <div>
              <label className="block text-sm font-medium text-finly-text mb-2">Monto total *</label>
              <input
                type="text"
                inputMode="decimal"
                name="monto_total"
                value={formData.monto_total}
                onChange={handleChange}
                onBlur={handleBlur}
                required
                className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-finly-primary focus:border-transparent transition-all"
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-finly-text mb-2">Tasa de interes anual (%)</label>
              <input
                type="number"
                name="annual_interest_rate"
                value={formData.annual_interest_rate}
                onChange={handleChange}
                step="0.01"
                min="0"
                className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-finly-primary focus:border-transparent transition-all"
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-finly-text mb-2">IVA sobre intereses (%)</label>
              <input
                type="number"
                name="interest_vat_rate"
                value={formData.interest_vat_rate}
                onChange={handleChange}
                step="0.01"
                min="0"
                max="100"
                className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-finly-primary focus:border-transparent transition-all"
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-finly-text mb-2">Total de cuotas</label>
              <input
                type="number"
                name="total_installments"
                value={formData.total_installments}
                onChange={handleChange}
                step="0.01"
                min="0"
                className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-finly-primary focus:border-transparent transition-all"
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-finly-text mb-2">Cuota actual (X, proxima a pagar)</label>
              <input
                type="number"
                name="current_installment"
                value={formData.current_installment}
                onChange={handleChange}
                step="0.01"
                min="0"
                className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-finly-primary focus:border-transparent transition-all"
              />
              <p className="text-xs text-gray-500 mt-1">Si ya pagaste 4 cuotas, la cuota actual es 5.</p>
            </div>

            <div>
              <label className="block text-sm font-medium text-finly-text mb-2">Cuotas pendientes</label>
              <input
                type="number"
                name="pending_installments"
                value={formData.pending_installments}
                onChange={handleChange}
                step="0.01"
                min="0"
                className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-finly-primary focus:border-transparent transition-all"
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-finly-text mb-2">Monto a pagar</label>
              <input
                type="text"
                inputMode="decimal"
                name="estimated_payment"
                value={formData.estimated_payment}
                onChange={handleChange}
                onBlur={handleBlur}
                className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-finly-primary focus:border-transparent transition-all"
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-finly-text mb-2">Monto ejecutado (Read-only)</label>
              <input
                type="text"
                inputMode="decimal"
                name="monto_ejecutado"
                value={formData.monto_ejecutado}
                readOnly
                className="w-full px-4 py-2 border border-gray-300 rounded-lg bg-gray-100 text-gray-600 cursor-not-allowed"
              />
            </div>

            <div className="md:col-span-2">
              <label className="flex items-center gap-3 cursor-pointer">
                <input
                  type="checkbox"
                  name="installment_mode_toggle"
                  checked={isSalaryPercent}
                  onChange={handleChange}
                  className="w-4 h-4 rounded border-gray-300 text-finly-primary focus:ring-finly-primary"
                />
                <span className="text-sm font-medium text-finly-text">
                  Cuota variable: z% del sueldo, aumento x% cada n meses
                </span>
              </label>
            </div>

            {isSalaryPercent && (
              <>
                <div>
                  <label className="block text-sm font-medium text-finly-text mb-2">Sueldo base inicial (ARS)</label>
                  <input
                    type="text"
                    inputMode="decimal"
                    name="base_salary"
                    value={formData.base_salary}
                    onChange={handleChange}
                    onBlur={handleBlur}
                    className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-finly-primary focus:border-transparent transition-all"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-finly-text mb-2">Cuota z% del sueldo</label>
                  <input
                    type="number"
                    name="installment_salary_percent"
                    value={formData.installment_salary_percent}
                    onChange={handleChange}
                    step="0.01"
                    min="0"
                    max="100"
                    className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-finly-primary focus:border-transparent transition-all"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-finly-text mb-2">Aumento sueldo x%</label>
                  <input
                    type="number"
                    name="salary_increase_percent"
                    value={formData.salary_increase_percent}
                    onChange={handleChange}
                    step="0.01"
                    min="0"
                    className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-finly-primary focus:border-transparent transition-all"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-finly-text mb-2">Cada n meses</label>
                  <input
                    type="number"
                    name="salary_increase_interval_months"
                    value={formData.salary_increase_interval_months}
                    onChange={handleChange}
                    step="1"
                    min="1"
                    className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-finly-primary focus:border-transparent transition-all"
                  />
                </div>
              </>
            )}
          </div>

          <div>
            <label className="block text-sm font-medium text-finly-text mb-2">Comentarios</label>
            <textarea
              name="notes"
              value={formData.notes}
              onChange={handleChange}
              rows={3}
              className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-finly-primary focus:border-transparent transition-all resize-none"
              placeholder="Notas adicionales de la deuda"
            />
          </div>

          <div className="flex gap-3 pt-4">
            <button
              type="button"
              onClick={onClose}
              className="flex-1 px-6 py-3 border border-gray-300 rounded-lg text-gray-700 font-semibold hover:bg-gray-50 transition-colors"
            >
              Cancelar
            </button>
            <button
              type="submit"
              className="flex-1 px-6 py-3 bg-finly-primary rounded-lg text-white font-semibold hover:bg-finly-secondary transition-colors"
            >
              Guardar Cambios
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

export default EditDebtModal;
