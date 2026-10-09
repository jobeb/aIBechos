"""Sub-pestañas de Configuración → 🖥 Cliente (cada equipo la suya): General,
Conexión FTP, Sincronizar visionado y Copia de seguridad. Mismos campos y
límites que App._collect_settings en Tk."""

from __future__ import annotations

import os
import re

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QHeaderView, QLineEdit,
                               QPushButton, QSlider, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from core.appdirs import is_linux, is_macos, is_windows
from core.transfer import PROTOCOL_LABELS, TOOLTIP_CONEXIONES_POR_ARCHIVO
from gui_qt import theme
from gui_qt.bridge import run_in_thread, ui
from gui_qt.settings.widgets import Page, desc_label, float_spin, hbox, int_or, set_status, status_label

_ACTIONS = ["Mantener original", "Mover a subcarpeta 'procesados'", "Eliminar original"]


class GeneralPage(Page):
    def build(self):
        c = self.card("Modo Automático", "La carpeta vigilada se monitoriza en segundo plano: detecta, "
                                         "renombra y sube archivos de vídeo automáticamente.")
        self.watch_folder = QLineEdit(self.cfg.get("watch_folder", ""))
        self.watch_folder.setPlaceholderText("Selecciona la carpeta que quieres vigilar...")
        browse = QPushButton("📂 Examinar")
        browse.setToolTip("Elegir la carpeta que vigilará el modo automático.")
        browse.clicked.connect(self._browse)
        row = hbox(self.watch_folder, browse, stretch=False)
        c.row("Carpeta vigilada:", row)
        self.bind("watch_folder", lambda: self.watch_folder.text().strip())
        c.spin("Intervalo de escaneo (seg):", "poll_interval", 5, 86400, 10)
        c.combo("Acción tras procesar (automático):", "auto_action", _ACTIONS, _ACTIONS[0])
        conf = QSlider(Qt.Horizontal)
        conf.setRange(0, 100)
        conf.setValue(int_or(self.cfg.get("min_confidence", 70), 70))
        conf.setFixedWidth(220)
        conf_lbl = desc_label(f"{conf.value()}%  (0 = aceptar todo)")
        conf_lbl.setWordWrap(False)
        conf.valueChanged.connect(lambda v: conf_lbl.setText(f"{v}%  (0 = aceptar todo)"))
        c.row("Confianza mínima (modo auto):", hbox(conf, conf_lbl))
        self.bind("min_confidence", conf.value)
        if is_windows():
            txt = "Iniciar con Windows (minimizado en bandeja)"
        elif is_macos():
            txt = "Iniciar con macOS (minimizado en bandeja)"
        elif is_linux():
            txt = "Iniciar con Linux (minimizado en bandeja)"
        else:
            txt = "Iniciar con el sistema (no disponible aquí)"
        sw = c.switch(txt, "start_with_windows", False)
        sw.setEnabled(is_windows() or is_macos() or is_linux())
        c.switch("Minimizar a la bandeja del sistema al cerrar la ventana (✕)",
                 "close_to_tray", False,
                 tip="Si está activo, el aspa de cerrar esconde la ventana en la bandeja\n"
                     "(al lado del reloj) y la app sigue funcionando en segundo plano.\n"
                     "Para salir del todo, usa \"Salir\" en el menú de la bandeja.")
        c.switch("Notificaciones de escritorio al completar subidas", "desktop_notifications", True)
        c.switch("Renombrar archivos en origen (local)", "rename_local", True)
        c.note("Si lo desactivas, los archivos locales conservan su nombre original al añadirlos o "
               "subirlos. El renombrado en el servidor se configura aparte, en Servidor → Plantillas.")
        c.switch("Descomprimir archivos comprimidos automáticamente (.zip, .7z, .rar, .tar...)",
                 "auto_extract_archives", False)
        c.note("La carpeta vigilada ya recorre subcarpetas. .zip, .7z y .tar (incluidos .tar.gz/.tgz, "
               ".tar.bz2/.tbz2, .tar.xz/.txz) funcionan siempre; .rar necesita tener \"unrar\" (o "
               "unar/bsdtar) instalado en el sistema.")

        c = self.card("Subida manual", "Se aplica cuando subes archivos a mano desde la pestaña Archivos.")
        c.combo("Acción tras procesar (manual):", "manual_action", _ACTIONS, _ACTIONS[0])

        c = self.card("Descargas con aMule",
                      "Conexión a aMule para buscar y descargar episodios que faltan desde la red "
                      "eDonkey/Kad. Necesitas tener aMule en ejecución y las External Connections "
                      "activadas (Preferencias > Control Remoto).")
        c.text("Host:", "amule_host")
        c.spin("Puerto:", "amule_port", 1, 65535, 4712)
        c.text("Contraseña:", "amule_password", secret=True, strip=False)
        c.subtitle("Desatasco automático de descargas colgadas en aMule",
                   "Si una descarga lleva tiempo sin avanzar (0 fuentes, velocidad 0 o % estancado), busca "
                   "otra fuente alternativa para el mismo capítulo. Cuando una de las dos termina, cancela "
                   "automáticamente la otra. Si no encuentra alternativa, espera cada vez más (backoff).")
        c.switch("Activar desatasco automático (tiempo + avance → alternativa)", "unstuck_enabled", False,
                 tip="Cuando está activo, el autocompletado vigila cada descarga.\n"
                     "Si lleva tiempo sin crecer en bytes/fuentes, lanza una búsqueda alternativa\n"
                     "(mismo capítulo con otro hash/search Kad↔Global). Si cualquiera termina, borra la otra.")
        c.spin("Intentos máx.:", "unstuck_max_retries", 1, 20, 5,
               tip="Cuántas veces como máximo se buscará una alternativa para el mismo capítulo.")
        c.spin("Espera inicial (min):", "unstuck_backoff_base_minutes", 5, 1440, 30,
               tip="Espera base del backoff: 1º reintento espera esto, 2º el doble, 3º el cuádruple…")
        c.spin("Espera máxima (min):", "unstuck_backoff_max_minutes", 30, 10080, 480,
               tip="Tope del backoff: aunque falle muchas veces no esperará más que esto.")
        c.spin("Sin avance (min):", "unstuck_file_ttl_minutes", 30, 10080, 1440,
               tip="Minutos sin avance (bytes/fuentes estancados) y con la descarga ya en marcha\n"
                   "necesarios para considerarla atascada y lanzar la alternativa.")
        c.subtitle("Solicitudes de la web",
                   "Este equipo atiende las peticiones hechas desde la web del móvil: las reclama, las busca "
                   "en aMule y las da por completadas cuando se suben. Con varios equipos colaborando, cada "
                   "solicitud la atiende solo uno.")
        c.switch("Colaborar con las solicitudes de la web", "download_requests_enabled", True)
        c.spin("Máx. descargas a la vez:", "download_requests_max_active", 1, 200, 5,
               tip="Cuántas solicitudes lanza este equipo como mucho en las últimas 2 h.\n"
                   "Las que llevan más tiempo esperando fuentes en aMule ya no cuentan.")

    def _browse(self):
        folder = QFileDialog.getExistingDirectory(self, "Seleccionar carpeta a vigilar",
                                                  self.watch_folder.text().strip() or os.path.expanduser("~"))
        if folder:
            self.watch_folder.setText(folder)


