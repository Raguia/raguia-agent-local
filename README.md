# Bienvenue sur l'Agent Raguia 🚀

L'Agent Raguia est un petit programme discret qui s'installe sur votre ordinateur. Son rôle est très simple : il surveille un dossier nommé **RAGUIA** sur votre ordinateur et envoie automatiquement (et de manière sécurisée) tous les documents que vous y placez directement vers votre portail web Raguia.

Il a été conçu pour être totalement invisible au quotidien : pas de fenêtres compliquées, juste une petite icône dans votre barre des tâches qui vous indique que tout va bien.

---

## 💡 Comment ça marche ? (Questions fréquentes)

### Dois-je l'installer sur tous les ordinateurs de l'entreprise ?
**Non, idéalement sur un seul ordinateur.** Le but est de centraliser vos documents sur une machine (ou un serveur de l'entreprise). Si plusieurs personnes doivent accéder à ce dossier, nous vous conseillons de le placer sur un lecteur réseau partagé que cet ordinateur surveillera.

### Que se passe-t-il si j'éteins mon ordinateur ?
**Absolument rien de grave !** L'agent se mettra en pause. Les fichiers que vous ajoutez ou modifiez pendant que l'ordinateur est éteint ne seront pas perdus. Dès que vous rallumerez votre ordinateur, l'agent Raguia détectera automatiquement tous les changements et rattrapera son retard de manière transparente.

### Que se passe-t-il si je change d'ordinateur ?
C'est très simple :
1. Installez l'agent sur votre nouvel ordinateur.
2. Connectez-le avec votre Jeton de sécurité (voir Installation ci-dessous).
3. Rapatriez vos documents dans le nouveau dossier `RAGUIA` créé.
L'agent reprendra son travail là où il s'était arrêté.

---

## 🛠️ Installation en 2 minutes chrono

L'installation a été simplifiée au maximum avec un assistant visuel.

### Étape 1 : Récupérer votre "Jeton" sur le portail web
Ce jeton est une clé de sécurité unique qui prouve à l'agent qu'il a le droit d'envoyer des documents sur votre espace.
1. Connectez-vous à votre **Portail web Raguia**.
2. Allez dans l'onglet **Paramètres**.
3. Dans la section **Agent de synchronisation**, cliquez sur **Générer un nouveau jeton**.
4. Copiez ce long texte, gardez-le sous la main.

### Étape 2 : Lancer l'assistant d'installation
Vous pouvez installer l'agent automatiquement depuis notre dépôt public.

1. Téléchargez l'agent (depuis [GitHub](https://github.com/ValMtp3/raguia-agent-local/archive/refs/heads/main.zip) ou via `git clone`).
2. Ouvrez le dossier téléchargé.
3. Lancez le script d'installation (`install.bat` sur Windows ou `install.sh` sur Mac/Linux).
4. S'il n'a jamais été configuré, une fenêtre d'assistant va s'ouvrir automatiquement.

L'assistant va vous poser 3 questions simples :
1. **URL de votre portail et Jeton** : Collez l'adresse de votre portail (ex: `https://raguia.monentreprise.com`) et le jeton copié à l'étape 1.
2. **Dossier parent** : Choisissez où vous voulez que le dossier `RAGUIA` soit créé (par défaut, il se mettra dans vos `Documents`).
3. **Test** : Un bouton pour vérifier que la connexion fonctionne bien.

Et c'est tout ! Cliquez sur "Enregistrer & Démarrer".

*(Pour les administrateurs : l'installation peut être totalement automatisée en ligne de commande. Voir le fichier `README_ADMIN.md` pour plus de détails).*

---

## 🟢 Au quotidien : La petite icône magique

Une fois lancé, l'agent Raguia se cache dans votre barre des tâches (en bas à droite sur Windows, en haut à droite sur Mac). 

Un simple coup d'œil à la couleur du petit cercle vous donne son état :
* 🟢 **Vert** : Tout va bien, l'agent est actif et le dossier est à jour.
* 🔵 **Bleu** : Envoi de documents en cours...
* 🟠 **Orange** : Attention, un fichier est bloqué (souvent parce que vous l'avez actuellement ouvert dans Word ou Excel, fermez-le pour qu'il s'envoie) ou votre Jeton va bientôt expirer.
* 🔴 **Rouge** : Erreur (généralement une coupure internet, ou votre Jeton est expiré).

### Clic droit sur l'icône
Faites un clic droit sur l'icône pour faire apparaître un menu très utile :
* **Ouvrir le dossier RAGUIA** : Raccourci rapide pour accéder à vos documents.
* **Synchroniser maintenant** : Force l'envoi immédiat si vous êtes pressé.
* **Lancer un diagnostic (Doctor)** : Vérifie automatiquement l'état de l'agent et affiche des messages simples (sans données sensibles).
* **Exporter un bundle support** : Génère un fichier ZIP de diagnostic pour l'assistance.
* **Voir l'état** : Indique combien de fichiers sont en attente d'envoi, et à quand remonte la dernière synchronisation.
* **Réinitialiser les fichiers bloqués** : Si l'icône est orange à cause d'un fichier bloqué, ce bouton demande à l'agent de réessayer.

---

## 🛡️ Résolution des problèmes courants

**Le fichier que je viens de déposer n'apparait pas sur le portail ?**
* Vérifiez l'icône : si elle est verte, l'envoi a bien été fait.
* Si le fichier est un document Word (`.docx`) ou Excel (`.xlsx`) et qu'il est ouvert sur votre ordinateur, c'est normal ! L'agent attend que vous ayez fini de travailler dessus (et fermé le logiciel) avant de l'envoyer pour éviter d'envoyer des fichiers à moitié terminés.

**J'ai renommé ou déplacé le dossier RAGUIA par erreur !**
Pas de panique. L'agent Raguia est intelligent : si vous avez juste déplacé le dossier, il va essayer de le retrouver tout seul. Si vous l'avez supprimé, l'icône passera au rouge/orange pour vous prévenir. Remettez le dossier en place, et tout rentrera dans l'ordre.

**Mon icône est rouge !**
Vérifiez d'abord votre connexion internet. Si internet fonctionne, c'est probablement que votre Jeton de sécurité a expiré. Retournez sur le portail web, générez un nouveau jeton, et remplacez-le. 

**Le support me demande un diagnostic**
Faites un clic droit sur l'icône puis :
1. **Lancer un diagnostic (Doctor)** pour voir le statut global.
2. **Exporter un bundle support** pour générer le ZIP à transmettre.
Ce bundle ne contient pas votre jeton en clair.

---

*L'Agent Raguia a été pensé pour vous faire gagner du temps sans vous poser de questions. Bon travail !*
 