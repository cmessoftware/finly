/**
 * Argentine currency/number helpers (DBT-BUG-017).
 * Display: 1.234.567,89 — parse accepts AR and plain decimal input.
 */

export function parseArNumber(value) {
  if (value === null || value === undefined || value === '') return NaN;
  if (typeof value === 'number') return Number.isFinite(value) ? value : NaN;

  const str = String(value).trim().replace(/\s/g, '').replace(/^\$/, '');
  if (!str) return NaN;

  const hasComma = str.includes(',');
  const hasDot = str.includes('.');

  if (hasComma && hasDot) {
    return parseFloat(str.replace(/\./g, '').replace(',', '.'));
  }
  if (hasComma) {
    return parseFloat(str.replace(',', '.'));
  }
  if (hasDot) {
    const parts = str.split('.');
    if (parts.length === 2 && parts[1].length <= 2) {
      return parseFloat(str);
    }
    return parseFloat(str.replace(/\./g, ''));
  }
  return parseFloat(str);
}

export function formatArNumber(value, options = {}) {
  const { minimumFractionDigits = 2, maximumFractionDigits = 2 } = options;
  const num = typeof value === 'number' ? value : parseArNumber(value);
  if (!Number.isFinite(num)) return '';
  return new Intl.NumberFormat('es-AR', {
    minimumFractionDigits,
    maximumFractionDigits,
  }).format(num);
}

export function formatArCurrency(value) {
  const num = typeof value === 'number' ? value : parseArNumber(value);
  if (!Number.isFinite(num)) return '';
  return new Intl.NumberFormat('es-AR', {
    style: 'currency',
    currency: 'ARS',
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(num);
}

/** Reformat amount text field on blur (empty stays empty). */
export function formatAmountInputOnBlur(value) {
  if (value === '' || value === null || value === undefined) return '';
  const n = parseArNumber(value);
  return Number.isFinite(n) ? formatArNumber(n) : String(value);
}

export const AMOUNT_FIELD_NAMES = new Set([
  'monto_total',
  'estimated_payment',
  'monto_ejecutado',
  'base_salary',
]);
