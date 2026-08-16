Modulo de registro de deudas bancarias y no bancarias (no de tarjeta de crédito)

Backlog Estructurado (OpenPEC-ready)

Modelo de ticket
- id: prefijo + numero (`DBT-FEAT-###` o `DBT-BUG-###`)
- tipo: `feature` | `bug`
- prioridad: `alta` | `media` | `baja`
- estado: `⏳ Todo` | `🔄 In progress` | `blocked` | `✅ Done`
- resumen: una linea con impacto de negocio
- alcance: backend | frontend | data
- criterio_aceptacion: resultado verificable
- evidencia: fix aplicado, endpoint, PR, query o captura

Espeficaciones funcioanales de alto nivel 
Crear opción "Carga de Deudas": 
1. Campos: Fecha,Monto, Interes anual (%),Cantidad de cuotas, Fuente de la deuda (banco, fintech, individuos),Número de cuota actual,pendiente de pago incluido cuota actual, Comentarios.
2. Al registrar una deuda cargarla en módulo de Presupuesto como Ingreso (tipo deuda) indicando monto de cuota actual y número de cuota. Por ejemplo 1000 (cuota 2/6). Propagar el item de presupuesto a meses siguientes hasta terminar la cantidad de cuotas. Usar Mes calendario.
3. Cuando el usuario registra un pago de un item tipo deuda actualizar el monto pendiente en item asociado en módulo deudas.
4. Habiitar opción de pago parcial o total de la deuda, actualizando item según punto 3 acorde al monto pagado. Si no es monto múltiplo de valor cuota asignar pendiente de cuotas fraccionaria. 
   Por ejempo:
    Deuda Total:1.000.000, 
    Interes: 20 % anual, 
    Cant de cuotas: 12, ya se pagaron 3 cuotas de 100.000,pendiente:900.000, cuotas pendientes: 9
    Se pagan 250.000: equivalente a 2,5 cuotas , cuotas pendientes: 6,5
En encabezado de pantalla que muestra la tabla de deudas (usando look&feel de Gastos/Ingresos):
5. Mostrar gráfico de torta del total de deuda mostrandoe el agrupamientos de cada fuente de deuda (banco A, banco B, fitexh A, fintech B, individuo A, individuo B, Empresa A, Empresa B etc).
6. Idem 5 pero del mes actual.
7. Gráfico de barras con linea continua que pase por lo pico de cada barra, indicando la variación de la deuda mes a mes, mostrar ventana de 12 meses, centrada en mes actual.

## Decisiones Funcionales Cerradas

1. Sistema fuente de verdad:
   - Se define `debt-records` como modulo fuente de verdad para deudas no tarjeta.
   - El modulo `Presupuesto` queda como proyeccion/impacto mensual de las deudas (no como entidad canonica de deuda).
   - Integracion requerida: cada deuda y su plan de cuotas debe reflejar items de presupuesto por mes calendario, segun cuota vigente.

2. Cuotas fraccionarias:
   - La cantidad de cuotas pendientes fraccionarias se guarda y muestra con 2 decimales.
   - Regla de redondeo: redondeo comercial a 2 decimales para visualizacion y persistencia funcional.
   - Ejemplo: 6.5 -> 6.50; 6.666 -> 6.67.

3. Alcance de integracion:
   - Alta de deuda en `debt-records` crea/actualiza proyeccion mensual en `Presupuesto` como Ingreso tipo deuda (cuota X/Y).
   - Registro de pago contra item asociado debe reconciliar saldo y cuotas pendientes en `debt-records`.
   - Los dashboards del modulo de deudas deben consumir `debt-records` + agregados por fuente y por mes.


## Features Pendientes

Estado sincronizado con Gitea (2026-06-01):
- Issues `#135` a `#152`: `open`.
- Avance real de implementacion: `DBT-FEAT-001` cerrado en `✅ Done` (BE + FE consolidados).
- Avance real de implementacion: `DBT-FEAT-002` en `✅ Done` (BE + FE con formulario completo de alta/edicion y mapeo API actualizado).
- Avance real de implementacion: `DBT-FEAT-003` en `✅ Done` (proyeccion mensual por calendario + reconciliacion de proyecciones; validado por pruebas automatizadas del servicio).

