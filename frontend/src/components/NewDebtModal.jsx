import { useState, useEffect } from 'react';
import { debtsAPI } from '../services/api';
import { useToast } from './ToastContainer';
import { parseArNumber, formatAmountInputOnBlur, AMOUNT_FIELD_NAMES } from '../utils/currencyUtils';

const getInitialDate = (yearMonth) => {
  if (typeof yearMonth === 'string' && /^\d{4}-\d{2}$/.test(yearMonth)) {
    return `${yearMonth}-01`;
  }
  return new Date().toISOString().split('T')[0];
};

const buildInitialFormData = (yearMonth) => {
  const defaultDate = getInitialDate(yearMonth);
  const hasSelectedMonth = typeof yearMonth === 'string' && /^\d{4}-\d{2}$/.test(yearMonth);

  return {
  debt_name: '',
  debt_type: 'PERSONAL',
  debt_source: 'BANCO',
  creditor: '',
  fecha: defaultDate,
  // First installment defaults to the selected month so the debt appears in that month filter.
  fecha_vencimiento: hasSelectedMonth ? defaultDate : '',
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
  installment_mode: 'FIXED',
  base_salary: '',
  installment_salary_percent: '',
  salary_increase_percent: '',
  salary_increase_interval_months: '',
  };
};

const computeSalaryInstallment = (baseSalary, percent, increasePercent, intervalMonths, installmentIndex) => {
  const base = parseArNumber(baseSalary);
  const z = Number(percent);
  const x = Number(increasePercent);
  const n = Number(intervalMonths);
  if (!Number.isFinite(base) || base <= 0 || !Number.isFinite(z) || z <= 0) return 0;
  let salary = base;
  if (Number.isFinite(x) && x >= 0 && Number.isFinite(n) && n >= 1) {
    const periods = Math.floor(Math.max(0, installmentIndex) / n);
    salary = base * Math.pow(1 + x / 100, periods);
  }
  return Math.round((salary * z / 100) * 100) / 100;
};

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

