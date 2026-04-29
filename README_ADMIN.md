# Guide d'administration — Agent Local Raguia

Ce document est destiné aux administrateurs techniques qui déploient, configurent et maintiennent l'agent chez un client.

---

## 1. Architecture de distribution

L'agent est distribué sous forme de **binaires natifs compilés par PyInstaller** (aucune dépendance Python, Git ou uv requise côté client). Le client télécharge un seul fichier et double-clique.

| Plateforme | Artefact | Taille indicative |
|---|---|---|
| Windows 10/11 x64 | `raguia-agent-windows.exe` | ~60-80 Mo |
| macOS Apple Silicon (M1/M2/M3) | `raguia-agent-macos-arm64.zip` | ~70-90 Mo |

Les binaires sont publiés à chaque release GitHub (`v*`) via le workflow `.github/workflows/build-agent-binaries.yml`. Le portail lit les variables `LOCAL_AGENT_DOWNLOAD_URL`, `LOCAL_AGENT_SHA256` et `LOCAL_AGENT_VERSION` pour exposer le lien de téléchargement et permettre les mises à jour automatiques.

---

## 2. Déploiement chez un client

### 2.1 Prérequis

- Connexion internet (HTTPS vers le portail Raguia).
- Un Jeton JWT agent généré depuis le portail (section **Paramètres → Agent de synchronisation**).
- Aucun autre prérequis logiciel.

### 2.2 Procédure (Windows)

1. Transmettre le lien de téléchargement `raguia-agent-windows.exe` au client (ou le déposer vous-même sur la machine).
2. Double-clic sur le `.exe`.
3. **Si SmartScreen s'affiche** ("Windows a protégé votre ordinateur") :
   - Cliquer **Informations supplémentaires** → **Exécuter quand même**.
   - Ce message est normal pour un exécutable sans signature Authenticode grand public. Il disparaîtra si vous investissez dans un certificat de signature de code (~100 €/an).
4. L'assistant de configuration s'ouvre. Saisir l'URL du portail, coller le Jeton, choisir le dossier parent.
5. Cliquer **Tester** → **Enregistrer & Démarrer**.

L'agent s'inscrit automatiquement dans `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` pour démarrer à chaque connexion de l'utilisateur (sans droits administrateur). Un ancien mode compatibilité via raccourci `Startup` peut aussi exister sur certains postes.

### 2.3 Procédure (macOS Apple Silicon)

1. Transmettre le `.zip` au client.
2. Dézipper → faire glisser `raguia-agent.app` dans le dossier `Applications` (recommandé) ou `Documents`.
3. **Gatekeeper bloque le premier lancement** car l'app n'est pas notarisée. Deux options :
   - **Option utilisateur** : Clic droit sur l'app → **Ouvrir** → **Ouvrir** dans le dialogue d'alerte.
   - **Option IT (recommandée)** : Exécuter en terminal une seule fois avant de remettre la main au client :
     ```bash
     xattr -d com.apple.quarantine /Applications/raguia-agent.app
     ```
   Après ce retrait du flag de quarantaine, l'app s'ouvre normalement par double-clic.
4. L'assistant s'ouvre. Saisir l'URL du portail, coller le Jeton, choisir le dossier parent.
5. **Enregistrer & Démarrer**.

L'agent crée automatiquement un LaunchAgent dans `~/Library/LaunchAgents/com.raguia.local.agent.plist` pour démarrer au login de l'utilisateur.

---

## 3. Mise à jour de l'agent

### 3.1 Mise à jour via le menu tray (recommandée)

L'agent vérifie l'endpoint `/api/portal/agent/version` toutes les 24 heures. Si une nouvelle version est disponible, un avertissement est affiché dans l'icône tray et l'information est visible dans les logs.

Le client (ou vous à distance) fait :
> Clic droit icône → **Vérifier / installer mise à jour** → Confirmer

L'agent :
1. Télécharge le nouveau binaire depuis `LOCAL_AGENT_DOWNLOAD_URL`.
2. Vérifie l'empreinte SHA256.
3. Spawne un processus shell détaché qui remplace le binaire après l'arrêt de l'agent.
4. Redémarre automatiquement.

