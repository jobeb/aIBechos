# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

**aIBechos** is a desktop GUI app for renaming and uploading media files (TV series, movies, anime, and books/comics). It queries TMDB for series/movie metadata, OpenLibrary (primary) with Google Books as automatic backup for ebooks, and ComicVine for comics/manga, generates clean filenames from configurable templates, and uploads via FTP/FTPS. Written in Python with customtkinter. Runs on Windows, macOS, and Linux (see "Platform notes" below).

## Running the app

All commands are run from the `aIBechos/` subdirectory:

```bash
cd aIBechos
python main.py               # launch normally (Windows: `python`, macOS/Linux: usually `python3`)
python diagnostico.py        # launch with diagnostics written to diagnostico.log
python crear_acceso_directo.py  # create a Desktop shortcut (Windows only)
```

Install dependencies with `pip install -r requirements.txt` (all installer scripts — `instalar.bat`/`instalar.ps1` on Windows, `instalar_mac.sh` on macOS — install from this same file; don't hand-list packages in those scripts, it drifts and silently drops deps like `keyring`, which is a hard, unguarded import in `config.py` — missing it means the app won't even open).

Unit tests cover the pure `core/` logic (no tkinter) in `aIBechos/tests/`:

```bash
cd aIBechos
pip install -r requirements-dev.txt
pytest tests/ -v
```

## Building the executable

From inside `aIBechos/`:

```bash
pip install -r requirements.txt
pyinstaller aIBechos.spec
# Windows → dist/aIBechos/aIBechos.exe
# macOS   → dist/aIBechos.app (BUNDLE block in the spec, only added when built on macOS)
```

The spec bundles `customtkinter`, `PIL`, `tkinterdnd2`, `pystray`, `py7zr`, and `rarfile` assets, and picks the icon by platform (`iconoPrincipal.ico` on Windows, `iconoPrincipal.icns` on macOS — both must be present in `aIBechos/`; regenerate the `.icns` from `IconoSinFondo.png` with `Image.open(...).save('iconoPrincipal.icns')` if the source art changes, no macOS-only tool needed). `py7zr`/`rarfile` are pure-Python (no binary bundled) — `.rar` extraction still needs `unrar`/`unar`/`bsdtar` installed on the end user's system; if absent, extraction fails with a clear message instead of crashing.

## Architecture

```
aIBechos/
├── main.py          # entry point — creates and starts App
├── config.py        # Config class, persists to %APPDATA%\aIBechos\config.json
├── core/
│   ├── api_client.py     # TMDBClient, MediaInfo dataclass, detect_episode()
│   ├── openlibrary_client.py # OpenLibraryClient — PRIMARY ebook identification, no API key ever needed
│   ├── book_client.py    # GoogleBooksClient — automatic ebook fallback when OpenLibrary fails/finds nothing
│   ├── book_identify.py  # identify_book_or_comic() — shared identification logic (OpenLibrary→Google Books fallback, ComicVine + AI title translation), used by both the GUI search panel and AutoWatcher
│   ├── comicvine_client.py # ComicVineClient — comic/manga identification (cbz/cbr)
│   ├── mangadex_client.py / anilist_client.py / kitsu_client.py # manga-focused alternatives to ComicVine (no API key), selectable manually — see "Manual search-provider selector" below
│   ├── ai_title_fallback.py # Groq-based title cleanup (video) and comic title translation (ES→EN) fallback
│   ├── learned_terms.py / learned_comic_titles.py # persisted caches for the AI fallbacks above
│   ├── archive_extract.py # extract_archive() — .zip/.7z/.rar/.tar(+.gz/.bz2/.xz variants), zip-slip safe
│   ├── renamer.py        # build_new_name(), rename_file(), is_video_file(), is_book_file(), is_archive_file()
│   ├── ftp_client.py     # FTPClient — upload with progress, speed limit, resume
│   ├── sftp_client.py    # SFTPClient — same public API over SSH (subclasses FTPClient)
│   ├── transfer.py       # make_client() — the single place that picks FTP vs SFTP
│   └── auto_watcher.py   # AutoWatcher — recursive folder polling thread; identifies video AND books/comics, optionally auto-extracts archives (see "auto_extract_archives" setting)
└── gui/
    └── app.py          # entire UI in one App class (~2200 lines)
```

