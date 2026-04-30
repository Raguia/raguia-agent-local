# Agent Raguia

L'Agent Raguia est un petit programme discret qui s'installe sur votre ordinateur. Son rôle est simple : il surveille un dossier nommé **RAGUIA** sur votre machine et envoie automatiquement, de manière sécurisée, tous les documents que vous y déposez vers votre espace Raguia.

Il a été conçu pour être totalement invisible au quotidien : pas de fenêtres compliquées, juste une petite icône dans votre barre des tâches qui vous indique que tout va bien.

---

## Installation en 2 étapes

### Étape 1 — Récupérer votre Jeton sur le portail

Le Jeton est une clé de sécurité unique qui autorise l'agent à envoyer des documents sur votre espace.

1. Connectez-vous à votre **Portail Raguia**.
2. Allez dans l'onglet **Paramètres**.
3. Dans la section **Agent de synchronisation**, cliquez sur **Générer un nouveau jeton**.
4. Copiez ce texte, gardez-le sous la main.

### Étape 2 — Télécharger et lancer l'agent

Votre administrateur vous a transmis un lien de téléchargement, ou vous le trouvez sur votre portail.

| Système | Fichier à télécharger |
|---|---|
| Windows 10/11 | `raguia-agent-windows.exe` |
| Mac (Apple Silicon M1/M2/M3) | `raguia-agent-macos-arm64.zip` |

**Windows :** Double-cliquez sur le fichier `.exe`. Si Windows affiche un avertissement "Application inconnue", cliquez sur **Informations supplémentaires** puis **Exécuter quand même** — c'est normal pour un logiciel professionnel sans signature numérique grand public.

**Mac :** Dézippez l'archive, puis faites un **clic droit → Ouvrir** sur l'application `raguia-agent`. Si macOS refuse, ouvrez les **Préférences Système → Sécurité & Confidentialité** et cliquez sur "Ouvrir quand même". Vous pouvez aussi supprimer la restriction manuellement en une commande (votre administrateur peut vous aider) :
```
xattr -d com.apple.quarantine raguia-agent.app
```

Une fenêtre d'assistant s'ouvre au premier lancement et vous pose 3 questions :
1. **URL du portail** : l'adresse de votre espace Raguia (ex: `https://raguia.monentreprise.com`).
2. **Jeton** : collez le jeton copié à l'étape 1.
3. **Dossier** : choisissez où créer le dossier `RAGUIA` (par défaut dans vos `Documents`).

Cliquez sur **Tester** puis **Enregistrer & Démarrer**. C'est tout.

L'agent démarrera automatiquement à chaque ouverture de session, sans que vous ayez à faire quoi que ce soit.

---

## Questions fréquentes

**Dois-je l'installer sur tous les ordinateurs de l'entreprise ?**
Non, idéalement sur un seul poste. Si plusieurs personnes doivent accéder aux documents, placez le dossier `RAGUIA` sur un lecteur réseau partagé que cet ordinateur surveillera.

**Que se passe-t-il si j'éteins mon ordinateur ?**
Rien de grave. L'agent se met en pause. Dès le rallumage, il détecte automatiquement tous les changements survenus pendant son absence et rattrape son retard.

**Que se passe-t-il si je change d'ordinateur ?**
1. Téléchargez l'agent sur le nouvel ordinateur.
2. Reconnectez-le avec votre Jeton (le même ou un nouveau depuis le portail).
3. Déplacez vos documents dans le nouveau dossier `RAGUIA`.

---

## L'icône dans la barre des tâches

Une fois lancé, l'agent s'installe discrètement dans votre barre des tâches (bas-droite sous Windows, haut-droite sur Mac).

| Couleur | Signification |
|---|---|
| Vert | Tout va bien, l'agent est actif |
| Bleu | Envoi de documents en cours |
| Orange | Un fichier est bloqué (souvent ouvert dans Word/Excel) ou le Jeton expire bientôt |
| Rouge | Erreur de connexion ou Jeton expiré |

**Clic droit sur l'icône :**

- **Ouvrir le dossier RAGUIA** — raccourci rapide vers vos documents.
- **Synchroniser maintenant** — force l'envoi immédiat.
- **Lancer un diagnostic** — vérifie l'état de l'agent et affiche un résumé simple.
- **Exporter un bundle support** — génère un fichier ZIP pour l'assistance (ne contient pas votre Jeton en clair).
- **Vérifier / installer mise à jour** — télécharge et installe la nouvelle version si disponible.
- **Mettre à jour le jeton JWT** — permet de coller un nouveau Jeton sans relancer l'agent.
- **Désinstaller l'agent** — arrête l'agent, supprime l'auto-démarrage et nettoie les fichiers locaux. Le dossier `RAGUIA` et vos documents ne sont pas touchés.

---

## Résolution des problèmes courants

**Un fichier déposé n'apparaît pas sur le portail**
Vérifiez la couleur de l'icône. Si le fichier est un `.docx` ou `.xlsx` encore ouvert dans Office, c'est normal : l'agent attend que vous l'ayez fermé pour l'envoyer en version complète.

**L'icône est rouge**
Vérifiez votre connexion internet. Si elle fonctionne, votre Jeton a probablement expiré. Générez-en un nouveau depuis le portail et collez-le via **Clic droit → Mettre à jour le jeton JWT**.

**Le support me demande un diagnostic**
Clic droit sur l'icône → **Lancer un diagnostic**, puis **Exporter un bundle support**. Transmettez le fichier ZIP généré.
