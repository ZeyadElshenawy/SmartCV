# PDF Rendering — WeasyPrint + Windows GTK3 setup

The primary resume-PDF path uses **WeasyPrint** (`resumes/services/pdf_exporter.py`,
`resumes/services/resume_render.py`). WeasyPrint relies on the GTK3 / Pango / Cairo
native stack via cffi `dlopen`.

- macOS / Linux: install GTK3 + Pango via the system package manager — see the
  [official WeasyPrint install guide](https://doc.courtbouillon.org/weasyprint/stable/first_steps.html#installation).
- **Windows: install the GTK3 runtime separately** (see below). Python alone is not enough.

The legacy `generate_optimized_pdf` path (`resumes/services/pdf_generator.py`,
routed at `/resume/download/<job_id>/`) still uses xhtml2pdf and does **not** need GTK.

## Windows install

1. Install Python deps:
   ```powershell
   pip install -r requirements.txt
   ```
   `weasyprint>=67.0` and `tinycss2>=1.5` come from `requirements.txt`.

2. Install the GTK3 Windows runtime via winget (requires admin / UAC approval):
   ```powershell
   winget install tschoonj.GTKForWindows --accept-source-agreements --accept-package-agreements
   ```
   This installs to `C:\Program Files\GTK3-Runtime Win64\bin` and adds it to the
   system `PATH`. **Open a new shell after the install** so child Python
   processes pick up the updated `PATH`.

   Manual alternative — download `gtk3-runtime-3.24.31-2022-01-04-ts-win64.exe`
   from [tschoonj/GTK-for-Windows-Runtime-Environment-Installer](https://github.com/tschoonj/GTK-for-Windows-Runtime-Environment-Installer/releases/tag/2022-01-04)
   and run as admin.

3. Verify:
   ```powershell
   python -c "from weasyprint import HTML; b = HTML(string='<p>hi</p>').write_pdf(); print(len(b), b[:4])"
   ```
   Expected output: a four-digit byte count followed by `b'%PDF'`.

## Troubleshooting

- `OSError: cannot load library 'libgobject-2.0-0'` — GTK3 runtime not installed
  or `C:\Program Files\GTK3-Runtime Win64\bin` is not on `PATH`. Re-run step 2,
  then open a fresh shell.
- `ModuleNotFoundError: No module named 'tinycss2.color5'` — old `tinycss2 < 1.5`
  was already installed and blocked the upgrade. Force-upgrade:
  ```powershell
  pip install --upgrade "tinycss2>=1.5"
  ```
- Harmless startup noise about `GLib-GIO-WARNING Unexpectedly, UWP app ... has no
  verbs` comes from GLib scanning the Windows shell registry. It does not affect
  PDF output.