### Data flow

1. Files are added (drag-drop, picker, "Add folder", or AutoWatcher) — all four entry points recognize video, book/comic, and archive files, and recursively scan subfolders; any `.zip`/`.7z`/`.rar`/`.tar` found is decompressed (via `core/archive_extract.py`, nested archives resolved too) before the resulting files are added — manual add always extracts, AutoWatcher only if "auto_extract_archives" is enabled in Settings
2. `detect_episode()` in `api_client.py` parses the filename using regex patterns to extract title, season, episode, and media type (tv/movie/anime); book/comic files (`is_book_file()`/`is_comic_file()` in `renamer.py`) skip these regexes entirely and get `media_type="libro"` directly, with comics additionally getting an issue number parsed from a `#NN` pattern, a bare trailing number (with any number of trailing `[...]`/`(...)` credit groups after it), or a `Capítulo/Chapter N` keyword found anywhere in the name (last resort, for scans where the number is followed by that chapter's own subtitle rather than sitting at the end of the filename) — a `folder_hint` param (the containing folder's name) is used as a fallback title when the filename itself yields nothing useful (bare-numbered manga chapters with the series name only on the folder), and the whole "Detectado" title/season/episode can also be overridden by hand (double-click the column, or right-click → "Editar título/episodio detectado…") when detection still gets it wrong — writes straight into `entry.detected`, so everything downstream (search query default, `_build_info_from_result`) picks it up with no other change
3. Identification is dispatched by file type: `TMDBClient.search_multi()`/`build_media_info()` for video; for books/comics, `core/book_identify.py::identify_book_or_comic()` tries `OpenLibraryClient.search_volumes()` first (no key needed), falls back to `GoogleBooksClient.search_volumes()` if OpenLibrary errors or finds nothing, or `ComicVineClient.search_volumes()` for comics (retrying with an AI-translated English title if the Spanish-derived title finds nothing) — all produce a `MediaInfo` dataclass (`media_type="libro"` for both books and comics, distinguished by `genre_ids` containing `"ebook"` or `"comic"`)
4. `build_new_name()` in `renamer.py` (via `build_name_for_media_info()`, shared by the GUI and AutoWatcher) applies the user's template string (e.g. `{serie} {temporada}x{episodio:02d} {titulo}{ext}`) — which template (tv/movie/anime/libro/comic) is chosen based on `media_type` and, for `"libro"`, the `genre_ids` ebook/comic distinction
5. `rename_file()` performs the disk rename
6. `FTPClient.upload_file()` uploads to the configured server

### Multi-select and bulk assign (Archivos tab)

The file table supports Ctrl+click / Shift+click multi-select (Explorer-style), independent of the single "anchor" entry that drives the search/detail panel (`self._selected_entry`; additional entries live in `self._multi_selected`), plus a "Seleccionar todos"/"Deseleccionar todos" toggle button that marks every file across all pages at once (pagination only limits what's rendered, not what can be selected). The "Asignar" button becomes "Asignar a la selección (N)" when more than one file is selected, applying the same chosen search result to every selected file while each keeps its own already-detected episode/issue number (`core/book_identify.py`'s per-entry `det` parameter already supported this; the bulk path just calls it once per selected file instead of once for the anchor). Mismatched types (e.g. a video mixed into a comic selection) are skipped with a warning, not force-applied.

