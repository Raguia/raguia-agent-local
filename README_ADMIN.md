# Guide de Déploiement & Administration - Agent Local Raguia

Ce document fournit la documentation complète pour déployer, administrer et dépanner l'agent local Raguia chez un client.

---

## 1. Vue d'ensemble de l'installation

Les scripts d'installation (`install.sh` / `install.bat`) ont été conçus pour être **totalement autonomes, simples et fiables**. En une seule commande, l'installeur effectue pour vous :

1. **Détection de l'OS** (Windows, macOS, Linux).
2. **Installation de Git** si absent (via `winget`/`choco` sur Windows, `brew`/`apt`/`dnf`... sur Unix).
3. **Installation de uv**, un gestionnaire Python ultra-rapide.
4. **Installation de Python 3.11** de manière isolée et invisible pour votre système.
5. **Création de la configuration** locale (`raguia_agent.yaml`).
6. **Création d'un environnement virtuel** isolé (`venv`).
7. **Installation des dépendances** logicielles de l'agent.
8. **Test de la connexion API** direct avec le portail client.
9. **Configuration du démarrage automatique** (LaunchAgent, raccourci Démarrage, ou service systemd).

### Prérequis Systèmes Minimaux
Pour que l'installation automatique se déroule sans encombre, vérifiez ces prérequis :
- **Connexion Internet active** : le script doit pouvoir joindre Github, astral.sh (uv) et l'API Raguia.
- **Utilitaires de base** : `PowerShell` sous Windows (intégré par défaut) ou `curl` sous macOS/Linux.
- **Droits Administrateur / Sudo** : requis **uniquement** si `git` n'est pas déjà installé sur la machine.

---

## 2. Procédure d'installation (Pas-à-Pas)

### Étape 1 : Récupérer l'agent sur la machine

**Option A : Par Git (Fortement recommandée pour les mises à jour automatiques)**
Ouvrez votre terminal (Terminal sur macOS/Linux, PowerShell ou Invite de commandes sur Windows) :

```bash
winget install --id Git.Git -e --source winget   
```
```bash
git clone https://github.com/ValMtp3/raguia-agent-local.git
cd raguia-agent-local
```

