def _shim_cairocffi_if_missing() -> None:
    """If ``cairocffi`` can't load its C dependency (libcairo-2.dll on
    Windows installs without GTK+), inject a stub before reportlab /
    xhtml2pdf import. Resume PDFs are text-only — they don't actually
    need a working cairo backend for the renderPM image path — so the
    stub keeps the import chain alive and PDF generation works.

    Without this shim, the cairocffi top-level ``dlopen`` raises OSError
    at module import time, which propagates up through:
        rlPyCairo → reportlab.graphics.renderPM → xhtml2pdf_reportlab →
        xhtml2pdf.tags → xhtml2pdf.tables → xhtml2pdf.parser →
        xhtml2pdf.pisa
    and PDF export 500s before we ever get to render a template.

    Idempotent — only stubs when the real cairocffi can't initialise.
    """
    import sys
    # Already shimmed or already working — leave alone.
    if 'cairocffi' in sys.modules:
        return
    try:
        import cairocffi  # noqa: F401 — real cairo works
        return
    except OSError as e:
        if 'cairo' not in str(e).lower():
            # Some other OSError — let it surface.
            raise
        # Fall through to install stub.
    import types
    stub = types.ModuleType('cairocffi')
    # Stable identity so stdlib introspection (inspect.getsourcefile,
    # debug_toolbar's SQL stacktrace recorder, etc.) doesn't blow up
    # walking the stub's metadata. Without these, the catch-all
    # __getattr__ below answered every dunder lookup with _StubObj
    # (the class), which then crashed `filename.endswith(...)` deep
    # in inspect.getsourcefile.
    stub.__file__ = '<cairocffi stub>'
    stub.__spec__ = None
    stub.__loader__ = None
    stub.__path__ = []

    # Minimum surface area rlPyCairo touches during its top-level import:
    # version metadata + a couple of class references. Anything else gets
    # an attribute access that returns a no-op callable so deeper code
    # paths don't blow up either (resume PDFs don't actually invoke them).
    stub.cairo_version = lambda: 11600
    stub.cairo_version_string = lambda: '1.16.0 (stub)'
    stub.FORMAT_ARGB32 = 0

    class _StubObj:
        def __init__(self, *a, **k):
            pass

        def __getattr__(self, name):
            return _StubObj()

        def __call__(self, *a, **k):
            return _StubObj()
    stub.Context = _StubObj
    stub.ImageSurface = _StubObj
    stub.Surface = _StubObj
    stub.Matrix = _StubObj
    stub.FontOptions = _StubObj

    # Some downstream code does ``from cairocffi import xxx``; missing
    # NON-dunder attributes default to a stub callable rather than
    # AttributeError. Dunders pass through (AttributeError) so stdlib
    # introspection sees expected "missing" semantics rather than a
    # non-string stand-in (the bug fixed alongside this comment).
    def _stub_getattr(name):
        if name.startswith('__') and name.endswith('__'):
            raise AttributeError(name)
        return _StubObj
    stub.__getattr__ = _stub_getattr

    sys.modules['cairocffi'] = stub
    sys.modules.setdefault('cairo', stub)


def generate_pdf(resume_obj, output_path, template_name='standard'):
    """
    Generate PDF from resume object using xhtml2pdf.

    Honors the user's saved section_order from resume.content if present,
    falling back to the default order otherwise. Templates iterate over
    `section_order` in their body so the rendered PDF matches the live
    preview's stacking on the edit page.
    """
    # Pre-emptively shim cairocffi so the xhtml2pdf import chain survives
    # on Windows installs without libcairo-2.dll. Resume PDFs are text-
    # only; the missing image backend doesn't affect output.
    _shim_cairocffi_if_missing()
    from django.template.loader import render_to_string
    from xhtml2pdf import pisa
    from resumes.views import RESUME_SECTION_KEYS, DEFAULT_SECTION_ORDER

    if template_name and template_name != 'standard':
        template_file = f'resumes/pdf_template_{template_name}.html'
    else:
        template_file = 'resumes/pdf_template.html'

    user = resume_obj.gap_analysis.job.user
    profile = user.profile

    # Resolve the section order with the same defensive logic as the edit
    # view: validate saved keys against the whitelist, fill in any missing
    # ones at the end so a partial order still produces a complete PDF.
    saved = (resume_obj.content or {}).get('section_order') or []
    valid_saved = [s for s in saved if s in RESUME_SECTION_KEYS]
    section_order = valid_saved + [s for s in DEFAULT_SECTION_ORDER if s not in valid_saved]

    html_string = render_to_string(template_file, {
        'resume': resume_obj.content,
        'user': user,
        'profile': profile,
        'section_order': section_order,
    })

    with open(output_path, "w+b") as result_file:
        pisa_status = pisa.CreatePDF(html_string, dest=result_file)

    if pisa_status.err:
        raise Exception(f"Failed to generate PDF with xhtml2pdf: {pisa_status.err}")

    return output_path
