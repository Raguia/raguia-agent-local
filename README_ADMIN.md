# Guide de Déploiement & Administration - Agent Local Raguia

Ce document fournit les procédures de déploiement de l'agent local Raguia chez un client.

## 1. Téléchargement et Installation Automatisée (Autonome)

Vous n'avez **plus besoin d'installer Python ou d'autres outils manuellement**. Les scripts d'installation se chargent de tout télécharger de manière autonome.

### Étape 1 : Télécharger l'agent

Récupérez le code de l'agent depuis notre dépôt GitHub public :

```bash
git clone https://github.com/ValMtp3/raguia-agent-local.git
cd raguia-agent-local
```

*(Si `git` n'est pas installé, vous pouvez [télécharger le ZIP ici](https://github.com/ValMtp3/raguia-agent-local/archive/refs/heads/main.zip) et l'extraire).*

### Étape 2 : Lancer l'installation

#### macOS / Linux

**Mode recommandé** — même URL de portail qu’à la main (`…/portal/<slug>`), seuls le slug et le mode diffèrent entre dev et prod :

```bash
./install.sh prod mon-client-slug "VOTRE_JETON_AGENT"
./install.sh local mon-client-slug "VOTRE_JETON_AGENT"
```

Sans argument, le script demande **prod ou local**, le **slug client** (la partie après `/portal/`), puis le jeton et le dossier parent.  
`prod` utilise `api_base=https://raguia.valentin-fiess.fr` ; `local` utilise par défaut `http://localhost:5173` (proxy Vite vers le backend). Pour pointer directement sur uvicorn : `export RAGUIA_LOCAL_API_BASE=http://127.0.0.1:8000` avant `install.sh`.  
Pour le défaut interactif « prod ou local », vous pouvez fixer `RAGUIA_INSTALL_ENV=local`. Pour une autre origine prod : `RAGUIA_PORTAL_ORIGIN_PROD=https://…`.

**Ancien mode (compatibilité)** — URL API complète en premier argument :

```bash
./install.sh "https://raguia.valentin-fiess.fr" "VOTRE_JETON_AGENT" "/chemin/dossier/parent"
```

#### Windows

```powershell
.\install.bat prod mon-client-slug "VOTRE_JETON_AGENT"
.\install.bat local mon-client-slug "VOTRE_JETON_AGENT"
```

Variables d’environnement identiques (`RAGUIA_INSTALL_ENV`, `RAGUIA_PORTAL_ORIGIN_PROD`, `RAGUIA_LOCAL_API_BASE`). Ancien mode : `.\install.bat "https://…" "JETON" "C:\Documents"`.

Sans argument, `install.bat` demande le mode (prod/local), le slug, le jeton et le dossier parent.

Le dossier `**.raguia_agent/**` est **fourni dans le dépôt** (scripts shell / batch). L’installation y ajoute ce qui est local à la machine : `**venv/`** (Python) et `**raguia_agent.yaml`** (jeton, chemins), non versionnés.  
Les scripts `**start.sh`** / `**test.sh**` (macOS/Linux) créent désormais automatiquement `venv/` s’il est absent. En revanche, sans `**raguia_agent.yaml**` valide (généré par `**install.sh**` / `**install.bat**`), l’agent ne peut pas se connecter correctement.

### Démarrage automatique (fait par l’installateur)

L’installateur détecte l’OS et configure le lancement au démarrage de session utilisateur :


| OS          | Comportement                                                                                                                                                                                               |
| ----------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Windows** | Raccourci **« Raguia Agent »** dans le dossier **Démarrage** (`Win+R` → `shell:startup`), cible `.raguia_agent\start.bat`.                                                                                 |
| **macOS**   | **LaunchAgent** `com.raguia.local.agent` dans `~/Library/LaunchAgents/`, exécution de `.raguia_agent/start.sh`.                                                                                            |
| **Linux**   | Unité **systemd utilisateur** `raguia-agent.service` sous `~/.config/systemd/user/`. Sur certains serveurs : `loginctl enable-linger $USER` pour que le service utilisateur tourne sans session graphique. |


Pour **désactiver** l’auto-démarrage : supprimez le raccourci Windows, ou le `.plist` / désactivez le service systemd utilisateur comme indiqué plus bas.

## 2. Commandes de contrôle (`.raguia_agent`)

Les scripts ne sont **pas** à la racine du clone : tout est sous `**.raguia_agent/`**.


| Action                | macOS / Linux      | Windows            |
| --------------------- | ------------------ | ------------------ |
| Aller dans le dossier | `cd .raguia_agent` | `cd .raguia_agent` |
| Lancer l'agent        | `./start.sh`       | `.\start.bat`      |
| Tester la connexion   | `./test.sh`        | `.\test.bat`       |
| Arrêter               | `./stop.sh`        | `.\stop.bat`       |


Depuis la racine du clone : `./.raguia_agent/test.sh` ou `.\.raguia_agent\test.bat`.

### Mise à jour complète du dépôt (équivalent à « refaire » git clone + dépendances)

Sans refaire `install.sh` pour la config ni le jeton :

