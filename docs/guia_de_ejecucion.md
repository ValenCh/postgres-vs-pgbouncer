# Guía de ejecución

Todos los comandos se ejecutan desde la raíz del proyecto (con el entorno virtual activo: `source .venv/bin/activate`).
**Las salidas marcadas _(ilustrativa)_ NO provienen de una ejecución real**; los números son placeholders.

## 0. Perfiles
| Perfil | Escala | Duración / calent. | Reps | Niveles V1 | V2 (legít. + atacantes) | Uso |
|---|---|---|---|---|---|---|
| `smoke` | 1 | 10 s / 3 s | 1 | 50 | 50 + 20 | Comprobar que todo funciona (minutos). **No es un experimento.** |
| `reducido` | 10 | 30 s / 10 s | 1 | 50–500 | 200 + 24 | Equipos modestos. No comparable con el paper. |
| `paper` | 50 | 120 s / 30 s | 3 | 50–2000 | 500 + 60 | Diseño del paper (72 puntos, ≈ 3,5 h). Comparable solo en hardware equivalente. |

Se editan en `config/experimento.toml`.

## 1. Iniciar el entorno e inicializar la base
```bash
cp .env.example .env     # una sola vez; cambie contraseñas
scripts/inicializar.sh smoke
```
Hace: validar `.env` → comprobar puertos 5432/6432 libres → generar `runtime/pgbouncer/*` → `docker compose up -d --build` →
esperar salud → `pgbench -i -s <escala>` (escala 50: 5.000.000 de filas, ~750 MB, 1–3 min según disco).

## 2. Comprobar salud
```bash
docker compose ps                                  # postgres y pgbouncer: healthy
docker compose exec postgres psql -U pgadmin -d bench -c "SHOW max_connections;"
python3 -m src.ejecutar_experimentos --vector todo --perfil smoke --solo-plan   # no envía carga
```
El orquestador repite estas comprobaciones (herramientas, `.env`, servicios, escala, parámetros de PostgreSQL) antes de cada ejecución.

## 3. Ejecutar
```bash
scripts/ejecutar_vector1.sh smoke           # solo Vector 1
scripts/ejecutar_vector2.sh smoke           # solo Vector 2
scripts/ejecutar_todo.sh smoke              # ambos
```
Equivalente directo y con todas las opciones:
```bash
python -m src.ejecutar_experimentos --vector {1|2|todo} --perfil {smoke|reducido|paper} \
    [--repeticiones 2] [--niveles 50,100] [--escenarios A,C] [--incluir-extras] [--si] [--solo-plan]
```
* `--repeticiones` y `--niveles` (solo V1) cambian el diseño: la ejecución queda marcada **no comparable** con el paper.
* `--incluir-extras` añade la variante D3a-PG (corte de 500 ms en PostgreSQL en vez de PgBouncer); no está en el paper.
* Sin `--si`, se muestra el plan y se pide confirmación (responder `N` cancela sin enviar carga).

**Salida _(ilustrativa)_:**
```text
PLAN DE EJECUCIÓN  run_id=20250101-120000-smoke  perfil=smoke: Prueba de funcionamiento...
  Puntos: 3 de 3 (nuevo) | 10 s + 3 s de calentamiento cada uno | repeticiones: 1
  Duración estimada: 0.0 h | escala pgbench: 1
  Docker: 8 CPU, 7900 MB, Docker Desktop, kernel 5.15...
╭ Resultados NO comparables directamente con el paper ╮
¿Iniciar la ejecución? [s/N] s

Punto 1/3 | Vector 1 | Escenario A | 50 clientes | repetición 1/1 | inicio 12:00:03
  medicion      ━━━━━━━━━━━━━━ 13/13s 0:00:13 ok=... rechazos=...
  OK TPS=...  P95=... ms  rechazo=... %  RAM=... MB
...
VECTOR 1 - RESUMEN EXPERIMENTAL
 Esc. | Clientes | TPS | P95 | Rechazo | RAM | CS/s | %Sys | Reps
Resultados guardados en: resultados/csv/<run_id>.csv
```
(El programa reemplaza los `...` por mediciones reales; una métrica no medida se muestra `N/D`.)

## 4. Progreso, interrupción y reanudación
* Progreso: barra por punto (fase `calentamiento`/`medicion`, aciertos y rechazos en vivo) y `Punto i/N`, repetición y nivel.
  Log técnico: `logs/<run_id>.log` (`tail -f`).
* **Interrumpir:** `Ctrl+C`. El orquestador envía SIGTERM al generador dentro del contenedor, detiene el muestreo,
  termina las conexiones residuales (`pg_terminate_backend`), registra el punto como `interrumpida` y muestra el comando para continuar.
* **Reanudar:** cada punto se guarda en el CSV apenas termina. Para continuar sin repetir los ya `ok`:
```bash
python -m src.ejecutar_experimentos --reanudar <run_id> --vector todo --perfil <mismo perfil>
```
* **Repetir sin mezclar datos:** cada ejecución nueva crea un `run_id` único (`AAAAMMDD-HHMMSS-perfil`), con su propio CSV y su
  propia carpeta en `data/raw/`. Nada se sobrescribe; no use `--reanudar` si quiere una ejecución independiente.

## 5. Consultar resultados
```bash
python -m src.analizar_resultados --ultima-ejecucion      # tablas con media ± desviación; guarda data/processed/<run_id>/
python -m src.comparar_paper --ultima-ejecucion           # Paper vs Medido (+ data/processed/<run_id>/comparacion_paper.csv)
python -m src.generar_graficos --ultima-ejecucion         # resultados/graficos/<run_id>/*.png
```
Los CSV de `resultados/csv/` tienen una fila por punto y repetición (campos en `metricas_y_resultados.md`). Las filas con
`estado` ≠ `ok` (`invalida`, `fallida`, `interrumpida`) se excluyen de los promedios y se listan al analizar.

## 6. Exportar el PDF
```bash
python -m src.generar_pdf --ultima-ejecucion          # o --run-id <id> ; --sin-referencia quita el paper de los gráficos
# PDF generado: /ruta/completa/resultados/pdf/informe_<run_id>.pdf
```
Solo con resultados reales: si no hay ejecuciones o ningún registro válido, **no genera nada** (no hay modo demo).
Si `reportlab`/`matplotlib` no pudieran instalarse, los CSV y los PNG siguen siendo la salida utilizable.

## 7. Detener y limpiar
```bash
scripts/limpiar.sh                # detiene contenedores
scripts/limpiar.sh --volumenes    # + borra la base (hay que reinicializar)
scripts/limpiar.sh --resultados   # + borra data/raw, data/processed, resultados/* (confirmación)
```
