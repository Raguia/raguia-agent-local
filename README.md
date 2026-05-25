# Agent Raguia

L'Agent Raguia est un petit programme discret dans votre barre des tâches. Il surveille un dossier nommé **RAGUIA** sur votre machine et envoie automatiquement les documents que vous y déposez vers votre espace Raguia.

**Technologie** : Application native Rust/Tauri (plus de dépendance Python).

---

## Installation

Téléchargez l'installeur depuis votre portail Raguia :

| Système | Fichier |
|---|---|
| macOS | `Raguia-Agent.dmg` |
| Windows | `Raguia-Agent.msi` |

Double-cliquez et suivez l'assistant.

> **⚠️ macOS – « logiciel potentiellement malveillant » ?**
> Si macOS bloque l'ouverture avec ce message, faites **Clic droit** sur `Raguia Agent.app` → **Ouvrir** (pas double-clic). Une fenêtre propose alors *« Ouvrir quand même »* — cliquez. Cela n'arrive qu'à la première ouverture, et uniquement tant que l'application n'est pas signée/notarisée Apple.

Au premier lancement, une fenêtre s'ouvre automatiquement pour saisir :
1. **URL du portail** — celle de votre espace Raguia
2. **Slug client** + **Mot de passe** — vos identifiants portail
3. **Dossier de travail** — où créer le dossier `RAGUIA`

Après connexion, l'agent démarre automatiquement à chaque ouverture de session.

---

## Icône dans la barre des tâches

| Icône | Signification |
|---|---|
| 🟢 Normale | Tout va bien, agent actif |
| 🔵 Synchro | Envoi de documents en cours |
| 🟠 Attention | Fichier bloqué ou session qui expire bientôt |
| 🔴 Erreur | Connexion perdue ou session expirée |

**Clic droit :** Synchroniser, reconnecter, vérifier les mises à jour, quitter.

---

## Dépannage

**L'icône est rouge** — Vérifiez votre connexion internet puis reconnectez-vous via le menu.

**Un fichier déposé n'apparaît pas** — S'il est encore ouvert (Word/Excel), l'agent attend sa fermeture.

**Le support me demande un diagnostic** — Menu tray → **À propos** pour voir la version.

---

## Développement

```bash
cd raguia_local_agent

pnpm install

# Compiler (debug)
pnpm tauri dev

# Compiler (release)
pnpm tauri build
```