class FtpPage(Page):
    def build(self):
        c = self.card("Conexión FTP")
        self.host_e = c.text("Servidor (host):", "ftp_host")
        self.port = c.spin("Puerto:", "ftp_port", 1, 65535, 21)
        self.user = c.text("Usuario:", "ftp_user")
        self.pwd = c.text("Contraseña:", "ftp_password", secret=True, strip=False)
        self.protocol = QComboBox()
        self.protocol.addItems(list(PROTOCOL_LABELS.values()))
        if str(self.cfg.get("ftp_protocol", "ftp")).lower() == "sftp":
            cur = PROTOCOL_LABELS["sftp"]
        else:
            cur = PROTOCOL_LABELS["ftps" if self.cfg.get("ftp_use_tls") else "ftp"]
        self.protocol.setCurrentText(cur)
        self.protocol.currentTextChanged.connect(self._on_protocol_changed)
        c.row("Protocolo:", self.protocol)
        self.bind("ftp_protocol", lambda: self.selected_protocol()[0])
        self.bind("ftp_use_tls", lambda: self.selected_protocol()[1])
        par = QComboBox()
        par.addItems(["1", "2", "3", "4", "5"])
        par.setCurrentText(str(max(1, min(5, int_or(self.cfg.get("ftp_parallel", 1), 1)))))
        c.row("Subidas simultáneas:", hbox(par))
        self.bind("ftp_parallel", lambda: int(par.currentText()))
        streams = QComboBox()
        streams.addItems(["1", "2", "4", "6", "8"])
        cur_streams = str(max(1, min(8, int_or(self.cfg.get("ftp_upload_streams", 4), 4))))
        if streams.findText(cur_streams) < 0:
            streams.addItem(cur_streams)
        streams.setCurrentText(cur_streams)
        streams.setToolTip(TOOLTIP_CONEXIONES_POR_ARCHIVO)
        c.row("Conexiones por archivo:", hbox(streams, "(solo SFTP)"))
        self.bind("ftp_upload_streams", lambda: int(streams.currentText()))
        speed = float_spin(0, 100000, self.cfg.get("ftp_speed_limit", 0))
        c.row("Límite de velocidad (MB/s):", hbox(speed, "MB/s  (0 = sin límite)"))
        self.bind("ftp_speed_limit", speed.value)
        c.spin("Reintentos en error:", "ftp_retries", 0, 10, 3)
        c.text("Carpeta compartida (datos):", "shared_data_ftp_path", placeholder="/datos2/aIBechos")
        self.user_name = c.text("Tu nombre (reservas):", "app_user_name",
                                placeholder="Para repartir la cuota de reservas por persona")
        test = QPushButton("Probar conexión")
        test.setToolTip("Conectar al FTP con los datos de arriba para comprobar que funcionan. "
                        "No guarda nada ni sube nada.")
        test.clicked.connect(self._test)
        c.row(None, hbox(test))
        self.status = status_label()
        c.row(None, self.status)

    def selected_protocol(self) -> tuple:
        clave = next((k for k, v in PROTOCOL_LABELS.items() if v == self.protocol.currentText()), "ftp")
        return ("sftp" if clave == "sftp" else "ftp"), clave == "ftps"

    def _on_protocol_changed(self, _text):
        """Propone el puerto estándar del protocolo, salvo que haya uno propio."""
        from core.transfer import DEFAULT_PORTS, default_port
        if self.port.value() not in set(DEFAULT_PORTS.values()):
            return
        self.port.setValue(default_port(self.selected_protocol()[0]))

    def _test(self):
        host, port, user, pwd = self.host_e.text().strip(), self.port.value(), self.user.text().strip(), self.pwd.text()
        protocolo, tls = self.selected_protocol()
        set_status(self.status, "Conectando...", theme.WARNING_COLOR)
        window = self.host.window

        def worker():
            from core.transfer import make_client
            own = make_client(protocolo)
            ok, msg = own.connect(host, port, user, pwd, tls)
            own.disconnect()
            ui(lambda: set_status(self.status, msg, theme.SUCCESS_COLOR if ok else theme.ERROR_COLOR))
            if ok and window is not None:
                ui(window.refresh_ftp_space)
        run_in_thread(worker)