| ID | Tipo | Prioridad |Issue Padre|Issue Gitea| Estado | Resumen |
|---|---|---|---|---|---|---|
| DBT-FEAT-001 | FEAT | alta | DEBTS-MVP | #135 (BE), #136 (FE) | ✅ Done | Definir `debt-records` como fuente de verdad e integrar contratos API con Presupuesto (BE validado, FE integrado en vista de Deudas). |
| DBT-FEAT-002 | FEAT | alta | DBT-FEAT-001 | #137 (BE), #138 (FE) | ✅ Done | Implementar alta de deuda no tarjeta con campos completos: interes anual, cuotas, cuota actual, cuotas pendientes, fuente y comentarios (BE + FE consolidados). |
| DBT-FEAT-003 | FEAT | alta | DBT-FEAT-001 | #139 (BE), #140 (FE) | ✅ Done | Proyeccion automatica en Presupuesto por mes calendario (cuota X/Y) al crear deuda, con reconciliacion de proyecciones faltantes y cobertura de pruebas en `backend/tests/test_debt_record_projection_service.py`. |
| DBT-FEAT-004 | FEAT | alta | DBT-FEAT-001 | #141 (BE), #142 (FE) | ⏳ Todo | Registrar pago parcial/total y reconciliar saldo + cuotas pendientes fraccionarias en `debt-records`. |
| DBT-FEAT-005 | FEAT | alta | DBT-FEAT-004 | #143 (BE), #144 (FE) | ⏳ Todo | Aplicar regla de cuotas fraccionarias con precision fija de 2 decimales en backend y frontend. |
| DBT-FEAT-006 | FEAT | media | DBT-FEAT-001 | #145 (BE), #146 (FE) | ⏳ Todo | Exponer dashboard de deudas por fuente (total historico) con grafico de torta. |
| DBT-FEAT-007 | FEAT | media | DBT-FEAT-001 | #147 (BE), #148 (FE) | ⏳ Todo | Exponer dashboard de deudas por fuente del mes actual con grafico de torta. |
| DBT-FEAT-008 | FEAT | media | DBT-FEAT-001 | #149 (BE), #150 (FE) | ⏳ Todo | Exponer variacion de deuda mes a mes (ventana 12 meses centrada en mes actual) con barras + linea. |
| DBT-FEAT-009 | FEAT | alta | DBT-FEAT-001 | TDB | ⏳ Todo | Integrar trazabilidad CC->EXP sin duplicar fuente canonica: registrar espejo operacional en EXP para compras CC con `source_module/source_id` y referencia de periodo de tarjeta. |
| DBT-FEAT-010 | FEAT | alta | DBT-FEAT-009 | TDB | ⏳ Todo | Implementar regla anti-duplicado contable entre CC, EXP y DBT para evitar doble computo del mismo pasivo. |
| DBT-FEAT-011 | FEAT | alta | DBT-FEAT-009 | TDB | ⏳ Todo | Conciliacion de pagos parciales de CC contra deuda reflejada en EXP y rollover de remanente al siguiente periodo de tarjeta. |
| DBT-FEAT-012 | FEAT | media | DBT-FEAT-006 | TDB | ⏳ Todo | Dashboard unificado de deuda (CC + no CC) por origen, fuente, vencimiento y estado, sin mezclar fuentes canonicas. |
| DBT-FEAT-013 | FEAT | media | | TDB | ✅ Done | Junto al resumen del periodo TC muestra comisiones acumuladas de extracciones en efectivo (`cash_advance_commission_total` en API + UI en CreditCardManager). |
| DBT-FEAT-014 | FEAT | baja | | TDB | ✅ Done | Spinner de carga en tabla de deudas (`DebtManager` modo debts) |
| DBT-FEAT-015 | FEAT | Alta | | TDB | ✅ Done | IVA sobre intereses configurable en alta/edición de deuda (default 21%); impacta monto de cuota proyectada en Presupuesto. |
| DBT-BUG-016 | BUG | Alta | | TDB | ✅ Done | Al registrar pago, el modal prellenaba el saldo total del préstamo en lugar de la cuota actual; fix en `DebtManager` + listado de pagos con opción eliminar para revertir pagos erróneos. |
| DBT-BUG-017 | BUG | Alta | DBT-BUG-016 | TDB | ✅ Done | Formato decimal AR unificado en UI + parseo correcto; fix backend: proyecciones mantienen cuota fija por mes (marzo=cuota 1) y marcan PAGADA al registrar pago completo. |
| DBT-FEAT-18 | FEAT | Alta || Dashboard en pagina de deudas, con gráficos de torta con montos totales separados por prestamista, otro por tipo (deuda tarjeta, préstamos personal, privados no bancarios etc) y un tercero de barras apiladas (total y pago realizados) de cada deuda. |

