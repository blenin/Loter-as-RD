# Loterías RD — MVP

Página estática con los resultados de las loterías dominicanas, actualizada
automáticamente varias veces al día.

## Cómo funciona

- `index.html` — la página. Lee los resultados de `data.json`.
- `data.json` — resultados del día. Lo genera `scripts/fetch_results.py`.
- `scripts/fetch_results.py` — descarga la página de resultados (portal
  informativo, no oficial), extrae los 3 premios de cada sorteo desde sus
  datos estructurados JSON-LD y reescribe `data.json`.
- `.github/workflows/update.yml` — GitHub Action que corre el script 11 veces al día,
  5 minutos después de cada sorteo, y publica los cambios automáticamente.
  Si un sorteo aún no publicó sus números, el script reintenta cada 15 minutos.

## Publicar en GitHub Pages

1. Crear el repositorio y subir estos archivos.
2. En el repo: Settings → Pages → Deploy from branch → `main` / `/ (root)`.
3. La página queda en `https://<usuario>.github.io/<repo>/`.

## Aviso

MVP provisional. Los datos vienen de un portal informativo, no de canales
oficiales: la página incluye el aviso de verificar antes de reclamar premios.