export default function NewDebtModal({ isOpen, onClose, onSuccess, yearMonth, onCreateDebt }) {
  const toast = useToast();
  const [formData, setFormData] = useState(() => buildInitialFormData(yearMonth));
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (isOpen) {
      setFormData(buildInitialFormData(yearMonth));
    }
  }, [isOpen, yearMonth]);

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

    setFormData((prev) => ({ ...prev, [name]: value }));
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
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  };

  const toNullableAmount = (value) => {
    if (value === '' || value === null || value === undefined) return null;
    const parsed = parseArNumber(value);
    return Number.isFinite(parsed) ? parsed : null;
  };

  const handleSubmit = async (e) => {
    e.preventDefault();

    if (!formData.debt_name || !formData.debt_name.trim()) {
      toast.warning('Ingrese un nombre para la deuda');
      return;
    }

    const montoTotal = parseArNumber(formData.monto_total);
    if (!Number.isFinite(montoTotal) || montoTotal <= 0) {
      toast.warning('Ingrese un monto valido');
      return;
    }

    const totalInstallments = toNullableNumber(formData.total_installments);
    const currentInstallment = toNullableNumber(formData.current_installment);
    if (totalInstallments != null && currentInstallment != null && currentInstallment > totalInstallments) {
      toast.warning('La cuota actual no puede ser mayor al total de cuotas');
      return;
    }

    const isSalaryPercent = formData.installment_mode === 'SALARY_PERCENT';
    if (isSalaryPercent) {
      const baseSalary = toNullableAmount(formData.base_salary);
      const z = toNullableNumber(formData.installment_salary_percent);
      const x = toNullableNumber(formData.salary_increase_percent);
      const n = toNullableNumber(formData.salary_increase_interval_months);
      if (!baseSalary || baseSalary <= 0) {
        toast.warning('Ingrese el sueldo base inicial');
        return;
      }
      if (!z || z <= 0) {
        toast.warning('Ingrese el porcentaje de cuota sobre sueldo (z)');
        return;
      }
      if (x == null || x < 0) {
        toast.warning('Ingrese el porcentaje de aumento de sueldo (x)');
        return;
      }
      if (!n || n < 1) {
        toast.warning('Ingrese cada cuantos meses aumenta el sueldo (n >= 1)');
        return;
      }
      if (!totalInstallments || totalInstallments <= 0) {
        toast.warning('Ingrese el total de cuotas para cuota variable');
        return;
      }
    }

    setLoading(true);
    try {
      const createDebt = onCreateDebt || debtsAPI.createDebt;
      const response = await createDebt({
        ...formData,
        monto_total: montoTotal,
        estimated_payment: formData.estimated_payment
          ? parseArNumber(formData.estimated_payment)
          : montoTotal,
        annual_interest_rate: toNullableNumber(formData.annual_interest_rate),
        interest_vat_rate: toNullableNumber(formData.interest_vat_rate) ?? 21,
        total_installments: totalInstallments,
        current_installment: currentInstallment,
        pending_installments: toNullableNumber(formData.pending_installments),
        installment_mode: formData.installment_mode || 'FIXED',
        base_salary: toNullableAmount(formData.base_salary),
        installment_salary_percent: toNullableNumber(formData.installment_salary_percent),
        salary_increase_percent: toNullableNumber(formData.salary_increase_percent),
        salary_increase_interval_months: toNullableNumber(formData.salary_increase_interval_months),
      });
      const created = response?.data || {};
      const recordId = created.id ?? created.debt_record_id;
      const recordName = created.debt_name || formData.debt_name;
      const projectionMonth = (created.due_date || formData.fecha_vencimiento || formData.fecha || '').slice(0, 7);

      toast.success(
        recordId
          ? `Deuda creada: "${recordName}" (ID ${recordId})${projectionMonth ? ` — visible en ${projectionMonth}` : ''}`
          : 'Deuda creada correctamente'
      );
      setFormData(buildInitialFormData(yearMonth));
      onSuccess(created);
      onClose();
    } catch (error) {
      const detail = error?.response?.data?.detail;
      toast.error(typeof detail === 'string' ? detail : 'Error al crear deuda');
      console.error('Error creating debt:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleClose = () => {
    if (loading) return;
    setFormData(buildInitialFormData(yearMonth));
    onClose();
  };

  if (!isOpen) return null;

  const isSalaryPercent = formData.installment_mode === 'SALARY_PERCENT';
  const previewCuota1 = isSalaryPercent
    ? computeSalaryInstallment(
      formData.base_salary,
      formData.installment_salary_percent,
      formData.salary_increase_percent,
      formData.salary_increase_interval_months,
      0
    )
    : 0;
  const previewCuotaAfterN = isSalaryPercent && formData.salary_increase_interval_months
    ? computeSalaryInstallment(
      formData.base_salary,
      formData.installment_salary_percent,
      formData.salary_increase_percent,
      formData.salary_increase_interval_months,
      Number(formData.salary_increase_interval_months)
    )
    : 0;

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl shadow-2xl max-w-3xl w-full max-h-[90vh] overflow-y-auto">
        <div className="sticky top-0 bg-white border-b px-6 py-4 flex justify-between items-center">
          <h2 className="text-2xl font-bold text-finly-text">Nueva Deuda</h2>
          <button
            onClick={handleClose}
            disabled={loading}
            className="text-gray-500 hover:text-gray-700 text-2xl disabled:opacity-50"
          >
            ✕
          </button>
        </div>

        <form onSubmit={handleSubmit} className="p-6">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <div>
              <label className="block text-sm font-medium text-finly-text mb-2">Nombre de la deuda *</label>
              <input
                type="text"
                name="debt_name"
                value={formData.debt_name}
                onChange={handleChange}
                placeholder="Ej: Prestamo auto"
                className="w-full px-4 py-3 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-finly-primary"
                required
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-finly-text mb-2">Tipo de deuda *</label>
              <select
                name="debt_type"
                value={formData.debt_type}
                onChange={handleChange}
                className="w-full px-4 py-3 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-finly-primary"
                required
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
                className="w-full px-4 py-3 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-finly-primary"
                required
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
                placeholder="Ej: Banco Nacion"
                className="w-full px-4 py-3 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-finly-primary"
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-finly-text mb-2">Fecha de toma de deuda *</label>
              <input
                type="date"
                name="fecha"
                value={formData.fecha}
                onChange={handleChange}
                className="w-full px-4 py-3 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-finly-primary"
                required
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
                className="w-full px-4 py-3 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-finly-primary"
              />
              <p className="text-xs text-gray-500 mt-1">
                Define en qué mes aparece la deuda en la grilla. Si se omite, se proyecta desde el mes siguiente a la toma.
              </p>
            </div>

            <div>
              <label className="block text-sm font-medium text-finly-text mb-2">Monto total (ARS) *</label>
              <input
                type="text"
                inputMode="decimal"
                name="monto_total"
                value={formData.monto_total}
                onChange={handleChange}
                onBlur={handleBlur}
                placeholder="0,00"
                className="w-full px-4 py-3 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-finly-primary"
                required
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
                placeholder="Ej: 48.50"
                className="w-full px-4 py-3 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-finly-primary"
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
                placeholder="21"
                className="w-full px-4 py-3 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-finly-primary"
              />
              <p className="text-xs text-gray-500 mt-1">Se suma al monto de cuota sobre la porcion de intereses. Default 21%.</p>
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
                placeholder="Ej: 12"
                className="w-full px-4 py-3 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-finly-primary"
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
                placeholder="Ej: 3"
                className="w-full px-4 py-3 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-finly-primary"
              />
              <p className="text-xs text-gray-500 mt-1">Si ya pagaste 4 cuotas, la cuota actual es 5.</p>
            </div>

            <div className="md:col-span-2">
              <label className="block text-sm font-medium text-finly-text mb-2">Cuotas pendientes</label>
              <input
                type="number"
                name="pending_installments"
                value={formData.pending_installments}
                onChange={handleChange}
                step="0.01"
                min="0"
                placeholder="Opcional (se calcula automaticamente si se omite)"
                className="w-full px-4 py-3 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-finly-primary"
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
                  Cuota variable: z% del sueldo, con aumento de sueldo x% cada n meses
                </span>
              </label>
              <p className="text-xs text-gray-500 mt-1 ml-7">
                Para prestamos personales donde la cuota es un porcentaje del sueldo y el sueldo se actualiza periodicamente.
              </p>
            </div>

            {isSalaryPercent && (
              <>
                <div>
                  <label className="block text-sm font-medium text-finly-text mb-2">Sueldo base inicial (ARS) *</label>
                  <input
                    type="text"
                    inputMode="decimal"
                    name="base_salary"
                    value={formData.base_salary}
                    onChange={handleChange}
                    onBlur={handleBlur}
                    placeholder="Ej: 1.500.000,00"
                    className="w-full px-4 py-3 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-finly-primary"
                  />
                </div>

                <div>
                  <label className="block text-sm font-medium text-finly-text mb-2">Cuota = z% del sueldo (z) *</label>
                  <input
                    type="number"
                    name="installment_salary_percent"
                    value={formData.installment_salary_percent}
                    onChange={handleChange}
                    step="0.01"
                    min="0"
                    max="100"
                    placeholder="Ej: 30"
                    className="w-full px-4 py-3 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-finly-primary"
                  />
                </div>

                <div>
                  <label className="block text-sm font-medium text-finly-text mb-2">Aumento de sueldo x% (x) *</label>
                  <input
                    type="number"
                    name="salary_increase_percent"
                    value={formData.salary_increase_percent}
                    onChange={handleChange}
                    step="0.01"
                    min="0"
                    placeholder="Ej: 8"
                    className="w-full px-4 py-3 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-finly-primary"
                  />
                </div>

                <div>
                  <label className="block text-sm font-medium text-finly-text mb-2">Cada n meses (n) *</label>
                  <input
                    type="number"
                    name="salary_increase_interval_months"
                    value={formData.salary_increase_interval_months}
                    onChange={handleChange}
                    step="1"
                    min="1"
                    placeholder="Ej: 6"
                    className="w-full px-4 py-3 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-finly-primary"
                  />
                </div>

                {(previewCuota1 > 0 || previewCuotaAfterN > 0) && (
                  <div className="md:col-span-2 rounded-lg bg-indigo-50 border border-indigo-100 px-4 py-3 text-sm text-indigo-900">
                    <p>Cuota estimada cuota 1: <strong>${previewCuota1.toLocaleString('es-AR')}</strong></p>
                    {previewCuotaAfterN > 0 && Number(formData.salary_increase_interval_months) >= 1 && (
                      <p className="mt-1">
                        Cuota estimada cuota {Number(formData.salary_increase_interval_months) + 1} (despues del 1er aumento):{' '}
                        <strong>${previewCuotaAfterN.toLocaleString('es-AR')}</strong>
                      </p>
                    )}
                  </div>
                )}
              </>
            )}

            <div className="md:col-span-2">
              <label className="block text-sm font-medium text-finly-text mb-2">Comentarios</label>
              <textarea
                name="notes"
                value={formData.notes}
                onChange={handleChange}
                rows="3"
                className="w-full px-4 py-3 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-finly-primary"
                placeholder="Notas adicionales de la deuda..."
              />
            </div>
          </div>

          <div className="mt-6 flex gap-3 justify-end">
            <button
              type="button"
              onClick={handleClose}
              disabled={loading}
              className="px-6 py-3 border border-gray-300 rounded-lg hover:bg-gray-50 transition-colors disabled:opacity-50"
            >
              Cancelar
            </button>
            <button
              type="submit"
              disabled={loading}
              className="bg-finly-primary text-white px-6 py-3 rounded-lg hover:bg-finly-secondary transition-colors disabled:opacity-50"
            >
              {loading ? 'Guardando...' : 'Guardar Deuda'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
