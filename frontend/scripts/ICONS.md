# Site icons

Regenerate the derived assets from the original white symbol on green and green
Cupesí Labs logotype on white. Keep those full-size sources outside the checkout.
The script requires Pillow from the project's Python environment and explicit
paths to regular and bold sans-serif TrueType fonts.

Run from the repository root, substituting local input paths:

```sh
uv run --no-sync python frontend/scripts/build_icons.py --symbol source-symbol.png --logotype source-logotype.png --font sans-regular.ttf --font-bold sans-bold.ttf
```

The output defaults to `frontend/public/`; `--output` selects another directory.
The current assets use Arial regular and bold with Pillow 13.0.0. Reusing those
inputs reproduces the files. The script prints the sampled theme color and file
sizes; keep the theme color in `frontend/index.html` aligned with the manifest
when changing the source brand color.

Icons use Lanczos downsampling and opaque backgrounds. The maskable icons keep
the symbol inside the central safe circle. The social preview contains the
logotype, site name, and English subtitle on white, with a 300 KB size limit.
Vite copies `public/` to the output for both the API frontend and static builds.
The icon links use the configured base path; social preview URLs use the
canonical domain.

Verify with `npm run check`, `npm test`, `npm run build`, and
`npm run build:static` in `frontend/`. The static build requires `LIVE_DATA_DIR`
pointing to a local checkout of the live-data branch. Run
`npm run test:e2e:static -- icons-static.spec.ts` to check the served assets and
metadata in the static preview.
