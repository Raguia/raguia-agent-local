# Politique de sécurité

## Versions supportées

| Version | Support sécurité |
|---------|-----------------|
| 0.2.x   | ✅ Oui           |
| < 0.2   | ❌ Non           |

## Signaler une vulnérabilité

Si vous découvrez une faille de sécurité dans cet agent, **ne pas ouvrir une issue publique**.

Contactez-nous directement via le portail Raguia ou par e-mail à l'adresse indiquée sur votre espace client.

Nous nous engageons à :
- Accuser réception sous 48h
- Vous tenir informé de l'avancement du correctif
- Publier un correctif dans les meilleurs délais

## Bonnes pratiques

- Le dépôt contient le dossier **`.raguia_agent/`** avec les scripts d’exécution ; en revanche **`raguia_agent.yaml`** et le répertoire **`venv/`** restent **locaux** et ne doivent **pas** être versionnés (jeton et environnement Python).
- **Ne jamais committer** votre fichier `raguia_agent.yaml` (il contient votre jeton JWT personnel).
- Regénérer votre jeton depuis le portail si vous pensez qu'il a été compromis.
- L'agent ne communique qu'avec l'URL configurée dans `raguia_agent.yaml` — vérifiez qu'elle correspond bien à votre portail.
