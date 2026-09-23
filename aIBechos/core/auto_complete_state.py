"""Estado del autocompletado de episodios: qué capítulo toca intentar en cada
pasada y cuál hay que dejar en paz.

Vive aquí y no dentro de gui/app.py::App porque es la parte del autocompletado
que se puede razonar (y probar) sin tkinter ni aMule ni FTP -- y porque es
donde estaba el fallo que motivó extraerlo: la pasada desmarcaba SIEMPRE el
"checked" de un episodio que siguiera faltando en el servidor, incluido el que
acababa de escribir una descarga lanzada con éxito. Como una descarga recién
encolada tarda horas en aparecer en el servidor (descarga + renombrado +
subida), el episodio seguía "faltando" en la pasada siguiente, se desmarcaba y
se relanzaba la misma descarga cada 30 minutos. En el log de un usuario, 'My
Hero Academia 7x21' se relanzó 9 veces en 20 horas.

Los dos mapas que gobiernan esto (ambos en config.json, personales, no viajan
por FTP):

  checked  {tmdb_id: {season: {ep: epoch}}}  descarga lanzada, o ya en el
           servidor. El epoch distingue "lanzado hace un rato, déjalo" de
           "lanzado anteayer y sigue sin aparecer, algo falló".
  retries  {tmdb_id: {season: {ep: {"tries": n, "ts": epoch}}}}  intentos que
           no encontraron candidato en aMule; se reintentan con espera
           creciente.

Formato antiguo de *checked*: {season: [ep, ...]}, una lista sin fecha que
además anotaba aquí los intentos FALLIDOS (no distinguía éxito de fracaso).
Se sigue leyendo: esas entradas se datan en 0 y caen al backoff la primera vez
que una pasada las toca, que es justo la migración que se quería.
"""

from typing import Optional

# Margen que se le concede a una descarga ya lanzada antes de darla por perdida
# y volver a intentarla. Relanzarla antes no acelera nada: aMule ya tiene el
# archivo en cola, y cada relanzamiento son ~20 s de búsqueda Kad ocupando la
# única conexión EC que aMule admite.
LAUNCHED_GRACE_S = 24 * 3600

# Reintento con espera CRECIENTE de los episodios que fallaron: tras el intento
# nº n se espera BASE * FACTOR**(n-1) (30 min, 1 h, 2 h, 4 h...) con tope MAX.
# Así un capítulo que aún no está bien indexado en Kad se vuelve a probar cada
# vez más espaciado, en vez de quedarse marcado para siempre (real: 3x08 de La
# casa del dragón se probó una vez sin candidato y nunca volvió a intentarse,
# aunque el release apareció después).
RETRY_BASE_S = 30 * 60
RETRY_FACTOR = 2
RETRY_MAX_S = 7 * 24 * 3600

# Resultados de decide_episode()
ATTEMPT = "intentar"                 # hay que lanzar la descarga ahora
WAIT_DOWNLOAD = "esperar_descarga"   # ya se lanzó hace poco, sigue en marcha
WAIT_BACKOFF = "esperar_backoff"     # falló y aún no toca reintentarlo


def checked_ts(series_checked: dict, season: int, episode: int) -> Optional[int]:
    """Epoch del "checked" de un episodio dentro del mapa de UNA serie, o None
    si no está marcado. Devuelve 0 para las entradas del formato antiguo (lista
    sin fecha), que no se pueden datar."""
    eps = (series_checked or {}).get(str(season))
    if isinstance(eps, list):
        return 0 if episode in eps else None
    if isinstance(eps, dict):
        rec = eps.get(str(episode))
        return int(rec) if rec is not None else None
    return None


def retry_wait_for(tries: int) -> float:
    """Segundos de espera para un episodio con *tries* intentos fallidos."""
    return min(RETRY_BASE_S * (RETRY_FACTOR ** (max(tries, 1) - 1)), RETRY_MAX_S)


def decide_episode(series_checked: dict, series_retries: dict,
                   season: int, episode: int, now: float) -> tuple:
    """Decide qué hacer con un episodio que SIGUE faltando en el servidor.

    Devuelve (acción, drop_checked): la acción es ATTEMPT / WAIT_DOWNLOAD /
    WAIT_BACKOFF, y *drop_checked* dice si hay que borrar su marca de checked
    por haber caducado (o por ser del formato antiguo) antes de reintentarlo."""
    ts = checked_ts(series_checked, season, episode)
    drop_checked = False
    if ts is not None:
        if ts and (now - ts) < LAUNCHED_GRACE_S:
            # Lanzado hace poco: la descarga sigue su curso, no se toca.
            return WAIT_DOWNLOAD, False
        # Lanzado hace demasiado y sigue sin aparecer -- o entrada legacy sin
        # fecha. La marca ya no vale: se quita y el episodio pasa al backoff.
        drop_checked = True

    rec = (series_retries or {}).get(str(season), {}).get(str(episode))
    if rec is not None:
        tries = int(rec.get("tries", 1))
        if now - int(rec.get("ts", 0)) < retry_wait_for(tries):
            return WAIT_BACKOFF, drop_checked
    return ATTEMPT, drop_checked


def set_checked(checked: dict, tmdb_id: int, season: int, episode: int,
                ts: int) -> dict:
    """Marca un episodio como resuelto con fecha *ts*. Muta y devuelve *checked*
    (convirtiendo de paso la temporada al formato nuevo si venía como lista)."""
    entry = checked.setdefault(str(tmdb_id), {})
    eps = entry.get(str(season))
    if isinstance(eps, list):
        eps = {str(e): 0 for e in eps}   # 0 = legacy, sin fecha conocida
    elif not isinstance(eps, dict):
        eps = {}
    eps[str(episode)] = int(ts)
    entry[str(season)] = eps
    return checked


def unset_checked(checked: dict, tmdb_id: int, season: int, episode: int) -> bool:
    """Quita la marca de un episodio. Muta *checked* y devuelve si cambió algo
    (para no reescribir config.json en cada pasada sin motivo)."""
    entry = checked.get(str(tmdb_id))
    if not entry:
        return False
    eps = entry.get(str(season))
    if isinstance(eps, list):
        if episode not in eps:
            return False
        eps.remove(episode)
    elif isinstance(eps, dict):
        if eps.pop(str(episode), None) is None:
            return False
    else:
        return False
    if not eps:
        entry.pop(str(season), None)
    if not entry:
        checked.pop(str(tmdb_id), None)
    return True