### Analisis de coherencia (DBT-FEAT-004 a DBT-FEAT-008)

- DBT-FEAT-004: Coherente para deuda no tarjeta; insuficiente para integracion CC porque no contempla pagos aplicados por ciclo de tarjeta ni arrastre de saldo por periodo.
- DBT-FEAT-005: Coherente y necesario; debe extenderse a montos espejo de EXP cuando provienen de CC para mantener consistencia visual/contable.
- DBT-FEAT-006: Parcialmente coherente; hoy habla solo de fuente de deuda DBT. Para la propuesta debe diferenciar origen (`CC` vs `DBT`) y luego permitir agregacion total.
- DBT-FEAT-007: Igual que FEAT-006 pero en corte mensual; debe usar calendario de periodos CC ademas de mes calendario.
- DBT-FEAT-008: Coherente para serie temporal, pero requiere normalizar eje temporal entre periodos de tarjeta y meses para no distorsionar tendencia.

Conclusion operativa:
- Mantener DBT-FEAT-004..008 vigentes para el eje no tarjeta.
- Agregar DBT-FEAT-009..012 para cubrir integracion CC+EXP+DBT y luego converger en dashboard unificado.

## Propuesta: Credit Cards + Debts (no card) integrado con Gastos/Ingresos

- Carga de gastos de tarjeta ingresar en modulod e Gastos/Ingresops (EXP) como gasto a crédito (deuda)
   - Comentario: Coherente como movimiento operativo (consumo), pero para coherencia contable se debe evitar que EXP sea fuente canonica de deuda; EXP deberia reflejar impacto presupuestario y la deuda consolidada mantenerse en CC (para tarjeta) y DBT (no tarjeta).
  - Flujo:
    - 1. El usuario ingreso a módulo Tarjeta de Credito (CC)
         - Comentario: Correcto. Punto de entrada coherente con la implementación actual, donde CC ya administra compras, periodos y pagos.
    - 2. El usuario regista un gastos del periodo vigente.
         - Comentario: Correcto para gasto devengado en periodo CC. Requiere validar fecha contra configuracion de periodo (closing/due) para evitar imputaciones inconsistentes.
    - 3. El sistema regitra el gasto en módulo CC y un registro en módulo EXP como gasto a pagar (deuda)
         - Comentario: Coherente si se define patron espejo con id de trazabilidad (`cc_purchase_id` o equivalente) y regla anti-duplicado. El registro en EXP deberia ser proyeccion/impacto, no duplicar saldo canonico.
    - 4. Validar si se justifica ingresar el item en módulo deuda (DBT) (originalmente no era para CC).
         - Comentario: Recomendacion funcional: no crear DBT para consumos normales de tarjeta; si para casos excepcionales (ej. extraccion en efectivo) donde ya existe naturaleza de deuda separada. Esto evita doble conteo de pasivos.
    - 5. En saldo de modulo EXP esos gastos se deben imputar como deuda a vencer en fecha de vencimiento de periodo actual de la CC.
         - Comentario: Contablemente correcto si se modela como pasivo de corto plazo por resumen de tarjeta. Debe calcularse por periodo de cierre, no por mes calendario puro.
    - 6. Cuando se paga la CC descontar esa deuda registrada hasta el mondo pagado, si queda deuda pendiente se computa como deuda para el siguiente periodo (tener en cuenta la opción de varios pagos parciales).
         - Comentario: Critico para coherencia contable. Debe aplicarse waterfall de pagos (minimo/intereses/capital segun regla definida) y trasladar saldo remanente al siguiente periodo sin crear inconsistencias entre CC y EXP.
    - 7. Unificar Dashboard de deudas por CC y no CC (prestamos bancarios etc.)
         - Comentario: Muy recomendable. Debe unificarse en vista analitica, no necesariamente en tabla fisica unica; conviene capa de agregacion que combine fuentes canonicas diferentes (CC y DBT).