Sécurité téléchargement : HTTPS obligatoire, redirections autorisées uniquement vers des hôtes approuvés (hôte portail, hôtes GitHub release, et éventuels hôtes explicitement autorisés par l'API).

### 3.2 Mise à jour manuelle

Téléchargez la nouvelle version et remplacez manuellement le fichier :
- Windows : fermez l'agent (menu tray → **Quitter**) puis remplacez `raguia-agent-windows.exe`.
- macOS : quittez l'agent puis remplacez le `.app` (glisser-déposer depuis le Finder en confirmant le remplacement).

Relancez ensuite l'agent normalement.

### 3.3 Publication d'une release (côté vous)

```bash
# Tagger la release (déclenche le build CI automatiquement)
git tag v0.3.0
git push origin v0.3.0
```

Le CI produit les artefacts et crée une GitHub Release. Mettez à jour dans votre `.env` :

```bash
LOCAL_AGENT_VERSION=0.3.0
LOCAL_AGENT_DOWNLOAD_URL=https://github.com/ValMtp3/raguia-agent-local/releases/download/v0.3.0/raguia-agent-windows.exe
LOCAL_AGENT_SHA256=<valeur du fichier .sha256>
```

Répétez pour macOS si vous gérez les deux plateformes (deux paires URL/SHA256, une par OS — à discriminer côté portail si nécessaire).

---

## 4. Configuration

### 4.1 Fichier de configuration

La configuration est stockée dans `~/.raguia/config.yaml` (créé par le wizard) :

```yaml
api_base: "https://raguia.monentreprise.com"
agent_token: "eyJhbGci..."          # ou sentinel keyring si secure_token_storage: true
watch_parent: "/Users/prenom/Documents"
root_folder_name: "RAGUIA"
secure_token_storage: false         # true = stocke le token dans le trousseau OS
structured_logging: true            # logs JSON (recommandé en prod)
auto_update: true
auto_update_check_hours: 24.0
```

Par défaut, le wizard pré-remplit `api_base` avec `https://raguia.valentin-fiess.fr`.

### 4.2 Variable d'environnement

Le Jeton peut être injecté via la variable d'environnement `RAGUIA_AGENT_TOKEN` (prioritaire sur le fichier YAML). Pratique pour les déploiements MDM/GPO sans passer par le wizard.

### 4.3 Démarrage sans interface tray (mode serveur)

Pour un déploiement sur un serveur sans affichage :
```bash
raguia-agent --no-tray
```
L'agent tourne en mode daemon pur, sans icône.

---

## 5. Dépannage

### Bascule cachée PROD / DEV depuis le tray

Par défaut, le menu tray **n'affiche pas** de bouton de bascule d'environnement.

Pour l'activer (usage admin uniquement), créer un fichier JSON :
- soit dans `~/.raguia/<nom-secret>.json`
- soit dans `assets/<nom-secret>.json` avant packaging PyInstaller

Exemple :
```json
{
  "enable_env_switch": true,
  "prod_api_base": "https://raguia.valentin-fiess.fr",
  "dev_api_base": "http://127.0.0.1:8000",
  "pin": "1234"
}
```

Si `pin` est défini, le tray demande ce code avant de basculer.

Le `<nom-secret>.json` peut être piloté par le secret GitHub
`RAGUIA_ADMIN_SWITCH_FILENAME` au build CI. Le workflow écrit ce nom dans
`assets/.raguia-admin-name.txt`, puis l'agent ne cherchera ce JSON qu'avec ce
nom précis.

Sans secret CI, le fallback reste `.raguia-admin.json` (compatibilité locale).

### L'agent ne se lance pas au démarrage

- **Windows** : Vérifier d'abord `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` → clé `Raguia Agent`. Sur certains anciens postes, vérifier aussi `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Raguia Agent.lnk`.
- **macOS** : Vérifier `~/Library/LaunchAgents/com.raguia.local.agent.plist`. Si absent, relancer l'agent manuellement une fois. Pour le recharger sans redémarrer :
  ```bash
  launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/com.raguia.local.agent.plist
  ```

### Erreur 401 / Jeton expiré

Menu tray → **Mettre à jour le jeton JWT** → coller le nouveau jeton depuis le portail.

Ou éditer directement `~/.raguia/config.yaml` et remplacer `agent_token`.

### Désactiver le démarrage automatique sans désinstaller

- **Windows** : Supprimer la clé de registre `Raguia Agent` dans `HKCU\...\Run`, et supprimer aussi (si présent) `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Raguia Agent.lnk`.
- **macOS** :
  ```bash
  launchctl bootout "gui/$(id -u)" ~/Library/LaunchAgents/com.raguia.local.agent.plist
  ```

### Désinstallation complète

Menu tray → **Désinstaller l'agent**. Cette action :
- Arrête l'agent.
- Supprime l'entrée de démarrage automatique (registre Windows ou LaunchAgent macOS).
- Supprime `~/.raguia/` (config, logs, états).
- **Ne supprime pas** le dossier `RAGUIA` contenant les documents du client.

Pour une désinstallation manuelle (si le tray n'est plus accessible) :
```bash
# macOS
launchctl bootout "gui/$(id -u)" ~/Library/LaunchAgents/com.raguia.local.agent.plist
rm -rf ~/Library/LaunchAgents/com.raguia.local.agent.plist ~/.raguia
```
```powershell
# Windows PowerShell
Remove-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" -Name "Raguia Agent" -ErrorAction SilentlyContinue
Remove-Item "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\Raguia Agent.lnk" -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force "$env:USERPROFILE\.raguia"
```

### Exporter les logs pour le support

Menu tray → **Exporter un bundle support** → transmettre le ZIP généré dans `~/.raguia/`.

Les logs bruts se trouvent dans `~/.raguia/agent.log` (et `agent.log.1`, `agent.log.2`, etc. pour la rotation).

---

## 6. Sécurité de la chaîne de mise à jour

L'agent refuse toute mise à jour si :
1. L'URL de téléchargement n'est pas en **HTTPS**.
2. La chaîne de redirection contient un hôte non approuvé.
3. L'empreinte **SHA256** du binaire téléchargé ne correspond pas à celle annoncée par le portail.

---

## 7. Workflow développement (usage interne uniquement)

Les scripts `install.sh` / `install.bat` et `update.sh` / `update.bat` restent présents dans le dépôt pour le **workflow de développement local**. Ils ne sont plus distribués aux clients.

Pour travailler sur l'agent en local :
```bash
cd raguia_local_agent
uv venv .venv --python 3.11
source .venv/bin/activate   # ou .venv\Scripts\activate.bat sur Windows
uv pip install -e ".[full]"
python -m raguia_local_agent
```

Pour builder un binaire en local (test du spec PyInstaller) :
```bash
cd raguia_local_agent
pip install pyinstaller
pyinstaller raguia_agent.spec
# → dist/raguia-agent.exe (Windows) ou dist/raguia-agent.app (macOS)
```
