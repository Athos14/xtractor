# Guide de Migration - gdprhubRSS v2 → v3

## Vue d'ensemble

Le script a été refactorisé pour utiliser un **design pattern Factory** avec des dataclasses configurables, permettant une meilleure extensibilité et maintenabilité.

## Architecture v3

### 1. Hiérarchie des Configurations

```python
ParserConfig (classe mère)
├── WikitableParserConfig
├── WikicodeParserConfig
└── ProseParserConfig
```

Chaque configuration définit :
- **Tags HTML/XML** à rechercher : `content_tags`, `reference_tags`, `source_tags`
- **Mapping** des clés : `key_mapping`
- **Patterns regex** : `regex_patterns`

### 2. Hiérarchie des Parsers

```python
Parser (classe abstraite)
├── WikitableParser
├── WikicodeParser
└── ProseParser
```

Chaque parser implémente :
- `can_parse(content)` : Détecte si applicable
- `parse_content(summary, src, artRGPD)` : Parse et retourne DecisionData
- `extract_references(summary)` : Extrait les articles RGPD

### 3. ParserFactory

La factory gère automatiquement :
- **Sélection du parser** approprié
- **Parsing avec auto-détection**
- **Extraction des références**

## Utilisation

### Avant (v2)

```python
# Appels en cascade avec try/except
try:
    decision = parser_Wikitable(...)
except:
    try:
        decision = parser_Wikicode_regex(...)
    except:
        decision = parser_prose(...)
```

### Après (v3)

```python
# Auto-détection avec la factory
factory = ParserFactory()
decision = factory.parse_with_auto_detection(summary, src, artRGPD)
refs = factory.extract_references_auto(summary)
```

## Ajout d'un nouveau site

Pour ajouter le support d'un nouveau site (ex: "EDPB") :

### 1. Créer la configuration

```python
@dataclass
class EDPBParserConfig(ParserConfig):
    def __post_init__(self):
        self.name = "EDPB"
        self.content_tags = ("div", "decision-box")
        self.reference_tags = ("ul", "article-list")
        super().__post_init__()

    def _default_key_mapping(self) -> Dict[str, str]:
        return {
            'Decision Number': 'numero',
            'Date': 'date',
            'Country': 'pays',
            # ... autres mappings
        }
```

### 2. Créer le parser

```python
class EDPBParser(Parser):
    def can_parse(self, content: Any) -> bool:
        # Logique de détection
        return 'edpb-specific-marker' in content

    def parse_content(self, summary, src, artRGPD):
        # Logique de parsing
        ...

    def extract_references(self, summary):
        # Logique d'extraction
        ...
```

### 3. Enregistrer dans la factory

```python
class ParserFactory:
    def __init__(self):
        self.parsers = [
            WikitableParser(WikitableParserConfig()),
            WikicodeParser(WikicodeParserConfig()),
            EDPBParser(EDPBParserConfig()),  # <-- Nouveau
            ProseParser(ProseParserConfig())
        ]
```

## Exemples de configurations

### Configuration avec tags personnalisés

```python
config = WikitableParserConfig()
config.content_tags = ("table", "custom-class")
config.reference_tags = ("div", "refs")
parser = WikitableParser(config)
```

### Configuration avec regex patterns

```python
config = WikicodeParserConfig()
config.regex_patterns['custom_field'] = r'\|\s*MyField=\s*([^\|]+)'
```

## Avantages de la nouvelle architecture

✅ **Extensibilité** : Ajout facile de nouveaux parsers
✅ **Maintenabilité** : Code mieux organisé et testé
✅ **Réutilisabilité** : Post-traitement commun factorisé
✅ **Auto-détection** : Pas besoin de spécifier le parser manuellement
✅ **Configuration claire** : Tags et mappings définis dans des dataclasses

## Tests

Pour tester le nouveau système :

```bash
python gdprhubRSS_refactored.py
# Entrer 'O' pour le mode test
```

Le mode test affichera quel parser est sélectionné pour chaque entrée.

## Rétrocompatibilité

La classe `DecisionData` reste identique, donc les fichiers générés ont le même format que la v2.

## Support

Pour toute question sur la migration, consultez le code source avec les commentaires détaillés.