### Comentarios de coherencia contable (resumen)

- Principio recomendado: una sola fuente de verdad por tipo de deuda.
- CC debe ser fuente canonica para deuda de tarjeta (resumen, pagos, remanente).
- DBT debe seguir como fuente canonica para deuda no tarjeta.
- EXP debe actuar como capa de impacto/presupuesto y trazabilidad operacional, no como saldo canonico duplicado.

### Funcionalidades propuestas a diseñar e implementar

- Definir contrato de trazabilidad cruzada CC->EXP: `cc_purchase_id`, `cc_statement_id`, `source_module`, `source_id`, `sync_status`.
- Implementar regla anti-duplicado contable: no permitir que el mismo pasivo se compute dos veces entre CC, EXP y DBT.
- Crear conciliador de saldos por periodo: total resumen CC, pagos aplicados, remanente y reflejo en EXP.
- Definir politica formal para casos frontera: extraccion en efectivo, refinanciacion, adelantos y recategorizacion a DBT.
- Diseñar motor de asignacion de pagos parciales de CC con orden de aplicacion auditable y reproducible.
- Implementar vista unificada de deuda (Dashboard) con dimensiones: origen (CC/DBT), fuente, vencimiento, estado, saldo, riesgo.
- Agregar alertas de desalineacion entre modulos (CC vs EXP vs DBT) con tolerancia configurable.
- Incorporar pruebas end-to-end de conciliacion contable (alta gasto CC -> reflejo EXP -> pago parcial -> rollover -> dashboard).


## Bugs Pendientes

| ID | Tipo | Prioridad |Issue Padre|Issue Gitea| Estado | Resumen |
|---|---|---|---|---|---|---|
| DBT-BUG-001 | bug | media | DBT-FEAT-004 | #151 | ⏳ Todo | Corregir desalineacion entre pago registrado en Presupuesto y saldo pendiente en `debt-records`. |
| DBT-BUG-002 | bug | media | DBT-FEAT-005 | #152 | ⏳ Todo | Corregir inconsistencias de redondeo de cuotas fraccionarias entre vistas y persistencia. |
| DBT-BUG-003 | bug | alta | DBT-FEAT-002 | TDB | ✅ Done | Separado sidebar en dos entradas (`Presupuesto` y `Deudas`) y desacoplado el flujo UI/API por vista: Presupuesto vuelve a modal/edicion clasica (fijo/variable, gasto/ingreso) y Deudas mantiene formulario especifico de deuda. |
| DBT-BUG-004 | bug | alta | DBT-BUG-003 | TDB | ✅ Done | Corregida la proyeccion mensual: ya no se concentra en un solo mes; se proyecta por calendario y se reconcilian meses faltantes. Evidencia: pruebas `test_create_without_due_date_defaults_next_month_and_generates_12`, `test_projection_count_matches_remaining_installments` y `test_reconcile_restores_missing_projection_rows`. |
| DBT-BUG-005 | bug | media | DBT-BUG-003 | TDB | 🔄 In progress | Implementado fix frontend: estado de cuotas ahora usa cuota actual/total (ej. 1/12) en lugar de 0/12 por campo inexistente. Pendiente smoke test funcional. 
Revisión DBT-BUG-005: En deuda muestra como ejecutado el mesa actual, eso al crear la deuda está pendiente.|
| DBT-BUG-006 | bug | alta | DBT-BUG-003 | TDB | 🔄 In progress | Implementado fix estructural en proyección mensual para evitar duplicación en mes de vencimiento y desalineación tabla/gráfico en Presupuesto. Pendiente validación con datos reales. 
Pendiente smoke test funcional luego de resueltos DBT-BUG-004 y DBT-BUG-005|