class WatchSyncConfigPage(Page):
    """Emparejamiento Plex↔Jellyfin y programación diaria. Cada cambio se
    guarda al momento (como en Tk): no entra en "Guardar configuración"."""

    def build(self):
        c = self.card("Sincronizar visionado",
                      "Emparejamiento de usuarios y sincronización automática. El botón manual y la vista "
                      "previa están en la pestaña \"🔄 Sincronizar visionado\".")
        if not (self.cfg.get("plex_enabled") and self.cfg.get("jellyfin_enabled")):
            c.note("Activa Plex y Jellyfin en Ajustes → Servidor → \"Servidores de medios\" para poder usar "
                   "esta utilidad.", theme.WARNING_COLOR)
            return
        self._plex_users, self._jf_users, self._pending = [], [], None
        c = self.card("Usuarios emparejados",
                      "Se verifica la contraseña de Jellyfin y se confirma antes de guardar cada pareja.")
        self.users_status = status_label()
        refresh = QPushButton("🔄 Actualizar")
        refresh.clicked.connect(self.load_users)
        c.row(None, hbox(self.users_status, refresh))
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Usuario Plex", "Usuario Jellyfin", ""])
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        hh.resizeSection(2, 40)
        c.row(None, self.table)
        self.plex_combo, self.jf_combo = QComboBox(), QComboBox()
        self.jf_pwd = QLineEdit()
        self.jf_pwd.setEchoMode(QLineEdit.Password)
        self.jf_pwd.setPlaceholderText("Contraseña Jellyfin")
        self.add_btn = QPushButton("+ Añadir")
        self.add_btn.setEnabled(False)
        self.add_btn.clicked.connect(self._start_add)
        c.row(None, hbox(self.plex_combo, self.jf_combo, self.jf_pwd, self.add_btn))
        self.confirm_lbl = desc_label("", theme.TEXT)
        self.confirm_lbl.setStyleSheet("font-weight: bold;")
        no = QPushButton("Cancelar")
        no.clicked.connect(self._cancel_add)
        yes = QPushButton("Sí, son la misma persona")
        yes.setProperty("accent", True)
        yes.clicked.connect(self._confirm_add)
        self.confirm_box = QWidget()
        cl = QVBoxLayout(self.confirm_box)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.addWidget(self.confirm_lbl)
        cl.addWidget(hbox(no, yes))
        self.confirm_box.hide()
        c.row(None, self.confirm_box)
        self.add_status = status_label()
        c.row(None, self.add_status)

        c = self.card("Sincronización automática diaria")
        c.note("A la hora indicada se sincroniza sola, SIN pedir confirmación -- a diferencia del botón "
               "manual. Solo funciona mientras aIBechos esté abierto en ese momento -- no es una tarea "
               "del sistema operativo.", theme.WARNING_COLOR)
        self.sched_on = QCheckBox("Activar")
        self.sched_on.setChecked(bool(self.cfg.get("watch_sync_schedule_enabled", False)))
        c.row(None, self.sched_on)
        self.sched_time = QLineEdit(self.cfg.get("watch_sync_schedule_time", ""))
        self.sched_time.setPlaceholderText("03:00")
        self.sched_time.setFixedWidth(80)
        save = QPushButton("Guardar")
        save.clicked.connect(self._save_schedule)
        c.row("Hora (HH:MM):", hbox(self.sched_time, save))
        self.sched_status = status_label()
        c.row(None, self.sched_status)
        self._render_rows()
        self.load_users()

    # ── Usuarios ──

    def load_users(self):
        set_status(self.users_status, "Cargando usuarios...", theme.WARNING_COLOR)
        cfg = self.cfg
        host, token = cfg.get("plex_host", ""), cfg.get("plex_token", "")
        jf_host, jf_key = cfg.get("jellyfin_host", ""), cfg.get("jellyfin_api_key", "")

        def worker():
            from core.media_server_refresh import get_jellyfin_users, get_plex_home_users
            plex = get_plex_home_users(host, token) or []
            jf = get_jellyfin_users(jf_host, jf_key) or []
            ui(lambda: self._users_loaded(plex, jf))
        run_in_thread(worker)

    def _users_loaded(self, plex, jf):
        self._plex_users, self._jf_users = plex, jf
        if not plex or not jf:
            set_status(self.users_status, "No se pudieron cargar los usuarios -- comprueba la conexión",
                       theme.ERROR_COLOR)
            return
        set_status(self.users_status, "")
        self.plex_combo.clear()
        self.plex_combo.addItems([u["title"] for u in plex])
        self.jf_combo.clear()
        self.jf_combo.addItems([u["name"] for u in jf])
        self.add_btn.setEnabled(True)

    def _render_rows(self):
        mappings = self.cfg.get("watch_sync_user_mappings", [])
        self.table.setRowCount(len(mappings))
        for i, m in enumerate(mappings):
            self.table.setItem(i, 0, QTableWidgetItem(m["plex_user_name"]))
            self.table.setItem(i, 1, QTableWidgetItem(m["jellyfin_user_name"]))
            b = QPushButton("✕")
            b.setStyleSheet(f"color: {theme.ERROR_COLOR};")
            b.setToolTip("Quitar este emparejamiento de usuarios. No borra nada en Plex ni en Jellyfin: "
                         "solo deja de sincronizar el visionado entre esas dos cuentas.")
            b.clicked.connect(lambda _=False, idx=i: self._remove(idx))
            self.table.setCellWidget(i, 2, b)
        self.table.setFixedHeight(28 + 30 * max(1, len(mappings)))

    def _remove(self, index):
        mappings = list(self.cfg.get("watch_sync_user_mappings", []))
        del mappings[index]
        self.cfg.set("watch_sync_user_mappings", mappings)
        self.cfg.save()
        self._render_rows()

    def _start_add(self):
        plex_user = next((u for u in self._plex_users if u["title"] == self.plex_combo.currentText()), None)
        jf_user = next((u for u in self._jf_users if u["name"] == self.jf_combo.currentText()), None)
        password = self.jf_pwd.text()
        if plex_user is None or jf_user is None:
            return
        if not password:
            set_status(self.add_status, "Escribe la contraseña de Jellyfin de esa persona para continuar",
                       theme.ERROR_COLOR)
            return
        existing = self.cfg.get("watch_sync_user_mappings", [])
        if any(m["plex_user_id"] == plex_user["id"] or m["jellyfin_user_id"] == jf_user["id"] for m in existing):
            set_status(self.add_status, "Uno de los dos usuarios ya está emparejado con otra persona",
                       theme.ERROR_COLOR)
            return
        self.add_btn.setEnabled(False)
        set_status(self.add_status, "Verificando contraseña...", theme.WARNING_COLOR)
        jf_host = self.cfg.get("jellyfin_host", "")

        def worker():
            from core.media_server_refresh import verify_jellyfin_password
            ok = verify_jellyfin_password(jf_host, jf_user["name"], password)
            ui(lambda: self._verified(ok, plex_user, jf_user))
        run_in_thread(worker)

    def _verified(self, ok, plex_user, jf_user):
        self.jf_pwd.clear()
        if not ok:
            self.add_btn.setEnabled(True)
            set_status(self.add_status, f"Contraseña incorrecta para '{jf_user['name']}' en Jellyfin",
                       theme.ERROR_COLOR)
            return
        set_status(self.add_status, "")
        self._pending = (plex_user, jf_user)
        self.confirm_lbl.setText(f"¿Confirmas que \"{plex_user['title']}\" (Plex) y \"{jf_user['name']}\" "
                                 f"(Jellyfin) son la misma persona?")
        self.confirm_box.show()

    def _cancel_add(self):
        self._pending = None
        self.confirm_box.hide()
        self.add_btn.setEnabled(True)

    def _confirm_add(self):
        if self._pending is None:
            return
        plex_user, jf_user = self._pending
        mappings = list(self.cfg.get("watch_sync_user_mappings", []))
        mappings.append({"plex_user_id": plex_user["id"], "plex_user_name": plex_user["title"],
                         "jellyfin_user_id": jf_user["id"], "jellyfin_user_name": jf_user["name"]})
        self.cfg.set("watch_sync_user_mappings", mappings)
        self.cfg.save()
        self._cancel_add()
        self._render_rows()

    def _save_schedule(self):
        """Se lee en caliente en cada comprobación del programador."""
        time_str = self.sched_time.text().strip()
        enabled = self.sched_on.isChecked()
        if enabled and not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", time_str):
            set_status(self.sched_status, "Formato de hora no válido -- usa HH:MM (24h), p.ej. 03:00",
                       theme.ERROR_COLOR)
            return
        self.cfg.set("watch_sync_schedule_enabled", enabled)
        self.cfg.set("watch_sync_schedule_time", time_str)
        self.cfg.save()
        set_status(self.sched_status, "Guardado", theme.SUCCESS_COLOR)


class BackupPage(Page):
    def build(self):
        c = self.card("Copia de seguridad de la configuración",
                      "Exporta tu configuración de CLIENTE (conexión FTP, carpeta vigilada, preferencias de "
                      "este equipo...) a un archivo, o impórtala en otra instalación. No incluye la "
                      "configuración de servidor -- categorías, plantillas, Plex/Jellyfin... esa se "
                      "sincroniza/publica aparte, ver Servidor. La contraseña FTP nunca se exporta en texto "
                      "plano — tendrás que volver a introducirla tras importar.")
        exp = QPushButton("⬇ Exportar configuración")
        exp.setProperty("accent", True)
        exp.setToolTip("Guardar en un archivo la configuración de ESTE equipo (FTP, carpeta vigilada, "
                       "preferencias). La contraseña FTP no se exporta nunca en texto plano.")
        exp.clicked.connect(self.host._export_config)
        imp = QPushButton("⬆ Importar configuración")
        imp.setToolTip("Cargar la configuración de cliente desde un archivo exportado. Tendrás que volver "
                       "a escribir la contraseña del FTP.")
        imp.clicked.connect(self.host._import_config)
        c.row(None, hbox(exp, imp))
