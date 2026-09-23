import os
import sys
import tempfile
from pathlib import Path

# Permite "from core.xxx import ..." al correr pytest desde cualquier carpeta,
# igual que hace main.py al insertar la raíz de aIBechos/ en sys.path.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Los logs de los tests NO van al %APPDATA% real del usuario.
#
# Varios módulos de core/ crean su logger al importarse
# (`_log = get_logger(..., "app.log")`), así que basta con importarlos desde un
# test para que la suite empiece a escribir en el app.log de verdad. Pasaba: al
# ejecutar los tests aparecían líneas "Duplicado película: 'Mi Pelicula'..." con
# datos de prueba mezcladas con la actividad real, en el mismo fichero que luego
# se pide al usuario para diagnosticar un fallo.
#
# Esto tiene que ir en conftest.py y no en cada test: se ejecuta ANTES de que
# pytest importe los módulos de prueba (y con ellos los de core/), que es el
# único momento en que se puede cambiar a dónde apunta el handler. Los tests que
# aíslan su propio log (test_applog_rotation.py, test_ai_title_fallback.py)
# siguen funcionando igual: monkeypatch restaura este valor, no el real.
from core import applog as _applog

_TEST_LOG_DIR = Path(tempfile.mkdtemp(prefix="aibechos-tests-"))
_applog.app_data_dir = lambda: _TEST_LOG_DIR