## Plan Issues Gitea (Padres Front/Back por FEAT)

Regla acordada:
- Para cada `DBT-FEAT-00X` crear 2 issues padre en Gitea: uno Front y uno Back.
- No crear sub-issues.
- Las tareas se cargan como checklist dentro de cada issue padre.

| Key | FEAT | Tipo | Titulo sugerido | Estado |
|---|---|---|---|---|
| DBT-FEAT-001-BE | DBT-FEAT-001 | Back | [DBT-FEAT-001][BE] Fuente de verdad debt-records + contratos API | ✅ Done |
| DBT-FEAT-001-FE | DBT-FEAT-001 | Front | [DBT-FEAT-001][FE] Consumo front de debt-records como fuente primaria | ✅ Done |
| DBT-FEAT-002-BE | DBT-FEAT-002 | Back | [DBT-FEAT-002][BE] Alta de deuda con campos completos | ✅ Done |
| DBT-FEAT-002-FE | DBT-FEAT-002 | Front | [DBT-FEAT-002][FE] Formulario alta/edicion con campos completos | ✅ Done |
| DBT-FEAT-003-BE | DBT-FEAT-003 | Back | [DBT-FEAT-003][BE] Proyeccion mensual a presupuesto por cuota | ✅ Done |
| DBT-FEAT-003-FE | DBT-FEAT-003 | Front | [DBT-FEAT-003][FE] Visualizacion de proyecciones y cuota X/Y | ✅ Done |
| DBT-FEAT-004-BE | DBT-FEAT-004 | Back | [DBT-FEAT-004][BE] Reconciliacion de pagos y saldo pendiente | ⏳ Todo |
| DBT-FEAT-004-FE | DBT-FEAT-004 | Front | [DBT-FEAT-004][FE] Flujo de pago parcial/total y estado de deuda | ⏳ Todo |
| DBT-FEAT-005-BE | DBT-FEAT-005 | Back | [DBT-FEAT-005][BE] Precision 2 decimales en cuotas fraccionarias | ⏳ Todo |
| DBT-FEAT-005-FE | DBT-FEAT-005 | Front | [DBT-FEAT-005][FE] Presentacion consistente de 2 decimales | ⏳ Todo |
| DBT-FEAT-006-BE | DBT-FEAT-006 | Back | [DBT-FEAT-006][BE] Agregados historicos por fuente de deuda | ⏳ Todo |
| DBT-FEAT-006-FE | DBT-FEAT-006 | Front | [DBT-FEAT-006][FE] Grafico de torta historico por fuente | ⏳ Todo |
| DBT-FEAT-007-BE | DBT-FEAT-007 | Back | [DBT-FEAT-007][BE] Agregados de mes actual por fuente | ⏳ Todo |
| DBT-FEAT-007-FE | DBT-FEAT-007 | Front | [DBT-FEAT-007][FE] Grafico de torta mes actual por fuente | ⏳ Todo |
| DBT-FEAT-008-BE | DBT-FEAT-008 | Back | [DBT-FEAT-008][BE] Serie 12 meses para variacion de deuda | ⏳ Todo |
| DBT-FEAT-008-FE | DBT-FEAT-008 | Front | [DBT-FEAT-008][FE] Grafico barras + linea (12 meses) | ⏳ Todo |
| DBT-FEAT-009-BE | DBT-FEAT-009 | Back | [DBT-FEAT-009][BE] Contrato de trazabilidad CC->EXP y persistencia de origen | ⏳ Todo |
| DBT-FEAT-009-FE | DBT-FEAT-009 | Front | [DBT-FEAT-009][FE] Reflejo UI de gastos CC en EXP con enlace de trazabilidad | ⏳ Todo |
| DBT-FEAT-010-BE | DBT-FEAT-010 | Back | [DBT-FEAT-010][BE] Reglas anti-duplicado contable CC/EXP/DBT | ⏳ Todo |
| DBT-FEAT-010-FE | DBT-FEAT-010 | Front | [DBT-FEAT-010][FE] Señalizacion UX de movimientos deduplicados y origen canonico | ⏳ Todo |
| DBT-FEAT-011-BE | DBT-FEAT-011 | Back | [DBT-FEAT-011][BE] Conciliador de pagos parciales y rollover por periodo CC | ⏳ Todo |
| DBT-FEAT-011-FE | DBT-FEAT-011 | Front | [DBT-FEAT-011][FE] Flujo de pagos parciales CC con estado de deuda reflejada en EXP | ⏳ Todo |
| DBT-FEAT-012-BE | DBT-FEAT-012 | Back | [DBT-FEAT-012][BE] Agregador unificado de deuda CC+DBT para analytics | ⏳ Todo |
| DBT-FEAT-012-FE | DBT-FEAT-012 | Front | [DBT-FEAT-012][FE] Dashboard unificado de deuda por origen/fuente/vencimiento | ⏳ Todo |