1. À la **racine du clone** (là où se trouvent `install.sh` et `pyproject.toml`) :
   - macOS / Linux : `./update.sh`
   - Windows : `update.bat`
2. Le script fait **`git pull`** (branche par défaut `main`, surcharge possible avec `RAGUIA_AGENT_BRANCH`) puis **`pip install -e ".[tray]"`** dans le venv `.raguia_agent/venv`.
3. Redémarrer l’agent : icône → Quitter puis relancer `start.sh` / `start.bat`.

Il faut que le dossier soit toujours un **clone git** (présence de `.git`). Sinon, refaire `git clone …` puis `install.sh`.

**Menu icône « Vérifier / installer mise à jour »** : exécute **directement** `git pull` puis `pip install -e ".[tray]"` dans le clone (comme `update.sh`), sans téléchargement distant. La variable **`RAGUIA_AGENT_REPO`** est définie par `start.sh` / `start.bat`. En ligne de commande équivalent : `raguia-local-update` ou `python -m raguia_local_agent.local_git_update`.

- **start** : surveillance du dossier RAGUIA (icône tray si installé).
- **test** : vérifie le portail / le jeton sans laisser l’agent tourner en continu.
- **stop** : arrête l’agent.
- **Mise à jour JWT via interface** : dans le menu tray, utilisez **« Mettre a jour le jeton JWT… »**. Le jeton est testé immédiatement puis sauvegardé dans la config.
- **Doctor (diagnostic client)** : dans le menu tray, utilisez **« Lancer un diagnostic (Doctor)… »** pour un état lisible (URL, connexion, queue, auto-start, stockage token), sans afficher de secrets.
- **Export support** : dans le menu tray, utilisez **« Exporter un bundle support… »** pour générer un ZIP (`~/.raguia/support_bundle_*.zip`) contenant les logs et le dernier diagnostic.
- **Désinstallation via interface** : dans le menu tray, utilisez **« Desinstaller l'agent… »** puis confirmez. La désinstallation :
  - arrête l’agent,
  - supprime le démarrage automatique (Windows/macOS/Linux),
  - supprime les fichiers locaux de l’agent (`.raguia_agent` et `~/.raguia`).
  - Le dossier de documents `RAGUIA` n’est pas supprimé.

### Erreur « no such file » ou venv manquant

- Vous avez lancé `./test.sh` à la racine : utilisez `./.raguia_agent/test.sh` ou `cd .raguia_agent` d’abord.
- `**python3` introuvable** : installez Python 3 puis relancez le script.
- **Module introuvable** : exécutez `**install.sh`** / `**install.bat`** pour créer la configuration `raguia_agent.yaml` et préparer l’environnement local.

## 3. Désactiver / ajuster le démarrage automatique

Si vous avez utilisé l’installateur et souhaitez revenir en arrière :

- **Windows** : supprimez le raccourci **Raguia Agent** dans le dossier Démarrage (`shell:startup`).
- **macOS** : `launchctl bootout "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.raguia.local.agent.plist"` (ou supprimez ce fichier puis reconnectez-vous).
- **Linux** : `systemctl --user disable --now raguia-agent.service`.

Installation manuelle du démarrage (sans passer par l’installateur) : possible en pointant toujours vers le **chemin absolu** de `start.bat` ou `start.sh` dans `.raguia_agent/`.

## 4. Dépannage Administrateur

- **Erreurs 401/403** : Vérifier le jeton et l’URL du portail dans `.raguia_agent/raguia_agent.yaml`. Testez avec `cd .raguia_agent && ./test.sh` (ou `.\test.bat` sous Windows).
- **Fichiers ignorés** : L'agent ignore volontairement les fichiers temporaires (`~$*.docx`, `.tmp`).
- **Logs** : Situés dans `~/.raguia/agent.log` avec rotation automatique (`agent.log.1` ... `agent.log.5`).

## 5. Sécurité et modes de config

- **Stockage du jeton** : par défaut, l'agent tente de stocker le token dans le trousseau OS (Keychain/Credential Manager/libsecret) avec fallback compatibilité.
- **Mode strict recommandé** : ajoutez `secure_token_storage: true` dans la config pour refuser le token en clair **si** le keyring est disponible.
- **Logging structuré** : `structured_logging: true` (par défaut) écrit des logs JSON adaptés au support.

Exemple de paramètres :

```yaml
secure_token_storage: true
structured_logging: true
```

## 6. Confiance de la chaîne de mise à jour

La mise à jour est refusée si :

- la somme `sha256` est absente,
- l'URL de téléchargement n'est pas en HTTPS,
- l'hôte de téléchargement diffère de l'hôte du portail.

Cela réduit les risques de source de mise à jour non approuvée.

### Paramètres backend requis pour exposer l'update

Renseigner côté backend (variables d'environnement) :

- `LOCAL_AGENT_VERSION` (ex: `0.2.3`)
- `LOCAL_AGENT_DOWNLOAD_URL` (HTTPS, même hôte que le portail)
- `LOCAL_AGENT_SHA256` (sha256 hex du script/fichier distribué)

Sans `LOCAL_AGENT_VERSION`, l’endpoint renvoie `200` avec `"version": null` et `"configured": false` (pas de mise à jour annoncée).