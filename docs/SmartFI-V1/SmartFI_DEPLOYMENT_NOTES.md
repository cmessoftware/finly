Notas:
|Ambiente | Comentario |
|---|---|
| DEV | Ambiente DEV del usuario, PC de desarrollo  puerto 5173|
| TEST | Ambiente en docker  puerto 3000 | 
| PROD  | Ambiente Render + Neon |

## Render + Neon — checklist post-deploy

1. **Migraciones Alembic (obligatorio)**  
   El backend ejecuta `alembic upgrade head` al arrancar (`backend/start.sh`).  
   Si hay errores, ejecutar manualmente contra Neon:
   ```bash
   cd backend
   # DATABASE_URL = connection string de Neon (Render → smartfi-api → Environment)
   alembic upgrade head
   alembic current   # debe mostrar: d4e5f6a7b8c9 (head)
   ```
   En Windows también: `.\scripts\migrate-neon.ps1` (requiere `NEON_DATABASE_URL` en `.env`).

2. **CORS / FRONTEND_URL**  
   En Render → `smartfi-api` → Environment:
   - `FRONTEND_URL` = `https://smartfi-frontend.onrender.com`  
   El código también incluye esa URL por defecto; conviene tener la variable explícita.

3. **Errores CORS en el browser con 500**  
   Si la API devuelve 500 sin manejar la excepción, el navegador muestra "blocked by CORS" aunque el origen esté bien configurado. Revisar logs de Render (`smartfi-api` → Logs) para el error real (columna faltante, FK, etc.).

4. **Verificar salud**
   ```bash
   curl https://smartfi-api.onrender.com/api/health
   ```
