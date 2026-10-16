# Pruebas de resiliencia de la capa de conexión de PostgreSQL (PgBouncer vs. nativo)

Proyecto para reproducir y medir, en un entorno **local y aislado con Docker**, los dos vectores del paper
*“Evaluación Empírica de la Resiliencia en la Capa de Conexión de PostgreSQL ante Ataques de Denegación de Servicio mediante Multiplexación Transaccional”*
(UTN FRLP): **V1** inundación de conexiones (A nativo, B sesión, C transacción; 50–2000 clientes) y **V2** retención de transacciones inactivas (A, D1, D2, D3a, D3b, D3c).

> Los valores del paper están en `config/referencia_paper.json` como **referencia publicada**. Todo lo que producen los scripts son **mediciones nuevas**, guardadas por separado. Nada se inventa ni se rellena con ceros (`N/D` si no se midió).

## Inicio rápido (5 comandos, perfil de prueba de funcionamiento)
```bash
cp .env.example .env && nano .env            # cambie las 3 contraseñas
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
scripts/inicializar.sh smoke                  # levanta Docker e inicializa pgbench
scripts/ejecutar_todo.sh smoke                # ambos vectores, minutos (NO es un experimento)
python -m src.generar_pdf --ultima-ejecucion  # PDF en resultados/pdf/
```
Experimento completo (≈ 3,5 h; requiere ≥ 9 CPU y ≥ 8 GB para Docker): `scripts/inicializar.sh paper && scripts/ejecutar_todo.sh paper`.
Equipos modestos: perfil `reducido` (**no comparable** con el paper). Windows: ver `docs/instalacion_windows.md` (flujo Docker Desktop + WSL2).

## Estado de verificación (léase antes de usar)
Verificado en Ubuntu 24.04 sin Docker: 51 pruebas unitarias, generador de carga y configuración de PgBouncer contra **PgBouncer 1.22.0 y PostgreSQL 16 reales**, y la cadena CSV → análisis → gráficos → PDF.
**No ejecutado:** `docker compose`, las imágenes y la capa Docker del orquestador (solo verificación estática). Detalle en `docs/limitaciones.md`.

## Hallazgos de la validación de parámetros
* `idle_transaction_timeout` **no existe en PostgreSQL 16** (D3a/D3c lo usan en **PgBouncer**, donde sí existe y acepta `0.5` s). D2 usa `idle_in_transaction_session_timeout` de PostgreSQL.
* El pool de 40 conexiones exige `default_pool_size=40` (el defecto 20 por par db/usuario lo impediría).
* La reacción del atacante ante un corte es ambigua en el paper; se ofrecen dos modos (`v2.modo_ataque`).

## Estructura
```text
config/      experimento.toml (todos los parámetros), referencia_paper.json, postgres/, pgbouncer/ (plantilla)
scripts/     inicializar, ejecutar_vector1/2/todo, limpiar, sonda_recursos (dentro de contenedores)
src/         generador_carga, ejecutar_experimentos, recolectar_metricas, analizar_resultados,
             comparar_paper, generar_graficos, generar_pdf, presentacion, configuracion, utilidades
tests/       configuración, métricas, resultados (51 pruebas, sin Docker)
docs/        instalación (Linux/Windows), ejecución, metodología, métricas, limitaciones, arquitectura, troubleshooting
data/raw | data/processed | resultados/{csv,graficos,pdf}   (generados; fuera de git)
```
Pruebas: `python -m pytest tests -q`. Documentación completa: `docs/README.md`.