**Option B : Par téléchargement manuel (Si Git est bloqué et irrécupérable)**
1. Téléchargez le code source en ZIP : [Télécharger Raguia Agent ZIP](https://github.com/ValMtp3/raguia-agent-local/archive/refs/heads/main.zip).
2. Extrayez le ZIP dans un dossier (ex: `Documents/raguia-agent-local`).
3. Ouvrez un terminal dans ce dossier extrait.

### Étape 2 : Lancer l'installation

*Astuce : Si vous lancez le script sans aucun argument, il vous posera les questions de manière interactive (mode, slug, jeton, dossier parent).*

**Sous macOS / Linux :**
```bash
./install.sh prod "slug-de-votre-client" "VOTRE_JETON_AGENT"
```
*(Si vous êtes en phase de développement local avec le backend sur votre machine : `./install.sh local ...`)*

**Sous Windows (PowerShell ou Invite de commandes) :**
```powershell
.\install.bat prod "slug-de-votre-client" "VOTRE_JETON_AGENT"
```

**Que se passe-t-il ensuite ?**
Le script s'occupe de tout. À la fin, s'il affiche **"Connexion réussie !"**, l'agent est pleinement fonctionnel et programmé pour démarrer en arrière-plan à chaque ouverture de session.

---

## 3. Scénarios alternatifs et Fallbacks (Quand ça coince)

Malgré l'automatisation, des politiques de sécurité d'entreprise peuvent bloquer l'installation. Voici comment pallier toutes les éventualités.

### Cas A : L'installation automatique de Git échoue
Si la machine bloque l'installation automatique de Git (pas de droits admin, pas de gestionnaire de paquets, Windows Store désactivé) :
1. Téléchargez et installez Git manuellement :
   - **Windows** : [Télécharger Git pour Windows](https://git-scm.com/download/win)
   - **macOS** : Tapez `xcode-select --install` dans le terminal.
   - **Linux** : Utilisez `sudo apt install git` ou équivalent.
2. Relancez simplement `install.sh` ou `install.bat`.

### Cas B : L'installation de `uv` échoue (Proxy / Réseau d'entreprise strict)
Si `curl` ou `powershell` n'arrivent pas à télécharger `uv` :
1. Téléchargez le binaire `uv` manuellement depuis la [page des releases Astral](https://github.com/astral-sh/uv/releases) et placez-le dans un dossier reconnu par votre `PATH`.
2. Relancez l'installeur.

### Cas C : Vous n'avez absolument aucun droit d'installation
Si vous êtes sur une machine verrouillée où l'installation système est impossible :
- Demandez à l'IT de fournir **Git** et **Python 3.11** en version "Portable".
- L'agent fonctionnera de manière portable tant que ces deux exécutables sont accessibles dans le terminal.

---

## 4. Dépannage & Erreurs Courantes

### Erreurs pendant l'installation
- **"git est introuvable après tentative automatique"** : Suivez le **Cas A** ci-dessus. L'installeur a échoué à cause de restrictions de sécurité OS.
- **"uv introuvable après installation"** : Le téléchargement a été bloqué par un proxy/antivirus. Suivez le **Cas B**.
- **"Échec de connexion au portail"** : Le jeton JWT est invalide, expiré, mal copié, ou l'URL de l'API est bloquée par le réseau client.
  - Vérifiez la validité de "VOTRE_JETON_AGENT".
  - Vérifiez que l'URL (`https://raguia.valentin-fiess.fr`) n'est pas bloquée par le pare-feu.

### Erreurs au fonctionnement quotidien de l'agent
- **Erreurs 401/403 (Non autorisé) dans les logs** :
  - Soit le token a expiré. Mettez-le à jour via l'icône de l'application dans la barre des tâches -> **"Mettre à jour le jeton JWT..."**.
  - Soit une variable système `RAGUIA_AGENT_TOKEN` sur votre machine écrase le token de configuration. Tapez `echo $RAGUIA_AGENT_TOKEN` pour vérifier.
- **"ModuleNotFoundError" ou "python3 introuvable"** :
  - Le dossier virtuel `venv` a été accidentellement corrompu ou supprimé.
  - **Solution simple** : Relancez `install.sh` / `install.bat`. Il recréera l'environnement sans casser votre configuration.
- **L'agent ne se lance plus au démarrage de l'ordinateur** :
  - Relancez le script d'installation, il répare le raccourci d'auto-démarrage.
  - Vous pouvez aussi vérifier le dossier de démarrage (Windows : `Win+R` -> `shell:startup`).

---

## 5. Commandes de Contrôle Quotidien

Les scripts d'action résident dans le sous-dossier caché `**.raguia_agent/**`. 

Depuis la racine du dossier `raguia-agent-local/` :

| Action                | macOS / Linux      | Windows            |
| --------------------- | ------------------ | ------------------ |
| Lancer l'agent        | `./.raguia_agent/start.sh` | `.\.raguia_agent\start.bat` |
| Tester la connexion   | `./.raguia_agent/test.sh`  | `.\.raguia_agent\test.bat`  |
| Arrêter l'agent       | `./.raguia_agent/stop.sh`  | `.\.raguia_agent\stop.bat`  |

### Interface Utilisateur (Menu Tray)
Lorsque l'agent tourne, une icône apparaît dans la zone de notification (Barre des tâches) :
- **Vérifier / Installer une mise à jour** : Télécharge les nouveautés de Github et met à jour l'agent instantanément.
- **Lancer un diagnostic (Doctor)** : Affiche l'état de santé du programme.
- **Exporter un bundle support** : Regroupe les logs dans un fichier ZIP, idéal pour le support technique.
- **Mettre à jour le jeton JWT** : Permet de changer le jeton sans utiliser le terminal.
- **Désinstaller l'agent** : Arrête l'agent, supprime l'auto-démarrage et nettoie les fichiers. Le dossier `RAGUIA` contenant les documents du client **n'est pas supprimé**.

### Forcer une mise à jour manuelle (Ligne de commande)
Si vous avez installé via Git (Option A), vous pouvez mettre à jour le code sans utiliser l'interface graphique :
- **macOS / Linux** : `./update.sh`
- **Windows** : `update.bat`

---

## 6. Configuration Avancée et Sécurité

### Le fichier `raguia_agent.yaml`
Toute la configuration de l'agent est stockée dans `.raguia_agent/raguia_agent.yaml` :

```yaml
api_base: "https://raguia.valentin-fiess.fr"
client_slug: "client-acme"
agent_token: "eyJhbGci..."
watch_parent: "/Users/nom/Documents"
root_folder_name: "RAGUIA"
runtime_env: "prod"
secure_token_storage: true  # (Recommandé) Tente de stocker le token crypté dans le trousseau OS
structured_logging: true    # Exporte les logs au format JSON pour une meilleure traçabilité
```

*(L'agent ignore par défaut les fichiers temporaires et systèmes comme `~$*.docx` ou `.tmp` pour éviter d'envoyer des déchets sur le portail).*

### Sécurité de la chaîne de mise à jour
L'agent dispose d'une sécurité intégrée pour ses mises à jour automatiques. Une mise à jour provenant du backend est refusée si :
1. L'URL de téléchargement n'est pas en **HTTPS**.
2. L'hôte de téléchargement est différent de celui de l'API (protection contre les injections).
3. L'empreinte de sécurité **SHA256** ne correspond pas.

### Désactivation manuelle du démarrage automatique
Si vous souhaitez empêcher l'agent de se lancer tout seul (sans pour autant le désinstaller) :
- **Windows** : Supprimez le raccourci `Raguia Agent` dans votre dossier Démarrage (`Win+R` -> `shell:startup`).
- **macOS** : `launchctl bootout "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.raguia.local.agent.plist"`
- **Linux** : `systemctl --user disable --now raguia-agent.service`
