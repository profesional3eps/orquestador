import importlib

try:
    importlib.import_module('app.services.messiah_autorizacion_pdf_service')
    importlib.import_module('app.api.routes')
    print('import ok')
except Exception as e:
    print('import error:', e)
    raise