## Guía de Prueba Manual UI - DBT-FEAT-004 (Pago parcial/total)

Objetivo:
- Validar desde UI que el registro de pagos en Deudas actualiza saldo, estado y avance de cuotas sin recarga manual.

Precondiciones:
- Usuario con permisos para leer y escribir deudas (`debt_records.read` y `debt_records.write`).
- Backend y Frontend levantados.
- Existencia de al menos una deuda no tarjeta activa, o capacidad de crearla desde UI.

Datos sugeridos para prueba controlada:
- Nombre: Prestamo Prueba FEAT-004
- Monto total: 1.200.000
- Interes anual: 0 (para hacer trazable el calculo manual)
- Total cuotas: 12
- Cuota actual: 1
- Cuotas pendientes: 12

Paso a paso:

1. Crear deuda base en UI
- Ir a Deudas.
- Click en Nueva Deuda.
- Completar los datos sugeridos y guardar.
- Verificar que la fila aparece con estado Pendiente y cuotas 1/12.

2. Validar apertura del flujo de pago
- En la fila de la deuda creada, hacer click en Pagar.
- Verificar que se abre modal Registrar Pago.
- Verificar que se visualiza el saldo pendiente en el modal.

3. Probar pago parcial
- En el modal, ingresar monto 250000 y una nota.
- Confirmar registro.
- Resultado esperado en UI:
   - Se muestra mensaje de exito.
   - La tabla se refresca automaticamente.
   - El saldo pendiente disminuye.
   - El avance de cuotas cambia (ejemplo esperado aproximado: 3.5/12).
   - El estado se mantiene Pendiente.

4. Probar validaciones de negocio
- Reabrir modal de pago para la misma deuda.
- Caso A: ingresar monto 0 o negativo.
   - Resultado esperado: mensaje de validacion y no registra pago.
- Caso B: ingresar monto mayor al saldo pendiente.
   - Resultado esperado: mensaje de validacion y no registra pago.

5. Probar pago total
- Reabrir modal de pago.
- Ingresar exactamente el saldo pendiente restante.
- Confirmar registro.
- Resultado esperado en UI:
   - La deuda queda con saldo 0.
   - Estado cambia a Pagada.
   - Cuotas pendientes quedan en 0.

6. Verificar consistencia de refresco sin recarga manual
- Sin refrescar navegador, revisar resumen superior y fila de la deuda.
- Resultado esperado: los valores visibles ya reflejan los pagos registrados.

Checklist de aceptación FEAT-004 (UI):
- [ ] Existe acción Pagar por fila en Deudas.
- [ ] Modal de pago muestra saldo pendiente.
- [ ] Pago parcial impacta saldo y progreso de cuotas.
- [ ] Pago inválido (<= 0 o sobrepago) es bloqueado.
- [ ] Pago total deja deuda en estado Pagada.
- [ ] La vista se actualiza sin recarga manual del navegador.

Evidencia recomendada a adjuntar:
- Captura antes de pagar (deuda activa).
- Captura después de pago parcial.
- Captura de validación por sobrepago.
- Captura después de pago total (deuda pagada).

### Template de checklist para issue Back (usar dentro de cada issue padre BE)

