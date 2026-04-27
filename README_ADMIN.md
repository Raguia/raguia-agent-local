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

Ouvrez le terminal dans le dossier téléchargé et exécutez la commande avec vos identifiants :

```bash
./install.sh "https://raguia.client-domaine.com" "VOTRE_JETON_SAAS" "/chemin/vers/dossier/cible"
```

Vous pouvez aussi lancer simplement `./install.sh` : le script vous pose les questions en CLI (URL, jeton JWT, dossier parent).
Par défaut, l’URL proposée est `https://raguia.valentin-fiess.fr` (prod).  
Pour forcer un setup local, passez `local` en 4e argument :

```bash
./install.sh "" "" "" local
```

#### Windows

Ouvrez PowerShell ou l'Invite de commandes dans le dossier téléchargé et exécutez :

```powershell
.\install.bat "https://raguia.client-domaine.com" "VOTRE_JETON_SAAS" "C:\chemin\vers\dossier\cible"
```

Vous pouvez aussi lancer simplement `.\install.bat` : le script vous guide en CLI et demande les champs manquants.
Par défaut, l’URL proposée est `https://raguia.valentin-fiess.fr` (prod).  
Pour forcer un setup local :

```powershell
.\install.bat "" "" "" local
```

Le dossier `**.raguia_agent/**` est **fourni dans le dépôt** (scripts shell / batch). L’installation y ajoute ce qui est local à la machine : `**venv/`** (Python) et `**raguia_agent.yaml**` (jeton, chemins), non versionnés.  
Les scripts `**start.sh**` / `**test.sh**` (macOS/Linux) créent désormais automatiquement `venv/` s’il est absent. En revanche, sans `**raguia_agent.yaml**` valide (généré par `**install.sh**` / `**install.bat**`), l’agent ne peut pas se connecter correctement.

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
- **`python3` introuvable** : installez Python 3 puis relancez le script.
- **Module introuvable** : exécutez `**install.sh`** / `**install.bat**` pour créer la configuration `raguia_agent.yaml` et préparer l’environnement local.

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

Sans `LOCAL_AGENT_VERSION`, l'endpoint `GET /api/portal/agent/version` renvoie `404`.

