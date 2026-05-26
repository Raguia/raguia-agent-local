# Guide d'administration — Agent Local Raguia (Tauri)

Ce document est destiné aux administrateurs qui déploient, configurent et maintiennent l'agent chez un client.

---

## 1. Architecture

L'agent est une application native **Rust/Tauri 2** (plus de Python). Compilé en binaire natif via `pnpm tauri build`, distribué en `.msi` (Windows) et `.dmg` (macOS).

| Plateforme | Artefact |
|---|---|
| Windows 10/11 x64 | `Raguia-Agent.msi` |
| macOS Apple Silicon | `Raguia-Agent.dmg` |

Les binaires sont publiés à chaque release GitHub (`v*`) via le workflow `.github/workflows/build_binaries.yml`.

---

## 2. Déploiement

### 2.1 Procédure (Windows)

1. Transmettre le `.msi` au client.
2. Double-clic → Assistant d'installation Windows.
3. L'assistant de configuration s'ouvre au premier lancement. Saisir URL, slug, mot de passe, dossier parent.
4. L'agent se configure automatiquement au démarrage du système.

### 2.2 Procédure (macOS)

1. Transmettre le `.dmg` au client.
2. Glisser `Raguia Agent.app` dans le dossier `Applications`.
3. Au premier lancement, si macOS affiche « logiciel potentiellement malveillant » (Gatekeeper — app non signée/notarisée) :
   - Faire **Clic droit** sur `Raguia Agent.app` → **Ouvrir**
   - Cliquer **« Ouvrir quand même »**
   - Ce contournement n'est nécessaire qu'à la première ouverture
4. L'assistant de configuration s'ouvre automatiquement.
5. L'agent se configure automatiquement au démarrage du système.

### 2.3 Déploiement MDM/GPO

L'agent supporte l'installation silencieuse via les outils MDM. L'auto-start est géré via `tauri-plugin-autostart` (LaunchAgent macOS / Run registry Windows).

---

## 3. Configuration

La configuration est stockée via `tauri-plugin-store` (chiffré via le trousseau OS : Keychain macOS, Credential Manager Windows).

Le wizard de configuration (HTML natif intégré) recueille les identifiants et persiste les données au premier lancement.

Fichiers stockés dans `~/Library/Application Support/com.raguia.agent/` (macOS) ou `%APPDATA%/com.raguia.agent/` (Windows) :
- `raguia-config.json` — configuration applicative (chiffré)
- `sync_queue.sqlite` — file d'attente des fichiers à synchroniser

---

## 4. Mises à jour

L'agent utilise `tauri-plugin-updater` pour les mises à jour automatiques. Le processus est signé cryptographiquement :

1. Publication d'une release GitHub avec les artefacts signés
2. Configuration de l'URL de l'endpoint de mise à jour dans `tauri.conf.json`
3. L'agent vérifie périodiquement les nouvelles versions
4. Téléchargement et installation atomique (swap du binaire)

---

## 5. Signature des binaires

### macOS

La signature Apple est configurée via les secrets GitHub :
- `APPLE_SIGNING_IDENTITY`, `APPLE_CERTIFICATE`, `APPLE_CERTIFICATE_PASSWORD`, `APPLE_TEAM_ID`, `APPLE_ID`, `APPLE_PASSWORD`

Les builds CI signent et notarisent automatiquement le `.dmg` quand ces secrets sont présents.

### Windows

La signature du `.msi` utilise `TAURI_SIGNING_PRIVATE_KEY` et `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` (clé de signature Tauri).

---

## 6. Publication d'une release

```bash
git tag v0.2.0
git push origin v0.2.0
```

Le CI build les binaires (Windows + macOS), les signe, et crée une GitHub Release avec les artefacts.

---

## 7. Dépannage

### Logs

Les logs sont visibles dans la console (stderr) ou via `RUST_LOG=raguia_agent=debug` pour le mode verbose.

Fichiers de données : `~/Library/Application Support/com.raguia.agent/raguia-agent/` (macOS) ou `%APPDATA%/com.raguia.agent/raguia-agent/` (Windows).

### Désinstallation

- **Windows** : Supprimer via `Programmes et fonctionnalités` → `Raguia Agent`
- **macOS** : Supprimer `Raguia Agent.app` des Applications

Les fichiers de configuration ne sont pas automatiquement supprimés (à nettoyer manuellement si nécessaire).

### Auto-start

Géré par `tauri-plugin-autostart` :
- **Windows** : clé Registre `HKCU\...\Run\com.raguia.agent`
- **macOS** : LaunchAgent `~/Library/LaunchAgents/com.raguia.agent.plist`
- **Linux** : `.config/autostart/com.raguia.agent.desktop`

---

## 8. Développement

```bash
cd raguia_local_agent

pnpm install

# Développement avec rechargement à chaud
pnpm tauri dev

# Build release
pnpm tauri build

# Lints
cargo clippy -- -D warnings
cargo fmt

# Tests
cargo test
```

---

## 9. Debug / Mode Admin

L'agent embarque un **mode admin discret** qui permet de visualiser les logs internes, l'état de la file d'attente et un résumé de la configuration depuis le menu tray.

### Activation

Le mode admin est stocké dans le fichier `raguia-config.json` sous une clé anodine :

```json
{ "_sk": true }
```

**Méthode 1 — Variable d'environnement** (au lancement) :
```bash
RAGUIA_ADMIN=1 ./raguia-agent
```
Le flag est persisté dans la config après le premier démarrage.

**Méthode 2 — Manuelle** :
1. Fermer l'agent
2. Éditer `raguia-config.json` (dans `~/Library/Application Support/com.raguia.agent/`)
3. Remplacer `"_sk": false` par `"_sk": true`
4. Relancer l'agent

**Méthode 3 — Commande Tauri** (via le wizard frontend) :
```js
await invoke('toggle_admin_mode')
```

### Interface

Une fois activé, cliquer sur **Admin** dans le menu tray affiche une boîte de dialogue avec :
- Les **20 dernières lignes de log** (anneau mémoire de 500 entrées)
- Les **statistiques de la file d'attente** : en attente, supprimés, synchronisés, bloqués
- Le **résumé de la config** : URL API, slug, intervalle de poll

### Désactivation

- Relancer sans `RAGUIA_ADMIN=1` puis repasser `"_sk"` à `false` dans le store
- Ou utiliser `toggle_admin_mode` depuis le wizard

> **Note** : Le nom `"_sk"` dans le store est volontairement discret pour ne pas attirer l'attention d'un utilisateur non-administrateur.
```
