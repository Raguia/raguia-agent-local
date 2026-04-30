# Runtime hook PyInstaller — résolution du bundle CA certifi pour SSL/HTTPS.
#
# En mode one-file Windows, sys._MEIPASS est le répertoire d'extraction temporaire.
# certifi.where() retourne le bon chemin dans ce répertoire, mais certaines
# versions de Python/OpenSSL sur Windows lisent aussi SSL_CERT_FILE et
# REQUESTS_CA_BUNDLE. Ce hook les positionne explicitement pour maximiser la
# compatibilité avec les proxies d'entreprise qui interceptent TLS.
import os
import sys

if getattr(sys, "frozen", False):
    try:
        import certifi as _certifi
        _ca = _certifi.where()
        os.environ.setdefault("SSL_CERT_FILE", _ca)
        os.environ.setdefault("REQUESTS_CA_BUNDLE", _ca)
    except Exception:
        pass
