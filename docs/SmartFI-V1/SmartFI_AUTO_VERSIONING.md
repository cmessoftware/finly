# Versionado Automático con Hash de Commit

## 📌 Cómo Funciona

La versión de la aplicación ahora se genera **automáticamente** usando el hash del último commit de Git. La versión se muestra en el formato: `v1.0.0.xxxxx` donde `xxxxx` son los primeros 5 caracteres del commit hash.

## Tabla de features

| ID | Tipo | Prioridad |Issue Padre|Issue Gitea| Estado | Resumen |
|---|---|---|---|
| UI-FEAT-001 |feat | Alta | TDB | TDB |✅ Done | Opción 1: Script Automático (Recomendado)|
| UI-FEAT-002 |feat | Alta | TDB | TDB |✅ Done | Opción 3: Script Interactivo|

## Tabla de bugs
| ID | Tipo | Prioridad |Issue Padre|Issue Gitea| Estado | Resumen |
|---|---|---|---|
| UI-BUG-001 | bug | Alta | UI-FEAT-001 | TDB |✅ Done | No muestra versión actual. Muestras siempre v1.0.0.dev | 


## 🚀 Despliegue Local

### Opción 1: Script Automático (Recomendado)

**Windows:**
```powershell
.\build.ps1
```

**Linux/Mac:**
```bash
chmod +x build.sh
./build.sh
```

Estos scripts automáticamente:
1. Obtienen el hash del commit actual
2. Lo pasan como variable de entorno a Docker
3. Construyen y levantan los contenedores
4. La versión se muestra correctamente en la UI

### Opción 2: Docker Compose Manual

```powershell
# Obtener el hash del commit
$env:COMMIT_HASH = git log -1 --format=%h

# Build y start
docker-compose build --no-cache
docker-compose up -d
```

### Opción 3: Script Interactivo

```powershell
.\scripts\docker-start.ps1
```

Este script también detecta automáticamente el commit hash.

## ☁️ Despliegue en Render

Render automáticamente inyecta la variable de entorno `RENDER_GIT_COMMIT` durante el build. El Dockerfile está configurado para usar esta variable automáticamente.

**No se requiere configuración adicional en Render** ✅

## 🔧 Detalles Técnicos

### Archivos Modificados:

1. **frontend/Dockerfile**
   - Acepta `ARG COMMIT_HASH` y `ARG RENDER_GIT_COMMIT`
   - Convierte a variable de entorno `VITE_COMMIT_HASH` para el build

2. **frontend/vite.config.js**
   - Define `__COMMIT_HASH__` como constante global reemplazada en tiempo de build

3. **frontend/src/components/Sidebar.jsx**
   - Lee `__COMMIT_HASH__` y muestra los primeros 5 caracteres

4. **docker-compose.yml**
   - Pasa `COMMIT_HASH` como build arg al servicio frontend

5. **build.ps1 / build.sh**
   - Scripts que automatizan el proceso de obtener el hash y hacer build

## 📝 Notas

- **Desarrollo local**: Usa `.\build.ps1` o `.\build.sh` para builds con versión correcta
- **Render**: Automático, usa `RENDER_GIT_COMMIT`
- **Sin git**: Si no hay repositorio git, usa "dev" como fallback
- **Reconstrucción**: Necesitas hacer rebuild para actualizar la versión (el hash se inyecta en tiempo de build, no en runtime)

## 🔍 Verificar Versión

Después del deploy, verifica la versión en:
- Esquina superior izquierda del sidebar
- Debe mostrar: `v1.2.0.xxxxx` donde `xxxxx` coincide con tu último commit

```powershell
# Ver tu commit actual
git log -1 --format=%h
```