- [ ] Definir/ajustar contrato de endpoint para el FEAT.
- [ ] Implementar logica de negocio en servicio correspondiente.
- [ ] Ajustar persistencia/modelo y migracion Alembic si aplica.
- [ ] Agregar validaciones y manejo de errores.
- [ ] Verificar compatibilidad con datos existentes.
- [ ] Agregar/actualizar pruebas backend.

### Template de checklist para issue Front (usar dentro de cada issue padre FE)

- [ ] Ajustar cliente API y mapeo de payload/response.
- [ ] Implementar/ajustar componentes y estados de UI.
- [ ] Manejar mensajes de validacion y error.
- [ ] Verificar consistencia visual de tabla, resumen y graficos.
- [ ] Probar flujo end-to-end con backend actualizado.

## Avance Implementacion DBT-FEAT-001 (Backend)

Fecha: 2026-06-01

Implementado en codigo:
- Contratos tipados para API `debt-records` (create/update/payment) en `backend/main.py`.
- Endpoint consolidado para frontend: `GET /api/debt-records/projected`.
- Reglas de trazabilidad `DebtRecord -> BudgetItem` en `backend/services/debt_record_service.py`.
- Sincronizacion de proyeccion en create/update/payment/delete de deuda.
- Migracion Alembic para trazabilidad DBT en `budget_items`:
   - `debt_record_id`
   - `debt_quota_number`
   - `debt_total_quotas`
   - `debt_source`
   - archivo: `backend/alembic/versions/f1c2d3e4b5a6_add_dbt_traceability_to_budget_items.py`

Validado localmente:
- Migracion aplicada en BD local: `alembic_version = f1c2d3e4b5a6`.
- Columnas DBT en `budget_items` presentes: `debt_record_id`, `debt_quota_number`, `debt_total_quotas`, `debt_source`.
- API backend levantada y endpoint proyectado operativo: `GET /api/debt-records/projected` (401 sin token, esperado por autenticacion).
- Endpoint visible en OpenAPI (`/openapi.json`).

Pendiente de este FEAT:
- Ajuste de reglas de proyeccion por cuota `X/Y` para DBT-FEAT-003.

Avance Frontend #136 (2026-06-01):
- `DebtManager` consume `GET /api/debt-records/projected` como fuente primaria de datos.
- Se agrego `debtRecordsAPI` en frontend con mapeo a la forma de datos esperada por la UI actual.
- Alta/edicion/baja desde la vista de Deudas ahora usa endpoints de `debt-records`.

## Avance Implementacion DBT-FEAT-002 (Backend)

Fecha: 2026-06-01

Implementado en codigo:
- Campos nuevos en `DebtRecord` para alta completa:
   - `debt_source`
   - `total_installments`
   - `current_installment`
   - `pending_installments`
- Contratos API actualizados para create/update en `backend/main.py`.
- Persistencia y serializacion actualizadas en `backend/services/debt_record_service.py`.
- Regla backend para cuotas: si no se envia `pending_installments` y existen `total_installments` + `current_installment`, se calcula automaticamente.
- Detalle de proyeccion mensual ahora incluye cuota `X/Y` cuando corresponde.

Migracion Alembic:
- `backend/alembic/versions/c7d8e9f0a1b2_add_installment_fields_to_debt_records.py`
- Estado local validado: `alembic_version = c7d8e9f0a1b2`.

Pendiente para cerrar FEAT-002 completo:
- Integracion frontend del formulario con campos explicitos de tasa, cuota actual y total de cuotas (`#138`).


OpenPEC CLI (plantilla de carga)
- Nota: `openpec` no esta instalado actualmente en el entorno local. Se deja plantilla para ejecutar cuando este disponible.
- Ejemplo de alta de bug:
   `openpec issue create --id DBT-BUG-001 --type bug --priority alta --status todo --title "Editar fecha de gasto no mueve de periodo" --scope "backend,frontend"`
- Ejemplo de alta de feature:
   `openpec issue create --id DBT-FEAT-001 --type feature --priority alta --status todo --title "Alta de deuda no tarjeta con proyeccion a presupuesto" --scope "backend,frontend,data"`
- Ejemplo de transicion de estado:
   `openpec issue update --id DBT-BUG-001 --status in_progress`
   `openpec issue update --id DBT-BUG-001 --status done`