Adding a whole folder (`+ Carpeta`) with 2+ book/comic files prompts "¿misma serie o colección?" before searching anything — answering yes preselects all of them (same mechanism as "Seleccionar todos") and skips the automatic per-file search entirely, so a single manual search + "Asignar a la selección" identifies the whole batch instead of firing one API call per file (real issue: 266 individually-identified chapters exhausted ComicVine's quota mid-batch). Answering no (or closing the dialog) falls back to identifying every file individually, same as `+ Archivos`/drag-drop, which never show this prompt.

### Manual search-provider selector (Archivos tab)

The search panel has a dropdown — Auto / OpenLibrary / GoogleBooks / ComicVine / MangaDex / AniList / Kitsu — that lets the user force a specific identification provider for the *manual* search box, regardless of the file's actual type (`self._search_provider_override`, persists across file selections within the session, not saved to config). "Auto" (default) keeps today's automatic chain (ComicVine for comics, OpenLibrary→Google Books for ebooks, TMDB otherwise) untouched; forcing anything else does a single direct call to that provider, no chained fallback. MangaDex/AniList/Kitsu (`core/mangadex_client.py`/`anilist_client.py`/`kitsu_client.py`) exist specifically because ComicVine's catalog is mostly Western/English and identifies manga poorly even with the AI title-translation fallback — MangaDex in particular tends to already carry Spanish (`es`/`es-la`) alt-titles, which the "busca en inglés" flag hint (shown only for ComicVine/AniList/Kitsu) reflects. This selector only affects the manual panel — AutoWatcher and the automatic identify-on-add (`core/book_identify.py`) are untouched.

### Template variables

Available in naming templates: `{serie}`, `{titulo}`, `{temporada}`, `{episodio}`, `{año}`, `{ext}`. Python format spec works: `{episodio:02d}`, `{episodio:03d}`, etc. For books, `{episodio}` holds the comic issue number (parsed from the filename) when applicable — ebooks and comics reuse the same variable set, no book-specific variables (e.g. no `{autor}`) were added; default comic format is `{serie} ({año}) #{episodio:02d}{ext}`, confirmed against real files on a user's server.

FTP path templates support: `{serie}`, `{temporada}`, `{temporada:02d}`, `{año}`, `{tipo}` (`"Series"`/`"Películas"`/`"Libros"`).

### Threading rules

tkinter is not thread-safe. All GUI mutations from worker threads must be scheduled via `self.after(0, lambda: ...)`. Worker threads are used for: TMDB searches (`ThreadPoolExecutor`, max 5), FTP uploads (`ThreadPoolExecutor`, max 5 parallel connections), AutoWatcher polling, poster image loading.

### Config and persistence

- Data dir via `core/appdirs.py:app_data_dir()` — the single source of truth for where app files live, used by `config.py`, `gui/app.py`, and `core/auto_watcher.py` alike (they used to each have their own copy of this logic, and only `config.py`'s distinguished macOS from Linux, so macOS installs ended up with files split across two different folders — don't reintroduce a local copy of this logic, import it):
  - Windows → `%APPDATA%\aIBechos\`
  - macOS → `~/Library/Application Support/aIBechos/`
  - Linux → `~/.config/aIBechos/`
- `config.json`, `session.json`, `upload_history.json`, `auto_processed.json`, `auto_watcher.log` all live directly in that folder.
- The `Config` class merges saved values over `DEFAULTS` in `config.py` — add new settings to `DEFAULTS` first
- FTP password is stored via `keyring` (Windows Credential Locker / macOS Keychain / whatever `keyring` resolves on Linux), never in `config.json` in plaintext.

### FTP or SFTP

`ftp_protocol` in config is either `"ftp"` (with `ftp_use_tls` for explicit FTPS)
or `"sftp"`. SFTP is not "FTP with encryption" — it is a different protocol
carried inside an SSH session, with no FTP commands and no separate data
connection — so it gets its own client rather than another flag on the FTP one.
Settings shows one combo (FTP / FTPS / SFTP) instead of a TLS switch, precisely
so "SFTP + TLS" cannot be selected; changing it proposes that protocol's default
port (21/22) unless the user set a custom one.

`SFTPClient` **subclasses** `FTPClient` and overrides only what genuinely depends
on the protocol: connect/disconnect, listing, stat, create/delete, and moving
bytes. Everything else — path templates, tree walking, folder sizes, recursive
delete, and the whole upload path (speed limit, progress, cancel/skip, final
size verification) — is application logic and is inherited, so uploads behave
identically over both. When touching `upload_file`, keep going through the
`_store_stream`/`_delete_remote_file`/`_remove_remote_dir`/`get_remote_size`
seams instead of reaching for `self.ftp` directly: `self.ftp` is `None` under
SFTP, and a direct call there breaks SFTP silently (it already did once, for
the resume check and the final size check).

Free space is one place where SFTP is strictly better than FTP: instead of
probing non-standard commands (AVBL/SITE AVAIL/XDISKFREE, none of which vsftpd
answers), it uses the `statvfs@openssh.com` extension. paramiko exposes **no**
statvfs method despite servers supporting it — `sftp.statvfs()` is just an
`AttributeError` — so it is sent as a raw extended request and the eleven
64-bit fields are unpacked by hand (`_statvfs_free`); use `f_bavail`, not
`f_bfree`, or you report the root-reserved space as usable. The `df`-over-SSH
fallback is mostly theoretical: SFTP-only accounts have no shell (the reference
server answers "This service allows sftp connections only" to everything), which
is why the output is validated as an actual df before being trusted.

Nothing constructs a client directly — all ~50 call sites go through
`App._new_ftp_client()` (which reads the setting) or `core/transfer.py::make_client()`.
`AutoWatcher` receives it as its `ftp_factory`. `paramiko` is imported *inside*
`connect()` so FTP users don't pay for loading `cryptography` at startup, which
also means it must stay listed in `hiddenimports` in `aIBechos.spec` — a
function-level import is invisible to PyInstaller, and without it SFTP would
fail only in the installed build, never when running from source.

### FTP encoding quirk

`FTPClient.connect()` starts with `latin-1` encoding (not the Python 3.9+ default of UTF-8), then upgrades to UTF-8 only if the server confirms it via `FEAT`. This avoids garbled Spanish characters on common NAS servers (vsftpd, ProFTPD).

### AutoWatcher

Polls the watch folder every `poll_interval` seconds (default 10), recursively (subfolders included, excluding any `procesados/` subfolder at any depth). If "auto_extract_archives" is enabled in Settings, `.zip`/`.7z`/`.rar`/`.tar` files found are decompressed first (nested archives resolved across scan cycles); the resulting video/book/comic files are picked up on a later cycle like any other file. For each new video/book/comic file it:
1. Waits 6 s for the file size to stabilize (detects incomplete downloads)
2. Calls `detect_episode()` + TMDB search (video) or `core/book_identify.py::identify_book_or_comic()` (books/comics) — for books/comics, `min_confidence` is enforced just like video (unlike the manual search panel, where the user reviews before assigning)
3. Renames the file
4. Optionally moves to `procesados/` subfolder or deletes
5. Uploads via FTP if configured
6. Persists the result to `auto_processed.json` so files aren't reprocessed

Launches minimized to tray when started with `--minimized` argument (used by the autostart entry — Windows Registry `Run` key or macOS LaunchAgent, see below).

## Platform notes

- **Tray icon (`pystray`)**: on macOS, `Icon.run()` must be called from the main thread (AppKit constraint, not optional) — `gui/app.py:_minimize_to_tray` branches on `core.appdirs.is_macos()` and calls `Icon.run_detached()` directly instead of spawning a background thread like Windows/Linux do. `run_detached()` on the darwin backend doesn't block; it just marks the icon ready and relies on Tk's own already-running Cocoa event loop (same shared `NSApplication` instance) to actually deliver clicks. If you touch tray code, keep that branch — don't unify it back into a single threaded `.run()` call.
- **Autostart**: `App._set_autostart` dispatches to `_set_autostart_windows` (Registry `HKCU\...\Run`), `_set_autostart_macos` (`~/Library/LaunchAgents/com.aibechos.app.plist`, loaded with `launchctl load -w`), or `_set_autostart_linux` (`~/.config/autostart/aibechos-autostart.desktop`, the XDG autostart convention). `_migrate_autostart_identity` moves an existing entry off the pre-rename identifiers (`com.arenombrar.app` / `arenombrar-autostart.desktop`) once, and only if the user had autostart on — leaving the old one behind would keep launching an executable that no longer exists.
- **Desktop notifications**: `App._send_notification` tries pystray's tray notification first, then falls back to a platform-specific OS call — PowerShell balloon on Windows, `osascript -e 'display notification ...'` on macOS, `notify-send` on Linux.
- **Window/dialog icon**: Tk's `iconbitmap()` only accepts `.ico` and only works on Windows; macOS/Linux use `iconphoto()` with a PNG (`IconoSinFondo.png`) instead. See `App.__init__` (loads `self._icon_path` or `self._icon_photo` depending on platform) and `App._apply_icon`.
- **"Archivo bloqueado" retry in AutoWatcher**: the substring match in `core/auto_watcher.py` (`_LOCKED_FILE_HINTS`) was originally Windows-only (`WinError 32` text). It now also matches common POSIX phrasing (`EBUSY`, "permission denied"), though this scenario is rare on macOS/Linux since POSIX generally allows renaming open files.
