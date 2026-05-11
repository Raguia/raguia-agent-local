"""Point d'entree CLI : ``python -m raguia_local_agent`` ou ``raguia-local-agent``."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from raguia_local_agent.api_client import PortalApiClient, http_response_detail
from raguia_local_agent.config import (
    APP_DATA_DIR,
    AgentConfig,
    is_first_launch,
    load_config,
)
from raguia_local_agent.logging_utils import setup_logging
from raguia_local_agent.sync_agent import SyncAgent


def _pid_is_running(pid: int) -> bool:
    """Vérifie sans danger qu'un PID est actif.

    Important sur Windows : ``os.kill(pid, 0)`` envoie en réalité un signal
    (CTRL_C_EVENT/SIGTERM) — pas un test de présence — et peut donc tuer un
    processus innocent dont le PID a été recyclé. On utilise ``OpenProcess``
    via ctypes : c'est l'équivalent fiable de ``kill -0`` sur Windows.
    """
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True

    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.argtypes = [
                wintypes.DWORD,
                wintypes.BOOL,
                wintypes.DWORD,
            ]
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL
            kernel32.GetExitCodeProcess.argtypes = [
                wintypes.HANDLE,
                ctypes.POINTER(wintypes.DWORD),
            ]
            kernel32.GetExitCodeProcess.restype = wintypes.BOOL

            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                return False
            try:
                code = wintypes.DWORD()
                if kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                    return code.value == STILL_ACTIVE
                return True
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return False

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _acquire_single_instance_lock() -> tuple[bool, Optional[Path]]:
    """Empêche plusieurs instances locales simultanées.

    Stratégie :
    1) Lit le fichier existant ; s'il référence un PID actif → refuse.
    2) Sinon, supprime puis recrée le fichier en exclusif (``O_CREAT | O_EXCL``)
       pour fermer la fenêtre de course quand deux processus démarrent
       simultanément.
    3) En cas d'échec, on retombe sur un write_text simple (compat).

    Retourne ``(ok, lock_path)``. Si ``ok=False``, une autre instance est active.
    """
    APP_DATA_DIR.mkdir(mode=0o700, exist_ok=True)
    lock_path = APP_DATA_DIR / "agent.pid"
    current_pid = os.getpid()

    if lock_path.exists():
        try:
            existing_pid = int(lock_path.read_text(encoding="utf-8").strip() or "0")
        except Exception:
            existing_pid = 0
        if (
            existing_pid
            and existing_pid != current_pid
            and _pid_is_running(existing_pid)
        ):
            return False, None
        try:
            lock_path.unlink()
        except OSError:
            pass

    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    try:
        fd = os.open(str(lock_path), flags, 0o600)
    except FileExistsError:
        try:
            existing_pid = int(lock_path.read_text(encoding="utf-8").strip() or "0")
        except Exception:
            existing_pid = 0
        if (
            existing_pid
            and existing_pid != current_pid
            and _pid_is_running(existing_pid)
        ):
            return False, None
        try:
            lock_path.write_text(str(current_pid), encoding="utf-8")
            return True, lock_path
        except OSError:
            return False, None
    except OSError:
        try:
            lock_path.write_text(str(current_pid), encoding="utf-8")
            return True, lock_path
        except OSError:
            return False, None

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(str(current_pid))
    except OSError:
        try:
            os.close(fd)
        except OSError:
            pass
        return False, None
    return True, lock_path


def _signal_existing_instance_restore_tray() -> bool:
    """Demande a l'instance deja lancee de re-afficher l'icone tray."""
    try:
        APP_DATA_DIR.mkdir(mode=0o700, exist_ok=True)
        signal_file = APP_DATA_DIR / "show_tray.signal"
        signal_file.write_text(str(int(time.time())), encoding="utf-8")
        return True
    except Exception:
        return False


def test_connection(cfg: AgentConfig) -> bool:
    import httpx

    print(f"Test de connexion vers {cfg.api_base}...")
    try:
        client = PortalApiClient(cfg.api_base, cfg.agent_token)
        st = client.sync_status()
        print("  OK Connecte")
        print(f"  - Sync demandee : {st.get('sync_requested', False)}")
        if st.get("last_sync_at"):
            print(f"  - Derniere sync : {st['last_sync_at']}")
        if st.get("last_error"):
            print(f"  - Derniere erreur : {st['last_error']}")
        return True
    except httpx.HTTPStatusError as e:
        detail = http_response_detail(e.response)
        if e.response.status_code == 401:
            print("  ERREUR 401 — connexion refusee par le portail :")
            print(f"     {detail or e}")
            print("  Utilisez « Se connecter / Reconnecter » dans le menu de l'icone.")
        else:
            print(f"  ERREUR HTTP {e.response.status_code} : {detail or e}")
        return False
    except Exception as e:
        print(f"  ERREUR {e}")
        return False


def _show_fatal_error_dialog(title: str, body: str) -> None:
    """Affiche un dialogue d'erreur visible même en mode binaire windowed.

    En binaire Windows ``--noconsole``, ``print()`` n'a pas de destination :
    l'utilisateur double-clique sur l'exe et rien ne se passe. On utilise
    PowerShell/osascript/zenity pour garantir un retour visuel.
    """
    try:
        import raguia_local_agent.tray_dialogs as tray_dialogs

        tray_dialogs.show_message(title, body, kind="error")
    except Exception:
        pass


def _run_wizard_if_needed(cfg_path: Path | None) -> AgentConfig:
    """Lance le wizard au premier lancement, retourne la config."""
    if is_first_launch():
        print("Premier lancement — ouverture de l'assistant de configuration...")
        try:
            from raguia_local_agent.wizard import run_wizard

            result = run_wizard()
            if result is None:
                print("Configuration annulee. Arret.")
                sys.exit(0)
        except Exception as e:
            msg = (
                f"L'assistant de configuration n'a pas pu démarrer : {e}\n\n"
                "Créez ~/.raguia/config.yaml manuellement"
            )
            if not getattr(sys, "frozen", False):
                msg += " ou relancez depuis le clone Git après 'pip install -e \".[tray]\"'"
            msg += "."
            print(msg)
            _show_fatal_error_dialog("Raguia — Assistant indisponible", msg)
            sys.exit(1)
    try:
        return load_config(cfg_path)
    except ValueError as e:
        msg = f"Configuration invalide : {e}"
        print(msg)
        _show_fatal_error_dialog("Raguia — Configuration invalide", msg)
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Agent de synchronisation RAGUIA")
    parser.add_argument(
        "-c",
        "--config",
        default=None,
        help="Fichier YAML (defaut : ~/.raguia/config.yaml)",
    )
    parser.add_argument(
        "--test", action="store_true", help="Teste la connexion et quitte"
    )
    parser.add_argument(
        "--no-tray",
        action="store_true",
        help="Demarre sans icone systray (mode serveur)",
    )
    args = parser.parse_args()

    cfg_path = Path(args.config).expanduser() if args.config else None
    cfg = _run_wizard_if_needed(cfg_path)
    setup_logging(
        cfg.app_data_dir,
        level=os.environ.get("RAGUIA_LOG_LEVEL", "INFO"),
        structured=bool(cfg.structured_logging),
    )

    lock_path: Optional[Path] = None
    lock_ok, lock_path = _acquire_single_instance_lock()
    if not lock_ok:
        if _signal_existing_instance_restore_tray():
            print(
                "Agent deja en cours d'execution (demande de re-affichage de l'icone envoyee)."
            )
        else:
            print("Agent deja en cours d'execution (icone tray deja active).")
        sys.exit(0)

    _cfg_p = cfg.cfg_path
    if _cfg_p is not None:
        try:
            _cfg_p = _cfg_p.resolve()
        except Exception:
            pass
    logging.info("Fichier config : %s | api_base : %s", _cfg_p, cfg.api_base)
    if os.environ.get("RAGUIA_AGENT_TOKEN"):
        logging.info(
            "Session chargee depuis RAGUIA_AGENT_TOKEN (variable d'environnement)."
        )

    try:
        if args.test:
            if not cfg.agent_password and not cfg.agent_token:
                print("Erreur : agent_password ou agent_token manquant")
                sys.exit(1)
            # Fetch token first if missing
            if not cfg.agent_token and cfg.agent_password:
                try:
                    from raguia_local_agent.api_client import auto_login
                    cfg.agent_token = auto_login(
                        cfg.api_base, cfg.client_slug, cfg.agent_password
                    )
                except Exception as e:
                    print(f"Erreur d'authentification: {e}")
                    sys.exit(1)
            sys.exit(0 if test_connection(cfg) else 1)

        if not cfg.agent_password and not args.test:
            # Le trousseau OS peut etre temporairement indisponible immediatement
            # apres un redemarrage (Windows Credential Locker, GNOME Keyring,
            # etc.). On retente plusieurs fois avec un court delai avant de
            # passer en mode degrade.
            from raguia_local_agent.secret_store import KEYRING_SENTINEL, load_token

            try:
                import yaml as _yaml

                _raw = (
                    _yaml.safe_load(cfg.cfg_path.read_text(encoding="utf-8"))
                    if (cfg.cfg_path and cfg.cfg_path.is_file())
                    else {}
                )
                _stored = str((_raw or {}).get("agent_password") or "").strip()
                if _stored == KEYRING_SENTINEL:
                    for _attempt in range(4):
                        time.sleep(0.5)
                        _pwd = load_token(cfg.cfg_path, KEYRING_SENTINEL)
                        if _pwd:
                            cfg.agent_password = _pwd
                            logging.info(
                                "Mot de passe keyring recupere apres %d tentative(s).",
                                _attempt + 1,
                            )
                            break
            except Exception as _e:
                logging.warning("Retry keyring echoue: %s", _e)

        if not cfg.agent_password and not cfg.agent_token and args.no_tray:
            logging.error(
                "Aucune session agent disponible. "
                "Définissez RAGUIA_AGENT_PASSWORD dans l'environnement, "
                "fournissez agent_token, ou lancez sans --no-tray pour vous reconnecter."
            )
            sys.exit(1)

        # Perform auto-login using the password to get the token
        _needs_reconnect = not cfg.agent_password and not cfg.agent_token
        if cfg.agent_password and not cfg.agent_token:
            try:
                from raguia_local_agent.api_client import auto_login
                cfg.agent_token = auto_login(
                    cfg.api_base, cfg.client_slug, cfg.agent_password
                )
            except Exception as e:
                logging.error("Erreur de connexion auto : %s", e)
                _needs_reconnect = True

        agent = SyncAgent(cfg)

        # Si le mot de passe est absent (trousseau inaccessible meme apres retry), on demarre
        # quand meme le tray en mode degrade : l'icone rouge s'affiche et le dialogue
        # de reconnexion est ouvert automatiquement pour que l'utilisateur puisse
        # fournir ses identifiants sans avoir a relancer l'application manuellement.
        try:
            # --- Mode sans tray (serveur / terminal) ---
            if args.no_tray:
                try:
                    agent.run_forever()
                except KeyboardInterrupt:
                    agent.stop()
                return

            # --- Mode avec tray (macOS : tray dans main thread, agent en daemon) ---
            t = threading.Thread(
                target=agent.run_forever, daemon=True, name="raguia-agent"
            )
            t.start()

            try:
                from raguia_local_agent.tray import RaguiaTray

                tray = RaguiaTray(agent, on_quit=agent.stop)
                if _needs_reconnect:
                    # Planifie l'ouverture automatique du dialogue de reconnexion
                    # une fois que le tray est initialise (apres un court delai).
                    def _auto_reconnect() -> None:
                        time.sleep(1.5)
                        try:
                            import raguia_local_agent.tray_dialogs as tray_dialogs

                            tray_dialogs.show_message(
                                "Reconnexion requise",
                                "La session d'authentification n'a pas pu etre chargee.\n"
                                "Utilisez « Se connecter / Reconnecter… » dans le menu de l'icone.",
                                kind="warning",
                            )
                        except Exception:
                            pass

                    threading.Thread(target=_auto_reconnect, daemon=True).start()
                tray.run()  # bloque dans le thread principal (requis sur macOS)
            except ImportError:
                logging.info(
                    "pystray/Pillow non disponible — mode sans tray. Ctrl+C pour arreter."
                )
                try:
                    t.join()
                except KeyboardInterrupt:
                    agent.stop()
        finally:
            agent.stop()
    finally:
        if lock_path and lock_path.exists():
            try:
                lock_pid = int(lock_path.read_text(encoding="utf-8").strip() or "0")
            except Exception:
                lock_pid = 0
            if lock_pid == os.getpid():
                try:
                    lock_path.unlink()
                except Exception:
                    pass


if __name__ == "__main__":
    main()
