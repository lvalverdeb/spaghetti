# spaghetti-detector

Detector de código espagueti y "malos olores" arquitectónicos.

Escanea los paquetes del espacio de trabajo en busca de anti-patrones, violaciones arquitectónicas y olores estructurales del código — desde problemas en una sola función (funciones largas, anidamiento profundo, alta complejidad cicломática) hasta problemas que solo se detectan al ver entre archivos: importaciones circulares reales (no solo una heurística padre/hijo), cuerpos de funciones copiados y pegados, y el patrón de duplicación gemela síncrono/asíncrono (`load`/`aload`, `foo`/`foo_async`) donde una corrección aplicada a una gemela silenciosamente nunca llega a la otra.

Cada paquete solicitado se revisa de forma concurrente — un agente por paquete — y luego se consolida en un único informe.

El registro de paquetes es genérico y configurable mediante un archivo YAML, parámetros ad-hoc de la línea de comandos, o ambos — véase [Configurar Paquetes](#configurar-paquetes).

## Por Qué Existe

El código espagueti generado por IA — comúnmente llamado "código basura" — es extremadamente común porque los Modelos de Lenguaje Grande (LLM) priorizan la finalización funcional inmediata (la "ruta feliz") sobre la arquitectura de software a largo plazo. Aunque se ve sintácticamente perfecto y está muy comentado, a menudo sufre de problemas estructurales:

- **Estructuras monolíticas:** La IA tiende a volcar grandes cantidades de lógica en archivos únicos y gigantes en lugar de separar responsabilidades.
- **Duplicación por copiar y pegar:** En lugar de refactorizar el código en funciones reutilizables, los LLM a menudo repiten el mismo bloque de código con variaciones menores.
- **Complejidad accidental:** Debido a que la IA carece de perspectiva del sistema completo, conecta las funcionalidades de una manera muy acoplada.
- **Dependencias alucinadas:** Un riesgo significativo donde la IA sugiere el uso de bibliotecas o paquetes que no existen.

Esta prevalencia proviene de la naturaleza fundamental del entrenamiento de la IA: los LLM están optimizados para predecir el siguiente token lógico basándose en probabilidad, no para diseñar software mantenible. Aunque una IA puede producir un script funcionando rápidamente, carece de la intuición anticipada que los desarrolladores experimentados utilizan para construir aplicaciones modulares y escalables.

El código espagueti escrito por humanos es extremadamente común y ha existido desde los inicios de la programación. Mientras que la IA crea código desordenado debido a una falta de conciencia situacional, los humanos usualmente lo crean por presión de tiempo, requisitos cambiantes, o falta de experiencia.

### Por Qué los Humanos Escriben Código Espagueti

- **Plazos ajustados:** Los desarrolladores se apuran para entregar funcionalidades, priorizando la velocidad sobre una arquitectura limpia.
- **Deriva de alcance:** Agregar constantemente nuevas funcionalidades a un sistema antiguo sin reescribir la estructura base.
- **Brechas de habilidades:** Los desarrolladores junior pueden no entender aún los patrones de diseño o cómo separar responsabilidades.
- **El hábito de "copiar y pegar":** Reutilizar bloques de código funcionando en un proyecto en lugar de construir funciones reutilizables.
- **Falta de revisiones de código:** Los equipos omiten revisiones entre pares, permitiendo que lógica desordenada pase a producción.

### IA vs. Código Espagueti Humano

- **Estilo humano:** A menudo presenta bucles anidados masivos, nombres de variables confusos (como `x` o `data1`), y notas `TODO` olvidadas.
- **Estilo de IA:** Generalmente se ve muy profesional, tiene una indentación perfecta, e incluye comentarios hermosos, pero la lógica subyacente está profundamente enredada y es redundante.

## Cómo Ayuda spaghetti-detector

Cada problema descrito arriba se corresponde con una o varias reglas mecánicamente aplicadas. El detector no adivina — mide umbrales concretos y reporta violaciones exactas.

### Mapeo de Problemas → Reglas

| Problema | Reglas del Detector | Qué Detecta |
|----------|-------------------|-------------|
| **Estructuras monolíticas** | `god-class`, `god-module`, `long-function`, `long-file`, `deep-nesting` | Clases con 25+ métodos, archivos de más de 400 líneas, funciones que exceden 50 líneas, anidamiento más allá de 5 niveles |
| **Duplicación por copiar y pegar** | `duplicate-function-body`, `sync-async-duplication` | Cuerpos de funciones idénticos (5+ líneas), pares gemelos síncrono/asíncrono con ≥60% de similitud textual |
| **Complejidad accidental** | `high-complexity`, `excessive-returns`, `message-chain`, `deep-inheritance`, `excessive-decorators` | Complejidad cicломática superior a 10, funciones con 4+ caminos de retorno, llamadas encadenadas más profundas que 3 niveles, herencia que excede 4 niveles |
| **Violaciones de capas** | `layer-violation`, `transport-in-library`, `import-cycle`, `encapsulation-violation` | Código de biblioteca importando marcos de transporte, cadenas de importaciones circulares, acceso a atributos privados entre objetos |
| **Brechas de seguridad de tipos** | `missing-return-type`, `missing-param-type`, `untyped-dict`, `bare-except` | Funciones públicas sin anotaciones, `dict` sin tipo en anotaciones, cláusulas `except:` sin tipo |
| **Código muerto y desorden** | `dead-code`, `unused-import`, `star-import`, `todo-marker`, `magic-number` | Sentencias inalcanzables después de `return`/`raise`/`break`, `from x import *`, literales numéricos sin explicación |

### De la Detección a la Remediación

El detector produce un informe consolidado con una puntuación de salud y calificación por paquete:

```
  Package          Grade   Score   Files   KLOC   Issues
  ──────────────── ───── ───────  ────── ────── ───────
  boti-data           B    78.3       18   3.2       12
  etl-core            A    92.1       14   2.8        5
  OVERALL             B    82.5       32   6.0       17
```

Use `--plan` para obtener una hoja de ruta priorizada de remediación puntuada por `peso_severidad × esfuerzo_corrección`:

```bash
uv run spaghetti --plan --top 10
```

```
  #   Pri  Rule                           Sev  Effort     Issues  Score
  ─── ──── ────────────────────────────── ──── ───────── ──────  ─────
  1   P0   import-cycle                   ✖    major        3   30.0
  2   P0   god-class                      ✖    major        2   30.0
  3   P1   high-complexity                ⚠    moderate     5   15.0
  4   P1   long-function                  ⚠    moderate     4   12.0
```

Esto garantiza que se corrijan los problemas estructurales (importaciones circulares, dioses de clase) antes de los cosméticos (anotaciones de tipos faltantes, números mágicos) — maximizando el impacto por unidad de esfuerzo.

## Uso

```bash
uv run spaghetti
uv run spaghetti --packages boti-data boti-dask
uv run spaghetti --severity error
uv run spaghetti --top 10 --exclude tests/ examples/
uv run spaghetti --json > report.json
uv run spaghetti --plan --top 10
uv run spaghetti --config spaghetti.yaml
uv run spaghetti --package my-lib=my-lib/src/my_lib
```

Códigos de salida: `0` (limpio), `1` (advertencias presentes), `2` (errores presentes) — seguro para integrar en CI como puerta de control.

### Opciones

| Parámetro | Valor por Defecto | Descripción |
| --- | --- | --- |
| `--config` | ninguno | Archivo YAML con un mapeo `packages: {name: path}` (véase abajo); reemplaza los valores por defecto integrados |
| `--package` | ninguno | Agrega o sobreescribe un paquete como `NAME=PATH` (repetible); se aplica encima de `--config` o los valores por defecto |
| `--packages` | todos los paquetes resueltos | Nombres a escanear del registro resuelto |
| `--severity` | `info` | Severidad mínima a mostrar (`info` / `warning` / `error`) |
| `--json` | desactivado | Salida como JSON en lugar del informe en consola |
| `--top` | `5` | Número de peores archivos a listar por paquete |
| `--exclude` | ninguno | Subcadenas de ruta a excluir del escaneo |
| `--min-duplicate-lines` | `5` | Longitud mínima de función a considerar para detección de cuerpos duplicados |
| `--twin-similarity` | `0.6` | Radio mínimo de similitud textual (0–1) para señalar un par gemelo síncrono/asíncrono |
| `--plan` | desactivado | Salida de un plan de remediación priorizado en lugar del informe estándar |

Ejecute `uv run spaghetti --help` para la lista completa.

## Supresión en Línea

Suprima hallazgos específicos en una línea con `# spaghetti-ignore[regla]`:

```python
# Suprimir una regla específica
def f():  # spaghetti-ignore[long-function]: intencionalmente grande
    ...

# Suprimir todas las reglas en una línea
x: dict = {}  # spaghetti-ignore: revisado, sin problema
```

El marcador se aplica a la línea donde aparece y a la línea directamente encima (de modo que un marcador puede colocarse encima de una línea `def` demasiado larga para un comentario al final). Los hallazgos suprimidos se contabilizan en el informe (`suppressed: N` en el encabezado) en lugar de eliminarse silenciosamente — permanecen visibles.

## Salida JSON

Con `--json`, el informe es un único objeto JSON en stdout:

```json
{
  "issues": [
    {
      "file": "src/my_module.py",
      "line": 42,
      "severity": "warning",
      "rule": "long-function",
      "message": "my_func() is 65 lines (max 50)",
      "package": "my-lib"
    }
  ],
  "suppressed": 3
}
```

## Plan de Remediación

Con `--plan`, el detector genera un orden de corrección priorizado en lugar del informe estándar. Cada regla se puntúa con `peso_severidad × esfuerzo_corrección` y se agrupa en niveles de prioridad (P0–P3):

```bash
uv run spaghetti --plan --top 10
```

**Niveles de prioridad:**
- **P0** (puntuación ≥ 12): CRÍTICO — corregir inmediatamente (p. ej., importaciones circulares, dioses de clase)
- **P1** (puntuación ≥ 7): ALTO — corregir este sprint
- **P2** (puntuación ≥ 3): MEDIO — planificar para el próximo ciclo
- **P3** (puntuación < 3): BAJO — rastrear en el backlog

El plan agrupa los problemas por regla, cuenta los archivos afectados y lista un orden de corrección recomendado. Esto facilita iniciar un ciclo de mejora de calidad del código con las correcciones de mayor impacto primero.

## Reglas

El detector verifica **36 reglas** en cuatro niveles:

**Verificaciones AST por archivo (30 reglas):** `long-function`, `high-complexity`, `missing-return-type`, `missing-param-type`, `too-many-params`, `excessive-returns`, `boolean-flag-params`, `deep-nesting`, `untyped-dict`, `unused-import`, `swallowed-exception`, `duplicate-branch`, `encapsulation-violation`, `god-class`, `layer-violation`, `transport-in-library`, `potential-circular-import`, `god-module`, `mutable-default`, `bare-except`, `star-import`, `global-mutable`, `scope-mutation`, `dead-code`, `message-chain`, `excessive-decorators`, `magic-number`, `missing-else`, `lazy-class`, `deep-inheritance`.

**Verificaciones de texto fuente por archivo (2 reglas):** `long-file`, `todo-marker`.

**Verificaciones de infraestructura (1 regla):** `syntax-error` (archivos que fallan `ast.parse()`).

**Verificaciones entre archivos por paquete (3 reglas):** `import-cycle`, `duplicate-function-body`, `sync-async-duplication`.

Véase [SDD.md](SDD.md) para el catálogo completo de reglas, umbrales y fórmula de puntuación.

## Configurar Paquetes

Sin parámetros, `spaghetti` escanea los `DEFAULT_PACKAGES` de este espacio de trabajo (`boti`, `boti-data`, `boti-dask`; véase `src/spaghetti/detector.py`). Para apuntar a otros paquetes — en este espacio de trabajo, otro espacio de trabajo, o cualquier directorio en disco — use `--config` y/o `--package`.

**Precedencia:**
1. No se da ningún parámetro → los valores por defecto integrados se usan tal cual.
2. Se da `--config` → su mapeo `packages:` **reemplaza** los valores por defecto completamente, de modo que un archivo de configuración establece el conjunto completo explícitamente en lugar de heredar silenciosamente paquetes no relacionados codificados.
3. Las entradas `--package NAME=PATH` se superponen encima de cualquiera de los conjuntos producidos por (1) o (2) — agregando nuevos nombres o sobreescribiendo los ya definidos, de modo que un archivo de configuración y una adición rápida ad-hoc funcionan juntos.

### `--config`: Archivo YAML

```yaml
# spaghetti.yaml
packages:
  my-lib: my-lib/src/my_lib
  my-service: services/my-service/src/my_service
```

Las rutas se resuelven **relativas al directorio propio del archivo de configuración**, no al directorio de trabajo del invocador, de modo que la misma configuración funciona sin importar desde dónde se invoque `spaghetti`.

```bash
uv run spaghetti --config spaghetti.yaml
```

### `--package`: Entradas Ad-hoc en la Línea de Comandos

```bash
uv run spaghetti --package my-lib=my-lib/src/my_lib --package other=../other/src/other
```

Repetible; las rutas se resuelven relativas al directorio actual. Combínese con `--config` para sobreescribir o extender un archivo de configuración para una ejecución sin editarlo.

## Desarrollo

```bash
uv run pytest spaghetti/tests/
```
